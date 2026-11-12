"""The report states what the subject could reach before any verdict."""

from types import SimpleNamespace

from scripts.cognition_loop.availability import availability


def test_availability_reads_switches_offered_tools_and_closed_calls():
    cfg = SimpleNamespace(memory=SimpleNamespace(
        index=SimpleNamespace(read=False), profile=SimpleNamespace(read=True, write=True)))
    trajectory = {
        "model_calls": [{"tools": [{"function": {"name": "note_create"}},
                                   {"name": "profile_read"}]}],
        "tool_calls": [
            {"tool": "profile_read", "status": "unavailable", "reason": "roster"},
            {"tool": "note_create", "status": "ok"},
        ],
    }
    got = availability(cfg, trajectory)
    assert got["switches"]["memory.index.read"] is False
    assert got["switches"]["memory.profile.read"] is True
    assert got["switches"]["lessons.enabled"] is None
    assert got["offered"] == ["note_create", "profile_read"]
    assert got["unavailable"] == [{"tool": "profile_read", "reason": "roster"}]
