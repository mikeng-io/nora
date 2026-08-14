from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.tool_call_log import (
    NaiveToolCallLogDatetimeError,
    ToolCallLogEntry,
    ensure_utc_log,
    normalise_tool_call_log_entry,
)


def _entry(**overrides):
    defaults = {
        "id": uuid4(), "conversation_id": "conv-1", "conversation_type": "group",
        "tag_id": "tag-1", "tool_name": "place_info",
        "tool_input": {"name": "Yoho Mall"},
        "tool_result": {"status": "ok", "rating": 4.2},
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ToolCallLogEntry(**defaults)


def test_a_tool_call_log_entry_constructs():
    entry = _entry()
    assert entry.tool_name == "place_info"
    assert entry.tool_result == {"status": "ok", "rating": 4.2}


def test_normalise_converts_non_utc_created_at_to_utc():
    from datetime import timedelta, timezone

    hkt = timezone(timedelta(hours=8))
    entry = _entry(created_at=datetime(2026, 8, 14, 20, 0, 0, tzinfo=hkt))
    normalised = normalise_tool_call_log_entry(entry)
    assert normalised.created_at.tzinfo == UTC
    assert normalised.created_at.hour == 12


def test_ensure_utc_log_refuses_a_naive_datetime():
    with pytest.raises(NaiveToolCallLogDatetimeError):
        ensure_utc_log(datetime(2026, 8, 14, 12, 0, 0), field="created_at")
