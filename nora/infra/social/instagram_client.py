"""`InstagramSocialClient` — the second `SocialProvider`, and the whole of the
exception list (design/instagram-adoption/08, /10, /11 Flow 3).

It serves exactly ONE `(platform, what)` row — `(instagram, story)` — over the
instagram-bridge's `POST /social/story` on the private compose `nora` network.
That row is here because OpenCLI 1.8.7 has no story READ anywhere (its `story`
verb posts one), so instameow's `FetchReel` is the only path that exists; every
other Instagram verb stays on the default backend. `SocialRouter`'s docstring
carries the rule this row is the single exception to.

## Three things this client does that the OpenCLI one does not

**It resolves a SUBJECT.** `target` is a `person_id`, not a handle — the model
never supplies an Instagram identity, because inferring one from a name is a
guess that reads a different human's stories rather than failing. The person
index is the only thing allowed to make that binding (`SocialSubjectIndex`),
and no entry is `ambiguous`, never a guess.

**It authorizes a TIER.** v1 is Tier 0 only: the subject must be someone in a
room Nora is in, which is the tier that needs no new policy machinery because
the room IS the grant. Anything else is `refused` — a boundary, not an outage.
Architecture bounds *how*, the tier ladder bounds *who*, retention bounds
*what survives*; all three are required and none substitutes for another.

**It returns REFERENCES, never content.** An item is `{id, kind, taken_at,
url}` and is BUILT FIELD BY FIELD from the envelope rather than passed
through, so a caption, a transcript or a description the bridge might one day
add cannot leak into a result. `social_fetch` finds out *that* something
exists and where; `media_fetch` finds out *what it is*. The moment a story
result carries the content inline it has absorbed `media_fetch`'s job and the
model stops spending deliberately — "does Miles have a story up" must stay a
different cost from "what is in it".

## No cursor, and stories do not paginate

A reel arrives WHOLE from the protocol (two items in the measured case), so
there is no `next_cursor` on this path and asking for more of a story is
asking for something that does not exist. `limit` is therefore a NORA-SIDE
clamp, applied here after the response — worth doing anyway, because a
fifteen-item story would otherwise arrive entire and every token of a tool
result is re-sent on every subsequent model call.

## The URLs expire, and that cuts in the design's favour

Instagram CDN URLs are signed (`oh=`, `oe=`, `_nc_sid`) and the signature IS
the authorization: measured, a story video fetches `200 video/mp4` with no
cookies, no UA and no referer, so `media_fetch` carrying no session is not a
problem. They are also valid for a window rather than indefinitely, which is
why `expires_at` rides the result: a two-step "list now, look later" flow has
to be SOON after, and a stored URL is a dead link.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from ...domain.ports import SocialProvider, SocialSubjectIndex
from .results import AMBIGUOUS, REFUSED, UNAVAILABLE

log = logging.getLogger("nora.social.instagram")

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


class InstagramSocialClient(SocialProvider):
    """`POST /social/story` on the instagram-bridge. NEVER raises.

    Collaborators through `__init__` only (test-seam policy). `subjects` is
    REQUIRED rather than optional: a client that could be built without an
    index would answer `ambiguous` for everyone while looking wired, and the
    honest expression of "the index does not exist yet" is the composition
    root not building this client at all — which then answers `unavailable`
    through the router, «I couldn't check», which is true.

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
        subjects: SocialSubjectIndex,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._subjects = subjects
        self._timeout_seconds = timeout_seconds

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        """One story read. NEVER raises (R-5).

        Five states, and the two beyond R-27's core are the ones that must not
        collapse into it: `ambiguous` (I don't know whose account), `refused`
        (I may not look), `ok`, `nothing_found` (I looked, no story up),
        `unavailable` (I could not look).
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
        # RESOLVE before AUTHORIZE: a tier is a property of a person, so there
        # is nothing to authorize until the subject is one. The order also
        # keeps the two answers distinct — "who?" and "not them" are different
        # sentences and the model says a different thing for each.
        igid = await self._subjects.igid_for(person_id=target)
        if not igid:
            return dict(AMBIGUOUS)
        if not await self._subjects.is_tier_zero(person_id=target):
            return dict(REFUSED)
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
            return {"status": "nothing_found", "note": str(payload.get("note") or "")}
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
        out: dict[str, Any] = {
            "status": "ok",
            "items": items,
            # The rendered form, because the model reads text natively and a
            # bare list of dicts is not what it acts on. The `[video <url>]`
            # marker is `media_fetch`'s own documented shape (the same one the
            # OpenCLI client emits for post media), so the model can hand a
            # row straight to `media_fetch(url=…)` — the deliberate second
            # call that is the whole separation of concerns.
            "text": _render(items),
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
        body = {"igid": igid, "limit": limit}
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

    **Built field by field, never copied.** This is the enforcement of "no
    content in a `social_fetch` result": an envelope that grows a `caption`,
    an `ocr_text` or a `description` cannot leak it here, because nothing
    reads an unlisted key. A passthrough with a denylist would invert that and
    fail open on whatever the bridge adds next.

    A malformed row is DROPPED rather than failing the whole read — one
    unparseable frame in a two-frame reel should still let the model look at
    the other — but a response with no usable row at all is `unavailable` at
    the call site above, never `nothing_found`.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw[: max(1, min(limit, _MAX_ITEMS))]:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        # https ONLY. The URL is handed to the model and from there to
        # `media_fetch`, which fetches it over NORA'S OWN network — so a
        # scheme this does not recognise is refused at the boundary rather
        # than being something the next tool has to defend against.
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        kind = row.get("kind")
        item: dict[str, Any] = {
            "id": str(row.get("id") or ""),
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
    return out


def _render(items: list[dict[str, Any]]) -> str:
    """Items → the marker lines the model reads, newest first (the bridge's
    own order, preserved rather than re-sorted — it is the protocol's).

    Deliberately thin: a kind, a time and a link per row. There is nothing to
    `untrusted`-wrap (R-13) because there is no stranger-written PROSE in a
    story reference — wrapping four machine-generated fields would be
    decoration, and the moment a caption is added to this render that stops
    being true and the wrap becomes mandatory.
    """
    lines = []
    for item in items:
        when = f" · {item['taken_at']}" if "taken_at" in item else ""
        lines.append(f"[{item['kind']} {item['url']}]{when}")
    return "\n".join(lines)


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
