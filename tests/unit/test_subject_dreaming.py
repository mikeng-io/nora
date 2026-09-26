"""`nora.subject.dreaming.with_roster_senders` — the eval dream path's batch
carries sender ids the roster resolves, as production's rows do (design 42
S1a). Without it the sandbox's minted ids resolve nobody, note-candidates'
own roster projection is a no-op in every eval run, and a before/after on
S1a measures the old input on both arms."""

from __future__ import annotations

from dataclasses import replace

from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.persons import Persons
from nora.subject.dreaming import shown_speakers, with_roster_senders

ROSTER = Persons(
    by_person={
        "p_ada": PersonRef(person_id="p_ada", name="Ada Roster", honcho_peer_id="p_ada"),
        "p_ben": PersonRef(person_id="p_ben", name="Ben Roster", honcho_peer_id="p_ben"),
        "p_cat": PersonRef(person_id="p_cat", name="Cat Roster", honcho_peer_id="p_cat"),
    },
    index={
        ("signal", "+852111"): "p_ada", ("signal", "+852222"): "p_ben",
        ("discord", "42"): "p_ben",
    },
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


def test_a_minted_id_that_resolves_to_someone_else_is_replaced():
    """The sandbox's default sender id is a real roster id in some
    identities: a row of Ben's must never be named as Ada."""
    row = _row(1, sender_id="+852111", person_id="p_ben")
    assert ROSTER.name_transcript([row], platform="signal")[0].person_name == "Ada Roster"
    [fixed] = with_roster_senders([row], persons=ROSTER, platform="signal")
    assert fixed.sender_id == "+852222"
    assert ROSTER.name_transcript([fixed], platform="signal")[0].person_name == "Ben Roster"


def test_rows_production_could_not_resolve_either_are_left_alone():
    rows = [
        _row(1, sender_id="+8529001", person_id=None),             # nobody at ingest
        _row(2, sender_id="+8529002", person_id="p_cat"),          # no Signal id on file
        _row(3, sender_id="+852111", person_id="p_ada"),           # already resolves
        _row(4, sender_id="+8529004", person_id="p_ada", is_nora=True),
    ]
    assert with_roster_senders(rows, persons=ROSTER, platform="signal") == rows


def test_the_speakers_the_instrument_grades_are_the_names_the_model_was_shown():
    """S0's `body_names`/`body_schema` resolve a row to its shown `from`;
    after S1a that is the roster name, for a minted sandbox row too."""
    batch = with_roster_senders(
        [
            _row(1, sender_id="+8529000", person_id="p_ada"),
            _row(2, sender_id="+8529001", person_id=None),
        ],
        persons=ROSTER, platform="signal",
    )
    assert shown_speakers(batch, persons=ROSTER, platform="signal") == (
        (1, "Ada Roster"), (2, "profile2"),
    )


def test_body_refusals_are_read_off_production_s_own_counter_by_rule():
    """Design 42 S2: a refused body is never staged, so the eval reads the
    refusals off `nora_note_candidate_body_refused_total{rule}`, the counter
    production scrapes, and a label it does not know is not counted as one."""
    from types import SimpleNamespace

    from nora.obs.metrics import Metrics
    from nora.subject.dreaming import _body_refusals

    metrics = Metrics()
    subject = SimpleNamespace(deps=SimpleNamespace(metrics=metrics))
    assert _body_refusals(subject) == {}  # type: ignore[arg-type]
    metrics.inc("nora_note_candidate_body_refused_total", rule="uncited")
    metrics.inc("nora_note_candidate_body_refused_total", rule="uncited")
    metrics.inc("nora_note_candidate_body_refused_total", rule="bad_date")
    metrics.inc("nora_note_candidate_gate_total", action="created")
    assert _body_refusals(subject) == {"uncited": 2, "bad_date": 1}  # type: ignore[arg-type]


def test_batch_after_keeps_only_rows_after_the_watermark_instant():
    """design 42 plan item 5: a case can pin production's own batch window."""
    from nora.subject.dreaming import batch_after

    rows = [_row(i, sender_id="s", person_id=None) for i in (1, 2, 3)]
    # ts is 1_000 + id
    assert [r.id for r in batch_after(rows, 1_002)] == [3]
    assert [r.id for r in batch_after(rows, None)] == [1, 2, 3]
