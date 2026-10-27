"""The Profile adaptor, in shadow: one Dreamer session per (unit, person),
writing typed lines through `profile_write` (design/unified-memory/45 §4).

**Shadow** (45 §6, 00 «Sequence» step 6): it writes `profile_line` and
nothing reads it. The Character head, `peer_card` and `peer_history` keep
rendering, Honcho's feed keeps running, and no prompt changes until the
switch (step 8). `[memory.profile] write` turns the sessions on.

**The wake** is the person's Character rule, counted over the rooms that
list them (35 §3): `[memory.profile.person] update_every_messages` of their messages since the
key's watermark, or `stale_seconds` with any. **Plus the stimulus wake** (45
§4 prerequisite 4): a `told` line a turn wrote about them (`remember` on the
graph surface) or a dispute of one of its lines (`profile_dispute` on the
same surface) since the account's last session counts as a full batch, so
the session weighs it at once.

**Historical recovery** (75): a separate durable, frozen Record boundary
and oldest-first own/mention union cursor recover the complete history in
bounded sessions. Normal recent updates keep their existing runtime watermark.
The first settled session still receives the legacy Character head as a gap
checklist, never as an uncited source of typed facts. `memory profile-history`
reports coverage and explicitly restarts an old incomplete shadow pass.

**What it reads** (45 §4): its own held lines by handle (`P1`, ...), the
person's new messages, the messages that MENTION them (the index's `mentions`
edges, written from the resolved `messages.mention_persons_json`), the
response episodes they answered, their social items (at most
`SOCIAL_ITEMS_MAX` a wake, with a post's or reel's latest counts, cited
`social:<network>:<item id>` — evidence about its author alone), their Rollup
ties and named-place presence Rollup observations, the
disputes of its lines raised since it last settled (35 §2), and the unit's
room cultures.

**One atomic write settles the session**: `profile_write(ops)` is judged
whole by `nora.domain.profile.plan_write` (one refused op refuses the call,
with every reason), lands at once, and applies the 60-day trait decay;
`profile_no_change(why)` settles with nothing added and applies the decay.
A session that does neither retains its source revisions without abandonment (45 §4a).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    render_group,
    render_row_window,
    restamp,
)
from nora.domain.culture import (
    CultureRevision,
    Episode,
    RoomMessage,
)
from nora.domain.errors import AccountNotRecordableError
from nora.domain.media import placeholders_in
from nora.domain.media_join import MediaJoin, body_with_custody_slots, shas_in
from nora.domain.ports import Proposal
from nora.domain.profile import (
    PROFILE_HKT,
    PROFILE_OUTCOMES,
    Account,
    ProfileCitable,
    ProfileDispute,
    ProfileLine,
    ProfileOp,
    ProfileWriteResult,
    Said,
    handles_for,
    identity_text,
    parse_ops,
    plan_write,
    rollup_citation,
)
from nora.domain.profile_inputs import PersonSourceRevision
from nora.domain.prompt import age, escape
from nora.domain.social_stream import PersonSocialItem, RecordedCounts, to_us
from nora.language.character import (
    CHARACTER_PERSON_TOOLS,
    SOCIAL_ITEMS_MAX,
    PersonKey,
    _caption,
    _Shown,
    _shows,
    _within_budget,
    room_cultures_block,
)
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "PROFILE_PERSON_ADAPTOR_NAME",
    "ProfileEvidence",
    "ProfilePersonAdaptor",
    "ProfileStaged",
]

#: The adaptor's `name`: its watermark identity, schedule, budget and grant
#: key, and its objective file.
PROFILE_PERSON_ADAPTOR_NAME = "profile-person"

#: The most response episodes one session is handed (the newest they
#: answered); the rest of what they said is in their messages.
EPISODES_MAX = 12

#: The most ties one session is handed (the most replied first).
TIES_MAX = 8

#: The most open disputes one session weighs (the oldest first).
DISPUTES_MAX = 10

# Whole-source input bound; larger roots remain pending, never partly citable.
SOURCE_BLOCK_CHARS = 20_000

#: `inputs` holds ten sites (the account's revision, lines, pending `told`
#: lines and open disputes, the Character head, cited rows in one read, the
#: ties, the rooms' cultures in one gather, the handed items' counts, the
#: cached media join);
#: `commit` one write.
PROFILE_STORE_CALLS_PER_TICK = 12

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("about_person", 5, "turn"),
    ("profile_lines", 10, "turn"),
    ("previous_character", 20, "turn"),
    ("profile_disputes", 30, "turn"),
    ("profile_messages", 40, "turn"),
    ("profile_mentions", 45, "turn"),
    ("profile_episodes", 50, "turn"),
    ("social_items", 55, "turn"),
    ("profile_ties", 60, "turn"),
    ("profile_source_revisions", 65, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {name: (order, key) for name, order, key in _BLOCKS}


def _at(name: str) -> dict[str, Any]:
    order, key = _ROW[name]
    return {"order": order, "key": key}


class _Reads(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s reads this adaptor uses."""

    async def history_state(self, *, key: str) -> tuple[int, int]: ...

    async def advance_history(self, *, key: str, cursor: int) -> None: ...

    async def historical_page(
        self,
        *,
        rooms: Sequence[tuple[str, str]],
        person_id: str,
        after: int,
        through: int,
        limit: int,
    ) -> list[RoomMessage]: ...

    async def person_count(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> int: ...

    async def person_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]: ...

    async def mentions_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]: ...

    async def episodes(
        self, *, unit: str, after: int, limit: int, evidence_only: bool = ...,
    ) -> tuple[list[Episode], int]: ...

    async def rows_by_id(self, *, ids: Sequence[int]) -> list[RoomMessage]: ...

    async def tie_totals(
        self, *, unit: str, person_id: str,
    ) -> list[tuple[str, int, int, int, int, int, int]]: ...

    async def social_cursor(self, *, adaptor: str, key: str) -> tuple[int, int]: ...

    async def advance_social_cursor(
        self, *, adaptor: str, key: str, cursor: int, read_at: int,
    ) -> None: ...


class _PersonSocial(Protocol):
    """`nora.infra.sql.social_stream.SqlSocialStreamStore`'s person-session
    reads (the readable store's: gone, hidden and opted-out items are left
    out there), plus `current_counts`, a post's or reel's latest counts."""

    async def polled_count(self, *, persons: Sequence[str], after_us: int) -> int: ...

    async def now(self) -> datetime: ...

    async def person_items(
        self, *, persons: Sequence[str], after_us: int, limit: int,
    ) -> list[PersonSocialItem]: ...

    async def current_counts(
        self, *, items: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], RecordedCounts]: ...


@dataclass
class _Read:
    """One social read in flight for a key: where it started, when (the
    database's clock, in microseconds) and how far it reaches once the
    session's items are known (`handed`). `PersonEvidence._Read`'s shape and
    discipline, for its reason: a failed run hands the same items again."""

    start: int
    at: int
    reach: int


class _Profile(Protocol):
    """`nora.infra.sql.profile.SqlProfileStore`."""

    async def room_context(self, *, key: str) -> CultureRevision | None: ...

    async def account_state(self, *, key: str) -> tuple[int, int | None]: ...

    async def account_heads(self, *, key: str) -> tuple[ProfileLine, ...]: ...

    async def pending_told(self, *, key: str) -> int: ...

    async def pending_disputes(self, *, key: str) -> int: ...

    async def open_disputes(
        self, *, key: str, limit: int
    ) -> tuple[ProfileDispute, ...]: ...

    async def profile_write(
        self, *, account: Account, ops: Sequence[ProfileOp], citable: ProfileCitable,
        now_ms: int, written_by_session: str,
        apply_decay: bool, expected_revision: int | None = None,
    ) -> ProfileWriteResult: ...


class _Culture(Protocol):
    async def head(self, *, key: str) -> CultureRevision | None: ...


class _Evidence(Protocol):
    """What a Profile adaptor and the dream loop need of an evidence source:
    the count and newest read the loop wakes on, and `held`, the watermark
    the last `newest` started after. `ProfileEvidence` for a person,
    `profile_collective.CollectiveEvidence` for a collective.

    `handed`/`forget`/`gave_up` keep a source's own cursor across runs
    (`ProfileEvidence`'s social items, 45 §4); a source with no such cursor
    implements them as no-ops (`CollectiveEvidence`), so the adaptor calls
    them directly and the store-call walk sees every call."""

    def historical(self, key: str) -> bool: ...

    def held(self, key: str) -> int | None: ...

    async def count(self, *, key: str, after: int) -> int: ...

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[Any], int]: ...

    def handed(self, key: str, items: Sequence[Any]) -> None: ...

    def forget(self, key: str) -> None: ...

    def gave_up(self, key: str) -> None: ...


def _listed(pk: PersonKey) -> list[tuple[str, str]]:
    """The `(platform, primary_id)` of the rooms that list them: their
    evidence (35 §3)."""
    return [(r.platform, r.key) for r in pk.rooms if r.key in pk.listed]


@dataclass(frozen=True)
class Mention:
    """A message that MENTIONS the person, wrapped so the batch keeps it
    apart from one they WROTE (both are `RoomMessage`): it renders in its own
    block and is citable the same way."""

    message: RoomMessage


class _SourceInputs(Protocol):
    async def recovery_requested(self, *, key: str) -> bool: ...
    async def claim_recovery(self, *, key: str) -> None: ...
    async def messages(self, *, key: str, rooms: Sequence[tuple[str, str]],
                       person: str, limit: int, after: int = 0) -> list[PersonSourceRevision]: ...
    async def media_recovery(self, *, key: str, rooms: Sequence[tuple[str, str]],
                             person: str, initialize: bool = False) -> dict[str, int | bool]: ...
    async def social(self, *, key: str, persons: Sequence[str],
                     limit: int) -> list[PersonSourceRevision]: ...
    async def register(self, *, key: str, unit: str, rooms: Sequence[tuple[str, str]],
                       person: str, limit: int) -> list[PersonSourceRevision]: ...
    async def rollup(self, *, key: str, unit: str, rooms: Sequence[tuple[str, str]],
                     person: str, limit: int) -> list[PersonSourceRevision]: ...
    async def settle(self, *, key: str, revisions: Sequence[PersonSourceRevision],
                     attempted: Sequence[PersonSourceRevision] = ()) -> None: ...


class ProfileEvidence:
    """The dream loop's count and read for a Profile key: the person's
    messages in the rooms that list them past the watermark, the episodes they
    answered past it, the stimulus wake (a turn's `told` line), and — with
    `social` — their social items not yet handed to a session (45 §4, the
    person account only), at most `SOCIAL_ITEMS_MAX` a wake.

    **A source is an item type.** `newest` hands the session a mixed batch;
    `ProfilePersonAdaptor.inputs` renders each type in its own block and adds
    what it may cite to `ProfileCitable` (a Record row by id, an episode as
    `response:<id>`, a social item as `social:<network>:<item id>`). Another
    source is one more read here, one more type there, and one more block row:
    nothing else changes shape.

    With `source_inputs`, owning-store snapshots use per-source revisions at
    the same consolidation seam. Cached readings on consumed messages, social
    enrichment/state, current Register heads and non-location Rollup changes
    remain pending until actually rendered and durably settled. The legacy
    message and frozen historical cursors are independent. Initial cached-media
    recovery has its own frozen boundary, reported by `profile-history`.
    Without that collaborator, legacy callers retain their chronological
    social cursor; an unsettled person batch is never abandoned.
    """

    def __init__(
        self,
        *,
        store: _Reads,
        profile: _Profile,
        keys: Mapping[str, PersonKey],
        wake_batch: int,
        source_inputs: _SourceInputs | None = None,
        source_timeout_seconds: float = 3.0,
        social: _PersonSocial | None = None,
        former_ids: Callable[[str], Sequence[str]] = lambda _person: (),
    ) -> None:
        """`former_ids` is `Persons.former_ids_for`: an item recorded under a
        person's retired id is theirs too."""
        self._store = store
        self._profile = profile
        self._keys = keys
        self._wake_batch = wake_batch
        self._source_inputs = source_inputs
        self._source_timeout = source_timeout_seconds
        self._source_handed: dict[str, list[PersonSourceRevision]] = {}
        self._source_selected: dict[str, list[PersonSourceRevision]] = {}
        self._social = social
        self._former_ids = former_ids
        self._held: dict[str, int] = {}
        self._history: dict[str, int] = {}
        self._reads: dict[str, _Read] = {}

    def held(self, key: str) -> int | None:
        """The watermark the last `newest` for `key` started after."""
        return self._held.get(key)

    def _ids(self, person_id: str) -> tuple[str, ...]:
        """Every id their social items may be recorded under."""
        return (person_id, *self._former_ids(person_id))

    async def _sources(self, key: str, limit: int, after: int) -> list[PersonSourceRevision]:
        source = self._source_inputs
        if source is None:
            return []
        pk = self._keys[key]
        args: dict[str, Any] = {"key": key, "rooms": _listed(pk),
                                "person": pk.person.person_id}
        out: list[PersonSourceRevision] = []
        readers: tuple[tuple[str, Callable[[], Awaitable[list[PersonSourceRevision]]]], ...] = (
            ("message", lambda: source.messages(**args, limit=limit, after=after)),
            ("social", lambda: source.social(key=key, persons=self._ids(pk.person.person_id),
                                             limit=SOCIAL_ITEMS_MAX)),
            ("register", lambda: source.register(**args, unit=pk.unit, limit=SOCIAL_ITEMS_MAX)),
            ("rollup", lambda: source.rollup(**args, unit=pk.unit, limit=EPISODES_MAX)),
        )
        for domain, read in readers:
            try:
                out.extend(await asyncio.wait_for(read(), timeout=self._source_timeout))
            except Exception:
                # A failed domain remains pending; other readable domains can
                # still settle. No absence token is fabricated for this read.
                log.exception("profile source unavailable: %s", domain)
                out.append(PersonSourceRevision(
                    domain=cast(Any, domain), source=f"{domain}:unavailable", revision="",
                    state="unavailable", recorded_at="unknown"))
        return list({(r.domain, r.source): r for r in out}.values())

    async def count(self, *, key: str, after: int) -> int:
        pk = self._keys[key]
        boundary, progress = await self._store.history_state(key=key)
        if progress < boundary:
            return self._wake_batch
        if self._source_inputs is not None:
            if await asyncio.wait_for(self._source_inputs.recovery_requested(key=key),
                                      timeout=self._source_timeout):
                return self._wake_batch
            # Read up to a whole batch: the wake rule is `new >= wake_batch`,
            # so a probe of one could never reach it (the person woke only
            # on the stale rule).
            n = len(await self._sources(key, self._wake_batch, max(after, boundary)))
            try:
                recovery = await asyncio.wait_for(self._source_inputs.media_recovery(
                    key=key, rooms=_listed(pk), person=pk.person.person_id, initialize=True),
                    timeout=self._source_timeout)
                if not recovery["initial_sweep_complete"] and recovery["unattempted_cached"]:
                    return self._wake_batch
            except Exception:
                log.exception("profile cached-media recovery unavailable")
                return max(1, n)
            if await self._profile.pending_told(key=key) or await self._profile.pending_disputes(
                key=key
            ):
                return self._wake_batch
            return n
        n = await self._store.person_count(
            rooms=_listed(pk), person_id=pk.person.person_id, after=after)
        if n >= self._wake_batch:
            return n
        if await self._profile.pending_told(key=key):
            return self._wake_batch
        if await self._profile.pending_disputes(key=key):
            return self._wake_batch
        if self._social is None:
            return n
        cursor, read_at = await self._store.social_cursor(
            adaptor=PROFILE_PERSON_ADAPTOR_NAME, key=key)
        polled = await self._social.polled_count(
            persons=self._ids(pk.person.person_id), after_us=max(cursor, read_at))
        return n + min(polled, SOCIAL_ITEMS_MAX)

    async def newest(
        self,
        *,
        key: str,
        after: int,
        limit: int,
    ) -> tuple[Sequence[Any], int]:
        """A frozen historical page until exhaustion; then recent updates.

        Recent updates read their newest `limit` messages past `after` and the cursor past all of
        them, the messages that mention them over the same span (same Record id
        space, so one cursor: the newest id either read reached), then the
        newest `EPISODES_MAX` closed episodes past it that they answered, then
        the oldest `SOCIAL_ITEMS_MAX` social items not yet handed. The returned
        cursor is the messages' alone; the items' is moved by `settle`.
        A mention never wakes the session (45 §4's wake is their own batch,
        stale, or a stimulus); it is read when something else did."""
        pk = self._keys[key]
        self._held[key] = after
        self._reads.pop(key, None)
        boundary, progress = await self._store.history_state(key=key)
        if progress < boundary:
            page = await self._store.historical_page(
                rooms=_listed(pk),
                person_id=pk.person.person_id,
                after=progress,
                through=boundary,
                limit=limit,
            )
            self._history[key] = page[-1].row.id if page else boundary
            historical_batch = [
                m if m.row.person_id == pk.person.person_id else Mention(message=m) for m in page
            ]
            # Recent runtime watermark is independent until history exhausts.
            return historical_batch, after
        self._history.pop(key, None)
        after = max(after, boundary)
        if self._source_inputs is not None:
            self._source_handed.pop(key, None)
            # The ordinary loop already owns this key's in-flight exclusion.
            # Consume before any source/model failure: this wake buys one
            # attempt, not an automatic paid retry until the backlog is empty.
            await asyncio.wait_for(self._source_inputs.claim_recovery(key=key),
                                   timeout=self._source_timeout)
            # Per-source revisions retain old messages and bounded leftovers;
            # the frozen historical and legacy Record positions stay separate.
            snapshots = await self._sources(key, limit, after)
            self._source_selected[key] = snapshots
            return snapshots, after
        messages, cursor = await self._store.person_since(
            rooms=_listed(pk), person_id=pk.person.person_id, after=after, limit=limit,
        )
        mentioned, mention_cursor = await self._store.mentions_since(
            rooms=_listed(pk), person_id=pk.person.person_id, after=after, limit=limit,
        )
        found, _ = await self._store.episodes(
            unit=pk.unit, after=after, limit=limit, evidence_only=True)
        listed = set(pk.listed)
        theirs = [e for e in found if e.room in listed
                  and any(r.person_id == pk.person.person_id for r in e.replies)]
        batch: list[RoomMessage | Episode | Mention | PersonSocialItem] = [
            *messages, *(Mention(message=m) for m in mentioned), *theirs[-EPISODES_MAX:]]
        if self._social is None:
            return batch, max(cursor, mention_cursor)
        start, _read_at = await self._store.social_cursor(
            adaptor=PROFILE_PERSON_ADAPTOR_NAME, key=key)
        # The database's clock BEFORE the read: an item recorded after it is
        # not in this batch, so it counts toward the next wake.
        at = to_us(await self._social.now())
        items = await self._social.person_items(
            persons=self._ids(pk.person.person_id), limit=SOCIAL_ITEMS_MAX,
            after_us=start,
        )
        self._reads[key] = _Read(start=start, at=at, reach=start)
        return [*batch, *items], max(cursor, mention_cursor)

    def historical(self, key: str) -> bool:
        return key in self._history

    def handed(self, key: str, items: Sequence[Any]) -> None:
        """The items the session for `key` was actually shown: the cursor
        may move past these and no further."""
        revisions = [i for i in items if isinstance(i, PersonSourceRevision)
                     and i.state != "unavailable" and i.revision]
        if revisions:
            self._source_handed[key] = revisions
            return
        read = self._reads.get(key)
        if read is not None and items:
            read.reach = max(read.start, *(i.recorded_us for i in items))

    def gave_up(self, key: str) -> None:
        """Unsettled person input never advances any source domain."""
        self.forget(key)

    def forget(self, key: str) -> None:
        """The session for `key` raised: the run is `unavailable`, nothing
        settles, and the same items are read again."""
        self._reads.pop(key, None)
        self._source_handed.pop(key, None)
        self._source_selected.pop(key, None)

    async def settle(self, *, key: str) -> None:
        """After the account committed, acknowledge exactly the rendered snapshots.
        Legacy social/history progress retains its existing separate domains."""
        revisions = self._source_handed.pop(key, [])
        attempted = self._source_selected.pop(key, [])
        if (revisions or attempted) and self._source_inputs is not None:
            await self._source_inputs.settle(key=key, revisions=revisions, attempted=attempted)
        history = self._history.pop(key, None)
        if history is not None:
            await self._store.advance_history(key=key, cursor=history)
        read = self._reads.pop(key, None)
        if read is None:
            return
        await self._store.advance_social_cursor(
            adaptor=PROFILE_PERSON_ADAPTOR_NAME, key=key, cursor=read.reach,
            read_at=read.at,
        )


@dataclass(frozen=True)
class ProfileStaged:
    """What a settled session hands `commit`: the write it staged (`()` for
    `profile_no_change`, which still applies the decay), what it may cite, and
    the revision its handles were read at."""

    account: Account
    ops: tuple[ProfileOp, ...]
    citable: ProfileCitable
    revision: int


@dataclass
class _Run:
    account: Account
    citable: ProfileCitable
    revision: int
    heads: tuple[ProfileLine, ...]
    batch_start: int | None
    #: A teller's name as the session saw it -> their person id, for
    #: `told_by` (a model names people, it never sees an id).
    tellers: Mapping[str, str]
    #: The settled write (`()`: no change); `None` until a verb settles it.
    staged: tuple[ProfileOp, ...] | None = None


def _screened_by_the_runtime(_text: str) -> str | None:
    """The verb-time judge's screen: none, because `DreamerRuntime._screened`
    refused any call whose arguments carry an identifier or coordinate
    before the verb ran. The store's own screen runs again at `commit`."""
    return None


def _said(m: RoomMessage) -> Said:
    # A quoted response/attachment is not another occasion. Shared media bytes
    # retain one root across repeated messages.
    media = sorted(shas_in(m.row.media_json))
    occasion = (f"message:{m.room}:{m.row.quoted_ts}" if m.row.quoted_ts else
                "media:" + ",".join(media) if media else f"message:{m.room}:{m.row.ts}")
    return Said(ts=m.row.edit.ts if m.row.edit is not None else m.row.ts,
                person_id=m.row.person_id, room=m.room, occasion=occasion,
                happened_at=m.row.quoted_ts or m.row.ts,
                medium="media" if media else "chat")


def _line_attrs(line: ProfileLine, handle: str, now: datetime,
                name_of: Callable[[str | None], str]) -> list[tuple[str, str | None]]:
    return [
        ("id", handle), ("part", line.part), ("kind", line.kind), ("mode", line.mode),
        ("state", None if line.state == "current" else line.state),
        ("told_by", name_of(line.told_by) if line.told_by else None),
        ("verdict", line.verdict),
        ("as_of", datetime.fromtimestamp(line.as_of / 1000, PROFILE_HKT).isoformat()
         if line.as_of is not None else None),
        ("supported", age(line.last_supported_at or line.written_at, now)),
        ("cites", str(len(line.citations))),
    ]


class ProfilePersonAdaptor:
    """One person's Profile in one unit, in shadow."""

    name = PROFILE_PERSON_ADAPTOR_NAME
    key_kind = "entity"
    uses_watermark = True
    objective = PROFILE_PERSON_ADAPTOR_NAME
    #: Unread: answers through `verbs`, never an envelope.
    envelope_key = "profile"
    #: The person session's read tools (`CHARACTER_PERSON_TOOLS`): memory on
    #: the selected surface, `lookup_person`, `group_members`; presence comes
    #: through scoped Rollup inputs.
    tools: frozenset[str] = CHARACTER_PERSON_TOOLS
    store_calls_per_tick = PROFILE_STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        profile: _Profile,
        culture: _Culture,
        evidence: _Evidence,
        reads: _Reads,
        keys: Mapping[str, PersonKey],
        name_of: Callable[[str | None], str],
        clock_ms: Callable[[], int],
        metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
        render: bool = False,
        media_join: MediaJoin | None = None,
        social: _PersonSocial | None = None,
        screen: Callable[[str], str | None] | None = None,
        render_chars: int = 0,
        handle_owners: Callable[[str], frozenset[str]] = lambda _handle: frozenset(),
        coordinate_screen: Callable[[str], bool] | None = None,
    ) -> None:
        """`social` is the readable social store the session's items and their
        counts come from (45 §4: the person account's alone); `screen` is
        `nora.agent.egress.scan_identifiers`, injected because this layer may
        not import it, and `render_chars` bounds each medium's reading
        (`[media] comprehension_render_chars`). A session built with `social`
        but no `screen` withholds every item's `shows` — fail-closed, as a
        withheld reading in `character._shows`. `handle_owners` says whose
        Instagram username a handle is (by default nobody's, so every handle
        in a caption is redacted)."""
        self._coordinate_screen = coordinate_screen
        self._profile = profile
        self._media_join = media_join
        self._render = render
        self._culture = culture
        #: Read by the dream loop, never awaited here.
        self.evidence = evidence
        self._reads = reads
        self._keys = keys
        self._population = tuple(sorted(keys))
        self._name_of = name_of
        self._clock_ms = clock_ms
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._social = social
        self._screen = screen
        self._render_chars = render_chars
        self._handle_owners = handle_owners
        self._runs: dict[str, _Run] = {}

    def keys(self) -> Sequence[str]:
        return self._population

    def tool_room(self, key: str) -> str | None:
        pk = self._keys.get(key)
        return None if pk is None else pk.home

    def _count(self, outcome: str, n: int = 1) -> None:
        if outcome not in PROFILE_OUTCOMES:
            raise ValueError(f"nora_profile_session_total{{{outcome}}} is outside its "
                             "vocabulary")
        if self._metrics is not None:
            self._metrics.inc("nora_profile_session_total", float(n), outcome=outcome)

    # -- inputs ------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """Who this is, the lines the account holds (by handle), the Character
        head until a session first settles the account, the rooms' cultures, their messages,
        the messages that mention them, the episodes they answered, their social
        items with a post's or reel's counts, their ties and the disputes
        awaiting weighing. `""` when nothing is new and no turn wrote a line or
        a dispute: no model call."""
        pk = self._keys.get(key)
        revisions = [r for r in batch if isinstance(r, PersonSourceRevision)]
        # Never cut the interior of an input: trimmed revisions stay pending.
        # Social retains its existing smaller block budget below.
        selected: list[PersonSourceRevision] = []
        used: dict[str, int] = {}
        for item_revision in revisions:
            size = len(json.dumps({"state": item_revision.state,
                "properties": item_revision.properties,
                "roots": [(x.row.id, x.row.body, x.row.media_json) for x in item_revision.roots]},
                ensure_ascii=False)) + len(item_revision.episode.text
                                           if item_revision.episode is not None else "")
            if used.get(item_revision.domain, 0) + size > SOURCE_BLOCK_CHARS:
                continue
            used[item_revision.domain] = used.get(item_revision.domain, 0) + size
            selected.append(item_revision)
        roots = {m.row.id: m for r in selected for m in r.roots}
        messages = [m for m in batch if isinstance(m, RoomMessage)]
        messages.extend(roots.values())
        mentions = [m.message for m in batch if isinstance(m, Mention)]
        episodes = [e for e in batch if isinstance(e, Episode)]
        episodes.extend(r.episode for r in selected if r.episode is not None)
        social = [m for m in batch if isinstance(m, PersonSocialItem)]
        social.extend(r.social for r in selected if r.social is not None)
        if pk is None:
            return ""
        # The revision before the lines: a write landing between the two reads
        # makes this session's write a `conflict`, never a write on shifted handles.
        revision, settled_at = await self._profile.account_state(key=key)
        heads = await self._profile.account_heads(key=key)
        pending = await self._profile.pending_told(key=key)
        disputes = await self._profile.open_disputes(key=key, limit=DISPUTES_MAX)
        if not (messages or mentions or episodes or social or selected or pending or disputes):
            return ""
        now = self._clock()
        person = pk.person.person_id
        # The social items, once each (45 §4): redacted, screened and cut to
        # the block budget. Their counts are read first — one `current_counts`
        # call over the batch (at most SOCIAL_ITEMS_MAX items) — so the block
        # carries them in one pass and the cursor passes nothing unrendered.
        counts = (
            await self._social.current_counts(
                items=[(it.network, it.item_id) for it in social])
            if social and self._social is not None and not revisions else {}
        )
        counts.update({(r.social.network, r.social.item_id): r.counts
                       for r in selected if r.social is not None and r.counts is not None})
        shown, social_block = _within_budget([
            _Shown(
                item=it,
                shows=None if self._screen is None else _shows(
                    it, screen=self._screen, render_chars=self._render_chars,
                    metrics=self._metrics, person=person, owners=self._handle_owners),
                caption=_caption(it.caption, person=person, owners=self._handle_owners),
                counts=counts.get((it.network, it.item_id)),
            )
            for it in social
        ], now)
        # Only what the block carries is handed, cited, and passed by the
        # cursor (`PersonEvidence.handed`'s rule).
        shown_items = {(r.item.network, r.item.item_id) for r in shown}
        selected = [r for r in selected if r.social is None or
                    (r.social.network, r.social.item_id) in shown_items]
        if not revisions:
            self.evidence.handed(key, [r.item for r in shown])
        # THE BACKFILL (35 §4): until a session first settles the account, it
        # is handed the prose Character head and the rows it cited, once. A
        # turn's `told` lines do not count: they may land before it.
        first = settled_at is None
        previous = await self._culture.head(key=key) if first else None
        # The rows a citation names, in one read: an old head's citations and
        # the message each dispute was said in (both citable).
        cited_ids = [int(c) for c in (previous.citations if previous else ()) if c.isdigit()]
        dispute_ids = {d.message_id for d in disputes}
        cited = (await self._reads.rows_by_id(ids=[*cited_ids, *dispute_ids])
                 if cited_ids or dispute_ids else [])
        old_cited = [m for m in cited if m.row.id not in dispute_ids]
        ties = ([] if revisions or self.evidence.historical(key) else
                (await self._reads.tie_totals(unit=pk.unit, person_id=person))[:TIES_MAX])
        cultures = await asyncio.gather(
            *(
                self._profile.room_context(key=r.key)
                if self._render
                else self._culture.head(key=r.key)
                for r in pk.rooms
            )
        )

        # Every code-citable legacy/dispute row is visible with its original
        # words and source time, not merely a number beside old model prose.
        visible = {m.row.id: m for m in [*old_cited, *messages, *mentions, *cited]
                   if m.room in pk.listed and not m.row.is_nora}
        # Join only the already scoped, visible source rows. The original row
        # remains the citation and attribution; cached readings are not claims
        # that the sender is pictured, owns an object, or has a habit.
        source_rows = {i: m.row for i, m in visible.items()}
        source_rows.update({r.id: r for e in episodes if e.room in pk.listed
                            for r in e.replies if r.person_id == person})
        joined = (await self._media_join.enrich(tuple(source_rows.values()), path="dreamer")
                  if self._media_join is not None else tuple(source_rows.values()))
        bodies = {}
        rendered_media: dict[int, set[str]] = {}
        for row in joined:
            raw = source_rows[row.id].body or ""
            if not shas_in(row.media_json):
                bodies[row.id] = raw
                continue
            state = "available_or_partial" if row.body != raw else "unavailable"
            rendered_media[row.id] = {handle for label, handle in placeholders_in(
                body_with_custody_slots(raw, row.media_json))
                                      if f"[{label} {handle}:" in (row.body or "")}
            bodies[row.id] = json.dumps({
                "raw_caption": raw,
                "cached_media_reading": {
                    "state": state,
                    "attribution": "shared_by_author",
                    "text": ((row.body or "") if row.body != raw
                             else "no readable media evidence"),
                    "limits": ("Sharing alone establishes no identity, ownership, residence "
                               "or habits. This reading is uncertain model interpretation, "
                               "not a self-statement by default. Readable attachment text may "
                               "supply a deduction premise when it explicitly states the claim "
                               "and identifies its subject, including through textual mapping "
                               "across cited roots; preserve uncertainty and attribution. "
                               "Told requires a human assertion or endorsement, never media "
                               "alone. No identity, ownership, residence or habit inference "
                               "from a pictured scene; no biometric or gender inference. "
                               "Location policy still applies; no home inference from a photo."),
                },
            }, ensure_ascii=False)
        if revisions:
            # Bound the actual joined text, not raw placeholder sizes. A source
            # is atomic under the budget and leftovers retain their revisions.
            actual: list[PersonSourceRevision] = []
            used = {}
            for candidate in selected:
                size = sum(len(bodies.get(x.row.id, "")) for x in candidate.roots)
                size += len(candidate.episode.text if candidate.episode is not None else "")
                if used.get(candidate.domain, 0) + size > SOURCE_BLOCK_CHARS:
                    continue
                used[candidate.domain] = used.get(candidate.domain, 0) + size
                actual.append(candidate)
            selected = actual
            episodes = [e for e in episodes if any(
                r.episode is e for r in selected) or e in batch]
            rendered_ids = {x.row.id for r in selected for x in r.roots}
            visible = {i: m for i, m in visible.items() if i in rendered_ids
                       or i in cited_ids or i in dispute_ids}
            acknowledged = [r for r in selected if not (
                r.domain == "message" and r.cache_ready
                and r.roots and any(not set(r.ready_handles).issubset(
                    rendered_media.get(x.row.id, set())) for x in r.roots)
            )]
            self.evidence.handed(key, acknowledged)
        messages = [m for m in visible.values() if m.row.person_id == person]
        mentions = [m for m in visible.values() if m.row.person_id != person]
        old_cited = [m for m in old_cited if m.row.id in visible]
        cited = [m for m in cited if m.row.id in visible]
        said: dict[int, Said] = {m.row.id: _said(m)
                                 for m in [*old_cited, *messages, *mentions, *cited]}
        replies: dict[int, Said] = {}
        for e in episodes:
            for r in e.replies:
                if r.person_id == person:
                    said[r.id] = Said(ts=r.ts, person_id=r.person_id, room=e.room,
                        occasion=f"response:{e.send_id}", medium="act")
                    replies.setdefault(e.send_id, said[r.id])
        tie_keys = {other: rollup_citation("tie", f"{pk.unit}|{','.join(sorted((person, other)))}")
                    for other, *_ in ties}
        account = Account(key=key, entity_kind="human", unit=pk.unit, subject=person)
        rollups: dict[str, list[PersonSourceRevision]] = {}
        for source_revision in selected:
            if (source_revision.domain == "rollup" and source_revision.citable is not None
                    and not source_revision.evidence):
                rollups.setdefault(source_revision.citable, []).append(source_revision)
        rollup_roots = {c: tuple(dict.fromkeys(str(x.row.id) for r in rows for x in r.roots))
                        for c, rows in rollups.items()}
        rollup_times = {c: max((x.row.ts for r in rows for x in r.roots), default=0)
                        for c, rows in rollups.items()}
        interpretation_only = set()
        for message in visible.values():
            caption = message.row.body or ""
            for label, handle in placeholders_in(caption):
                caption = caption.replace(f"[{label} {handle}]", "")
            if shas_in(message.row.media_json) and not caption.strip():
                interpretation_only.add(message.row.id)
        tellers: dict[str, str] = {}
        ambiguous: set[str] = set()
        for author in {person, *(m.row.person_id for m in visible.values()
                                 if m.row.person_id is not None), *(o for o, *_ in ties)}:
            for name in ((pk.person.name, *pk.person.aliases) if author == person
                         else (self._name_of(author),)):
                normalized = name.casefold()
                if normalized == "someone" or normalized in ambiguous:
                    continue
                if normalized in tellers and tellers[normalized] != author:
                    ambiguous.add(normalized)
                    tellers.pop(normalized)
                else:
                    tellers[normalized] = author
        self._runs[key] = _Run(
            account=account,
            citable=ProfileCitable(
                sources={c: s for r in selected for c, s in r.evidence},
                messages=said, interpretation_only=frozenset(interpretation_only),
                episodes={e.send_id: replies[e.send_id] for e in episodes
                          if e.send_id in replies} | {
                    int(c.split(":", 1)[1]): Said(ts=rollup_times[c], person_id=person,
                        room=rows[0].roots[0].room) for c, rows in rollups.items()
                    if c.startswith("response:") and rows[0].roots},
                rollup_keys=frozenset((*tie_keys.values(), *(c for c in rollups
                                                          if c.startswith("rollup:")))),
                rollup_roots=rollup_roots, rollup_as_of=rollup_times,
                # A social item is evidence about its author alone, on its
                # `taken_at` day, in no room (45 §4; the validator's
                # `social_not_author`/`unlisted_room` refusals).
                social={f"{r.item.network}:{r.item.item_id}": Said(
                    ts=int(r.item.taken_at.timestamp() * 1000),
                    person_id=person, room=None, medium="social",
                    occasion=f"social:{r.item.network}:{r.item.item_id}",
                ) for r in shown},
                listed_rooms=frozenset(pk.listed),
            ),
            revision=revision, heads=heads,
            batch_start=self.evidence.held(key),
            tellers=tellers,
        )
        labels = {r.key: r.label for r in pk.rooms}
        platform_sets: dict[str, set[str]] = {}
        for room in pk.rooms:
            platform_sets.setdefault(room.key, set()).add(room.platform)
        platforms = {key: next(iter(values)) for key, values in platform_sets.items()
                     if len(values) == 1}
        identity_names = {k.person.person_id for k in self._keys.values() if k.unit == pk.unit}
        def identity_name(pid: str) -> str:
            return self._name_of(pid) if pid in identity_names else "someone"
        handles = handles_for(heads)
        handle_of = {i: h for h, i in handles.items()}
        # A dispute names the line as it was when it matched; a state change
        # (hidden, disputed) superseded that row, so its id still resolves.
        handle_of.update({h.supersedes: handle_of[h.id]
                          for h in heads if h.supersedes and h.id in handle_of})
        held = [h for h in heads if h.id in handle_of]
        parts: list[ContextBlock | None] = [
            ContextBlock(
                name="about_person",
                **_at("about_person"),
                body="",
                when_empty="self_close",
                attrs=(("name", pk.person.name),
                       ("now", now.astimezone(PROFILE_HKT).isoformat()),
                       ("unit", pk.unit),
                       ("evidence_mode",
                        "historical" if self.evidence.historical(key) else "recent"))
                + ((("aliases", ", ".join(pk.person.aliases)),) if pk.person.aliases else ()),
            ),
            None
            if not held
            else ContextBlock(
                name="profile_lines",
                **_at("profile_lines"),
                body=render_elements(
                    held,
                    tag="line",
                    attrs=lambda x, _i: _line_attrs(x, handle_of[x.id], now, self._name_of),
                    body=lambda x: (
                        identity_text(x, name_of=identity_name) or "legacy untyped: " + x.text
                    ),
                ),
            ),
            None
            if previous is None
            else ContextBlock(
                name="previous_character",
                **_at("previous_character"),
                body=escape(previous.body),
                attrs=(("written", age(previous.written_at, now)),)
                + ((("cites", ", ".join(str(m.row.id) for m in old_cited)),) if old_cited else ()),
            ),
            room_cultures_block(pk.rooms, cultures, now),
            None
            if not disputes
            else ContextBlock(
                name="profile_disputes",
                **_at("profile_disputes"),
                body=render_elements(
                    disputes,
                    tag="dispute",
                    attrs=lambda d, _i: (
                        ("by", self._name_of(d.disputed_by)),
                        ("kind", d.kind),
                        ("age", age(d.written_at, now)),
                        ("said", str(d.message_id)),
                        ("line", handle_of.get(d.line_id or "")),
                    ),
                    body=lambda d: d.claim,
                ),
            ),
            None
            if not messages
            else ContextBlock(
                name="profile_messages",
                **_at("profile_messages"),
                body=render_row_window(
                    messages,
                    row_id=lambda m: m.row.id,
                    attrs=lambda m: (
                        ("room", labels.get(m.room) or "unnamed room"),
                        ("platform", platforms.get(m.room)),
                        ("age", age(m.row.ts, now)),
                        ("source_at", datetime.fromtimestamp(
                            _said(m).ts / 1000, PROFILE_HKT).isoformat()),
                    ),
                    body=lambda m: bodies[m.row.id],
                ),
            ),
            None
            if not mentions
            else ContextBlock(
                name="profile_mentions",
                **_at("profile_mentions"),
                body=render_row_window(
                    mentions,
                    row_id=lambda m: m.row.id,
                    attrs=lambda m: (
                        ("room", labels.get(m.room) or "unnamed room"),
                        ("platform", platforms.get(m.room)),
                        ("by", self._name_of(m.row.person_id)),
                        ("age", age(m.row.ts, now)),
                        ("source_at", datetime.fromtimestamp(
                            _said(m).ts / 1000, PROFILE_HKT).isoformat()),
                    ),
                    body=lambda m: bodies[m.row.id],
                ),
            ),
            None
            if not replies
            else ContextBlock(
                name="profile_episodes",
                **_at("profile_episodes"),
                body="\n".join(
                    self._episode(e, person, labels, bodies)
                    for e in episodes if e.send_id in replies
                ),
            ),
            # The block is built by `character._within_budget`, which stamps
            # it with CHARACTER's row (order 45); this prompt's table puts it
            # at 55, between the episodes and the ties.
            restamp(social_block, _ROW),
            None
            if not ties
            else ContextBlock(
                name="profile_ties",
                **_at("profile_ties"),
                body=render_elements(
                    ties,
                    tag="tie",
                    attrs=lambda t, _i: (
                        ("cite", tie_keys[t[0]]),
                        ("with", self._name_of(t[0])),
                        ("replies_to", str(t[1])),
                        ("replied_by", str(t[2])),
                        ("mentions", str(t[3])),
                        ("mentioned_by", str(t[4])),
                        ("reacts_to", str(t[5])),
                        ("reacted_by", str(t[6])),
                    ),
                    body=lambda _t: None,
                ),
            ),
        ]
        if selected:
            parts.append(ContextBlock(
                name="profile_source_revisions", **_at("profile_source_revisions"),
                body=render_elements(selected, tag="source", attrs=lambda r, _i: (
                    ("domain", r.domain), ("handle", r.source), ("state", r.state),
                    ("subject", self._name_of(r.subject) if r.subject else None),
                    ("revision", r.revision), ("recorded", r.recorded_at),
                    ("cite", r.citable),
                ), body=lambda r: json.dumps({
                    "roots": [str(x.row.id) for x in r.roots],
                    "effective_from": r.effective_from, "effective_until": r.effective_until,
                    "as_of": r.as_of, "computed": dict(r.properties), "links": r.links,
                    "cached_reading_present": r.cache_ready, "media_state": r.media_state,
                    "authority": ("context and links only; never cite this Register entry; "
                                  "open/cancelled is not an occurrence")
                    if r.domain == "register" else "Record or deterministic Rollup",
                }, ensure_ascii=False)),
            ))
        return self._checked_input(key, assemble([p for p in parts if p is not None], sep="\n\n"))

    def _checked_input(self, key: str, text: str) -> str:
        if self._coordinate_screen is not None and self._coordinate_screen(text):
            self.evidence.forget(key)
            raise AccountNotRecordableError("coordinate in Character input; source remains pending")
        return text

    def _episode(
        self, e: Episode, person: str, labels: Mapping[str, str], bodies: Mapping[int, str],
    ) -> str:
        """One `<episode>`: her send and their replies to it, each with its id."""
        rows = render_row_window(
            [(e.send_id, "you", e.text, None), *(
                (r.id, self._name_of(r.person_id), bodies.get(r.id, r.body or ""),
                 r.ts if shas_in(r.media_json) else None)
                for r in e.replies if r.person_id == person)],
            row_id=lambda m: m[0],
            attrs=lambda m: (("from", m[1]),) + (() if m[3] is None else (
                ("source_at", datetime.fromtimestamp(m[3] / 1000, PROFILE_HKT).isoformat()),
            )), body=lambda m: m[2],
        )
        return render_group("episode", [
            ("n", f"response:{e.send_id}"), ("room", labels.get(e.room) or "unnamed room"),
            ("outcome", e.outcome),
        ], rows)

    # -- the verbs ---------------------------------------------------------

    def verbs(self, *, key: str) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`profile_write` and `profile_no_change`, bound to this run. A write
        is judged at once against the lines the session was shown, so the
        model reads every refusal and can fix it; what passes is STAGED, and
        `commit` lands it, judged again inside the store's transaction (the
        account may have moved). The runtime screens every verb's arguments
        before it runs (`DreamerRuntime._screened`); the store screens again."""

        def _over() -> dict[str, object] | None:
            run = self._runs.get(key)
            if run is None:
                return {"status": "unavailable", "reason": "no_session",
                        "detail": "this session is over; nothing was written"}
            if run.staged is not None:
                return {"status": "refused", "reason": "settled",
                        "detail": "this session already settled the account; stop"}
            return None

        async def profile_write(ops: list[dict[str, Any]]) -> dict[str, object]:
            """Change this account when its evidence supports changes: submit
            every add, keep, revise and retire together.

            A refused write settles nothing.

            Returns `status`: ok, or refused with `refusals` (each a `reason`,
            a `detail` and the `op` index). Fix or drop the refused ops and
            call profile_write again. If nothing valid remains, call
            profile_no_change. Stop only after status: ok.

            Args:
                ops: The changes, each an object with `op`: add (part, kind,
                    text, mode, citations, and draws_on, told_by, prediction,
                    prediction_observable, prediction_window_end or
                    conflicts_with where they apply), keep (line, citations),
                    revise (line, citations, text, mode, draws_on,
                    conflicts_with) or retire (line, why). A line is named by
                    its `id` as shown, like P1.
            """
            over = _over()
            if over is not None:
                return over
            run = self._runs[key]
            named = [
                {**op, "told_by": run.tellers.get(str(op["told_by"]).strip().casefold(),
                                                  op["told_by"])}
                if isinstance(op, dict) and op.get("told_by") else op
                for op in ops
            ] if isinstance(ops, list) else ops
            if isinstance(named, list):
                for op in named:
                    if not isinstance(op, dict) or op.get("fact_key") != "relationship":
                        continue
                    value = op.get("fact_value")
                    if isinstance(value, dict) and isinstance(value.get("unresolved_name"), str):
                        pid = run.tellers.get(value["unresolved_name"].strip().casefold())
                        if pid is not None and any(k.unit == run.account.unit
                            and k.person.person_id == pid for k in self._keys.values()):
                            op["fact_value"] = {"relation": value.get("relation"), "person_id": pid}
            parsed, bad = parse_ops(named)
            if not bad:
                bad = plan_write(
                    account=run.account, heads=run.heads, ops=parsed, citable=run.citable,
                    now_ms=self._clock_ms(), written_by_session="dry-run",
                    screen=_screened_by_the_runtime, apply_decay=True,
                ).refusals
            if bad:
                self._count("refused")
                return {"status": "refused", "refusals": [
                    {"reason": r.reason, "detail": r.detail, "op": r.op_index} for r in bad]}
            run.staged = parsed
            return {"status": "ok"}

        async def profile_no_change(why: str) -> dict[str, object]:
            """Close this session with nothing added. Call this when what you
            were given adds nothing the account does not already say, or a
            refused write leaves no valid changes.

            Returns `status`: ok, or refused when the session already settled.

            Args:
                why: One short sentence: why nothing changes.
            """
            over = _over()
            if over is not None:
                return over
            self._runs[key].staged = ()
            log.info("profile: key=%s unchanged: %s", key, why[:200])
            return {"status": "ok"}

        run = self._runs.get(key)
        if run is not None:
            owner = {"human": "this person's", "room": "the room's",
                     "unit": "the unit's", "nora": "Nora's"}[run.account.entity_kind]
            profile_write.__doc__ = (profile_write.__doc__ or "").replace(
                "this account", f"{owner} account")
        return [profile_write, profile_no_change]

    # -- accept / commit ---------------------------------------------------

    async def accept(
        self,
        *,
        key: str,
        items: Sequence[Mapping[str, object]],
    ) -> Sequence[object]:
        """The staged write, or a refusal to spend the batch. Unsettled (no
        verb settled the session) RAISES `AccountNotRecordableError` (the
        watermark holds). No person-source batch is abandoned or consumed
        without a durable account settlement (45 §4a)."""
        run = self._runs.pop(key, None)
        if run is None or run.staged is None:
            log.warning("profile: key=%s ended without settling; HOLDING the watermark", key)
            self._count("unsettled")
            self.evidence.forget(key)
            raise AccountNotRecordableError(
                "person source input did not settle; retaining revisions")
        return (ProfileStaged(account=run.account, ops=run.staged, citable=run.citable,
                              revision=run.revision),)

    async def commit(
        self,
        *,
        key: str,
        proposals: Sequence[Proposal],
        now_ms: int,
    ) -> int:
        """Land the staged write: judged again in the store's transaction,
        against the revision the session read (a write landing meanwhile is a
        `conflict` and writes nothing; the next wake reads the new account),
        with the 60-day trait decay. Returns the rows it wrote."""
        written = 0
        for proposal in proposals:
            staged = proposal.payload
            if not isinstance(staged, ProfileStaged):
                raise TypeError("ProfilePersonAdaptor.commit takes its own write only")
            result = await self._profile.profile_write(
                account=staged.account,
                ops=staged.ops,
                citable=staged.citable,
                now_ms=now_ms,
                written_by_session=proposal.established_by,
                apply_decay=True,
                expected_revision=staged.revision,
            )
            if result.status != "ok":
                log.warning(
                    "profile: key=%s staged write not landed: %s %s",
                    key,
                    result.status,
                    [r.reason for r in result.refusals],
                )
                self._count("refused")
                raise AccountNotRecordableError("Profile commit failed; retaining evidence")
            written += len(result.written)
            self._count("written" if result.written else "kept")
        return written
