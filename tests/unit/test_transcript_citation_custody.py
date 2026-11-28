"""The rows a tool result reports as shown carry the body the model was shown."""

from datetime import UTC, datetime

from nora.agent.toolkit.transcript import _transcript_messages
from nora.domain.models import DeliveryStatus, TranscriptRow

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
URL = f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0"


def test_the_shown_rows_hold_the_rendered_body_never_the_raw_file_link():
    row = TranscriptRow(
        id=7, sender_id="+852", sender_name="A", ts=1_000, body=f"budget {URL} pls",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    shown = []
    _transcript_messages(
        [row], now=datetime(2026, 10, 10, tzinfo=UTC), display_name="Nora", on_rows=shown.extend,
    )
    assert shown and ID not in shown[0].body
