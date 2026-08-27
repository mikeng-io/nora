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
2. **Every read is workspace-scoped, and a cross-workspace `read` returns
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
    Note,
    NoteCreationProposal,
    NoteRevision,
    NoteRevisionProposal,
    NoteSupersedeReason,
    StaleNoteHeadError,
)
from nora.domain.ports import Proposal


@dataclass(frozen=True)
class NoteCandidate:
    """One row of the miss cache (§4.5), `note_candidate`'s shape in a
    dataclass. Frozen and replaced rather than mutated, so a caller holding an
    earlier read cannot watch it change underneath."""

    workspace: str
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
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    async def create(
        self,
        *,
        workspace: str,
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
        note_id = f"note-{next(self._ids)}"
        revision_id = f"rev-{next(self._ids)}"
        note = Note(
            id=note_id, workspace=workspace, title=title, description=description,
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

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        """Uncapped (§5.1) and ordered oldest-first, matching `SqlNoteStore`'s
        `ORDER BY created_at, id`. Dict insertion order would give the same
        answer for every test that inserts in time order, and a different one
        the first time a test does not — sorting explicitly is what makes the
        two implementations agree by construction rather than by coincidence."""
        live = [
            n for n in self.notes.values()
            if n.workspace == workspace and n.is_standing
        ]
        live.sort(key=lambda n: (n.created_at, n.id))
        return tuple(live)

    async def read(
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None:
        """The note and its HEAD revision, or `None` — including `None` for a
        note in another workspace (module docstring (2)). Retired notes stay
        readable: retirement is a status change, not a delete (§7.4)."""
        note = self.notes.get(note_id)
        if note is None or note.workspace != workspace:
            return None
        return note, self.revisions[note.head_revision_id]

    async def append_revision(
        self,
        *,
        workspace: str,
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
        workspace, matching `SqlNoteStore`: from the caller's side those are the
        same situation with the same remedy, and reporting the real head across
        a workspace boundary would confirm the note exists.
        """
        note = self.notes.get(note_id)
        if note is None or note.workspace != workspace:
            raise StaleNoteHeadError(
                f"note {note_id} is not in workspace {workspace!r} — "
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
            id=f"rev-{next(self._ids)}", note_id=note_id, body=body,
            parent_revision_id=parent_revision_id, written_at=now_ms,
            established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        self.revisions[revision.id] = revision
        self.notes[note_id] = replace(note, head_revision_id=revision.id)
        return revision

    async def search(
        self, *, workspace: str, query: str, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """Head bodies only, standing only, workspace-scoped — module docstring
        (4). The body comes from `self.revisions[note.head_revision_id]`, never
        from a scan of `self.revisions`, because a scan would reach superseded
        bodies and the whole point of the SQL store's join is that it cannot.

        A non-positive limit clamps to `()` rather than slicing: `list[:-1]`
        silently drops the last entry, which is a different, wrong answer to the
        same input (2026-08-21 review, `standing_facts`)."""
        if limit <= 0 or not query.strip():
            return ()
        hits: list[tuple[Note, str]] = []
        for note in await self.standing(workspace=workspace):
            body = self.revisions[note.head_revision_id].body
            if _like_matches(body, query):
                hits.append((note, body))
        return tuple(hits[:limit])

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
        """`SqlNoteStore.propose`'s behaviour, including its refusals."""
        ids: list[str] = []
        for p in proposals:
            if isinstance(p.payload, NoteCreationProposal):
                created = await self.create(
                    workspace=key,
                    title=p.payload.title,
                    description=p.payload.description,
                    uncertainty=p.payload.uncertainty,
                    closing_condition=p.payload.closing_condition,
                    established_in=p.payload.established_in,
                    body=p.payload.body,
                    established_by=p.established_by,
                    now_ms=now_ms,
                    anchor_date=p.payload.anchor_date,
                    anchor_entity_ids=p.payload.anchor_entity_ids,
                    instruction_sha256=p.instruction_sha256,
                    model=p.model,
                )
                ids.append(created.id)
                continue
            if not isinstance(p.payload, NoteRevisionProposal):
                raise TypeError(
                    f"InMemoryNoteStore.propose accepts a "
                    f"NoteRevisionProposal or a NoteCreationProposal, got "
                    f"{type(p.payload).__name__}"
                )
            revision = await self.append_revision(
                workspace=key,
                note_id=p.payload.note_id,
                body=p.payload.body,
                parent_revision_id=p.payload.parent_revision_id,
                established_by=p.established_by,
                now_ms=now_ms,
                instruction_sha256=p.instruction_sha256,
                model=p.model,
            )
            _ = revision
            ids.append(p.payload.note_id)
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

        **`source` is accepted and DROPPED, and that is not an oversight — it
        is what the SQL store does too.** `note` has no column for a
        supersession's citation: a note is an open question, and what closed it
        is the revision history, not a URL. The port carries `source` because
        the fact tier needs it (a contradicting source is the warrant for the
        supersession), and a tier that cannot record it says so through the
        contract suite's axis rather than by inventing a column.

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
        if note is None or note.workspace != key or note.retired_at is not None:
            return 0
        self.notes[target] = replace(
            note, retired_at=now_ms, replaced_by=replaced_by,
            retire_reason=because,
        )
        return 1

    async def record_miss(
        self, *, workspace: str, probe: str, conversation_id: str, now_ms: int,
    ) -> int:
        """The negative cache (§4.5). Upserts on `(workspace, probe)`, keeps
        `first_seen_at`, appends a conversation to `observed_in` only if it is
        not already there, and returns the new count — the same three decisions
        `SqlNoteStore.record_miss`'s docstring argues for."""
        key = (workspace, probe)
        existing = self.candidates.get(key)
        if existing is None:
            self.candidates[key] = NoteCandidate(
                workspace=workspace, probe=probe, first_seen_at=now_ms,
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
