"""`InMemoryNoteStore` — the note tier's testing double, domain-only.
`nora.testing` may not import `nora.infra` (`lint-imports`' layered contract
puts them side by side), so this module builds everything from
`nora.domain.notes` alone; nothing here talks to SQL.

**This is a SECOND implementation of the note store, not a fake shaped to say
yes.** CLAUDE.md's rule — "never write a test whose fake answers every request
the same way… if reverting the code under test doesn't turn the test red, the
test is decorative" — is the whole reason this file makes the same four
refusals `nora.infra.sql.notes.SqlNoteStore` makes, and it commits to them here
so a reader can check the promise without running the suite:

1. **`append_revision` is a compare-and-swap and it REFUSES a stale parent.**
   If `parent_revision_id` is not the note's current `head_revision_id`, this
   raises `StaleNoteHeadError` and writes nothing — no revision appended, no
   head moved. A fake that accepted the write would make
   `tests/contract/test_note_store_contract.py` green against an
   implementation that has the defect the CAS exists to prevent, which is worse
   than having no fake at all: it converts a missing guarantee into a passing
   gate.
2. **Every read is unit-scoped, and a cross-unit `read` returns
   `None`** — indistinguishable from a note that does not exist, because being
   able to tell the two apart is itself the disclosure.
3. **`create` is atomic**, which in a dict means the note and its first
   revision are inserted together and the note's `head_revision_id` always
   names a revision this store holds. `Note`/`NoteRevision` are constructed
   before either insert, so a note the domain type refuses (no anchor, an
   over-long index entry) leaves NOTHING behind — the same outcome the SQL
   store gets from one transaction.
4. **`search` reads head bodies only**, by looking up `head_revision_id` rather
   than scanning `self.revisions`. Scanning would find superseded bodies, which
   is exactly the "true-in-the-past returned as current" error the SQL store's
   join is written to avoid; a fake that got the right answer by a different
   route would stop testing that the route matters.

**One honest asymmetry, recorded rather than papered over.** `search` here is
`query.lower() in body.lower()`, and the contract suite's wildcard test — a
query of `%` must return nothing — cannot fail against it, because Python's
`in` has no wildcards to escape in the first place. That test is load-bearing
against `SqlNoteStore` (drop `autoescape=True` there and it goes red) and is a
consistency check here. `re.escape` + `re.search` and `in` are the same function
of the same inputs, so no test could tell them apart. The property the suite
pins is the SEMANTIC one both implementations owe — case-insensitive literal
substring, the caller's query is data — and the simpler expression of it is the
one that does not imply a mechanism it does not have.

`notes` and `revisions` are public attributes because the contract suite
needs to reach state the store's own surface cannot produce. That is the
`read_row`-per-implementation shape `tests/contract/test_dream_sink_contract.py`
also uses, kept out of the production surface where it would be a
test seam.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from nora.domain.notes import (
    MERGE_FORWARD_HOPS,
    Note,
    NoteRevision,
    NoteSupersedeReason,
    RetiredNoteError,
    StaleNoteHeadError,
    note_handle,
    proactive_row_forwarded_is_moved,
    tag_path_progressed_since_wake,
)
from nora.domain.reminders import (
    CANCELLED_DUPLICATE_NOTE,
    CANCELLED_NOTE_MOVED,
    OPEN_STATES,
    PROACTIVE_KIND,
    Reminder,
    ReminderState,
)
from nora.testing.reminders import FakeReminderStore


def _like_matches(body: str, query: str) -> bool:
    """What `body ilike '%' || escape(query) || '%'` means: case-insensitive
    literal substring, with no wildcard syntax in the caller's query. See the
    module docstring's asymmetry note for why this is `in` and not an escaped
    regex — the two are the same function."""
    return query.lower() in body.lower()


def _looks_like_an_id(value: str) -> bool:
    """What this double calls malformed, and it is NOT what Postgres calls it.

    `SqlNoteStore` refuses a `replaced_by` that is not a UUID, because the
    column is `pg.UUID`. This store's ids are a counter, so it cannot apply that
    rule without making every one of its own ids illegal. It refuses the cases
    it CAN — empty, blank — and the contract suite must not read the two as
    identical: a double that claims a refusal it does not implement is worse
    than one that states the gap.
    """
    return bool(value and value.strip())


def _refuse_if_retired(note: Note) -> None:
    """Guard 1, as `SqlNoteStore._append_on_conn` enforces
    it: an append requires an open note."""
    if note.retired_at is not None:
        raise RetiredNoteError(
            f"note {note.id} was retired ({note.retire_reason}) — read the "
            f"notebook again",
            observed_head=note.head_revision_id,
            retire_reason=note.retire_reason,
            replaced_by=note.replaced_by,
        )


def _open_proactive_row(reminders: FakeReminderStore, note_id: str) -> Reminder | None:
    """The one OPEN (`pending`/`firing`) proactive row naming `note_id`, or
    `None` — `nora.infra.sql.notes._repoint_proactive_rows`'s `SELECT ...
    FOR UPDATE` read, over `FakeReminderStore._rows` instead of a table.
    At most one exists: `FakeReminderStore.create` enforces the same
    `open_proactive_note` uniqueness `uq_reminders_proactive_open_note`
    enforces in Postgres."""
    for r in reminders._rows.values():
        if (
            r.kind == PROACTIVE_KIND and r.state.value in OPEN_STATES
            and (r.payload or {}).get("note_id") == note_id
        ):
            return r
    return None


def _keep_open_reminder(a: Reminder, b: Reminder) -> tuple[Reminder, Reminder]:
    """`nora.infra.sql.notes._keep_open_row`'s twin, over `Reminder` objects
    instead of a `Row`. Same rule, same tie-break, same firing exception —
    see that function's docstring."""
    a_firing = a.state is ReminderState.FIRING
    b_firing = b.state is ReminderState.FIRING
    if a_firing != b_firing:
        return (a, b) if a_firing else (b, a)
    key = (a.due_at, a.created_at, a.id), (b.due_at, b.created_at, b.id)
    return (a, b) if key[0] <= key[1] else (b, a)


@dataclass
class InMemoryNoteStore:
    """The note store over three plain dicts. `__init__` takes no arguments —
    there is no engine to inject — so a contract-suite fixture constructs this
    with zero setup, the same affordance `SqlNoteStore(engine)`'s docstring
    states for its own constructor."""

    notes: dict[str, Note] = field(default_factory=dict)
    revisions: dict[str, NoteRevision] = field(default_factory=dict)
    #: Optional — `None` is the default (`InMemoryNoteStore()`), and `merge_into`
    #: then skips the reminders step
    #: entirely (module docstring: this double CANNOT know a duplicate held
    #: a proactive row unless something hands it the store that would know).
    #: A caller that wants the fold's reminders half modelled constructs
    #: `InMemoryNoteStore(reminders=a_fake_reminder_store)` and passes that
    #: SAME instance wherever it also builds a `ProactiveScreen`/`remind`
    #: fixture, so both sides observe one shared table — exactly what two
    #: real stores over one Postgres database give for free.
    reminders: FakeReminderStore | None = None
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    #: **UUID-SHAPED, like the real store's.** The Dreamer screens every adaptor
    #: item against R-18/R-28 and `scan_identifiers` matches any hyphenated uuid,
    #: so a counter-shaped id (`note-1`, `rev-2`) would sail through this double
    #: while production rejects every item.
    #:
    #: Deterministic rather than random — `uuid4` would make a failure
    #: unreproducible — but SHAPED like the thing it stands in for, because the
    #: shape is what the screen reads. A double may be simpler than the real
    #: store; it may not differ on a property something downstream branches on.
    @staticmethod
    def _shaped_id(n: int, kind: str) -> str:
        return f"{n:08x}-0000-4000-8000-{ord(kind[0]):012x}"

    async def create(
        self,
        *,
        unit: str,
        title: str,
        description: str,
        uncertainty: str,
        closing_condition: str,
        established_in: str,
        body: str,
        established_by: str,
        now_ms: int,
        anchor_date: int | None = None,
        anchor_entity_ids: tuple[str, ...] = (),
        # Passed through to the FIRST revision, and `None` from the tag path.
        # `note_create` has no instruction digest; the Cognition session's
        # writes may. See
        # `0036_note_revision_provenance` for why the columns are nullable.
        instruction_sha256: str | None = None,
        model: str | None = None,
        receipt: Any = None,
    ) -> Note:
        """See the module docstring's (3): both objects are built before either
        is stored, so a refused note leaves no half-written revision behind."""
        note_id = self._shaped_id(next(self._ids), "note")
        revision_id = self._shaped_id(next(self._ids), "rev")
        note = Note(
            id=note_id, unit=unit, title=title, description=description,
            uncertainty=uncertainty, closing_condition=closing_condition,
            established_in=established_in, created_at=now_ms,
            head_revision_id=revision_id, anchor_date=anchor_date,
            anchor_entity_ids=tuple(anchor_entity_ids),
        )
        revision = NoteRevision(
            id=revision_id, note_id=note_id, body=body, parent_revision_id=None,
            written_at=now_ms, established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        self.notes[note_id] = note
        self.revisions[revision_id] = revision
        if receipt is not None:
            await receipt(None, note)
        return note

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        """Uncapped and ordered oldest-first, matching `SqlNoteStore`'s
        `ORDER BY created_at, id`. Dict insertion order would give the same
        answer for every test that inserts in time order, and a different one
        the first time a test does not — sorting explicitly is what makes the
        two implementations agree by construction rather than by coincidence."""
        live = [
            n for n in self.notes.values()
            if n.unit == unit and n.is_standing
        ]
        live.sort(key=lambda n: (n.created_at, n.id))
        return tuple(live)

    async def successor(self, *, unit: str, note_id: str) -> Note | None:
        """`SqlNoteStore.successor`: the open note a `retired-merged` chain
        ends on, or `None`."""
        current = note_id
        for _ in range(MERGE_FORWARD_HOPS):
            note = self.notes.get(current)
            if note is None or note.unit != unit:
                return None
            if note.retired_at is None:
                return note if current != note_id else None
            if (note.retire_reason != NoteSupersedeReason.RETIRED_MERGED
                    or note.replaced_by is None):
                return None
            current = note.replaced_by
        return None

    async def merged_successor_by_title(
        self, *, unit: str, title: str,
    ) -> Note | None:
        """`SqlNoteStore.merged_successor_by_title`: exactly one open note the
        merged notes of this title forward to, or `None`."""
        wanted = note_handle(title)
        found: dict[str, Note] = {}
        for note in list(self.notes.values()):
            if (note.unit != unit
                    or note.retire_reason != NoteSupersedeReason.RETIRED_MERGED
                    or note_handle(note.title) != wanted):
                continue
            survivor = await self.successor(unit=unit, note_id=note.id)
            if survivor is not None:
                found[survivor.id] = survivor
        return next(iter(found.values())) if len(found) == 1 else None

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None:
        """The note and its HEAD revision, or `None` — including `None` for a
        note in another unit (module docstring (2)). Retired notes stay
        readable: retirement is a status change, not a delete."""
        note = self.notes.get(note_id)
        if note is None or note.unit != unit:
            return None
        return note, self.revisions[note.head_revision_id]

    async def append_revision(
        self,
        *,
        unit: str,
        note_id: str,
        body: str,
        parent_revision_id: str,
        established_by: str,
        now_ms: int,
        instruction_sha256: str | None = None,
        model: str | None = None,
        evidence_room: str | None = None,
        receipt: Any = None,
    ) -> NoteRevision:
        """The compare-and-swap — the refusal in the module docstring's (1).

        `evidence_room` is accepted and not kept: lineage is computed from the
        `messages` a body cites, which this fake does not hold, so its notes
        carry `Lineage()` (every section unknown, origin room only). Lineage is
        `SqlNoteStore`'s, tested against Postgres
        (`tests/infra/test_lineage_store.py`).

        `observed_head` is `None` when the note is absent OR in another
        unit, matching `SqlNoteStore`: from the caller's side those are the
        same situation with the same remedy, and reporting the real head across
        a unit boundary would confirm the note exists.
        """
        note = self.notes.get(note_id)
        if note is None or note.unit != unit:
            raise StaleNoteHeadError(
                f"note {note_id} is not in unit {unit!r} — "
                f"read it again",
                observed_head=None,
            )
        _refuse_if_retired(note)
        if note.head_revision_id != parent_revision_id:
            raise StaleNoteHeadError(
                f"note {note_id} changed under you: this revision was written "
                f"against {parent_revision_id}, the head is "
                f"{note.head_revision_id} — read it again",
                observed_head=note.head_revision_id,
            )
        revision = NoteRevision(
            id=self._shaped_id(next(self._ids), "rev"), note_id=note_id, body=body,
            parent_revision_id=parent_revision_id, written_at=now_ms,
            established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        self.revisions[revision.id] = revision
        self.notes[note_id] = replace(note, head_revision_id=revision.id)
        if receipt is not None:
            await receipt(None, revision)
        return revision

    async def search(
        self, *, unit: str, query: str, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """Head bodies only, standing only, unit-scoped — module docstring
        (4). The body comes from `self.revisions[note.head_revision_id]`, never
        from a scan of `self.revisions`, because a scan would reach superseded
        bodies and the whole point of the SQL store's join is that it cannot.

        A non-positive limit clamps to `()` rather than slicing: `list[:-1]`
        silently drops the last entry, which is a different, wrong answer to the
        same input."""
        if limit <= 0 or not query.strip():
            return ()
        hits: list[tuple[Note, str]] = []
        for note in await self.standing(unit=unit):
            body = self.revisions[note.head_revision_id].body
            if _like_matches(body, query):
                hits.append((note, body))
        return tuple(hits[:limit])

    async def revision_chain(
        self, *, unit: str, note_id: str,
    ) -> tuple[NoteRevision, ...]:
        """`SqlNoteStore.revision_chain`: every revision of one note, oldest
        first (`written_at`, then `id`); `()` when the note is not reachable
        from `unit`."""
        note = self.notes.get(note_id)
        if note is None or note.unit != unit:
            return ()
        chain = [r for r in self.revisions.values() if r.note_id == note_id]
        chain.sort(key=lambda r: (r.written_at, r.id))
        return tuple(chain)

    # ---- retirement ------------------------------------------------------

    async def supersede(
        self,
        *,
        key: str,
        target: str,
        replaced_by: str | None,
        because: str,
        source: str | None,
        now_ms: int,
        expected_head: str | None = None,
    ) -> int:
        """`SqlNoteStore.supersede`'s behaviour, including guard 3's
        `expected_head`: a head that moved answers 0.

        **`source` is REFUSED, not accepted and dropped** — the same as
        `SqlNoteStore`. `note` has no column for a supersession's citation: a
        note is an open question, and what closed it is the revision history,
        not a URL. The port carries `source` because the fact tier needs it (a
        contradicting source IS the warrant there), and a tier that cannot
        record a field says so by refusing rather than by discarding.

        The refusals below are deliberately NOT delegated to the domain type:
        a double that refuses only because a constructor refused is more
        permissive than the real store for any caller that bypasses the
        constructor.
        """
        if source is not None:
            # REFUSED, not accepted-and-dropped, and the shared suite has a
            # test for exactly this distinction. `note` has no column for a
            # supersession's citation because what closed an open question is
            # its revision history, not a URL — so a caller passing one is
            # asking for something this tier cannot do, and answering `ok`
            # while discarding it is `unavailable` wearing `ok`'s clothes at
            # the storage boundary. A tier that cannot record a field says so.
            raise ValueError(
                f"InMemoryNoteStore.supersede cannot record a supersession source "
                f"({source!r}): `note` has no column for one. What closed a "
                f"note is its revision history, not a citation — the fact tier "
                f"needs `source` because a contradicting source IS the warrant "
                f"there, and the port carries it for that tier, not this one."
            )
        if because not in NoteSupersedeReason.ALL:
            raise ValueError(
                f"InMemoryNoteStore.supersede got because={because!r}; the "
                f"note tier's vocabulary is {NoteSupersedeReason.ALL}"
            )
        if replaced_by is not None and not _looks_like_an_id(replaced_by):
            raise ValueError(
                f"InMemoryNoteStore.supersede got a malformed replaced_by "
                f"{replaced_by!r}. A caller bug, not an absent row."
            )
        note = self.notes.get(target)
        if note is None or note.unit != key or note.retired_at is not None:
            return 0
        if expected_head is not None and note.head_revision_id != expected_head:
            return 0
        self.notes[target] = replace(
            note, retired_at=now_ms, replaced_by=replaced_by,
            retire_reason=because,
        )
        return 1

    async def merge_into(
        self,
        *,
        unit: str,
        survivor_id: str,
        survivor_head: str,
        duplicate_id: str,
        duplicate_head: str,
        body: str,
        anchor_date: int | None,
        revisit_at: int,
        reset_strikes: bool,
        established_by: str,
        now_ms: int,
        instruction_sha256: str | None = None,
        model: str | None = None,
        on_reminder_outcome: Callable[[str], None] | None = None,
    ) -> NoteRevision:
        """`SqlNoteStore.merge_into`: both compare-and-swaps are decided
        BEFORE either mutation, which is where the SQL transaction's
        all-or-nothing is observable from outside.

        **Models the reminders half too, when
        `self.reminders` is wired.** `SqlNoteStore.merge_into` re-points or
        cancels either note's open proactive reminder inside the same
        transaction as the fold, because two notes that each held one would
        otherwise both forward to the survivor and both fire — see that
        method's docstring, `_repoint_proactive_rows`'s, and
        `_repoint_row`'s for the exact rules (both tie-breaks, the
        `proactive_row_forwarded_is_moved` classification, the
        firing-collision exemption). `self.reminders` defaults to `None`,
        and `None` is a real, named gap rather than papered over: the
        property is pinned for real against `self.reminders` only.
        **Not** through `tests/contract/test_note_store_contract.py`'s dual
        parametrization — that harness never wires a `reminders` collaborator
        into either store, so it cannot see this behaviour on either side.
        `tests/infra/test_note_merge_repoints_reminders.py` pins the SQL
        half against real Postgres; `tests/unit/
        test_note_merge_repoints_reminders_fake.py` pins THIS store's own
        logic directly — two files, not two arms of one suite, because
        nothing here runs them as one."""
        if survivor_id == duplicate_id:
            raise ValueError("a note cannot be folded into itself")
        revision = NoteRevision(
            id=self._shaped_id(next(self._ids), "rev"), note_id=survivor_id,
            body=body, parent_revision_id=survivor_head, written_at=now_ms,
            established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        for note_id, head in (
            (survivor_id, survivor_head), (duplicate_id, duplicate_head),
        ):
            note = self.notes.get(note_id)
            if (
                note is None or note.unit != unit
                or note.retired_at is not None
                or note.head_revision_id != head
            ):
                raise StaleNoteHeadError(
                    f"note {note_id} moved or retired before the fold — read "
                    f"it again",
                    observed_head=None,
                )
        survivor = self.notes[survivor_id]
        self.revisions[revision.id] = revision
        self.notes[survivor_id] = replace(
            survivor, head_revision_id=revision.id, anchor_date=anchor_date,
            revisit_at=revisit_at,
            strikes=0 if reset_strikes else survivor.strikes,
        )
        self.notes[duplicate_id] = replace(
            self.notes[duplicate_id], retired_at=now_ms,
            replaced_by=survivor_id,
            retire_reason=NoteSupersedeReason.RETIRED_MERGED,
        )
        reminders = self.reminders
        if reminders is not None:
            self._repoint_proactive_rows(
                reminders, survivor_id=survivor_id, duplicate_id=duplicate_id,
                duplicate_head=duplicate_head, revision_id=revision.id,
                now_ms=now_ms, on_reminder_outcome=on_reminder_outcome,
            )
        return revision

    def _chain_by_id(self, note_id: str) -> dict[str, tuple[str | None, str]]:
        """One note's whole chain as `tag_path_progressed_since_wake`/
        `proactive_row_forwarded_is_moved` want it."""
        return {
            r.id: (r.parent_revision_id, r.established_by)
            for r in self.revisions.values() if r.note_id == note_id
        }

    def _chain_pairs(self, note_id: str) -> list[tuple[int, str]]:
        """One note's whole chain as `proactive_row_forwarded_is_moved`'s
        `survivor_chain` wants it — `(written_at, established_by)` pairs."""
        return [
            (r.written_at, r.established_by)
            for r in self.revisions.values() if r.note_id == note_id
        ]

    def _repoint_proactive_rows(
        self, reminders: FakeReminderStore, *, survivor_id: str,
        duplicate_id: str, duplicate_head: str, revision_id: str,
        now_ms: int, on_reminder_outcome: Callable[[str], None] | None,
    ) -> None:
        """`SqlNoteStore._repoint_proactive_rows`'s twin over
        `reminders._rows` — same order, same `proactive_row_forwarded_
        is_moved` call, same outcome vocabulary. No `FOR UPDATE`/
        `IntegrityError` translation: nothing here awaits between a read
        and its matching write (the whole method is synchronous), so there
        is no window for a concurrent insert to land in the way
        `SqlNoteStore`'s does. `reminders` is a parameter, not read back off
        `self` — the caller (`merge_into`) already narrowed `self.reminders
        is not None`, and re-reading `self.reminders` here would need a
        second `None` check `mypy`/`ruff`'s `S101` (no bare `assert`
        outside `tests/`) both refuse to let this module skip."""
        d_row = _open_proactive_row(reminders, duplicate_id)
        if d_row is None:
            return
        fired_at = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
        if d_row.state is not ReminderState.FIRING:
            revision_id_linked = (d_row.payload or {}).get("revision_id")
            moved = proactive_row_forwarded_is_moved(
                duplicate_head=duplicate_head,
                duplicate_chain=self._chain_by_id(duplicate_id),
                revision_id=revision_id_linked,
                created_at_ms=int(d_row.created_at.timestamp() * 1000),
                survivor_chain=self._chain_pairs(survivor_id),
            )
            if moved:
                reminders._put(replace(
                    d_row, state=ReminderState.CANCELLED,
                    outcome=CANCELLED_NOTE_MOVED, fired_at=fired_at,
                ))
                if on_reminder_outcome is not None:
                    on_reminder_outcome("moved")
                return
        s_row = _open_proactive_row(reminders, survivor_id)
        if s_row is not None and s_row.state is not ReminderState.FIRING and (
            tag_path_progressed_since_wake(
                head_revision_id=revision_id,
                last_wake_revision_id=(s_row.payload or {}).get("revision_id"),
                chain=self._chain_by_id(survivor_id),
            )
        ):
            # The SURVIVOR's own row is stale — the ordinary fire-time walk
            # on a live note would cancel it `moved`. Classify it before the
            # keeper is chosen, or a stale survivor row can win the
            # tie-break over a live duplicate row.
            reminders._put(replace(
                s_row, state=ReminderState.CANCELLED,
                outcome=CANCELLED_NOTE_MOVED, fired_at=fired_at,
            ))
            if on_reminder_outcome is not None:
                on_reminder_outcome("moved")
            s_row = None
        if s_row is None:
            self._repoint_reminder(
                reminders, d_row, survivor_id=survivor_id,
                revision_id=revision_id, on_reminder_outcome=on_reminder_outcome,
                outcome="repointed",
            )
            return
        keep, drop = _keep_open_reminder(d_row, s_row)
        if drop.state is ReminderState.FIRING:
            # Both firing (the only way `_keep_open_reminder` hands back a
            # firing `drop`, since a live-or-firing `d_row` never returned
            # above): BOTH rows are left alone, keeper included —
            # `SqlNoteStore._repoint_proactive_rows`'s twin rule. Re-pointing
            # `keep` here would model a note holding two open reminders at
            # once, which is exactly the shape `uq_reminders_proactive_open_
            # note` refuses in Postgres; this fake would not catch that on
            # its own (a dict has no unique index), so the rule has to be
            # enforced here explicitly rather than left to raise.
            if on_reminder_outcome is not None:
                on_reminder_outcome("skipped_firing_collision")
            return
        reminders._put(replace(
            drop, state=ReminderState.CANCELLED,
            outcome=CANCELLED_DUPLICATE_NOTE, fired_at=fired_at,
        ))
        if keep.id == d_row.id:
            self._repoint_reminder(
                reminders, keep, survivor_id=survivor_id,
                revision_id=revision_id, on_reminder_outcome=on_reminder_outcome,
                outcome="collision_repointed",
            )
        elif on_reminder_outcome is not None:
            on_reminder_outcome("collision_kept_survivor")

    def _repoint_reminder(
        self, reminders: FakeReminderStore, row: Reminder, *,
        survivor_id: str, revision_id: str,
        on_reminder_outcome: Callable[[str], None] | None, outcome: str,
    ) -> None:
        """`SqlNoteStore._repoint_row`'s twin: the re-point alone — `row`
        has already been classified `live` (or is `firing`, never
        classified) by the caller. Moves BOTH `note_id` and `revision_id`;
        see `SqlNoteStore._repoint_stmt`'s docstring for why `revision_id`
        has to move too."""
        payload = dict(row.payload or {})
        payload["note_id"] = survivor_id
        payload["revision_id"] = revision_id
        reminders._put(replace(row, payload=payload))
        if on_reminder_outcome is not None:
            on_reminder_outcome(outcome)

    # ---- the create path -----------------------------------------------------
