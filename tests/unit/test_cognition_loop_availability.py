"""The report states what the subject could reach before any verdict."""

from types import SimpleNamespace

from evals.cognition_loop.availability import availability


def test_availability_reads_switches_offered_tools_and_closed_calls():
    cfg = SimpleNamespace(documents=SimpleNamespace(enabled=True))
    trajectory = {
        "model_calls": [{"tools": [{"function": {"name": "note_create"}},
                                   {"name": "memory_read"}]}],
        "tool_calls": [
            {"tool": "memory_read", "status": "unavailable", "reason": "roster"},
            {"tool": "note_create", "status": "ok"},
        ],
    }
    got = availability(cfg, trajectory)
    assert got["switches"]["documents.enabled"] is True
    assert got["switches"]["lessons.enabled"] is None
    assert got["offered"] == ["memory_read", "note_create"]
    assert got["unavailable"] == [{"tool": "memory_read", "reason": "roster"}]


def test_the_report_lists_unavailable_calls_from_every_wake(tmp_path):
    import json

    from evals.cognition_loop.html import _availability

    for i, calls in enumerate(([], [{"tool": "memory_read", "reason": "roster"}])):
        (tmp_path / f"wake-{i}-{i}.json").write_text(json.dumps({"trajectory": {
            "availability": {"switches": {"documents.enabled": True},
                             "offered": ["memory_read"], "unavailable": calls}}}))
    html = _availability(tmp_path)
    assert "wake-1-1: memory_read" in html
