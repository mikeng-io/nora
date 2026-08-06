"""Eval harness: drives a real pipeline graph (real guards, fake db/memory/adapter)
against a scripted turn sequence and reports what happened as a `Trajectory`, for
the deterministic evaluators in `evals.evaluators` to grade.

Design note (this file's `run_case`/`Case` are design guidance, not pinned by
`tests/test_evals_gate.py` — that test only exercises `Trajectory` + the evaluators):
`run_case` builds its own pipeline graph from `nora.app.offline.build_offline_pipeline`
plus `nora.testing`'s fakes, rather than reusing `tests/integration/harness.py` —
`evals/` may import `nora.app` and `nora.testing` and nothing else from `nora/`, and
may not import `tests/` at all (`tests/test_boundaries.py`). That machinery
deliberately makes the real, live-network branch of invocation unreachable by
construction: `run_case` always wraps whatever `script` you pass into a
`FakeAgentRunner`. So today, `run_case` only supports the offline/scripted path:
`script=None` degrades to a zero-turn `FakeAgentRunner` (a harmless silent run), it
does NOT reach out to a real model. Wiring an actual live-model code path (for the
M7 stage's `--suite all` network-requiring verification command) is out of scope for
this task and is left for later work.

`FakeAuditStore`/`FakeMemoryStore` replace `run_case`'s earlier real (tmp) SQLite
audit store: nothing here is pytest-gated (see the module note above), and the
evaluators only ever read the returned `Trajectory` — not persisted rows — so an
in-memory double is equivalent for this harness's purpose and drops the tmp-dir/
migration machinery `tests/integration/harness.py` needs for its own, broader,
fault-injection-capable test suite.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nora.app.eval_support import Metrics, DoctrineStore, load_eval_config, load_persons
from nora.app.offline import build_offline_pipeline
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeAuditStore,
    FakeMemoryStore,
    ScriptedTurn,
    group_event,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTRINE_DIR = REPO_ROOT / "nora" / "doctrine"

# The one sender `group_event`'s default produces, mapped so the post-reply Honcho
# feed (which requires a resolved `ctx.tagger`, `nora/engagement/stages/feedback.py`)
# actually fires — matches `tests/integration/harness.py`'s own default identity.
_IDENTITY_TOML = (
    '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
)

# Kept in sync with config.example.toml's engagement.trigger.text — the single real
# trigger string every case file and the harness itself must agree on.
TRIGGER_TEXT = "nora.000"


@dataclass
class Trajectory:
    tool_calls: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    reply: str | None = None
    honcho_feeds: int = 0
    prefill_sufficient: bool = True


@dataclass
class Case:
    name: str
    input: str
    expected_output: str | None = None
    trigger: bool = True
    prefill_sufficient: bool = True

    @classmethod
    def from_dict(cls, raw: dict) -> Case:
        meta = raw.get("metadata") or {}
        return cls(
            name=raw["name"],
            input=raw["input"],
            expected_output=raw.get("expected_output"),
            trigger=bool(meta.get("trigger", True)),
            prefill_sufficient=bool(meta.get("prefill_sufficient", True)),
        )


def load_cases(path: Path | str) -> list[Case]:
    """`yaml.safe_load` a case file into `Case`s. An empty/missing file yields []."""
    raw = yaml.safe_load(Path(path).read_text()) or []
    return [Case.from_dict(item) for item in raw]


async def _drain(background) -> None:
    """Await every background task spawned so far — ambient memory-feeds,
    spawned tags, AND `EngagementPipeline`'s post-reply exchange feed all land
    in the same shared `BackgroundTasks` now (`EngagementPipeline` takes an
    injected `spawn` instead of rolling its own private task set;
    `nora.app.offline.build_offline_pipeline` wires both pipelines' spawns to
    this one `background.spawn`) — including ones spawned by tasks that are
    themselves still in flight.

    Mirrors `tests/integration/harness.py::Harness.drain` exactly (same
    `asyncio.sleep(0)`-after-`gather` shape, for the same reason: a task's own
    coroutine can be reported `done()` before its `add_done_callback` discard
    runs, since done-callbacks are delivered via `call_soon` one loop tick
    later). Deliberately NOT `BackgroundTasks.cancel_all()` — that cancels
    outstanding tasks rather than letting them finish, which would lose the
    post-reply Honcho feed `Trajectory.honcho_feeds` depends on.
    """
    while background._tasks:
        pending = list(background._tasks)
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


def _build_body(case: Case) -> str:
    """Prefix `case.input` with the trigger text for trigger cases (unless the author
    already embedded it plausibly in the message), pass no-trigger cases through as-is.
    """
    if not case.trigger:
        return case.input
    if TRIGGER_TEXT.lower() in case.input.lower():
        return case.input
    return f"{TRIGGER_TEXT} {case.input}"


async def run_case(case: Case, *, script: list[ScriptedTurn] | None = None) -> Trajectory:
    """Run one eval `Case` through a real pipeline graph and return the resulting
    `Trajectory`.

    `script` is the scripted model turn sequence (offline mode — see module docstring).
    The tool names it *attempts* become `Trajectory.tool_calls`: in scripted mode we
    already know exactly which tools the script tries to call, whether or not
    `ReplyGuard` ends up vetoing any given call, and that's the simplest, most direct
    signal of "what did the model try to do" for the evaluators to grade.
    """
    turns = script or []
    tool_calls = [name for turn in turns for name, _ in turn.tool_calls]

    cfg = load_eval_config((REPO_ROOT / "config.toml", REPO_ROOT / "config.example.toml"))
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        DOCTRINE_DIR, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, metrics=metrics,
    )

    with tempfile.TemporaryDirectory(prefix="nora-eval-") as tmp:
        identity_path = Path(tmp) / "identity.toml"
        identity_path.write_text(_IDENTITY_TOML)
        persons = load_persons(identity_path)

        audit = FakeAuditStore()
        memory = FakeMemoryStore()
        adapter = FakeAdapter()
        runner = FakeAgentRunner(turns)
        try:
            dispatcher, _engagement, background = build_offline_pipeline(
                cfg, adapter=adapter, audit=audit, memory=memory, persons=persons,
                doctrine_store=doctrine_store, runner=runner, metrics=metrics,
            )
            body = _build_body(case)
            # Mirrors tests/integration/harness.py::_HandlerView.on_event: parse the
            # raw signal-cli-shaped event the same way SignalAdapter.listen() would
            # internally (Trap B moved that step off IngestPipeline and onto the
            # adapter), then dispatch only if it parsed to something.
            message = await adapter.parse(group_event(body=body))
            if message is not None:
                await dispatcher.on_message(message)
            await _drain(background)
        finally:
            await audit.aclose()

        feeds = len([f for f in memory.fed if "[Nora]" in f[1]])
        sent = list(adapter.texts)
        return Trajectory(
            tool_calls=tool_calls,
            sent=sent,
            reply=sent[-1] if sent else None,
            honcho_feeds=feeds,
            prefill_sufficient=case.prefill_sufficient,
        )
