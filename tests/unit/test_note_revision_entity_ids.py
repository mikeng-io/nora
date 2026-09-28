"""Design 42, finding 4, confirmed rather than read (its S0): a revision
proposal's `entity_ids` is dropped on the way to the store.

`NoteRevisionProposal` carries `entity_ids`; `SqlNoteStore.propose` builds the
`NoteRevision` it writes without them, and `note_revision` has no column that
could hold them. 37 §5 Stage A's decision («code adds the involved
person_ids») therefore has nowhere to land; design 42 §11 G5 recommends a
`person_ids` column. This test pins today's behaviour so that change, when
it is made, has to change this test on purpose.
"""

from __future__ import annotations

import contextlib
import dataclasses
import uuid
from typing import Any

from nora.domain.notes import NoteRevision, NoteRevisionProposal
from nora.domain.ports import Proposal
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import NOTE_REVISION


class _Engine:
    """Only `begin()`: `propose` opens one transaction and hands its
    connection to `_append_on_conn`, which the test replaces."""

    @contextlib.asynccontextmanager
    async def begin(self) -> Any:
        yield object()


async def test_a_revision_proposals_entity_ids_are_not_persisted(monkeypatch):
    store = SqlNoteStore(_Engine())
    written: list[NoteRevision] = []

    async def _capture(conn: object, *, unit: str, revision: NoteRevision) -> None:
        written.append(revision)

    monkeypatch.setattr(store, "_append_on_conn", _capture)
    await store.propose(
        key="friends",
        proposals=[Proposal(
            payload=NoteRevisionProposal(
                note_id=str(uuid.uuid4()), body="a revised body",
                parent_revision_id=str(uuid.uuid4()),
                entity_ids=("p_ada", "p_ben"),
            ),
            instruction_sha256="0" * 64, model="m", established_by="note-candidates",
        )],
        now_ms=1,
    )

    [revision] = written
    assert not any(
        "p_ada" in repr(getattr(revision, f.name)) for f in dataclasses.fields(revision)
    )
    # And nothing downstream could keep them: the table's whole column set.
    assert {c.name for c in NOTE_REVISION.c} == {
        "id", "note_id", "body", "parent_revision_id", "written_at",
        "established_by", "instruction_sha256", "model",
        # system time (migration 0068): a timestamp, which cannot hold an id
        "recorded_at",
        # the derived-row columns (migration 0070): code writes `lineage` from
        # the body's cited message ids (rooms, never entities) and `as_of`
        # from their times; `reasoning`, `confidence` and `exclude_persons`
        # take no value from a proposal.
        "reasoning", "confidence", "lineage", "exclude_persons", "as_of",
    }
