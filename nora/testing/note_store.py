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
from dataclasses import dataclass, field, replace

from nora.domain.notes import Note, NoteRevision, StaleNoteHeadError


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
