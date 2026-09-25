"""`nora.subject.dreaming.with_roster_senders` — the eval dream path's batch
carries sender ids the roster resolves, as production's rows do (design 42
S1a). Without it the sandbox's minted ids resolve nobody, note-candidates'
own roster projection is a no-op in every eval run, and a before/after on
S1a measures the old input on both arms."""

from __future__ import annotations

from dataclasses import replace

from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.persons import Persons
from nora.subject.dreaming import with_roster_senders

ROSTER = Persons(
    by_person={
        "p_ada": PersonRef(person_id="p_ada", name="Ada Roster", honcho_peer_id="p_ada"),
        "p_ben": PersonRef(person_id="p_ben", name="Ben Roster", honcho_peer_id="p_ben"),
    },
    index={("signal", "+852111"): "p_ada", ("discord", "42"): "p_ben"},
)


def _row(
    row_id: int, *, sender_id: str, person_id: str | None, is_nora: bool = False,
) -> TranscriptRow:
    return TranscriptRow(
        id=row_id, sender_id=sender_id, sender_name=f"profile{row_id}", ts=1_000 + row_id,
        body="x", is_nora=is_nora, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id=person_id,
    )


def test_a_minted_sender_is_given_back_an_id_the_roster_resolves():
    minted = _row(1, sender_id="+8529000", person_id="p_ada")
    assert ROSTER.name_transcript([minted], platform="signal")[0].person_name is None

    [fixed] = with_roster_senders([minted], persons=ROSTER, platform="signal")

    assert fixed == replace(minted, sender_id="+852111")
    assert ROSTER.name_transcript([fixed], platform="signal")[0].person_name == "Ada Roster"


def test_rows_production_could_not_resolve_either_are_left_alone():
    rows = [
        _row(1, sender_id="+8529001", person_id=None),             # nobody at ingest
        _row(2, sender_id="+8529002", person_id="p_ben"),          # no Signal id on file
        _row(3, sender_id="+852111", person_id="p_ada"),           # already resolves
        _row(4, sender_id="+8529004", person_id="p_ada", is_nora=True),
    ]
    assert with_roster_senders(rows, persons=ROSTER, platform="signal") == rows
