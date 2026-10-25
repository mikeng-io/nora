"""The default prefill remains byte-identical to the pre-rename capture."""
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from nora.agent.prefill import build_prefill
from nora.domain.culture import CultureRevision
from nora.domain.models import NormalizedMessage, PersonRef, TagContext, TranscriptRow

NOW = datetime(2026, 10, 25, tzinfo=UTC)
TS = int(NOW.timestamp() * 1000)


def render_default_prefill():
    person = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
    message = NormalizedMessage(
        platform="signal", conversation_id="synthetic-room", conversation_type="group",
        sender_id="synthetic-sender", sender_name="Ada", timestamp=TS,
        body="Nora, what do you know?", raw={},
    )
    def prose(body):
        return CultureRevision(id="synthetic-revision", culture_key="synthetic-room",
                               body=body, written_at=TS - 3600000,
                               established_by="synthetic")
    ctx = TagContext(
        tag_id="synthetic-tag", message=message, tagger=person,
        transcript=(TranscriptRow(id=1, ts=TS - 300000, sender_id="synthetic-sender",
                                  sender_name="Ada", person_id="ada", person_name="Ada",
                                  body="We read on Sundays.", is_nora=False,
                                  delivery_status="sent", raw_json={}),),
        peer_card=("Ada likes books.",), started_at=0, room_members=(person,),
    )
    ctx = replace(ctx, culture=prose("A quiet reading room."),
                  unit_culture=prose("A group of readers."),
                  character=(("Ada", prose("Ada plans the reading list.")),),
                  self_account=prose("She answers briefly."))
    return build_prefill(ctx, display_name="Nora", context_window=10, now=NOW)


def test_default_prefill_is_byte_identical_before_and_after_rename():
    baseline = Path(__file__).parents[1] / "fixtures/profile-vocabulary/prefill-before.json"
    assert render_default_prefill() == json.loads(baseline.read_text())
