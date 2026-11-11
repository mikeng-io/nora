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

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from nora.agent.security import PlaceLocator
from nora.app.documents_media import document_maps, ground_maps, resolve_media
from nora.documents import catalog_prompt
from nora.documents.check import Checked, Finding, check_document
from nora.domain.documents import (
    DOCUMENT_STATES,
    DocumentAuthority,
    DocumentState,
    DocumentStore,
    RevisionIn,
    RoomKey,
    StoredDocument,
    handle_of,
    is_handle,
    may_read,
)
from nora.domain.ports import MediaObjectStore, RecordStore
from nora.policy import Policy

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
        for convo in policy.rooms()
        if convo.persons is not None and person_id in convo.persons
        for cid in convo.ids
    )


def room_names_audience(policy: Policy, platform: str, conversation_id: str) -> bool:
    """Whether everyone the room `(platform, conversation_id)` names can read a document made in
    it: true only for an explicit, non-empty `persons` list. A `"*"` room names nobody, so only a
    document's creator reads what was made there (`may_read`), and an unlisted room names nobody.
    The caller reads the document room-scoped, so it was made in this room, and `rooms_named`
    gives each listed person that room's read. Fail closed: anything else is False."""
    convo = policy.conversation(platform, conversation_id)
    return convo is not None and convo.persons is not None and bool(convo.persons)


def room_label(policy: Policy, platform: str, conversation_id: str) -> str:
    """The operator's label of the room `(platform, conversation_id)` names, or "" when no
    allowed room answers to it. The portal shows it beside a document, but only to someone
    whose own rooms include it (`nora.web.documents`), so it never names a room to a person
    who is not in it."""
    for convo in policy.rooms():
        if convo.platform == platform and conversation_id in convo.ids:
            return convo.label
    return ""


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
        content_checks: bool = True,
        shared_room: Callable[[str, str, str], bool] | None = None,
        autonomous_screen: Callable[[str], str | None] | None = None,
    ) -> None:
        self._autonomous_screen = autonomous_screen
        self._shared_room = shared_room
        self._content_checks = content_checks
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
    @property
    def link_base(self) -> str:
        """The public origin a document link is built under; `""` while none is configured."""
        return self._public_url

    async def _target_problem(
        self,
        platform: str,
        conversation_id: str,
        document_id: UUID | None,
        base_revision: int | None,
        person_id: str | DocumentAuthority,
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
                    f"{base_revision}. Read it again before deciding whether to revise."
                ),
            )
        return None

    # ------------------------------------------------------------ catalog, read
    def catalog(self) -> str:
        """The catalog prompt the checker's schema was generated with: the components, the
        limits and the rules for writing one, so a model that has not seen them learns them
        from the tool."""
        return catalog_prompt()

    def _named(self, person_id: str | DocumentAuthority | None) -> frozenset[RoomKey]:
        """The rooms whose explicit list names `person_id`; nothing without a wiring or a
        requester, so only a creator reads (`may_read`)."""
        if isinstance(person_id, DocumentAuthority):
            return frozenset(
                (p, c)
                for p, c in person_id.rooms
                if self._shared_room is not None and self._shared_room(person_id.unit, p, c)
            )
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
        person_id: str | DocumentAuthority | None,
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
        if (isinstance(person_id, DocumentAuthority) and person_id.as_of_ms is not None
            and not current.written_by.startswith(f"cognition:{person_id.session_id};")):
            history = await self._store.history(document_id=current.summary.id)
            candidates = [h for h in history
                          if int(h.recorded_at.timestamp() * 1000) <= person_id.as_of_ms]
            if not candidates:
                return None
            head = max(candidates, key=lambda h: h.revision)
            revision = await self._store.revision(document_id=current.summary.id,
                                                  revision=head.revision)
            if revision is None:
                return None
            current = replace(current, summary=replace(current.summary,
                title=revision.title, revision=revision.revision, updated_at=revision.recorded_at),
                spec=revision.spec, text=revision.text, sha256=revision.sha256,
                media=revision.media, written_by=revision.written_by)
        if isinstance(person_id, DocumentAuthority):
            from nora.agent.egress import scan_reply

            if scan_reply(current.text, content_checks=self._content_checks) or (
                self._autonomous_screen and self._autonomous_screen(current.text)
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
        person_id: str | DocumentAuthority | None = None,
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
        here, so it can be restored."""
        if isinstance(person_id, DocumentAuthority) or not self._is_person(person_id):
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
    async def _autonomous_revise(
        self, *, platform: str, conversation_id: str, person_id: DocumentAuthority,
        raw: bytes | str | dict[str, Any], document_id: UUID | None,
        base_revision: int | None,
    ) -> PublishResult | tuple[dict[str, Any], str]:
        """The `DocumentAuthority` publish path: cited replacements on the stored spec,
        answered with `(spec, written_by)` so the common path stores what was judged."""
        if person_id.read_only:
            return PublishResult("refused", note="Historical sessions cannot revise documents.")
        if (
            document_id is None
            or not person_id.evidence
            or (platform, conversation_id) not in self._named(person_id)
        ):
            return _no_such_document()
        current = await self._in_room(
            platform=platform,
            conversation_id=conversation_id,
            document_id=document_id,
            handle="",
            person_id=person_id,
        )
        if current is None or current.summary.state != "active":
            return _no_such_document()
        if base_revision != current.summary.revision:
            return PublishResult("conflict", note="The revision changed; nothing was updated.")
        if not isinstance(raw, dict) or set(raw) != {"changes"}:
            return PublishResult(
                "refused", note="Autonomous revisions require cited replacements."
            )
        changes = raw["changes"]
        if not isinstance(changes, list) or not changes:
            return PublishResult("refused", note="No supported replacement was supplied.")
        revised = copy.deepcopy(current.spec)
        sources = {n: (p, c, text) for n, p, c, text in person_id.evidence}
        cited = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"path", "old", "new", "sources"}:
                return PublishResult(
                    "refused", note="Each replacement needs its path and sources."
                )
            path, old, new = change["path"], change["old"], change["new"]
            ids = change["sources"]
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or not isinstance(old, str)
                or not old
                or not isinstance(new, str)
                or not new
                or not isinstance(ids, list)
                or not ids
                or any(type(n) is not int or n not in sources for n in ids)
            ):
                return PublishResult("refused", note="The replacement lacks shown evidence.")
            here = self._room_ids(platform, conversation_id) | {conversation_id}
            if any(sources[n][0] != platform or sources[n][1] not in here for n in ids):
                return PublishResult(
                    "refused", note="The evidence is outside this document's room."
                )
            if not any(new == sources[n][2].strip() for n in ids):
                return PublishResult(
                    "refused", note="The replacement must quote its shown evidence."
                )
            try:
                parts = [x.replace("~1", "/").replace("~0", "~") for x in path[1:].split("/")]
                node = revised
                for part in parts[:-1]:
                    node = node[int(part)] if isinstance(node, list) else node[part]
                key = int(parts[-1]) if isinstance(node, list) else parts[-1]
                value = node[key]
                if not isinstance(value, str) or value.count(old) != 1:
                    raise ValueError("replacement target changed")
                node[key] = value.replace(old, new, 1)
            except (KeyError, IndexError, TypeError, ValueError):
                return PublishResult("refused", note="The replacement target does not match.")
            cited.update(ids)
        return revised, f"cognition:{person_id.session_id};sources:" + ",".join(
            map(str, sorted(cited))
        )

    async def _revision_target(
        self, platform: str, conversation_id: str, document_id: UUID,
        base_revision: int | None, person_id: str | DocumentAuthority,
    ) -> StoredDocument | PublishResult:
        """The stored document a revision lands on, or the refusal/conflict to answer."""
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
                "conflict", document_id=document_id, revision=current.summary.revision,
                note=(
                    f"This document is at revision {current.summary.revision}, not "
                    f"{base_revision}. Read it again before deciding whether to revise."
                ),
            )
        return current

    async def publish(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str | DocumentAuthority,
        raw: bytes | str | dict[str, Any],
        written_by: str,
        document_id: UUID | None = None,
        base_revision: int | None = None,
        person_names: frozenset[str] | None = None,
        live_addresses: frozenset[str] | None = None,
    ) -> PublishResult:
        if isinstance(person_id, DocumentAuthority):
            outcome = await self._autonomous_revise(
                platform=platform, conversation_id=conversation_id,
                person_id=person_id, raw=raw, document_id=document_id,
                base_revision=base_revision,
            )
            if isinstance(outcome, PublishResult):
                return outcome
            raw, written_by = outcome
        if isinstance(person_id, str) and not self._is_person(person_id):
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

        rewritten, media, findings, down = await resolve_media(
            records=self._records, media=self._media, room_ids=self._room_ids,
            now_ms=self._now_ms, window=self._window, platform=platform,
            conversation_id=conversation_id, document=checked.document,
        )
        if down:
            return PublishResult(
                "unavailable", note="The room's pictures could not be read, so nothing was saved."
            )
        if findings:
            return PublishResult("refused", findings=tuple(findings))
        if document_maps(rewritten):
            # The target's cheap checks first: a stale `base_revision` or another room's id is
            # answered before any billed lookup, so the model's «read it again and publish
            # again» after a conflict does not pay for the same places twice.
            early = await self._target_problem(
                platform, conversation_id, document_id, base_revision, person_id
            )
            if early is not None:
                return early
        rewritten, findings, down = await ground_maps(
            place_locator=self._place_locator, content_checks=self._content_checks,
            document=rewritten, person_names=person_names, live_addresses=live_addresses,
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
                if not isinstance(person_id, str):
                    return _no_such_document()
                made = await self._store.create(
                    document_id=await self._fresh_id(platform, conversation_id, person_id),
                    platform=platform,
                    conversation_id=conversation_id,
                    created_by=person_id,
                    revision=revision,
                )
                return self._ok(made.summary.id, made.summary.revision)
            current = await self._revision_target(
                platform, conversation_id, document_id, base_revision, person_id
            )
            if isinstance(current, PublishResult):
                return current
            stored = await self._store.add_revision(
                document_id=document_id,
                base_revision=current.summary.revision,
                revision=revision,
            )
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`
            return PublishResult(
                "unavailable", note="The document could not be saved; nothing was stored."
            )
        if stored.status == "conflict":
            return PublishResult(
                "conflict",
                document_id=document_id,
                note="Someone revised this document a moment ago. Read it again and publish again.",
            )
        if stored.status == "not_active":
            # Archived or deleted between the read above and the write.
            return _not_active("no longer active")
        if stored.status == "not_found" or stored.document is None:
            return _no_such_document()
        return self._ok(document_id, stored.document.summary.revision)


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
    "room_names_audience",
    "rooms_named",
]
