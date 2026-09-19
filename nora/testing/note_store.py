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
against `SqlNoteStore` (drop `autoescape=True` there and it goes red, measured
2026-08-21) and is a consistency check here. An earlier draft of this module
used an escaped regex and a docstring claiming the escape was what the test
caught; it was not — `re.escape` + `re.search` and `in` are the same function of
the same inputs, so no test could tell them apart, and the claim was decoration
of exactly the kind CLAUDE.md's rule is about. The property the suite pins is
the SEMANTIC one both implementations owe — case-insensitive literal substring,
the caller's query is data — and the simpler expression of it is the one that
does not imply a mechanism it does not have.

`notes`, `revisions` and `candidates` are public attributes because the
contract suite needs to reach state the store's own surface cannot produce —
retiring a note (there is no `retire` verb; retirement arrives later as
`DreamSink.supersede`) and reading a `note_candidate` row back. That is the
`read_row`-per-implementation shape `tests/contract/test_dream_sink_contract.py`
already established, kept out of the production surface where it would be a
test seam.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from nora.domain.notes import (
    CANDIDATE_CREATED,
    CANDIDATE_PENDING,
    CANDIDATE_REFUSED,
    Note,
    NoteCreateCandidate,
    NoteCreationProposal,
    NoteRevision,
    NoteRevisionProposal,
    NoteSupersedeReason,
    StaleNoteHeadError,
    WakeOutcome,
    tag_path_progressed_since_wake,
)
from nora.domain.ports import Proposal


@dataclass(frozen=True)
class NoteCandidate:
    """One row of the miss cache (§4.5), `note_candidate`'s shape in a
    dataclass. Frozen and replaced rather than mutated, so a caller holding an
    earlier read cannot watch it change underneath."""

    unit: str
    probe: str
    first_seen_at: int
    last_seen_at: int
    occurrence_count: int
    observed_in: tuple[str, ...]


def _like_matches(body: str, query: str) -> bool:
    """What `body ilike '%' || escape(query) || '%'` means: case-insensitive
    literal substring, with no wildcard syntax in the caller's query. See the
    module docstring's asymmetry note for why this is `in` and not an escaped
    regex — the two are the same function, and pretending otherwise put a
    mechanism in a docstring that no test could hold to."""
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


@dataclass
class InMemoryNoteStore:
    """The note store over three plain dicts. `__init__` takes no arguments —
    there is no engine to inject — so a contract-suite fixture constructs this
    with zero setup, the same affordance `SqlNoteStore(engine)`'s docstring
    states for its own constructor."""

    notes: dict[str, Note] = field(default_factory=dict)
    revisions: dict[str, NoteRevision] = field(default_factory=dict)
    candidates: dict[tuple[str, str], NoteCandidate] = field(default_factory=dict)
    #: `kind='create'` rows, by id — the create path's own table (§4, issue
    #: #98), sharing NOTHING with `candidates` above beyond the table they
    #: both stand in for: that dict is the miss cache, keyed on
    #: `(unit, probe)`; this one has no probe at all and is looked up by
    #: its OWN staging key, `(unit, anchor_date, anchor_entity_ids,
    #: normalised title)` (#235), in
    #: `record_create_candidate` below — a linear scan rather than a second
    #: index, which is fine at this store's scale and keeps one dict from
    #: drifting out of step with the other.
    create_candidates: dict[str, NoteCreateCandidate] = field(default_factory=dict)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    #: **UUID-SHAPED, like the real store's.** This double minted `note-1` and
    #: `rev-2` until 2026-08-28, and that one divergence hid the defect that
    #: made the whole curator inert: the Dreamer screens every adaptor item
    #: against R-18/R-28 and `scan_identifiers` matches any hyphenated uuid, so
    #: production rejected every item while this double's ids sailed through.
    #: Eighteen tests passed over it.
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
        # `note_create` has no `Proposal` and no instruction digest; the
        # debrief, arriving through `DreamSink.propose`, has both. See
        # `0036_note_revision_provenance` for why the columns are nullable.
        instruction_sha256: str | None = None,
        model: str | None = None,
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
        return note

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        """Uncapped (§5.1) and ordered oldest-first, matching `SqlNoteStore`'s
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

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None:
        """The note and its HEAD revision, or `None` — including `None` for a
        note in another unit (module docstring (2)). Retired notes stay
        readable: retirement is a status change, not a delete (§7.4)."""
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
    ) -> NoteRevision:
        """§6.3's compare-and-swap — the refusal in the module docstring's (1).

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
        same input (2026-08-21 review, `standing_facts`)."""
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

    async def record_wake(
        self, *, unit: str, note_id: str, next_revisit_at: int,
        now_ms: int,
    ) -> WakeOutcome | None:
        """`SqlNoteStore.record_wake`.

        The head is read HERE and branched on here, matching the real store —
        a double that took the decision from its caller would agree on every
        happy path and diverge on the one case §9.6 exists for, a background
        pass's own write being mistaken for progress.
        """
        note = self.notes.get(note_id)
        if (
            note is None
            or note.unit != unit
            or note.retired_at is not None
        ):
            return None
        # THE SPAN SINCE THE WATERMARK, not the author of the head — the same
        # shared predicate the SQL store walks, for the reason it states: a
        # room revision followed by a background one is progress, and both
        # implementations used to answer «no» to it in agreement.
        progressed = tag_path_progressed_since_wake(
            head_revision_id=note.head_revision_id,
            last_wake_revision_id=note.last_wake_revision_id,
            chain={
                r.id: (r.parent_revision_id, r.established_by)
                for r in self.revisions.values()
                if r.note_id == note_id
            },
        )
        if progressed:
            self.notes[note_id] = replace(
                note, strikes=0, last_wake_revision_id=note.head_revision_id,
                revisit_at=next_revisit_at,
            )
            return WakeOutcome(strikes=0, reset=True)
        self.notes[note_id] = replace(note, strikes=note.strikes + 1)
        return WakeOutcome(strikes=note.strikes + 1, reset=False)

    async def anchored_before(
        self, *, unit: str, day: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """`SqlNoteStore.anchored_before`."""
        if limit <= 0:
            return ()
        hits = [
            n for n in await self.standing(unit=unit)
            if n.anchor_date is not None and n.anchor_date < day
        ]
        hits.sort(key=lambda n: (n.anchor_date or 0, n.id))
        return tuple(
            (n, self.revisions[n.head_revision_id].body) for n in hits[:limit]
        )

    async def due_for_revisit(
        self, *, unit: str, now_ms: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """`SqlNoteStore.due_for_revisit`: standing, `revisit_at` set and not
        in the future, oldest `revisit_at` first — NOT also "or anchor
        passed"; see the SQL store's own docstring for why that is the
        caller's union to make, not this read's job."""
        if limit <= 0:
            return ()
        hits = [
            n for n in await self.standing(unit=unit)
            if n.revisit_at is not None and n.revisit_at <= now_ms
        ]
        hits.sort(key=lambda n: (n.revisit_at or 0, n.id))
        return tuple(
            (n, self.revisions[n.head_revision_id].body) for n in hits[:limit]
        )

    # ---- DreamSink -------------------------------------------------------
    #
    # **This double must refuse what Postgres refuses, and it does not get that
    # for free.** A double whose only refusals come from the domain constructor
    # is more permissive than the store it stands in for the moment a caller
    # reaches around the type — which is CLAUDE.md's "a fake that answers every
    # request the same way", and a contract suite cannot see it unless a test
    # constructs the illegal state directly. So the checks below are written
    # HERE rather than relied upon from `NoteRevisionProposal.__post_init__`.

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> Sequence[str]:
        """`SqlNoteStore.propose`'s behaviour, including its refusals and its
        ORDER OF OPERATIONS.

        **RESOLVE THE WHOLE BATCH, THEN APPLY IT.** This double has no
        transaction, so it cannot roll back — what it CAN do is decide every
        refusal before performing any mutation, which is where the SQL store's
        atomicity is observable from outside.

        **This docstring claimed exactly that on 2026-08-27 while the code did
        not do it**, because only the `isinstance` loop had been hoisted:
        `append_revision`'s CAS and `create`'s domain refusals still fired
        inside the write loop, so a batch of (valid, stale) left the valid one
        applied where Postgres rolls it back. Found by a reviewer executing it,
        one fix after the same file's docstring was corrected for the same
        class of mistake. Recorded rather than quietly repaired: **a claim
        about a double is a claim about the real store**, and this one was
        wrong twice in the same file in one day.

        Resolving first also removes the SQL-only exemption the contract suite
        needed for the rollback assertion — and that exemption was the tell
        that the two implementations were never being compared on this.
        """
        typed: list[tuple[Proposal, NoteCreationProposal | NoteRevisionProposal]] = []
        for p in proposals:
            payload = p.payload
            if not isinstance(
                payload, NoteCreationProposal | NoteRevisionProposal
            ):
                raise TypeError(
                    f"InMemoryNoteStore.propose accepts a "
                    f"NoteRevisionProposal or a NoteCreationProposal, got "
                    f"{type(payload).__name__}"
                )
            typed.append((p, payload))

        # --- resolve: build every object and check every CAS, writing nothing
        #
        # `heads` simulates the batch's effect on each note's head, seeded from
        # the store as it stands. `declared` is what each note's FIRST proposal
        # claimed to be written against — the same distinction the SQL store
        # draws, and for the same reason: the tracked head must be a CHECK, not
        # a silent override, or two proposals parented on one read both land
        # and the second buries the first.
        pending: list[tuple[Note | None, NoteRevision]] = []  # (new note | None, revision)
        ids: list[str] = []
        heads: dict[str, str] = {}
        declared: dict[str, str] = {}
        for p, payload in typed:
            if isinstance(payload, NoteCreationProposal):
                note_id = self._shaped_id(next(self._ids), "note")
                revision_id = self._shaped_id(next(self._ids), "rev")
                note = Note(
                    id=note_id, unit=key, title=payload.title,
                    description=payload.description,
                    uncertainty=payload.uncertainty,
                    closing_condition=payload.closing_condition,
                    established_in=payload.established_in, created_at=now_ms,
                    head_revision_id=revision_id,
                    anchor_date=payload.anchor_date,
                    anchor_entity_ids=tuple(payload.anchor_entity_ids),
                    # `SqlNoteStore.propose`'s own `_new_note` sets this from
                    # the same field — the note-curator merge's §5.3
                    # resolution (c).
                    revisit_at=payload.revisit_at,
                )
                revision = NoteRevision(
                    id=revision_id, note_id=note_id, body=payload.body,
                    parent_revision_id=None, written_at=now_ms,
                    established_by=p.established_by,
                    instruction_sha256=p.instruction_sha256, model=p.model,
                )
                pending.append((note, revision))
                heads[note_id] = revision_id
                ids.append(note_id)
                continue

            if payload.note_id in declared:
                if payload.parent_revision_id != declared[payload.note_id]:
                    raise StaleNoteHeadError(
                        f"note {payload.note_id} was already revised in this "
                        f"batch against {declared[payload.note_id]}; this "
                        f"proposal names {payload.parent_revision_id} — read "
                        f"it again",
                        observed_head=heads.get(payload.note_id),
                    )
            else:
                declared[payload.note_id] = payload.parent_revision_id

            existing = self.notes.get(payload.note_id)
            reachable = existing is not None and existing.unit == key
            head_now = existing.head_revision_id if existing is not None else None
            # The CAS runs only for a note this batch has NOT already revised.
            # For a later proposal the declared-parent check above has already
            # established that it names the same parent the first one did, and
            # the chain below supplies the real predecessor — so re-running the
            # comparison against the STORED head would refuse every legitimate
            # chained write.
            first_touch = payload.note_id not in heads
            stale = first_touch and (
                not reachable
                or head_now != payload.parent_revision_id
            )
            if not reachable or stale:
                raise StaleNoteHeadError(
                    f"note {payload.note_id} changed under you or is not in "
                    f"unit {key!r} — read it again",
                    observed_head=head_now if reachable else None,
                )
            revision = NoteRevision(
                id=self._shaped_id(next(self._ids), "rev"), note_id=payload.note_id,
                body=payload.body,
                parent_revision_id=heads.get(
                    payload.note_id, payload.parent_revision_id,
                ),
                written_at=now_ms, established_by=p.established_by,
                instruction_sha256=p.instruction_sha256, model=p.model,
            )
            pending.append((None, revision))
            heads[payload.note_id] = revision.id
            ids.append(payload.note_id)

        # --- apply: nothing above this line mutated anything
        for created, revision in pending:
            if created is not None:
                self.notes[created.id] = created
            else:
                self.notes[revision.note_id] = replace(
                    self.notes[revision.note_id], head_revision_id=revision.id,
                )
            self.revisions[revision.id] = revision
        return tuple(ids)

    async def supersede(
        self,
        *,
        key: str,
        target: str,
        replaced_by: str | None,
        because: str,
        source: str | None,
        now_ms: int,
    ) -> int:
        """`SqlNoteStore.supersede`'s behaviour.

        **`source` is REFUSED, not accepted and dropped** — the same as
        `SqlNoteStore`. `note` has no column for a supersession's citation: a
        note is an open question, and what closed it is the revision history,
        not a URL. The port carries `source` because the fact tier needs it (a
        contradicting source IS the warrant there), and a tier that cannot
        record a field says so by refusing rather than by discarding.

        **This docstring said "accepted and DROPPED" until 2026-08-27**, twelve
        lines above code that raises, because it was the un-updated half of the
        fix that added the refusal. Recorded rather than quietly corrected: a
        double's docstring is what the next person reads to learn what the REAL
        store does, so a reader trusting it would have built against
        drop-semantics and been refused at runtime. Same family as the day's
        other findings — two things that must agree, one silently stale.

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
        self.notes[target] = replace(
            note, retired_at=now_ms, replaced_by=replaced_by,
            retire_reason=because,
        )
        return 1

    async def record_miss(
        self, *, unit: str, probe: str, conversation_id: str, now_ms: int,
    ) -> int:
        """The negative cache (§4.5). Upserts on `(unit, probe)`, keeps
        `first_seen_at`, appends a conversation to `observed_in` only if it is
        not already there, and returns the new count — the same three decisions
        `SqlNoteStore.record_miss`'s docstring argues for."""
        key = (unit, probe)
        existing = self.candidates.get(key)
        if existing is None:
            self.candidates[key] = NoteCandidate(
                unit=unit, probe=probe, first_seen_at=now_ms,
                last_seen_at=now_ms, occurrence_count=1,
                observed_in=(conversation_id,),
            )
            return 1
        observed = existing.observed_in
        if conversation_id not in observed:
            observed = (*observed, conversation_id)
        updated = replace(
            existing,
            last_seen_at=now_ms,
            occurrence_count=existing.occurrence_count + 1,
            observed_in=observed,
        )
        self.candidates[key] = updated
        return updated.occurrence_count

    # ---- the create path (§4, issue #98) -----------------------------------

    async def record_create_candidate(
        self,
        *,
        unit: str,
        title: str,
        description: str,
        body: str,
        closing_condition: str,
        anchor_date: int,
        anchor_entity_ids: Sequence[str],
        message_ids: Sequence[int],
        now_ms: int,
    ) -> NoteCreateCandidate:
        """`SqlNoteStore.record_create_candidate`'s behaviour: upserts on the
        staging key `(unit, anchor_date, anchor_entity_ids,
        normalised title)` (#235) — the entity list sorted and de-duplicated
        HERE, exactly as the SQL store normalises it before the row reaches its
        own unique index — and the update is a no-op once the row has left
        `pending`, matching the SQL upsert's `WHERE status = 'pending'` guard:
        a settled candidate is never relitigated by a later re-observation.

        **The title normalisation DIVERGES from the SQL store's, and the
        divergence is stated rather than papered over.** This scan uses
        `casefold()` (matching `note_handle`, the tier's own normal form); the
        index uses Postgres `lower(btrim(...))`, which does not fold `ß`→`ss`.
        The two agree on every case this corpus produces (English and
        Cantonese), so the contract suite must not fixture a title where they
        differ — a test that did would be asserting an equivalence neither
        store promises.

        `message_ids` is APPENDED to the existing row's own list, deduplicated
        only WITHIN this call's own argument — not merged with the existing
        array — mirroring the SQL store's `||` concatenation exactly, so the
        two implementations agree on the one property the contract suite can
        actually observe: old ids are never lost, and a repeat sighting may
        legitimately grow the array rather than staying capped at its
        distinct count. This attribution list is not the count either store's
        uniqueness protects (that is the anchor key), so an occasional
        duplicate in it costs nothing.
        """
        entities = tuple(sorted(set(anchor_entity_ids)))
        ids = tuple(dict.fromkeys(message_ids))
        existing = next(
            (c for c in self.create_candidates.values()
             if c.unit == unit and c.anchor_date == anchor_date
             and c.anchor_entity_ids == entities
             and c.title.strip().casefold() == title.strip().casefold()),
            None,
        )
        if existing is None:
            candidate = NoteCreateCandidate(
                id=self._shaped_id(next(self._ids), "cand"),
                unit=unit, title=title, description=description,
                body=body, closing_condition=closing_condition,
                anchor_date=anchor_date, anchor_entity_ids=entities,
                message_ids=ids, status=CANDIDATE_PENDING, attempts=0,
                first_seen_at=now_ms, last_seen_at=now_ms,
            )
            self.create_candidates[candidate.id] = candidate
            return candidate
        if existing.status != CANDIDATE_PENDING:
            # SETTLED: a re-observation of this occasion must not reopen a
            # decision the gate already made. Returned unchanged, exactly as
            # the SQL store's guarded upsert leaves the row untouched and
            # reads it back.
            return existing
        updated = replace(
            existing, title=title, description=description, body=body,
            closing_condition=closing_condition, last_seen_at=now_ms,
            attempts=existing.attempts + 1,
            message_ids=(*existing.message_ids, *ids),
        )
        self.create_candidates[existing.id] = updated
        return updated

    async def set_create_candidate_status(
        self, *, unit: str, candidate_id: str, status: str, now_ms: int,
    ) -> int:
        """`SqlNoteStore.set_create_candidate_status`'s behaviour: refuses a
        non-terminal status, and is a no-op — never a second decision — for a
        row that is gone, in another unit, or already terminal."""
        if status not in (CANDIDATE_CREATED, CANDIDATE_REFUSED):
            raise ValueError(
                f"InMemoryNoteStore.set_create_candidate_status got "
                f"status={status!r}; only a TERMINAL status may be set this "
                f"way — {(CANDIDATE_CREATED, CANDIDATE_REFUSED)}"
            )
        existing = self.create_candidates.get(candidate_id)
        if (
            existing is None
            or existing.unit != unit
            or existing.status != CANDIDATE_PENDING
        ):
            return 0
        self.create_candidates[candidate_id] = replace(
            existing, status=status, last_seen_at=now_ms,
        )
        return 1
