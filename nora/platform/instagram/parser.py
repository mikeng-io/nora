"""instagram-bridge SSE event → NormalizedMessage.

The bridge owns the Instagram wire; this module owns the normalization into
Nora's vocabulary, and the bridge's transport contract is the only place the
shapes are defined — a drift
here is a contract-test failure, not a judgment call. Field names below
(`kind`, `room_id`, `sender.fbid`, `mentions`, `reply_to`, `media`,
`timestamp_ms`, `from_me`) are the bridge's, verbatim.

Dropped before a `NormalizedMessage` is built:

- anything whose `kind` is not `message`, `edit`, `reaction` or `unsend` —
  `heartbeat`, `dropped` and `typing` are listener-level or not ingested, and
  this function still answers None for them so it stays total on its own.
  A `reaction` and an `unsend` become a `Touch` (`_parse_touch`), recorded
  beside the message they touch and never as a message;
- `from_me` — R-12's first pass, never ingest Nora's own sends. Unlike
  WhatsApp this field is **structural rather than derived**: the bridge
  compares `sender_fbid` against `client.GetOwnFBID()` itself, so the adapter
  is not inferring its own echo from a config value;
- a turn carrying neither text nor media, which is how the system deltas
  (`ADD_PARTICIPANT_XMAT` and friends — they arrive as ordinary messages)
  leave without a special case. A shared reel is NOT one of them: the bridge
  emits `shared`, and `_shared_line` turns it into text, so these turns
  survive;
- a `room_id` that is missing or empty. There is no `is_broadcast` sibling to
  check: Instagram has no status feed or newsletter address in this transport,
  so the WhatsApp parser's two feed drops have no analogue here.

## The one field this contract cannot ground: `conversation_type`

The inbound event (the transport contract's inbound event shape, and
`deploy/instagram-bridge/inbound.go`'s `messageEvent`) carries NO group/DM
discriminator of any kind, and the bridge
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
`NormalizedMessage.edit_of` every network produces. It carries the target's
`message_id`, the new text and usually a
timestamp — and NO sender: `from_me` is null by contract, because the delta
names nobody. So `sender_id` is `""` and ingest takes the sender from the
message the edit targets; an edit of a message Nora never recorded cannot be
attributed and is dropped there, counted.

Never raises: one malformed event is logged and skipped, mirroring the sibling
parsers — a bad turn must never kill the stream behind it.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import replace

from ...domain.instagram_links import is_handle, link_shape, parse_link
from ...domain.media import MediaRef, label_for, render_placeholder
from ...domain.models import EditTarget, NormalizedMessage, Quote, Touch
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

    Singular, not a list: the contract's `media` is one object and `firstAttachment`
    on the bridge side surfaces exactly one. A tuple still leaves here,
    because `NormalizedMessage.media` is a tuple everywhere and a
    platform-shaped exception would push the difference into every consumer.

    **`fetch_token` IS the CDN url.** The bridge exposes no media-fetch route,
    but `media.url` is a directly fetchable url whose query string carries its
    own signature (no cookies, user-agent or referer needed). There is nothing
    to ask the bridge for — the url IS the handle, and `InstagramMediaSource`
    is what resolves it. Without it an image lands as a bare `[image]` with no
    handle and Nora cannot read it.

    `platform_id` is `attachment_fbid` when the delta carried one: Instagram's
    own id for the stored attachment, the same KIND of thing signal-cli's
    attachment id is. Nothing fetches by it — the url does that — so it is
    recorded and never rendered, exactly as `MediaRef` says.

    A `one_time` (`RAVEN_*`) delta carries `attachment: null` and so has NO
    url by design; it therefore gets no token and stays unfetchable, which is
    deliberate. On
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


# The link rules live in ONE module, `nora.domain.instagram_links`, which
# `instagram_read` reads too, so the transcript and the tool cannot disagree
# about what a valid Instagram link is. `_is_handle` stays this module's name
# for the shared predicate.
_is_handle = is_handle


def _shared_target(target_url: object, author: str = "") -> tuple[str, str]:
    """`(kind, link)` for a shared item, both read off `target_url`'s PATH.

    **The kind comes from the url, because nothing else on the wire carries
    it.** A shared reel and a shared story both arrive with `typename`
    `SlideMessagePortraitXMA`, and `caption`, `title`, `subtitle` and `layout`
    empty or absent. The path is the only field that tells them apart:
    `/reel/<code>/` (or `/reels/<code>/`) is a reel,
    `/p/<code>/` a post, `/stories/<user>/<pk>` a story. Anything else is
    `post`: no new word is invented for an unmeasured shape.

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
        # renders is `post` for those, with no link.
        return "post", ""
    link = parse_link(target_url)
    if link is None or link.kind != kind:
        return kind, ""
    if kind == "story" and link.key.lower() != author.strip().lstrip("@").lower():
        return kind, ""
    return kind, link.url


#: Where a caption's sentence ends. A Latin `.`, `!` or `?` (a run of them,
#: so `?!` and `...` end one sentence) ends it only when whitespace or the
#: end of the text follows, so the dot in `3.5`, `example.com` or a
#: shortcode never cuts a sentence in half. A Chinese full stop, `!` or `?`
#: (U+3002, U+FF01, U+FF1F) ends it where it stands, because Chinese puts
#: no space after one. A newline ends it too, and emoji and hashtags never
#: do. Closing quotes and brackets right after the mark stay with the
#: sentence they close.
_SENTENCE_END = re.compile(
    r"[.!?]+[\"'\u201d\u2019\u300d\u300f\uff09)]*(?=\s|$)"
    r"|[\u3002\uff01\uff1f]+[\"'\u201d\u2019\u300d\u300f\uff09)]*"
    r"|\n"
)

#: The longest caption excerpt the transcript carries, in characters, before
#: its `…`. One sentence can run to hundreds of characters (a Cantonese
#: caption often has no stop in it at all), and the excerpt exists to be
#: SHORT, so a longer one is cut: at the last space inside the limit when
#: that space is in its second half, at the limit itself otherwise.
_CAPTION_EXCERPT_MAX = 160

#: How much REAL text an excerpt must hold before it stops at a sentence
#: end: letters and digits, not counting emoji, `#tags` or `@mentions`. A
#: «sentence» ended by the dot in `Mr.`, `U.S.`, `e.g.` or `Step 1.`, or a
#: first line that is only emoji, hashtags or a brand word, says nothing
#: about the post, so the excerpt runs on to the next sentence end.
_CAPTION_EXCERPT_MIN_TEXT = 40

#: A `#tag` or an `@mention`, which the minimum does not count.
_TAG_OR_MENTION = re.compile(r"[#@][^\s#@]+")

#: What marks an excerpt as cut: the one character that says «there is more
#: of this caption than the line shows». A trailing `...` or `…` the
#: caption already had is dropped first, so there is only ever one.
_ELLIPSIS = "\u2026"
_TRAILING_ELLIPSIS = re.compile(r"(?:\.{3,}|\u2026)+$")

#: Code points that continue the character before them: a zero width
#: joiner, a variation selector, a skin tone, a keycap, a tag character.
#: A cut before one of these, or just after a joiner, would split an emoji.
_ZWJ = "\u200d"


def _continues_previous(ch: str) -> bool:
    cp = ord(ch)
    return (
        ch == _ZWJ
        or 0xFE00 <= cp <= 0xFE0F          # variation selectors
        or 0x1F3FB <= cp <= 0x1F3FF        # skin tone modifiers
        or 0xE0020 <= cp <= 0xE007F        # tag characters (subdivision flags)
        or cp == 0x20E3                    # combining keycap
        or unicodedata.category(ch).startswith("M")
    )


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _hard_cut(text: str, limit: int) -> str:
    """`text[:limit]`, moved back so it never splits an emoji: not before
    a code point that continues the one before it, not just after a joiner,
    and not between the two halves of a flag."""
    i = limit
    while 0 < i < len(text) and (_continues_previous(text[i]) or text[i - 1] == _ZWJ):
        i -= 1
    if 0 < i < len(text) and _is_regional_indicator(text[i]):
        run = 0
        while run < i and _is_regional_indicator(text[i - 1 - run]):
            run += 1
        if run % 2:
            i -= 1
    return text[:i]


def _real_text_length(text: str) -> int:
    """Letters and digits in `text`, once `#tags` and `@mentions` are out.
    Emoji and punctuation are neither, so they never count."""
    return sum(1 for ch in _TAG_OR_MENTION.sub(" ", text) if ch.isalnum())


def _caption_excerpt(caption: str) -> str:
    """A LINKED shared item's caption as the transcript carries it: its
    first sentence, or as many as it takes to hold
    `_CAPTION_EXCERPT_MIN_TEXT` characters of real text, then `…` when
    anything was left out, and never more than `_CAPTION_EXCERPT_MAX`
    characters before that `…`. A caption that is all one short sentence
    comes back unchanged, with no `…`.

    **Why the transcript does not carry the whole caption.** A line carrying a
    full ~1,000 character caption is answered from the line alone, as if it
    were the whole post, and no tool is called. A whole caption reads as the
    whole post. An excerpt that visibly
    stops reads as a pointer to something she has not seen, and the link
    beside it is how she sees it: `instagram_read` returns the full caption
    from Instagram itself. That is also why only a linked share is cut
    (`_shared_line`): without a link nothing can read the rest.
    """
    text = caption.strip()
    cut = False
    for end in _SENTENCE_END.finditer(text):
        head = text[:end.end()].rstrip()
        if len(head) >= _CAPTION_EXCERPT_MAX or (
            _real_text_length(head) >= _CAPTION_EXCERPT_MIN_TEXT
        ):
            if text[end.end():].strip():
                text, cut = head, True
            break
    if len(text) > _CAPTION_EXCERPT_MAX:
        window = text[:_CAPTION_EXCERPT_MAX]
        space = window.rfind(" ")
        # At a word boundary when the last space is in the window's second
        # half. An early one («新店開張 » before two hundred Chinese
        # characters, `Hi ` before a row of flags) would throw away almost
        # everything, so the cut is then at the limit, never inside an emoji.
        if space >= _CAPTION_EXCERPT_MAX // 2:
            text = window[:space]
        else:
            text = _hard_cut(text, _CAPTION_EXCERPT_MAX)
        cut = True
    if not cut:
        return text
    return _TRAILING_ELLIPSIS.sub("", text.rstrip()).rstrip() + _ELLIPSIS


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

    **The caption is usually absent.** A shared reel or story typically carries
    only `author`, `target_url` and `typename`. So the line that is free is
    «somebody shared a reel by @x, here is the link», and the caption is
    rendered when a delta carries one.

    **When there is one and the line has a link, it carries the caption's
    opening, not all of it** (`_caption_excerpt`): a line
    holding a whole long caption is answered as if it were the whole post.
    The `…` says there is more, and the link reads it. A line with NO link
    keeps the whole caption: nothing can fetch the rest for it, so cutting
    it would lose words for good.

    A shared reel arrives with no text and no media, so without this line it
    would be dropped for having neither and Nora would never learn anything had
    been posted. That is why this renders even when there is no caption:
    «somebody shared a reel» is a fact about the room.

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
    # put `[shared post]` on ordinary messages;
    # `test_a_message_with_no_shared_object_is_unchanged` guards it, and it
    # matters most here because it fires on EVERY message rather than on
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
        # declares both, so either may carry it.
        caption = str(shared.get("title") or "").strip()
    # NO SQUARE BRACKETS FROM A STRANGER. A caption of `x] [image 0a1b2c] [y`
    # closes this bracket and opens a forged placeholder, and
    # `enrich_placeholders` would then hang whatever reading `0a1b2c` resolves
    # to off it. Round brackets read the same to a model and parse as nothing.
    caption = caption.replace("[", "(").replace("]", ")")
    # Its opening, and `…` when there was more (`_caption_excerpt`): a whole
    # caption read as the whole post, and the link is what reads the rest.
    # Only WITH a link: without one nothing can read the rest, so the line
    # keeps every word.
    caption = _caption_excerpt(caption) if link else caption
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


def _parse_touch(event: dict) -> NormalizedMessage | None:
    """A `kind: "reaction"` or `kind: "unsend"` event → a `Touch` on the
    message it names.

    A reaction carries its reactor (`sender_fbid`), the emoji, `removed` and
    usually a time; `from_me` true is Nora's own reaction and is dropped
    (R-12). An unsend is a message id and nothing else — `from_me` is null by
    contract — so its sender is `""` and ingest attributes it to the target's
    author (only an author can unsend on Instagram). A target naming nothing
    is dropped."""
    if event.get("from_me"):
        return None
    room_id = conversation_id_for(event.get("room_id"))
    target = str(event.get("message_id") or "")
    if not room_id or not target:
        return None
    kind = event.get("kind")
    if kind == "reaction":
        emoji = event.get("emoji")
        emoji = emoji if isinstance(emoji, str) else ""
        removed = bool(event.get("removed"))
        # A REMOVAL may carry no emoji: the bridge builds it from
        # `slidetypes.DeleteReactionEvent`, whose `Reaction` is the same
        # struct as a new reaction's and nothing guarantees Meta fills its
        # emoji on a delete. It is recorded as a removal with `""`, as
        # WhatsApp's is, rather than lost. A NEW reaction with no emoji
        # names nothing and is dropped.
        if not emoji and not removed:
            return None
        touch = Touch(
            kind="reaction", target=EditTarget(message_id=target), emoji=emoji,
            removed=removed,
        )
        sender = fbid_form(event.get("sender_fbid"))
    else:
        touch = Touch(kind="delete", target=EditTarget(message_id=target))
        sender = ""
    timestamp_ms = event.get("timestamp_ms")
    return NormalizedMessage(
        platform="instagram",
        conversation_id=room_id,
        conversation_type="group",
        sender_id=sender,
        sender_name=None,
        timestamp=(
            timestamp_ms
            if isinstance(timestamp_ms, int) and not isinstance(timestamp_ms, bool)
            and timestamp_ms > 0
            else 0          # ingest stamps arrival (`nora.ingest.stages.touch`)
        ),
        body="",
        raw=event,
        touch=touch,
    )


def parse_event(event: dict) -> NormalizedMessage | None:
    try:
        if isinstance(event, dict) and event.get("kind") == "edit":
            return _parse_edit(event)
        if isinstance(event, dict) and event.get("kind") in ("reaction", "unsend"):
            return _parse_touch(event)
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
            # `MediaRef`, no custody. A `[cover image <handle>]` in the line
            # is an affordance a cheap `media_fetch` call SUCCEEDS on, with a
            # one-frame reading, and success ends the turn. So the line
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
            # WITHOUT A LINK, the cover is all there is: held like any photo,
            # read only when `media_fetch` asks (Nora reads shared content when
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
            # — choosing the wrong one of the three produces no error anywhere.
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
