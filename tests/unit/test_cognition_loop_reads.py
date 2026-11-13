"""Preflight read outcomes, room binding and the absence of model execution."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from strands import tool

from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.cases import ReadProbe, generated_case, load_case, write_case
from scripts.cognition_loop.reads import preflight_reads


@pytest.mark.parametrize(("query", "expected", "passes"), [
    ("known", "found", True), ("empty", "nothing_found", True),
    ("empty", "found", False), ("unavailable", "found", False),
    ("refused", "refused", True), ("refused", "found", False),
])
async def test_preflight_executes_room_bound_reads_and_requires_the_outcome(
    query, expected, passes, tmp_path,
):
    calls = []

    @tool
    async def memory_search(query: str) -> dict:
        """Read a generated key, an empty scope, or an unavailable scope."""
        calls.append(query)
        return {"status": "ok" if query == "known" else
                "nothing_found" if query == "empty" else query,
                "keys": [["topic", "generated-key"]] if query == "known" else []}

    def reads(unit, rooms, *, clock):
        assert unit == "generated-unit"
        assert [(r.platform, r.conversation_id) for r in rooms] == [("signal", "generated-room")]
        assert clock().timestamp() == 1800000000
        return [memory_search]

    case = replace(generated_case(), read_probes=(
        ReadProbe(0, "memory_search", {"query": query}, expected),))
    path = tmp_path / "case.json"
    write_case(case, path)
    case = load_case(path)
    runner = SimpleNamespace(with_collaborators=lambda: SimpleNamespace(_read_tools_for=reads))
    inspection = {"items": [{"platform": "signal", "conversation_id": "generated-room"}]}
    if passes:
        rows = await preflight_reads(runner, case, 0, inspection, 1800000000000)
        assert rows[0]["status"] == "PASS"
        assert rows[0]["expected"] == expected
    else:
        with pytest.raises(EvidenceError, match="read preflight"):
            await preflight_reads(runner, case, 0, inspection, 1800000000000)
    assert calls == [query]
    assert await preflight_reads(runner, case, 1, inspection, 1800000000000) == []
    assert calls == [query]


def test_preflight_cannot_request_a_write():
    with pytest.raises(EvidenceError, match="only call memory"):
        ReadProbe(0, "note_create", {"text": "generated"})
