"""instagram-bridge SSE event → NormalizedMessage
(design/instagram-adoption/03-transport-contract.md).

The bridge owns the Instagram wire; this module owns the normalization into
Nora's vocabulary, and `03` is the only place the shapes are defined — a drift
here is a contract-test failure, not a judgment call. Field names below
(`kind`, `room_id`, `sender.fbid`, `mentions`, `reply_to`, `media`,
`timestamp_ms`, `from_me`) are the bridge's, verbatim.

Dropped before a `NormalizedMessage` is built:

- anything whose `kind` is not `message` or `edit` — `heartbeat`, `dropped`,
  `reaction`, `unsend` and `typing` are listener-level or not ingested, and
  this function still answers None for them so it stays total on its own;
- `from_me` — R-12's first pass, never ingest Nora's own sends. Unlike
  WhatsApp this field is **structural rather than derived**: the bridge
  compares `sender_fbid` against `client.GetOwnFBID()` itself, so the adapter
  is not inferring its own echo from a config value;
- a turn carrying neither text nor media, which is how the system deltas
  (`ADD_PARTICIPANT_XMAT` and friends — they arrive as ordinary messages,
  measured 02) leave without a special case. **A shared reel used to leave
  through this same line and it was a bug, not a drop**: `MessageContentXMA`
  had no case in the bridge's content switch, so it produced no text and no
  media and was discarded here — Nora did not fail to read the reel, she never
  learned one had been posted. The bridge now emits `shared`, and
  `_shared_line` turns it into text, so these turns survive;
- a `room_id` that is missing or empty. There is no `is_broadcast` sibling to
  check: Instagram has no status feed or newsletter address in this transport,
  so the WhatsApp parser's two feed drops have no analogue here.

## The one field this contract cannot ground: `conversation_type`

**Reported as an ambiguity rather than papered over.** The inbound event
(`03` §"Inbound event shape", and `deploy/instagram-bridge/inbound.go`'s
`messageEvent`) carries NO group/DM discriminator of any kind, and the bridge
exposes no thread-info route to ask. WhatsApp reads it off the JID's server
and Discord off `message.guild is None`; Instagram hands over an opaque
numeric thread id that is spelled identically either way.

So this parser reports `group` unless the bridge says otherwise, and the
deny-by-default that v1 actually rests on is `policy.toml`: it is exact-string
on that opaque id, and an operator has to obtain and paste a thread id before
anything in it is answered. The send-side `conversation_type != "group"`
refusal in `send.py`/`react.py` is still real code and still fires — for every
caller that builds a `ConversationTarget` itself (a reminder row, the fire
path) — it simply cannot fire on an inbound Instagram DM, because nothing on
the wire distinguishes one. `is_group` is read when present so the day the
bridge grows the field this parser already honours it, with no second place to
change.

An EDIT arrives as its own `kind: "edit"` event rather than as a message with
an `edit_of` (WhatsApp's shape), and `_parse_edit` turns it into the same
`NormalizedMessage.edit_of` every network produces (design/message-edits/03
§1). It carries the target's `message_id`, the new text and usually a
timestamp — and NO sender: `from_me` is null by contract, because the delta
names nobody. So `sender_id` is `""` and ingest takes the sender from the
message the edit targets; an edit of a message Nora never recorded cannot be
attributed and is dropped there, counted.

Never raises: one malformed event is logged and skipped, mirroring the sibling
parsers — a bad turn must never kill the stream behind it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from ...domain.instagram_links import is_handle, link_shape, parse_link
from ...domain.media import MediaRef, label_for, render_placeholder
from ...domain.models import EditTarget, NormalizedMessage, Quote
from .ids import conversation_id_for, fbid_form, is_person_mention

log = logging.getLogger("nora.instagram.parser")

#: The bridge's `media.kind` → the media type this adapter RECORDS.
#:
#: The wire carries no mimetype anywhere — not on the delta, not on the CDN
#: url in any form worth reading — so the subtype is genuinely unknown and
#: `*` says exactly that. `nora.domain.media.parse_media_type` needs a
#: `top/subtype` pair to classify at all, and the TOP level is the part that
#: decides the label, so this records the half that was observed and refuses
#: to invent the half that was not. Deriving a subtype from the url's path
#: would be scraping — the one thing `inbound.go`'s first rule forbids,
#: because an Instagram CDN url's query string is full of `&` and a url read
#: one character wrong comes back `403 Bad URL hash`, which reads exactly like
#: an expired session.
#:
#: `animated` is absent on purpose: it becomes `sticker=True` instead, which
#: is the axis `nora.domain.media` already has for "an image that is pack art
#: rather than somebody's photograph".
#:
#: `one_time` is absent too, and that is the honest answer rather than a gap.
#: A RAVEN delta with `attachment: null` does not say whether the bytes were
#: an image or a video — `fromRaven` only reaches that branch when there is no
#: attachment to read a kind from — so anything named here would be a guess
#: about content Nora deliberately did not fetch. It falls through to `""` and
#: renders `[file]`: something arrived, and she cannot see it.
_MEDIA_TYPE_BY_KIND = {
    "image": "image/*",
    "video": "video/*",
    "audio": "audio/*",
}


def _media_ref(media: dict) -> MediaRef | None:
    """The bridge's single `media` object reduced to what labelling needs.

    Singular, not a list: `03`'s `media` is one object and `firstAttachment`
    on the bridge side surfaces exactly one. A tuple still leaves here,
    because `NormalizedMessage.media` is a tuple everywhere and a
    platform-shaped exception would push the difference into every consumer.

    **`fetch_token` IS the CDN url**, and it took a live defect to get here.
    This function recorded an empty `platform_id` and an empty `fetch_token`
    on the reasoning that the bridge exposes no media-fetch route, so a handle
    would name something no code could resolve. The premise is true and the
    conclusion did not follow: `media.url` is a directly fetchable url whose
    query string carries its own signature, measured returning `200` with no
    cookies, no user-agent and no referer. There is nothing to ask the bridge
    for — the url IS the handle, and `InstagramMediaSource` is what resolves
    it. Until it was recorded, every image Miles sent landed as a bare
    `[image]` with no handle and Nora could not read one of them.

    `platform_id` is `attachment_fbid` when the delta carried one: Instagram's
    own id for the stored attachment, the same KIND of thing signal-cli's
    attachment id is. Nothing fetches by it — the url does that — so it is
    recorded and never rendered, exactly as `MediaRef` says.

    A `one_time` (`RAVEN_*`) delta carries `attachment: null` and so has NO
    url by design; it therefore gets no token and stays unfetchable, which is
    the decision `03-transport-contract.md` made and not an oversight. On
    Instagram, fetching view-once media IS viewing it and is visible to the
    sender.

    `voice_note` is FALSE even for `kind: "audio"`. The bridge added that kind
    for voice notes, but Instagram does not distinguish a shared song from
    somebody speaking, and `nora/domain/media.py`'s own rule is that calling a
    song a voice note puts words in a member's mouth. `[audio]` is the honest
    label; a wrong `[voice note]` would make Nora answer a track as if a human
    had spoken.
    """
    kind = media.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    url = media.get("url")
    fbid = media.get("attachment_fbid")
    return MediaRef(
        # A STRING on the wire (`inbound.go`'s `AttachmentFBID string`), so a
        # number here is a contract drift and not something to coerce quietly.
        platform_id=fbid if isinstance(fbid, str) else "",
        media_type=_MEDIA_TYPE_BY_KIND.get(kind, ""),
        sticker=(kind == "animated"),
        # `attachment:` is the same prefix `DiscordMediaSource` reads, because
        # it is the same shape: a url off the event rather than one derived
        # from an id. An absent or non-string url leaves the token empty and
        # the ref unfetchable, which is what `one_time` needs.
        fetch_token=f"attachment:{url}" if isinstance(url, str) and url else "",
    )


# The link rules (#435) live in ONE module, `nora.domain.instagram_links`, which
# `instagram_read` reads too, so the transcript and the tool cannot disagree
# about what a valid Instagram link is. `_is_handle` stays this module's name
# for the shared predicate.
_is_handle = is_handle


def _shared_target(target_url: object, author: str = "") -> tuple[str, str]:
    """`(kind, link)` for a shared item, both read off `target_url`'s PATH.

    **The kind comes from the url, because nothing else on the wire carries
    it.** Measured 2026-09-24 on a live shared reel and a live shared story:
    `typename` was `SlideMessagePortraitXMA` for BOTH, and `caption`, `title`,
    `subtitle` and `layout` were empty or absent. The path is the only field
    that told them apart: `/reel/<code>/` (or `/reels/<code>/`) is a reel,
    `/p/<code>/` a post, `/stories/<user>/<pk>` a story. Anything else is
    `post`, which is what every share was called before this — no new word is
    invented for a shape nobody has measured.

    **The link is REBUILT, never passed through.** The raw url's query is
    tracking flags plus, on a story, `reel_owner_id`: the owner's igid, an
    identifier R-18 keeps off every surface a model reads. So the link is
    assembled from validated path segments onto a fixed origin with no query
    and no fragment, and any segment outside its charset, a host that is not
    Instagram's, a port, or credentials in the netloc leave the link out
    entirely (`""`) rather than render anything raw. The kind survives a
    rejected link: it is a label about the path's shape, not a claim about
    where it points.

    **A story's username segment must BE the author.** The segment is the
    one place in any of the three shapes where a path carries a name, and the
    charset alone admits an all-digit one — which is what an igid looks like.
    So the segment must hold a non-digit AND match `author` (the bridge's
    `shared.author`, `@` stripped) case-insensitively; anything else leaves
    no link. That binds the rendered name to a value the delta already
    carries, rather than to whatever the url's path happens to say.
    """
    if not isinstance(target_url, str) or not target_url.strip():
        return "post", ""
    kind = link_shape(target_url, story_ring=False)
    if kind not in ("reel", "post", "story"):
        # A profile link, an unknown path, or no path at all: the label this
        # renders has always been `post` for those, with no link.
        return "post", ""
    link = parse_link(target_url)
    if link is None or link.kind != kind:
        return kind, ""
    if kind == "story" and link.key.lower() != author.strip().lstrip("@").lower():
        return kind, ""
    return kind, link.url


def _shared_line(shared: object) -> tuple[str, str, str]:
    """A SHARED reel, post or story, rendered as text the model can read for
    free, with its kind and the validated link that line carries (`""` when
    it carries none). The caller needs both on their own: whether the
    thumbnail is kept at all turns on them (`parse_event`).

    The bridge's `shared` object (`deploy/instagram-bridge/inbound.go`'s
    `eventShared`) names something posted elsewhere on Instagram and sent into
    the room. What it renders — the kind, the author, a clean link, and the
    caption when there is one — is already in the delta, so a room where
    people share reels all day costs nothing extra to follow: no fetch, no
    model call, no vision pass. Only a direct question about one particular
    item reaches for the item itself, through its link (`instagram_read`),
    or, when the line has no link, for the cover (`media_fetch`).

    **The caption is usually absent.** `design/instagram-adoption/13` called
    it the free depth; measured 2026-09-24, a live shared reel and a live
    shared story each carried only `author`, `target_url` and `typename`. So
    the line that is actually free is «somebody shared a reel by @x, here is
    the link», and the caption is rendered when a delta carries one.

    Until this existed, a shared reel arrived with no text and no media and was
    dropped for having neither, so Nora never learned anything had been posted
    at all. That is why this renders even when there is no caption: «somebody
    shared a reel» is a fact about the room, and it was previously invisible.

    ## Where the link goes

    INSIDE the bracket, after the author and before the caption. Inside,
    because it belongs to the shared item and not to the sharer's own words;
    before the caption, because the caption is a stranger's free text and a
    link after it would read as part of it. A post or reel line with a link
    has no placeholder after it at all (`parse_event` drops the thumbnail);
    a story line, or a line without a link, keeps its `[cover image]`
    TRAILING, the run `body_with_handles` rewrites once custody has the
    bytes.

    ## The author is NOT the sender, and the wording has to keep them apart

    `sender` is who shared it; `shared.author` is whose post it is. They are
    different people and the render says so explicitly, because a line that
    blurred them would attribute a stranger's content to a member of the room
    — the same class of error as calling a shared song a voice note.

    ## It is quoted, and marked as somebody else's words

    The caption is written by whoever posted the reel, who is very often
    outside the room and outside `identity.toml` entirely. It is wrapped the
    way the social tools' results are wrapped, for the same reason: it is text
    from a stranger arriving inside Nora's context, and it must not read as
    something a member said or as an instruction.

    Returns `("", "", "")` when there is nothing to say, so the caller's
    `parts` list is unchanged and a non-XMA message keeps exactly the body it
    had.
    """
    # An EMPTY dict is not the same as a dict whose values are empty, and the
    # difference decides whether a claim is made. `{}` carries no keys at all
    # and is indistinguishable from the field being absent, so it renders
    # nothing. A dict WITH keys whose values happen to be blank is the bridge
    # saying "there was a shared post here and I could not read its text",
    # which still deserves the bare `[shared post]` below. Collapsing the two
    # put `[shared post]` on ordinary messages — caught by
    # `test_a_message_with_no_shared_object_is_unchanged`, which is the test
    # that matters most here because it fires on EVERY message rather than on
    # the rare one this function was written for.
    if not isinstance(shared, dict) or not shared:
        return "", "", ""
    raw_author = str(shared.get("author") or "").strip().lstrip("@")
    kind, link = _shared_target(shared.get("target_url"), raw_author)
    # THE AUTHOR RENDERS ONLY AS A HANDLE. It is a stranger's string inside a
    # bracket the transcript join parses, so anything outside the username
    # charset — a `]`, a space, `The Site Title` from a link-preview XMA, or
    # an all-digit string shaped like an igid — is dropped rather than
    # escaped: an author that is not a handle is not worth an `@`. One `@`,
    # however the bridge spelled it.
    author = raw_author if _is_handle(raw_author) else ""
    caption = str(shared.get("caption") or "").strip()
    if not caption:
        # `title` is sometimes the only text on the delta — the library
        # declares both and no probe has shown which a live one populates.
        caption = str(shared.get("title") or "").strip()
    # NO SQUARE BRACKETS FROM A STRANGER. A caption of `x] [image 0a1b2c] [y`
    # closes this bracket and opens a forged placeholder, and
    # `enrich_placeholders` would then hang whatever reading `0a1b2c` resolves
    # to off it. Round brackets read the same to a model and parse as nothing.
    caption = caption.replace("[", "(").replace("]", ")")
    # The bridge answers `null` rather than an empty object for an XMA it
    # could not read, so a dict with nothing readable in it is a shape it read
    # that carried neither name nor words nor link. It still renders
    # `[shared post]`: something WAS shared, and silence is what this whole
    # function exists to end.
    line = f"shared {kind}"
    if author:
        line += f" by @{author}"
    if link:
        line += f" {link}"
    if caption:
        line += f" — {caption}"
    return f"[{line}]", kind, link



def _mentioned_ids(entries: object) -> frozenset[str]:
    """Every fbid a `mentions[]` entry names a PERSON with.

    ONE id form, unlike WhatsApp's two: a mention carries `user_fbid` and
    nothing else, and `self_id.derive_identity` advertises the same number, so
    there is no second spelling for a tag to hide behind.

    `range_type: "THREAD"` entries are DROPPED — see `ids.is_person_mention`
    for why admitting a room's own id into a person-resolving set is the bug
    and not the convenience.
    """
    out: set[str] = set()
    if not isinstance(entries, list):
        return frozenset()
    for m in entries:
        if not isinstance(m, dict):
            continue
        if not is_person_mention(m.get("range_type")):
            continue
        fbid = fbid_form(m.get("fbid"))
        if fbid:
            out.add(fbid)
    return frozenset(out)


def _quoted(reply_to: object) -> Quote | None:
    """The message this one replies to, as a pointer — `ReplyToNoraPolicy`'s
    half of the engagement contract.

    `author_id` gets the same normalization as `sender_id` (bare fbid),
    because the policy matches it against the same self-alias set.
    `quoted_ts` is the parent's `timestamp_ms`, or **0 when the delta carried
    only the id** — `replyOf` on the bridge side reports the relation even
    when the parent body did not come with it, which is the same honest
    degradation WhatsApp takes on a quote older than its cache: the reply
    relation survives, the parent pointer does not, and a `by_ts` lookup
    misses on 0 and renders no parent.
    """
    if not isinstance(reply_to, dict):
        return None
    ts = reply_to.get("timestamp_ms")
    return Quote(
        author_id=fbid_form(reply_to.get("sender_fbid")),
        text=str(reply_to.get("text") or "").strip(),
        quoted_ts=ts if isinstance(ts, int) and not isinstance(ts, bool) else 0,
    )


def _parse_edit(event: dict) -> NormalizedMessage | None:
    """A `kind: "edit"` event → an edit of the message it names.

    `from_me` is null by contract (the bridge cannot know), so R-12 is not
    decided here: ingest refuses an edit whose target is one of Nora's own
    rows. The edit time falls back to ARRIVAL when the delta carried none —
    a zero would sort the edit before every other and render no age, which is
    exactly the «edited, but when?» the fold exists to answer; arrival is
    seconds late, never wrong in kind. The text is the whole new body:
    Instagram edits text messages only, so there is no placeholder run to
    keep."""
    room_id = conversation_id_for(event.get("room_id"))
    target = str(event.get("message_id") or "")
    text = str(event.get("text") or "").strip()
    if not room_id or not target or not text:
        return None
    timestamp_ms = event.get("timestamp_ms")
    return NormalizedMessage(
        platform="instagram",
        conversation_id=room_id,
        conversation_type="group",
        sender_id="",
        sender_name=None,
        timestamp=(
            timestamp_ms
            if isinstance(timestamp_ms, int) and not isinstance(timestamp_ms, bool)
            and timestamp_ms > 0
            else int(time.time() * 1000)
        ),
        body=text,
        raw=event,
        # The delta carries no mentions, so none are claimed: ingest carries
        # the original's forward (`EditTarget.mentions_known`).
        edit_of=EditTarget(message_id=target, mentions_known=False),
    )


def parse_event(event: dict) -> NormalizedMessage | None:
    try:
        if isinstance(event, dict) and event.get("kind") == "edit":
            return _parse_edit(event)
        if not isinstance(event, dict) or event.get("kind") != "message":
            return None                     # heartbeat / dropped are listener-level
        if event.get("from_me"):
            return None                     # R-12's first pass: never ingest our own sends
        room_id = conversation_id_for(event.get("room_id"))
        if not room_id:
            return None

        shared, kind, link = _shared_line(event.get("shared"))
        media = event.get("media")
        ref = _media_ref(media) if isinstance(media, dict) else None
        if ref is not None and shared:
            # The media on a shared item is its THUMBNAIL (`fromXMAPreview`),
            # not something a member sent. Keyed on the SAME predicate that
            # wrote the shared line, so what happens to it and the line cannot
            # disagree.
            #
            # A POST OR REEL WITH A LINK DROPS THE THUMBNAIL: no placeholder, no
            # `MediaRef`, no custody. Measured live twice (2026-09-24, after
            # #454): the line read `[shared reel by @x <link>] [cover image
            # <handle>]`, the question was «what is in this reel», and she
            # called `media_fetch` on the handle, got a real one-frame reading
            # WITH the note saying it was only the cover, and answered from
            # that frame. Renaming the handle and annotating the result were
            # both wording; a handle in the line is an affordance that a
            # cheap call SUCCEEDS on, and success ends the turn. So the line
            # carries no handle to settle for, and the link is the one thing
            # left to open (`instagram_read`, whose result hands each item's
            # url to `media_fetch`). The cover is not kept for later either:
            # a post or reel link reads that item itself, frames included.
            #
            # A STORY KEEPS ITS COVER, link or not. A story link does not read
            # the frame that was shared: `instagram_read` reads the author's
            # CURRENT story (the link's `story_pk` is never used), which may
            # no longer hold that frame. The thumbnail is the only faithful
            # copy of what was shared, so it is held on demand like the
            # no-link fallback below.
            #
            # WITHOUT A LINK, the cover is all there is, and #454's fallback
            # stands: held like any photo, read only when `media_fetch` asks
            # (the operator's rule is that Nora reads shared content when
            # tagged, never on every share; `MediaRef.on_demand`), and
            # rendered `[cover image]` (`label_for`) so the line says the
            # picture is the item's cover and not the item.
            ref = None if link and kind != "story" else replace(ref, on_demand=True)
        refs = (ref,) if ref is not None else ()

        parts = []
        text = str(event.get("text") or "").strip()
        if text:
            parts.append(text)
        # BETWEEN the sharer's own words and the media placeholder, because
        # that is the order they happened in: somebody typed something, about
        # a post, which (when it has no link) carries a thumbnail. The
        # placeholder run has to stay TRAILING (`body_with_handles` rewrites
        # it in place), so this cannot go after it.
        if shared:
            parts.append(shared)
        # Placeholders LAST — the same load-bearing trailing run
        # `nora.domain.media.body_with_handles` rewrites on the Signal side
        # once custody has the bytes. A media-only turn survives through this
        # line as a bare `[image]`.
        parts += [render_placeholder(label_for(r)) for r in refs]
        if not parts:
            return None                     # neither text nor media — nothing to ingest

        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        timestamp_ms = event.get("timestamp_ms")
        is_group = event.get("is_group")
        return NormalizedMessage(
            platform="instagram",
            conversation_id=room_id,
            # See the module docstring: the wire carries no discriminator, so
            # an absent `is_group` reads as a group and `policy.toml` is the
            # gate that actually holds.
            conversation_type="group" if is_group is None or is_group else "dm",
            # The MESSAGING id, never `sender.igid` and never `sender.username`
            # — 02's opening failure is choosing the wrong one of the three,
            # which produces no error anywhere.
            sender_id=fbid_form(sender.get("fbid")),
            # DISPLAY ONLY, exactly like a Signal profile name or a WhatsApp
            # push name: shown to the operator and rendered in a transcript,
            # never matched on. `username` is not a matchable kind for this
            # platform and `Identifier` refuses to carry one.
            sender_name=sender.get("username") or None,
            timestamp=(
                timestamp_ms
                if isinstance(timestamp_ms, int) and not isinstance(timestamp_ms, bool)
                else 0
            ),
            body=" ".join(parts),
            raw=event,
            mentions=_mentioned_ids(event.get("mentions")),
            quote=_quoted(event.get("reply_to")),
            media=refs,
            # `mid.$…` — what a reaction and an unsend address a message by
            # (`/threads/{thread_fbid}/messages/{message_id}`, and its
            # `/reaction`), and what a reply's `reply_to` names.
            # `(author, timestamp)` means nothing to any of them.
            message_id=str(event.get("message_id") or ""),
        )
    except Exception:
        log.exception("unparseable instagram event, skipping")
        return None
