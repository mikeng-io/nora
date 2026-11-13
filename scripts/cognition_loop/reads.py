"""Free retrieval checks through the same room-bound read source as the session."""

from datetime import UTC, datetime
from typing import Any

from nora.domain.cognition_session import Room
from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.trajectory import tool_outcome


async def preflight_reads(runner: Any, case: Any, index: int,
                          inspection: dict, at_ms: int) -> list[dict]:
    probes = [p for p in case.read_probes if p.wake == index]
    if not probes:
        return []
    rooms = list(dict.fromkeys(
        Room(i["platform"], i["conversation_id"]) for i in inspection["items"]))
    source = runner.with_collaborators()._read_tools_for
    if source is None:
        raise EvidenceError("read preflight: session has no read source")
    tools = {t.tool_name: t for t in source(
        case.unit, rooms, clock=lambda: datetime.fromtimestamp(at_ms / 1000, UTC))}
    rows = []
    for n, probe in enumerate(probes):
        tool = tools.get(probe.tool)
        if tool is None:
            raise EvidenceError(f"read preflight: {probe.tool} is not offered")
        use = {"toolUseId": f"preflight-{n}", "name": probe.tool,
               "input": probe.arguments}
        result = None
        async for event in tool.stream(use, {}):
            if "tool_result" in event:
                result = event["tool_result"]
        outcome = tool_outcome(result)
        status = outcome.get("status")
        holds = (status in ("ok", "partial") and bool(outcome.get("keys"))
                 if probe.expected == "found" else status == probe.expected)
        if not holds or not result or result.get("status") != "success":
            raise EvidenceError(
                f"read preflight {n}: {probe.tool} expected {probe.expected}, got {status}")
        rows.append({"tool": probe.tool, "arguments": probe.arguments,
                     "result": result, "expected": probe.expected, "status": "PASS"})
    return rows
