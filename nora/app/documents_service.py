"""Publishing a document, and who may read one (ADR 0020).

`DocumentService.publish` is the ONE path a model-written document takes into the
store, and the order is the point, because each step decides what the next may trust:

1. `check_document` judges the bytes (`nora.documents.check`). A refusal carries
   findings; a failure of the checker itself is `unavailable`, never a pass.
2. Every `Image` names a picture by a media code. The code is resolved WITHIN THIS
   ROOM and to exactly one picture, then rewritten to that picture's full sha256:
   the stored document holds content addresses, never the short code a model typed.
3. The rewritten document is checked AGAIN, so what is stored is what was judged, and
   its canonical form, hash and search text are the ones the checker computed.
4. A new document or a new revision is written. A revision must be made in the room
   the document was made in, so a room can never overwrite another room's document.

MEDIA IN A ROOM. A code resolves against the pictures in view in that room, the way
`media_fetch` resolves one: the room's own message rows (`messages.media_json`, which
is where an inbound photo lives, and is the same-room rule for free: a photo sent to
room A is not in room B's rows), and the media index rows born in this room
(`provenance.platform` and `provenance.conversation_id`: a picture Nora generated, or
one the computer registered). A code that matches nothing is refused; a code that
matches more than one picture is refused and the model is told to use a longer one. It
never picks. A row with no room on it (a sticker, a comprehension-born row) cannot be
placed, so it never resolves: the rule fails closed.

LIFECYCLE (`set_state`). A document made in this room can be archived, deleted or
restored by Nora on behalf of a known person, through the same same-room rule a revision
takes; the store appends a `document_state` row and never touches a revision. A revision
of a document that is not `active` is refused (`document_not_active`), and `read` answers
a deleted document like an unknown one, so only `set_state` reaches it, to restore it.

`rooms_named` answers the other question, for the read side: which rooms name a
person, from the CURRENT policy, so a removal revokes access. A `persons = "*"` room
names nobody (the policy cannot say who is in it), so only a document's creator reads
what was made there.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from nora.agent.egress import scan_identifiers
from nora.agent.security import PlaceLocator, screen_and_locate_place
from nora.documents import catalog_prompt
from nora.documents.check import Checked, Finding, check_document
from nora.domain.documents import (
    DOCUMENT_STATES,
    DocumentState,
    DocumentStore,
    RevisionIn,
    RoomKey,
    StoredDocument,
    handle_of,
    is_handle,
    may_read,
)
from nora.domain.media import code_matches, parse_media_type, resolve_room_media, room_sightings
from nora.domain.ports import MediaObjectStore, RecordStore
from nora.policy import Policy

_HEX = re.compile(r"^[0-9a-f]{6,64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HANDLES_SHOWN = 5
_HANDLE_TRIES = 5


def _wall_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class PublishResult:
    """`ok` carries the id, revision and portal path. `refused` carries findings the
    model can act on. `conflict` means someone revised the document a moment ago.
    `unavailable` means nothing is known to have been stored; say so, never "done"."""

    status: Literal["ok", "refused", "conflict", "unavailable"]
    document_id: UUID | None = None
    revision: int = 0
    url: str = ""
    findings: tuple[Finding, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class ReadResult:
    """A document as its room sees it: the current revision and its spec, so a model
    can apply a change to what is stored. `refused` with `no_such_document` is the one
    answer for an unknown id and for a document made in another room."""

    status: Literal["ok", "refused", "unavailable"]
    document_id: UUID | None = None
    revision: int = 0
    title: str = ""
    spec: dict[str, Any] | None = None
    url: str = ""
    findings: tuple[Finding, ...] = ()
    note: str = ""
    state: str = "active"


@dataclass(frozen=True, slots=True)
class StateResult:
    """`ok` carries the state the document is now in and the one it was in (`changed` is
    false when it was already there, and nothing was written). `refused` carries a
    finding: `no_such_document` (unknown here, or another room's), `unknown_person`,
    `bad_state`. `unavailable` means nothing is known to have changed."""

    status: Literal["ok", "refused", "unavailable"]
    document_id: UUID | None = None
    state: str = ""
    previous: str = ""
    changed: bool = False
    title: str = ""
    url: str = ""
    findings: tuple[Finding, ...] = ()
    note: str = ""


def rooms_named(policy: Policy, person_id: str) -> frozenset[RoomKey]:
    """Every `(platform, conversation_id)` whose room names `person_id`, every id a
    room answers to included. A `"*"` room is left out: it names nobody."""
    return frozenset(
        (convo.platform, cid)
        for convo in policy.conversations
        if convo.persons is not None and person_id in convo.persons
        for cid in convo.ids
    )


def room_label(policy: Policy, platform: str, conversation_id: str) -> str:
    """The operator's label of the room `(platform, conversation_id)` names, or "" when no
    allowed room answers to it. The portal shows it beside a document, but only to someone
    whose own rooms include it (`nora.web.documents`), so it never names a room to a person
    who is not in it."""
    for convo in policy.conversations:
        if convo.platform == platform and conversation_id in convo.ids:
            return convo.label
    return ""


def _images(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every Image node and its JSON Pointer. Iterative: the document has passed the
    checker, so depth is bounded, but nothing here needs to rely on that."""
    found: list[tuple[str, dict[str, Any]]] = []
    stack: list[tuple[dict[str, Any], str]] = [(document["root"], "/root")]
    while stack:
        node, where = stack.pop()
        if node.get("type") == "Image":
            found.append((where, node))
        for index, child in enumerate(node.get("children") or []):
            stack.append((child, f"{where}/children/{index}"))
    return sorted(found, key=lambda pair: pair[0])


def _maps(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every Map node and its JSON Pointer, in document order (see `_images`)."""
    found: list[tuple[str, dict[str, Any]]] = []
    stack: list[tuple[dict[str, Any], str]] = [(document["root"], "/root")]
    while stack:
        node, where = stack.pop()
        if node.get("type") == "Map":
            found.append((where, node))
        for index, child in enumerate(node.get("children") or []):
            stack.append((child, f"{where}/children/{index}"))
    return sorted(found, key=lambda pair: pair[0])


#: How many places one publish looks up at once. Each is a billed Places call, so the
#: fan-out is bounded even though a Map may name up to 50.
_LOCATE_CONCURRENCY = 5

#: How many DISTINCT places one publish may look up, over every Map in it. Each is a billed
#: Places call, and the schema bounds a Map at 50 but not how many Maps a document holds.
_MAX_PLACES_PER_PUBLISH = 50

#: Findings a Map can return, like the checker's own cap, so 50 unknown names are not 50 lines.
_MAX_PLACE_FINDINGS = 10


class DocumentService:
    def __init__(
        self,
        *,
        store: DocumentStore,
        records: RecordStore,
        media: MediaObjectStore,
        media_window: int,
        room_ids: Callable[[str, str], frozenset[str]],
        is_person: Callable[[str], bool],
        new_id: Callable[[], UUID] = uuid4,
        now_ms: Callable[[], int] = _wall_ms,
        public_url: str = "",
        place_locator: PlaceLocator | None = None,
        # The rooms whose explicit `persons` list names a person: `rooms_named` from the
        # CURRENT policy, the same answer the portal reads with. `None` names nothing, so a
        # document is read by its creator alone (8765_44827).
        rooms_of: Callable[[str], frozenset[RoomKey]] | None = None,
    ) -> None:
        self._store = store
        self._rooms_of = rooms_of
        self._records = records
        self._media = media
        self._window = media_window
        # Every id a room answers to (`Conversation.ids`): a room that answers to two ids
        # is one room, for reading its pictures and for revising its own document.
        self._room_ids = room_ids
        # Whether `person_id` is a resolved, known person. `created_by` decides who may
        # read a document in a room that does not name them, so a raw platform id that
        # happens to equal a real person id must never reach it.
        self._is_person = is_person
        self._new_id = new_id
        self._now_ms = now_ms
        # Empty means no link is ever built (`[documents] public_url`).
        self._public_url = public_url.rstrip("/")
        # Looks a named place up for a `Map`. `None` while `[route]` is
        # off, and then a document with a Map is `unavailable`, never stored without one.
        self._place_locator = place_locator

    # ------------------------------------------------------------------ media
    async def _sightings(self, platform: str, conversation_id: str, code: str) -> set[str] | None:
        """The distinct full sha256 of every picture in this room whose sha256
        starts with `code`, or `None` when the room could not be read, or when the
        scan was cut short and found nothing (`could not check that far` is never
        `no such picture`)."""
        found: set[str] = set()
        truncated = False
        try:
            ids = self._room_ids(platform, conversation_id) | {conversation_id}
            now = self._now_ms()
            for cid in sorted(ids):
                # The one resolver the sandbox session shares (DEC-251): the recent window
                # plus the room's last 90 days of message rows, keyed by the message row.
                seen = await room_sightings(
                    self._records, conversation_id=cid, now_ms=now, window=self._window
                )
                truncated = truncated or seen.truncated
                for sha, sighting in code_matches(seen.sightings, code).items():
                    # Images only (this block's own rule): a clip or a voice note is not a
                    # picture, and a shared item's cover is a preview of someone else's post.
                    if (
                        _SHA.match(sha)
                        and not sighting.cover
                        and parse_media_type(sighting.media_type)[0] == "image"
                    ):
                        found.add(sha)
                # What Nora made or kept in this room is in the index, not in a message row.
                look = await resolve_room_media(
                    self._media, code=code, platform=platform, conversation_id=cid
                )
                if not look.available:
                    return None
                found.update(sha for sha in look.objects if _SHA.match(sha))
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`, not a guess
            return None
        if truncated and not found:
            return None
        return found

    async def _resolve(
        self, platform: str, conversation_id: str, document: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[str, ...], list[Finding], bool]:
        """Rewrite every Image's code to its full sha256. Returns the rewritten
        document, the shas it names, the findings, and whether a store was down."""
        rewritten = copy.deepcopy(document)
        findings: list[Finding] = []
        named: list[str] = []
        cache: dict[str, str | None] = {}
        for where, node in _images(rewritten):
            code = str(node["props"].get("media", ""))
            path = f"{where}/props/media"
            if not _HEX.match(code):
                findings.append(
                    Finding(path, "media_code", "must be a media code: 6 to 64 hex characters")
                )
                continue
            if code not in cache:
                seen = await self._sightings(platform, conversation_id, code)
                if seen is None:
                    return document, (), [], True
                if len(seen) == 1:
                    cache[code] = next(iter(seen))
                else:
                    cache[code] = None
                    shown = ", ".join(sorted(s[:8] for s in seen)[:_HANDLES_SHOWN])
                    if not seen:
                        message = f"no picture with code {code} in this room's recent messages"
                        findings.append(Finding(path, "media_unknown", message))
                    else:
                        message = (
                            f"{code} matches {len(seen)} pictures in this room ({shown}); "
                            "use a longer code"
                        )
                        findings.append(Finding(path, "media_ambiguous", message))
                    continue
            sha = cache[code]
            if sha is None:
                findings.append(
                    Finding(path, "media_unknown", f"no single picture for code {code}")
                )
            else:
                node["props"]["media"] = sha
                named.append(sha)
        return rewritten, tuple(sorted(set(named))), findings, False

    @property
    def link_base(self) -> str:
        """The public origin a document link is built under; `""` while none is configured."""
        return self._public_url


    async def _target_problem(
        self, platform: str, conversation_id: str, document_id: UUID | None,
        base_revision: int | None, person_id: str,
    ) -> PublishResult | None:
        """The refusal a revision's TARGET would earn at the write (another room's or an unknown
        id, a document `person_id` may not revise, not active, a stale revision), or `None`.
        Read-only, and the write checks again, so a race between the two is still caught there."""
        if document_id is None:
            return None
        try:
            current = await self._store.get(document_id=document_id)
        except Exception:  # noqa: BLE001 - the write reports a store that is down
            return None
        here = self._room_ids(platform, conversation_id) | {conversation_id}
        if (
            current is None
            or current.summary.platform != platform
            or current.summary.conversation_id not in here
            or not may_read(current.summary, person_id, self._named(person_id))
        ):
            return _no_such_document()
        if current.summary.state != "active":
            return _not_active(current.summary.state)
        if current.summary.revision != base_revision:
            return PublishResult(
                "conflict", document_id=document_id, revision=current.summary.revision,
                note=(
                    f"This document is at revision {current.summary.revision}, not "
                    f"{base_revision}. Read it again, apply your change to that, and publish again."
                ),
            )
        return None

    async def _ground_maps(
        self, document: dict[str, Any], person_names: frozenset[str] | None,
        live_addresses: frozenset[str] | None = None,
    ) -> tuple[dict[str, Any], list[Finding], bool]:
        """Fill every Map place's coordinate from its NAME.

        The model writes names and never a coordinate (DEC-24), so whatever `lat`, `lng` or
        `route` a spec carries is DISCARDED and the pair is looked up here, through the same
        outbound and return-path screens `place_info` runs: a person's name, an identifier or a
        coordinate in the name is refused before any call. A place that does not resolve to
        exactly one is a finding that names it, never a guess. Returns the document, the
        findings, and whether the locator was down."""
        rewritten = copy.deepcopy(document)
        maps = _maps(rewritten)
        if not maps:
            return rewritten, [], False
        findings: list[Finding] = []
        resolved: dict[str, dict[str, Any] | None] = {}
        gate = asyncio.Semaphore(_LOCATE_CONCURRENCY)
        live = {a.strip().casefold() for a in (live_addresses or ()) if a.strip()}

        def key(name: str) -> str:
            return " ".join(name.split()).casefold()

        async def look(name: str) -> None:
            folded = key(name)
            if any(a in folded or folded in a for a in live):
                # R-57: an address `reverse_geocode` gave out THIS turn is a live position, the
                # one `route` already refuses; naming it on a Map would pin it in a document.
                resolved[key(name)] = {"status": "refused", "reason": "live_position"}
                return
            async with gate:
                got = await screen_and_locate_place(
                    name, place_locator=self._place_locator, person_names=person_names
                )
            resolved[key(name)] = got.data if got.status == "resolved" else {"status": got.status}
            if got.status == "refused":
                resolved[key(name)] = {"status": "refused", "reason": got.reason}

        first_spelling: dict[str, str] = {}
        for _, m in maps:
            for p in m["props"]["places"]:
                first_spelling.setdefault(key(str(p.get("name", ""))), str(p.get("name", "")))
        if len(first_spelling) > _MAX_PLACES_PER_PUBLISH:
            return rewritten, [Finding(
                "", "place_limit",
                f"a document may map at most {_MAX_PLACES_PER_PUBLISH} different places in total, "
                f"and this one names {len(first_spelling)}; keep the ones that matter",
            )], False
        await asyncio.gather(*(look(n) for n in first_spelling.values()))
        if any((resolved[k] or {}).get("status") == "unavailable" for k in first_spelling):
            return document, [], True
        for where, node in maps:
            props = node["props"]
            props.pop("route", None)
            for index, place in enumerate(props["places"]):
                got = resolved[key(str(place.get("name", "")))] or {}
                path = f"{where}/props/places/{index}"
                status = got.get("status")
                if status == "ok" and "lat" in got and "lng" in got:
                    place["lat"], place["lng"] = got["lat"], got["lng"]
                    continue
                place.pop("lat", None)
                place.pop("lng", None)
                name = str(place.get("name", ""))
                if status == "ambiguous":
                    could = ", ".join(
                        c for c in (str(c) for c in got.get("could_be", [])[:3])
                        if scan_identifiers(c) is None  # as `place_info` screens its candidates
                    )
                    findings.append(Finding(
                        path, "place_ambiguous",
                        f"{name!r} matches several places ({could}); name it more fully",
                    ))
                elif status == "refused":
                    findings.append(Finding(
                        path, "place_refused",
                        "a Map shows public places by name; a person's home or position, an "
                        "identifier or a coordinate cannot be mapped",
                    ))
                else:
                    findings.append(Finding(
                        path, "place_unknown",
                        f"no place found for {name!r}; name a place a map knows",
                    ))
        return rewritten, findings[:_MAX_PLACE_FINDINGS], False

    # ------------------------------------------------------------ catalog, read
    def catalog(self) -> str:
        """The catalog prompt the checker's schema was generated with: the components, the
        limits and the rules for writing one, so a model that has not seen them learns them
        from the tool."""
        return catalog_prompt()

    def _named(self, person_id: str | None) -> frozenset[RoomKey]:
        """The rooms whose explicit list names `person_id`; nothing without a wiring or a
        requester, so only a creator reads (`may_read`)."""
        if self._rooms_of is None or person_id is None:
            return frozenset()
        return self._rooms_of(person_id)

    async def _in_room(
        self,
        *,
        platform: str,
        conversation_id: str,
        document_id: UUID | None,
        handle: str,
        person_id: str | None,
    ) -> StoredDocument | None:
        """The document named by `document_id`, or by the 12-hex `handle` a link carries,
        when it was made in THIS room (any id the room answers to), in ANY state, and `person_id`
        may read it (`may_read`); else `None`.

        The ONE resolution `read` and `set_state` share. A handle is matched among this
        room's documents `person_id` may read, and one that matches none or more than one, like
        another room's document, one the viewer may not read or an unknown id, is the same `None`:
        it is never picked."""
        here = self._room_ids(platform, conversation_id) | {conversation_id}
        named = self._named(person_id)
        current: StoredDocument | None = None
        if document_id is not None:
            current = await self._store.get(document_id=document_id)
        elif is_handle(handle):
            mine = [
                d
                for d in await self._store.find_by_handle(handle=handle)
                if d.summary.platform == platform and d.summary.conversation_id in here
                and may_read(d.summary, person_id, named)
            ]
            current = mine[0] if len(mine) == 1 else None  # two is never picked
        if (
            current is None
            or current.summary.platform != platform
            or current.summary.conversation_id not in here
            or not may_read(current.summary, person_id, named)
        ):
            return None
        return current

    async def read(
        self,
        *,
        platform: str,
        conversation_id: str,
        document_id: UUID | None = None,
        handle: str = "",
        person_id: str | None = None,
    ) -> ReadResult:
        """The current revision of a document made in THIS room that `person_id` may read,
        named by its id or by the 12-hex handle its link carries. Another room's document, a
        document of this room `person_id` may not read, an unknown id, a handle that fits none
        or two of those, and a deleted document all answer exactly alike (`no_such_document`)."""
        try:
            current = await self._in_room(
                platform=platform, conversation_id=conversation_id,
                document_id=document_id, handle=handle, person_id=person_id,
            )
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`
            return ReadResult("unavailable", note="The document could not be read right now.")
        if current is None or current.summary.state == "deleted":
            # gone from every read; `set_state` restores
            return ReadResult("refused", findings=_no_such_document().findings)
        return ReadResult(
            "ok",
            document_id=current.summary.id,
            revision=current.summary.revision,
            title=current.summary.title,
            spec=current.spec,
            url=self._link(current.summary.id),
            state=current.summary.state,
        )

    async def set_state(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str,
        document_id: UUID | None = None,
        state: str,
        handle: str = "",
    ) -> StateResult:
        """Archive, delete or restore a document made in THIS room, for `person_id`.

        The document is named by its id, or by the 12-hex handle its link carries. The gate
        is the one a revision takes: a known person, and a document made in this room (any
        id the room answers to). A handle is resolved among THIS room's documents in any
        state, and one that matches none or more than one, like another room's document,
        answers exactly like an unknown id. A deleted document is found here, and only
        here, so it can be restored.        """
        if not self._is_person(person_id):
            return StateResult(
                "refused",
                findings=(Finding("", "unknown_person", "only a known member can do that"),),
            )
        if state not in DOCUMENT_STATES:
            return StateResult(
                "refused",
                findings=(
                    Finding("", "bad_state", "state must be one of " + ", ".join(DOCUMENT_STATES)),
                ),
            )
        wanted = cast(DocumentState, state)  # checked against DOCUMENT_STATES just above
        try:
            current = await self._in_room(
                platform=platform, conversation_id=conversation_id,
                document_id=document_id, handle=handle, person_id=person_id,
            )
            if current is None:
                return StateResult("refused", findings=_no_such_document().findings)
            previous = current.summary.state
            target = current.summary.id
            if previous != wanted:
                written = await self._store.set_state(
                    document_id=target, state=wanted, set_by=person_id
                )
                if not written:
                    return StateResult("refused", findings=_no_such_document().findings)
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`
            return StateResult(
                "unavailable", note="The document's state could not be changed right now."
            )
        return StateResult(
            "ok",
            document_id=target,
            state=wanted,
            previous=previous,
            changed=previous != wanted,
            title=current.summary.title,
            url="" if wanted == "deleted" else self._link(target),
        )

    async def _fresh_id(self, platform: str, conversation_id: str, person_id: str) -> UUID:
        """A new document id whose 12-hex handle no document its room can read already has,
        in ANY state: an archived or deleted one counts, because a restore would bring it
        back and two documents would then share the handle.

        A link resolves its handle among what the viewer may read and refuses a handle that
        matches two, so a clash would make both documents' links 404 for the whole room
        forever. 48 bits make one astronomically unlikely, so this is a guard, not a loop of
        retries: a few draws, then the last one (and a clash is still refused, never picked)."""
        rooms = frozenset(
            (platform, cid)
            for cid in self._room_ids(platform, conversation_id) | {conversation_id}
        )

        def _reads(d: StoredDocument) -> bool:
            s = d.summary
            return s.created_by == person_id or (s.platform, s.conversation_id) in rooms

        candidate = self._new_id()
        for _ in range(_HANDLE_TRIES - 1):
            found = await self._store.find_by_handle(handle=handle_of(candidate))
            if not any(_reads(d) for d in found):
                break
            candidate = self._new_id()
        return candidate

    def _link(self, document_id: UUID) -> str:
        """The link to hand the room, or "" when there is no public origin. It carries the
        12-hex handle, never the UUID, so it passes egress whole."""
        if not self._public_url:
            return ""
        return f"{self._public_url}/documents/{handle_of(document_id)}"

    def _ok(self, document_id: UUID, revision: int) -> PublishResult:
        return PublishResult(
            "ok", document_id=document_id, revision=revision, url=self._link(document_id)
        )

    # ---------------------------------------------------------------- publish
    async def publish(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str,
        raw: bytes | str | dict[str, Any],
        written_by: str,
        document_id: UUID | None = None,
        base_revision: int | None = None,
        person_names: frozenset[str] | None = None,
        live_addresses: frozenset[str] | None = None,
    ) -> PublishResult:
        if not self._is_person(person_id):
            return PublishResult(
                "refused",
                findings=(
                    Finding("", "unknown_person", "only a known member can publish a document"),
                ),
            )
        if document_id is not None and base_revision is None:
            # A revision that does not say which revision it started from would silently
            # replace newer work.
            return PublishResult(
                "refused",
                findings=(
                    Finding(
                        "",
                        "base_revision_required",
                        "say which revision you started from: the `revision` of the document "
                        "you read",
                    ),
                ),
            )
        checked = check_document(raw)
        if checked.status == "error":
            return PublishResult(
                "unavailable", note="The document could not be checked, so it was not saved."
            )
        if not checked.ok or checked.document is None:
            return PublishResult("refused", findings=checked.findings)

        rewritten, media, findings, down = await self._resolve(
            platform, conversation_id, checked.document
        )
        if down:
            return PublishResult(
                "unavailable", note="The room's pictures could not be read, so nothing was saved."
            )
        if findings:
            return PublishResult("refused", findings=tuple(findings))
        if _maps(rewritten):
            # The target's cheap checks first: a stale `base_revision` or another room's id is
            # answered before any billed lookup, so the model's «read it again and publish
            # again» after a conflict does not pay for the same places twice.
            early = await self._target_problem(
                platform, conversation_id, document_id, base_revision, person_id
            )
            if early is not None:
                return early
        rewritten, findings, down = await self._ground_maps(
            rewritten, person_names, live_addresses
        )
        if down:
            return PublishResult(
                "unavailable", note="The places could not be looked up, so nothing was saved."
            )
        if findings:
            return PublishResult("refused", findings=tuple(findings))
        final: Checked = check_document(rewritten)
        if final.status == "refused":
            # What grounding added (a pair per place) can push a document that passed the first
            # check past a limit; that is the writer's to fix, not «could not be re-checked».
            return PublishResult("refused", findings=final.findings)
        if not final.ok or final.document is None:
            return PublishResult(
                "unavailable", note="The document could not be re-checked, so it was not saved."
            )
        revision = RevisionIn(
            title=final.title,
            spec=final.document,
            text=final.text,
            sha256=final.sha256,
            media=media,
            written_by=written_by,
        )

        try:
            if document_id is None:
                made = await self._store.create(
                    document_id=await self._fresh_id(platform, conversation_id, person_id),
                    platform=platform,
                    conversation_id=conversation_id,
                    created_by=person_id,
                    revision=revision,
                )
                return self._ok(made.summary.id, made.summary.revision)
            current = await self._store.get(document_id=document_id)
            # A document in another room is answered exactly like one that does not exist:
            # a model in room B must not learn that an id is a document in room A (the read
            # API never distinguishes them either).
            here = self._room_ids(platform, conversation_id) | {conversation_id}
            if (
                current is None
                or current.summary.platform != platform
                or current.summary.conversation_id not in here
                or not may_read(current.summary, person_id, self._named(person_id))
            ):
                return _no_such_document()
            if current.summary.state != "active":
                return _not_active(current.summary.state)
            if current.summary.revision != base_revision:
                return PublishResult(
                    "conflict",
                    document_id=document_id,
                    revision=current.summary.revision,
                    note=(
                        f"This document is at revision {current.summary.revision}, not "
                        f"{base_revision}. Read it again, apply your change to that, and "
                        "publish again."
                    ),
                )
            outcome = await self._store.add_revision(
                document_id=document_id,
                base_revision=current.summary.revision,
                revision=revision,
            )
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`
            return PublishResult(
                "unavailable", note="The document could not be saved; nothing was stored."
            )
        if outcome.status == "conflict":
            return PublishResult(
                "conflict",
                document_id=document_id,
                note="Someone revised this document a moment ago. Read it again and publish again.",
            )
        if outcome.status == "not_active":
            # Archived or deleted between the read above and the write.
            return _not_active("no longer active")
        if outcome.status == "not_found" or outcome.document is None:
            return _no_such_document()
        return self._ok(document_id, outcome.document.summary.revision)


def _no_such_document() -> PublishResult:
    return PublishResult(
        "refused",
        findings=(Finding("", "no_such_document", "no document with that id in this room"),),
    )


def _not_active(state: str) -> PublishResult:
    return PublishResult(
        "refused",
        findings=(
            Finding(
                "",
                "document_not_active",
                f"this document is {state}; restore it (state active) before revising it",
            ),
        ),
    )


__all__ = [
    "DocumentService",
    "PublishResult",
    "ReadResult",
    "StateResult",
    "room_label",
    "rooms_named",
]
