"""Dump the Cognition session's built tool specs, in order, to canonical JSON + sha256.

Sibling of `dump_tool_specs` for the session lane: builds
`CognitionSessionRunner._build_tools` output over the existing in-memory test
fakes (the same construction the unit rig and `test_cognition_session_tool_specs`
use — memory, notes, events, standing, reminders, memos, the documents sentinels
and a lessons stub), runs one scripted wake that only captures the tool list,
then dumps every Strands tool's spec fields.

Per tool the dump carries `name`, the full `tool_spec` (description and
inputSchema — the model-facing surface), the callable's verbatim docstring and
its signature (name, kind, annotation repr, default repr, in order).

Run: `uv run python -m scripts.dump_cognition_tool_specs [out.json]`.
Prints the sha256 of the JSON. Compare hashes across commits — they must match
byte-for-byte.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sys
from typing import Any

from nora.agent.dreamer import as_strands_tools
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_grounding import StaticGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000
HOME = Room("signal", "home-room")


class _Spy:
    """Captures the tool list a session hands its agent."""

    def __init__(self) -> None:
        self.tools: list[Any] = []

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.tools = list(tools)
        return CognitionRunResult(stop_reason="end_turn")


def _const(value: Any) -> Any:
    async def f(*args: Any, **kwargs: Any) -> Any:
        return value
    return f


class _Lessons:
    async def shown(self, *, unit: str) -> Any:
        from nora.domain.lessons import LessonEvidenceView
        return LessonEvidenceView()

    def verbs(self, **kw: Any) -> list[Any]:
        async def lesson_write(**kw2: Any) -> dict[str, object]:
            return {"status": "unavailable"}

        async def lesson_retire(**kw2: Any) -> dict[str, object]:
            return {"status": "unavailable"}
        return [lesson_write, lesson_retire]

    async def take(self, **kw: Any) -> None:
        return None

    async def decay(self, **kw: Any) -> None:
        return None


def _wake_memory() -> Any:
    from nora.app.cognition_memory import WakeMemory
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph
    return WakeMemory(
        graph=FakeMemoryGraph(), records=InMemoryComprehendRecords(),
        metrics=Metrics())


async def _tools() -> list[Any]:
    from datetime import UTC, datetime

    from nora.app.gatherings import GatheringService
    from nora.obs.metrics import Metrics
    from nora.testing.gatherings import FakeGatheringStore
    from nora.testing.memo_store import InMemoryMemoStore
    from nora.testing.note_store import InMemoryNoteStore
    from nora.testing.reminders import FakeReminderStore
    from nora.testing.standing_store import InMemoryStandingStore
    spy = _Spy()
    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    stimuli.seed_wake("u", 1)
    runner = CognitionSessionRunner(
        store=ledger, stimulus_store=stimuli,
        agent_runner=spy, home_rooms={"u": HOME},
        read_tools_for=lambda unit, rooms, **kw: [],
        skill_text_for=lambda: "",
        grounder=StaticGrounder(Grounding()),
        doctrine_text="D", doctrine_sha256="sha",
        open_note_titles=_const([]), open_events=_const([]),
        prior_standing=_const(None),
        budget=AdaptorBudget(
            turns=4, total_tokens=20_000, output_tokens=2_000,
            timeout_seconds=30.0),
        cooldown_seconds=300.0, units=("u",), session_timeout_seconds=900.0,
        max_items=12, clock=lambda: NOW / 1000,
        memory=_wake_memory(),
        notes=InMemoryNoteStore(),
        events=GatheringService(
            store=FakeGatheringStore(), route_source=None, honcho=None,
            ai_peer="nora",
            now=lambda: datetime.fromtimestamp(NOW / 1000, UTC)),
        standing=InMemoryStandingStore(),
        reminders=FakeReminderStore(),
        memos=InMemoryMemoStore(),
        ai_peer="nora",
        roster_of=_const({"Ada": "p_ada"}),
        nora_names=frozenset({"Nora"}),
        room_proactive=lambda platform, conversation_id: True,
        max_pending_per_requester=20, remind_min_lead_seconds=120,
        remind_max_horizon_days=30,
        metrics=Metrics(),
        documents=object(), documents_context=object(),
        lessons=_Lessons(),
    )
    pending = (Stimulus(
        id=1, unit="u", kind="message", item="message", ref="messages:1",
        at_ms=NOW, recorded_at_ms=NOW, platform="signal",
        conversation_id="home-room"),)
    view = UnitView(unit="u", pending=pending, last_activity_ms=None, last_wake=None)
    await asyncio.wait_for(runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=1), 15)
    return as_strands_tools(spy.tools)


def _specs() -> list[dict[str, Any]]:
    out = []
    for tool in asyncio.run(_tools()):
        call = getattr(tool, "_func", None) or getattr(tool, "_tool_func", None)
        doc = getattr(call, "__doc__", None)
        try:
            params = [
                {
                    "name": p.name,
                    "kind": str(p.kind),
                    "annotation": repr(p.annotation),
                    "default": repr(p.default),
                }
                for p in inspect.signature(call).parameters.values()
            ] if call is not None else None
        except (TypeError, ValueError):
            params = None
        entry: dict[str, Any] = {
            "name": tool.tool_name,
            "tool_spec": tool.tool_spec,
            "doc": doc,
            "signature": params,
        }
        out.append(entry)
    return out


def _canon(node: Any) -> Any:
    # Some schema `enum` lists are built from sets, so their element order
    # varies run to run; sorting keeps the comparison byte-stable.
    if isinstance(node, dict):
        return {
            k: sorted(v) if k == "enum" and v and all(isinstance(x, str) for x in v)
            else _canon(v)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_canon(x) for x in node]
    return node


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "cognition_specs.json"
    data = json.dumps(_canon(_specs()), indent=2, sort_keys=True) + "\n"
    with open(out_path, "w") as f:
        f.write(data)
    print(f"{hashlib.sha256(data.encode()).hexdigest()}  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
