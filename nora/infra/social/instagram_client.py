"""`InstagramSocialClient` — the second `SocialProvider`, and the whole of the
exception list (design/instagram-adoption/08, /10, /11 Flow 3).

It serves exactly ONE `(platform, what)` row — `(instagram, story)` — over the
instagram-bridge's `POST /social/story` on the private compose `nora` network.
That row is here because OpenCLI 1.8.7 has no story READ anywhere (its `story`
verb posts one), so instameow's `FetchReel` is the only path that exists; every
other Instagram verb stays on the default backend. `SocialRouter`'s docstring
carries the rule this row is the single exception to.

## What this client does NOT do, and why that is the interesting part

**It does not resolve a subject, and it does not authorize a tier.** Both used
to live here and moved to `SocialRouter` on 2026-09-22
(design/instagram-adoption/10 §"Who owns what"). The reason is not tidiness:
an authorization check inside ONE backend can only ever govern that backend's
rows, and the seven OpenCLI rows were therefore running with no subject
authorization at all while node 07 says the tier ladder governs *all* social
reads. A rule that reaches one of eight rows is not a ladder.

So `target` arrives here already RESOLVED and already AUTHORIZED: it is the
igid the person index bound, not the `person_id` the model named. This client
never sees a `person_id`, has no `SocialSubjectIndex`, and cannot produce
`ambiguous` or `refused` — those are the router's sentences now, said before
anything is dispatched. Architecture bounds *how*, the tier ladder bounds
*who*, retention bounds *what survives*; all three are required, and this file
owns only the first.

## What it does do that the OpenCLI client does not

**It returns REFERENCES, never content.** An item is `{id, kind, taken_at,
url}` and is BUILT FIELD BY FIELD from the envelope rather than passed
through, so a caption, a transcript or a description the bridge might one day
add cannot leak into a result. `social_fetch` finds out *that* something
exists and where; `media_fetch` finds out *what it is*. The moment a story
result carries the content inline it has absorbed `media_fetch`'s job and the
model stops spending deliberately — "does Miles have a story up" must stay a
different cost from "what is in it".

**A whitelist of KEY NAMES is not that rule — it is a third of it.** An
adversarial audit (2026-09-22) executed the three value channels the key
whitelist left open and got content through all three: `note` was a verbatim
unbounded passthrough into the model's instruction slot; `url` was
scheme-checked only, so any https host arrived rendered as text plus an
instruction to `media_fetch` it; and `id` accepted ANY type and `str()`-ed it,
so `id={"caption": "at the beach with tyler"}` put that caption in the result
while the guard test — which asserted the key SET and that "beach" was not in
`text` — stayed green on both counts. So every value is now bound to its own
shape: the note is ours and the bridge's is dropped, the URL host must be a
MEASURED Instagram CDN host, and the id must look like an id. A key whitelist
answers "which fields"; only a value check answers "carrying what".

## No cursor, and stories do not paginate

A reel arrives WHOLE from the protocol (two items in the measured case), so
there is no `next_cursor` on this path and asking for more of a story is
asking for something that does not exist. `limit` is therefore a NORA-SIDE
clamp, applied after the response — worth doing anyway, because a fifteen-item
story would otherwise arrive entire and every token of a tool result is
re-sent on every subsequent model call. The SAME clamp is applied to the
number the bridge is told, so the two ends cannot disagree about what was
asked for: `limit=0` used to ride through verbatim to a process that gets to
invent what it means.

## The URLs expire, and that cuts in the design's favour

Instagram CDN URLs are signed (`oh=`, `oe=`, `_nc_sid`) and the signature IS
the authorization: measured, a story video fetches `200 video/mp4` with no
cookies, no UA and no referer, so `media_fetch` carrying no session is not a
problem. They are also valid for a window rather than indefinitely, which is
why `expires_at` rides the result: a two-step "list now, look later" flow has
to be SOON after, and a stored URL is a dead link.

## A story must be SAID to be a story, and the result is what says it

The media vanishes in 24 hours. Nora's REPLY about it does not: every sent
message is fed to Honcho as durable conclusions
(`nora/engagement/stages/feedback.py`). So «he was at the beach» launders a
perishable observation into permanent knowledge, indistinguishable a week
later from something he actually said, while «his story from 3h ago showed a
beach» ages honestly on its own (design/instagram-adoption/06 §"a story must
be SAID to be a story"). The phrasing is the whole mitigation, because it
travels WITH the claim — a retention rule cannot reach into a Honcho
conclusion and a phrasing rule is already inside it.

Two things therefore ride an `ok` result: `source_kind` says what this is in
one token nothing can mistake — `instagram_story`, not a bare URL that could
be anything — and `note` carries the framing, adjacent to the `taken_at` and
`expires_at` it is talking about.

**In the RESULT, not in the tool description.** Measured while compressing
prompt tokens: 54% of all tool-description text (8,602 of 16,045 tokens) is
result-handling prose read only after a call returns, paid on every call of
every turn for all 54 tools. A result-carried instruction costs nothing unless
a story was actually fetched, and it arrives at the moment the model is
choosing how to phrase the answer rather than thousands of tokens earlier.
That also sets its length: this is prose re-sent on every subsequent model
call of the turn, so it is three sentences and not a policy.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from typing import Any

import aiohttp

from ...domain.blocks import wrap_outside_text
from ...domain.ports import SocialProvider
from .results import NOTHING_FOUND, UNAVAILABLE, clamp_chars

log = logging.getLogger("nora.social.instagram")

#: What this result IS, in a field nothing can mistake for a URL. A model
#: cannot attribute what it was not told, and "there is a `taken_at` in here"
#: is not being told.
SOURCE_KIND = "instagram_story"

#: The framing, carried BY the result (module docstring). Short on purpose —
#: it is re-sent on every later model call in the turn — and it names the
#: contrast rather than a rule, because the failure is a phrasing that reads
#: as knowledge rather than a phrasing that breaks a policy.
_STORY_FRAMING = (
    "This is their Instagram STORY — it disappears about 24h after it was "
    "posted (`taken_at`, and `expires_at` is when the links die). If you "
    "mention any of it, say it was their story and when: «his story from "
    "this morning», never «he was at the gym». You saw something "
    "perishable; you don't know where he was."
)

#: The one row this backend serves. A request for anything else reaching here
#: is a ROUTING bug, not a user error — `SocialRouter` sends only the exception
#: list this way — so it is `unavailable` (a wiring gap) and is logged.
_SERVES: frozenset[tuple[str, str]] = frozenset({("instagram", "story")})

#: Story media kinds, normalised. An unrecognised kind becomes `"media"`
#: rather than riding through verbatim: the value is rendered into text the
#: model reads and reasons about, and a vocabulary that can grow silently is
#: a vocabulary nothing can be asserted against.
_KINDS: frozenset[str] = frozenset({"image", "video"})

#: A hard ceiling on items regardless of `limit`, mirroring the OpenCLI path's
#: `content_max_chars`. The reel arrives whole and the caller's `limit` has
#: already been clamped at the tool layer, so this only binds if BOTH of those
#: are wrong at once — which is the case worth being defended against, since
#: what it costs is the model's whole re-sent input.
_MAX_ITEMS = 20

#: The hosts a story frame is actually served from, MEASURED — the live
#: `FetchReel` envelope carries `https://instagram.f….fbcdn.net/…`
#: (design/instagram-adoption/05) and the `scontent.*.cdninstagram.com` form
#: of the same CDN. `https://` alone is not a check: the URL is rendered into
#: text the model reads TOGETHER WITH an instruction to `media_fetch` it, so a
#: scheme-only guard lets any host on the internet arrive as an attacker-chosen
#: fetch target dressed as a story frame. Matched on the parsed hostname, never
#: on the string — `https://scontent.cdninstagram.com.evil.test/x` and
#: `https://evil.test/?u=.fbcdn.net` both defeat a substring test.
_CDN_HOST_SUFFIXES: tuple[str, ...] = (".fbcdn.net", ".cdninstagram.com")

#: What a media id may look like. Instagram's are decimal, but the point is
#: not the charset — it is that the value is TYPE-checked and SHAPE-checked
#: before `str()` touches it. `str(row.get("id") or "")` rendered whatever
#: arrived, so a dict smuggled a caption into the result through the id slot.
_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class InstagramSocialClient(SocialProvider):
    """`POST /social/story` on the instagram-bridge. NEVER raises.

    Collaborators through `__init__` only (test-seam policy), and there is no
    `SocialSubjectIndex` among them any more: `target` is the igid the ROUTER
    resolved and authorized, so this client cannot look a person up and cannot
    decide whether it may. Not having the collaborator is what makes that
    structural rather than a convention — a backend holding an index is a
    backend one edit away from carrying its own copy of the tier rule.

    `base_url` reaches the bridge as a co-networked container
    (`http://instagram-bridge:PORT`); nothing is published to the host. The
    bridge serves BOTH axes from one authenticated session — messaging on
    `/events`, `/send`, … and social on `/social/*` — and that shared session
    is an implementation fact inside one process, invisible here: this client
    cannot send a message and the platform adapter cannot fetch a story.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 30.0,
        content_max_chars: int = 4000,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._content_max_chars = content_max_chars

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        """One story read. NEVER raises (R-5).

        Three states, which is R-27's core and nothing more: `ok`,
        `nothing_found` (I looked, no story up), `unavailable` (I could not
        look). The other two a story read can produce — `ambiguous` (I don't
        know whose account) and `refused` (I may not look) — are decided ABOVE
        this, in the router, and are never reached with a call to make.
        """
        try:
            return await self._read(platform=platform, what=what, target=target, limit=limit)
        except Exception:
            # The never-raises backstop, identical in posture to the OpenCLI
            # client's: a contract violation degrades to `unavailable` and
            # never propagates into the loop.
            log.exception("instagram social read failed")
            return dict(UNAVAILABLE)

    async def _read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        if (platform, what) not in _SERVES:
            log.warning("instagram social client asked for a row it does not serve")
            return dict(UNAVAILABLE)
        # `target` is the igid the router resolved out of the person index —
        # never a `person_id` and never a handle. An empty one is a routing
        # bug in the same family as the row check above, so it is a wiring
        # gap rather than a question about the subject, and it must not be
        # sent: the bridge would be handed a blank igid and would get to
        # decide what that means.
        igid = target.strip()
        if not igid:
            log.warning("instagram social client asked for a story with no subject")
            return dict(UNAVAILABLE)
        payload = await self._post_story(igid=igid, limit=limit)
        if payload is None:
            return dict(UNAVAILABLE)
        status = payload.get("status")
        if status not in ("ok", "nothing_found", "unavailable"):
            # An unknown status is the envelope not matching the documented
            # shape — a wiring gap, so `unavailable`, and never passed through
            # to the model as a status nothing downstream validates.
            log.warning("instagram bridge returned an unknown status: %r", status)
            return dict(UNAVAILABLE)
        if status == "nothing_found":
            # The bridge's cheap discovery step (`latest_reel_media`) found no
            # reel. This is the ONLY path to `nothing_found` here: it means the
            # read ran and there is no story up, which is a fact about the
            # subject rather than about us.
            #
            # The bridge's own `note` is DROPPED. It was a verbatim, unbounded,
            # unescaped passthrough from another process into the slot that
            # tells the model what it may say — executed, a 230-character
            # bridge note reading «IGNORE PREVIOUS INSTRUCTIONS…» arrived at
            # the model intact. There is one honest sentence for this state and
            # it is ours (`results.py`).
            return dict(NOTHING_FOUND)
        if status == "unavailable":
            return dict(UNAVAILABLE)
        items = _items(payload.get("items"), limit=limit)
        if not items:
            # `ok` with nothing usable in it is a wiring gap, NOT «no story» —
            # the bridge has a dedicated `nothing_found` for that and this is
            # not it. Answering `nothing_found` here would manufacture a claim
            # about the subject out of our own failure to read an envelope.
            log.warning("instagram bridge ok envelope carried no usable items")
            return dict(UNAVAILABLE)
        text, truncated = clamp_chars(_render(items), self._content_max_chars)
        out: dict[str, Any] = {
            "status": "ok",
            # WHAT this is, explicitly. A story result that says only «here
            # are some URLs» is a result the model can only describe as a
            # fact about the world, and a fact about the world does not
            # expire. `taken_at` alone is not this: a timestamp says when,
            # not what kind of thing it is attached to.
            "source_kind": SOURCE_KIND,
            # The framing, beside the timestamps it is about. `note` is Nora's
            # own instruction slot — nothing from the bridge ever lands in one
            # (`results.py`) — so this is the same channel `unavailable` and
            # `refused` already use to say what she may claim, spent here on
            # the one state where the risk is not a wrong answer but a
            # permanent one.
            "note": _STORY_FRAMING,
            "items": items,
            # The rendered form, because the model reads text natively and a
            # bare list of dicts is not what it acts on. The `[video <url>]`
            # marker is `media_fetch`'s own documented shape (the same one the
            # OpenCLI client emits for post media), so the model can hand a
            # row straight to `media_fetch(url=…)` — the deliberate second
            # call that is the whole separation of concerns.
            #
            # WRAPPED and CLAMPED, the same as the OpenCLI client, because
            # `SocialProvider`'s docstring says every implementation does and
            # a docstring that is true of one of two implementations is not a
            # contract. The old justification here — "there is no
            # stranger-written prose in a story reference" — was defeated by
            # measurement: the audit put a caption through the `id` slot and a
            # bridge sentence through `note`. The `items` list beside this
            # carries each `url` UNESCAPED, so the machine-usable handoff to
            # `media_fetch(url=…)` is unaffected by the wrapper's escaping.
            "text": wrap_outside_text("social_post", "instagram story", text),
            "truncated": truncated,
        }
        expires_at = _as_int(payload.get("expires_at"))
        if expires_at is not None:
            out["expires_at"] = expires_at
        return out

    async def _post_story(self, *, igid: str, limit: int) -> dict[str, Any] | None:
        """The HTTP round trip. `None` for anything that is not a JSON object
        on a 200 — a refusal by the bridge's OWN allowlist arrives as a 4xx
        and is a wiring gap (the two allowlists have drifted), not «nothing
        there», exactly as the OpenCLI client reads a 400."""
        # Clamped BEFORE it leaves, not only on the way back. `limit <= 0` was
        # forwarded verbatim while being clamped Nora-side, so the two ends
        # disagreed about what was asked for and the bridge was handed a
        # number whose meaning is its own to invent (all of them? none?).
        body = {"igid": igid, "limit": _cap(limit)}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(f"{self._base_url}/social/story", json=body) as response,
        ):
            text = await response.text()
            if response.status != 200:
                log.warning("instagram bridge answered %s", response.status)
                return None
        try:
            payload: Any = json.loads(text)
        except ValueError:
            log.warning("instagram bridge did not return JSON")
            return None
        if not isinstance(payload, dict):
            log.warning("instagram bridge returned a non-object envelope")
            return None
        return payload


def _items(raw: Any, *, limit: int) -> list[dict[str, Any]]:
    """The envelope's `items` → at most `limit` reference rows.

    **Built field by field, never copied, and every value bound to a shape.**
    This is the enforcement of "no content in a `social_fetch` result": an
    envelope that grows a `caption`, an `ocr_text` or a `description` cannot
    leak it here, because nothing reads an unlisted key — and nothing renders
    an unchecked VALUE either, which is the half a key whitelist does not
    cover (module docstring).

    A malformed row is DROPPED rather than failing the whole read — one
    unparseable frame in a two-frame reel should still let the model look at
    the other — but a response with no usable row at all is `unavailable` at
    the call site above, never `nothing_found`.

    **Drop THEN clamp.** Clamping the raw list first made the two sentences
    above contradict each other: with `limit=1`, a two-frame reel whose FIRST
    frame was malformed returned zero rows and therefore `unavailable`, and
    the good second frame was never examined — the exact case the docstring
    promises survives, and the exact shape of design node 10's worked "latest"
    example (`FetchReel`, newest first, clamped to `limit=1`). The loop stops
    as soon as `limit` usable rows exist, so dropping first costs nothing on
    the healthy path.
    """
    if not isinstance(raw, list):
        return []
    cap = _cap(limit)
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        # https AND a measured Instagram CDN host. The URL is handed to the
        # model and from there to `media_fetch`, which fetches it over NORA'S
        # OWN network — so a host this does not recognise is refused at the
        # boundary rather than being something the next tool has to defend
        # against. Scheme alone was not that check: it admitted every https
        # host on the internet, rendered as a story frame with an instruction
        # to go and fetch it.
        if not _is_cdn_url(url):
            continue
        # The id is TYPE-checked before it is rendered. `str(row.get("id"))`
        # accepted anything — a dict, a list — and stringified it into the
        # result, which is a content channel wearing a reference field's name.
        item_id = _media_id(row.get("id"))
        if item_id is None:
            continue
        kind = row.get("kind")
        item: dict[str, Any] = {
            "id": item_id,
            "kind": kind if kind in _KINDS else "media",
            "url": url,
        }
        taken_at = _as_int(row.get("taken_at"))
        if taken_at is not None:
            # Carried because the ANSWER often turns on it («is this from
            # today?»), and a model asked a question about time from a block
            # with no time in it answers anyway, wrong, at a rate nothing
            # reports. Absent rather than guessed when the bridge omits it.
            item["taken_at"] = taken_at
        out.append(item)
        if len(out) >= cap:
            break
    return out


def _cap(limit: Any) -> int:
    """`limit` → a usable count. One expression, read by the request body and
    by the clamp on the way back, so the two cannot disagree about what was
    asked for."""
    try:
        return max(1, min(int(limit), _MAX_ITEMS))
    except (TypeError, ValueError):
        return 1


def _is_cdn_url(value: Any) -> bool:
    """A story frame's URL, or not. Parsed, never substring-matched: the
    hostname is the only part of a URL that says where a fetch lands, and
    `https://scontent.cdninstagram.com.evil.test/x` contains the allowed text
    while pointing somewhere else entirely."""
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    try:
        host = (urllib.parse.urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in _CDN_HOST_SUFFIXES)


def _media_id(value: Any) -> str | None:
    """A media id, or None for anything that is not one.

    A bool is refused although Python would `str()` it happily, for the same
    reason `_as_int` refuses one: `True` is not an identifier, and a value
    that only LOOKS renderable is how a non-reference gets into a reference
    field.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if _ID_RE.fullmatch(text) else None


def _render(items: list[dict[str, Any]]) -> str:
    """Items → the marker lines the model reads, newest first (the bridge's
    own order, preserved rather than re-sorted — it is the protocol's).

    Deliberately thin: a kind, a time and a link per row. The caller WRAPS and
    CLAMPS what comes back (R-13) — this function only builds the lines.

    That wrap used to be argued away here, on the grounds that there is no
    stranger-written PROSE in a story reference and wrapping four
    machine-generated fields would be decoration. The argument was wrong in
    its premise, not in its logic: three of those four fields were not
    machine-generated at all, they were whatever the bridge put in them, and
    an audit got a caption and a sentence of injected instruction through two
    of them. The values are bound now — and the wrap is there anyway, because
    "this particular envelope carries nothing unsafe today" is a fact about
    today and a wrapper is what makes it not matter.
    """
    now = int(time.time())
    lines = []
    for item in items:
        when = _age(item.get("taken_at"), now)
        lines.append(f"[{item['kind']} {item['url']}]{when}")
    return "\n".join(lines)


def _age(taken_at: Any, now: int) -> str:
    """A story item's time, rendered as an AGE rather than an epoch.

    The result's note asks the model to attribute what it saw — "his story
    from this morning", never "he was at the gym". That is a judgement about
    WHEN, and `1789935632` cannot ground it: the model has no now-anchor, so
    it either declines to attribute or invents an interval. Measured elsewhere
    in this system (the Relevance Gate, 2026-09-09/10): three questions about
    time against an input carrying none, and rendering an age cut the error
    rate by two thirds. Rewriting the instruction did not — every rewrite was
    worse, one of them unsafely so.

    So the age is the fix and the note is not. Both are kept: the note says
    what to do, this says what with.

    The epoch stays in `items[].taken_at` for anything that computes; this
    renders only the line the model reads."""
    epoch = _as_int(taken_at)
    if epoch is None:
        return ""
    delta = now - epoch
    if delta < 0:  # clock skew between us and Instagram — say nothing rather than "in 3h"
        return ""
    if delta < 3600:
        return f" · {max(1, delta // 60)}m ago"
    if delta < 86400:
        return f" · {delta // 3600}h ago"
    # A story older than 24h should not exist; if one arrives, say so plainly
    # rather than rendering a day count that implies it is still live.
    return f" · {delta // 86400}d ago (past its 24h expiry)"


def _as_int(value: Any) -> int | None:
    """An epoch-seconds field, or None. A bool is REFUSED although Python
    would happily int() it — `True` becoming a 1970 timestamp is the kind of
    wrong answer that reads as data."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
