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

`FakeRecordStore`/`FakeMemoryStore` replace `run_case`'s earlier real (tmp) SQLite
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

from nora.app.eval_support import (
    TOOL_NAMES,
    DoctrineStore,
    Metrics,
    build_eval_tool_registry,
    build_live_runner,
    load_eval_config,
    load_persons,
)
from nora.app.offline import build_offline_pipeline
from nora.testing import (
    FakeAdapter,
    FakeAgentRunner,
    FakeMemoryStore,
    FakeRecordStore,
    FakeRosterSource,
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

# The harness drives `group_event(body=...)` with its DEFAULTS — conversation "g1",
# sender "+852111" — and `_IDENTITY_TOML` maps that sender to person "miles". The
# live-mode roster below names that one member so the room resolves to {miles}, a
# verified subset of the eval circle (`nora.app.eval_support`), which is what lets
# `saved_places`/`where_is` disclose instead of degrading (F4). If `group_event`'s
# defaults change, these must change with them.
_EVAL_GROUP_ID = "g1"
_EVAL_MEMBER_ID = "+852111"


@dataclass
class Trajectory:
    tool_calls: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    reply: str | None = None
    honcho_feeds: int = 0
    prefill_sufficient: bool = True
    # HOW the agent loop ended, read back off `nora_llm_stop_reason_total`
    # (live runs only; None in scripted mode, which has no loop to end).
    #
    # Added 2026-08-10 because its ABSENCE made a root-cause analysis
    # unfalsifiable: `high` reasoning produced 3x the silent turns of `none`,
    # the obvious hypothesis was the loop hitting `limit_output_tokens`, and
    # grepping the eval log for it returned zero — which looked like evidence
    # and was nothing of the kind, because the harness never recorded a stop
    # reason at all. A null result from an instrument that cannot measure the
    # thing is not a null result (CLAUDE.md). `end_turn` with no reply and
    # `limit_output_tokens` are completely different failures and were
    # indistinguishable from here.
    stop_reason: str | None = None


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


# The counters that carry a per-tool `tool` label, so a live call's tool NAME can
# be read back. `nora.agent.tools` does NOT record every tool through one series:
# native tools use `nora_tool_calls_total{tool,status}` (`_record`), but the
# room/memory tools — group_members, lookup_person, about, ask_memory,
# search_memory — use `nora_room_lookups_total{tool,status}` (`_record_room`), a
# SEPARATE series (`tests/test_tools.py::test_the_new_counter_never_double_counts_a
# _room_scoped_tool` pins the split, asserting `nora_tool_calls_total == 0` and
# `nora_room_lookups_total == 3` for a room tool). Reading only the first would be
# BLIND to the whole MEMORY cluster of `SEED_INTENT_GRAPH`, silently false-PASSing
# a `NoSiblingLeakage` leak into `about`/`ask_memory`. `media_fetch` records
# `nora_media_fetch_total` which carries NO `tool` label and so cannot be
# reconstructed by name — it is deliberately not an intent-graph node, and
# `evals.evaluators.UNMEASURABLE_LIVE` + its guard test fail closed if one is added.
_TOOL_CALL_METRICS = ("nora_tool_calls_total", "nora_room_lookups_total")


def _stop_reason_from_metrics(metrics: Metrics) -> str | None:
    """How the agent loop ended, off `nora_llm_stop_reason_total{reason}`.

    One turn per `run_case`, so exactly one series is expected to have been
    incremented; if several are (a retry, a future multi-turn case), the one
    with the highest count wins and ties break alphabetically so the value is
    deterministic rather than dict-order-dependent. None when nothing recorded
    — a turn that faulted before the model was invoked has no stop reason, and
    inventing `"unknown"` would make that indistinguishable from a real one.
    """
    counts: dict[str, float] = {}
    for (name, labels), value in metrics.snapshot().items():
        if name == "nora_llm_stop_reason_total" and value:
            reason = dict(labels).get("reason", "")
            if reason:
                counts[reason] = counts.get(reason, 0.0) + value
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _tool_calls_from_metrics(metrics: Metrics) -> list[str]:
    """Reconstruct which tools the LIVE model actually called, from the
    per-tool call counters (`_TOOL_CALL_METRICS`).

    In scripted mode the harness knows the tool sequence up front — it is what
    the script tries to call. A live run has no script, so the tool names are
    read back off the counters that record real invocations. This is a MULTISET,
    not an ordered trace: Prometheus counters carry a value per label set, not an
    event log, so a tool called twice appears as two list entries but the
    interleaving with other tools is lost. That is enough for the tool-CHOICE
    evaluators D2 grades ("did she reach for weather or where_is?"), which is the
    "same same but different" signal; a strict path-order evaluator would need a
    separate ordered trace and is out of scope here.

    `status` ("ok"/"refused"/"unavailable") is summed over on purpose: a call the
    tool BODY saw is a routing choice whether or not it succeeded — a `place_info`
    that refused a person name still reached for place_info. But this counts only
    calls that reached the tool body: a call `ReplyGuard` VETOES first (R-7 budget,
    an unknown/hallucinated name) records `nora_guard_vetoes_total{rule}` — NO
    `tool` label — and is invisible here (external review, 2026-08-10). So the
    reconstructed count is a LOWER BOUND on attempts, capped by the ReplyGuard
    budgets: `ConvergesInBudget` therefore cannot see the worst flail (a tool
    hammered past its budget), and a journey budget ≥ a tool's guard budget can
    never fail on that tool. Good enough for tool-CHOICE grading, not for a true
    attempt count.
    """
    calls: list[str] = []
    for (name, labels), value in metrics.snapshot().items():
        if name not in _TOOL_CALL_METRICS:
            continue
        tool = dict(labels).get("tool")
        if tool is None:
            continue
        calls.extend([tool] * int(value))
    return calls


def _build_body(case: Case) -> str:
    """Prefix `case.input` with the trigger text for trigger cases (unless the author
    already embedded it plausibly in the message), pass no-trigger cases through as-is.
    """
    if not case.trigger:
        return case.input
    if TRIGGER_TEXT.lower() in case.input.lower():
        return case.input
    return f"{TRIGGER_TEXT} {case.input}"


async def run_case(
    case: Case, *, script: list[ScriptedTurn] | None = None, live: bool = False,
) -> Trajectory:
    """Run one eval `Case` through a real pipeline graph and return the resulting
    `Trajectory`.

    Two mutually exclusive drive modes:

    - **Scripted (default, `live=False`)** — `script` is the scripted model turn
      sequence (offline, no network — see module docstring). The tool names it
      *attempts* become `Trajectory.tool_calls`: in scripted mode we already know
      exactly which tools the script tries to call, whether or not `ReplyGuard`
      ends up vetoing any given call, and that's the simplest, most direct signal
      of "what did the model try to do" for the evaluators to grade.

    - **Live (`live=True`)** — drives Nora's REAL model
      (`nora.app.eval_support.build_live_runner`, `cfg.llm`) through the same
      real pipeline, reaching the network. `script` is ignored. There is no
      scripted tool list to read, so `Trajectory.tool_calls` is reconstructed
      after the run from the `nora_tool_calls_total` metric the tools emit (see
      `_tool_calls_from_metrics` for the multiset-not-trace caveat). A live run
      needs the LLM key env set; `build_live_runner` raises otherwise.
    """
    turns = script or []
    tool_calls = [name for turn in turns for name, _ in turn.tool_calls]

    cfg = load_eval_config((REPO_ROOT / "config.toml", REPO_ROOT / "config.example.toml"))
    metrics = Metrics()
    doctrine_store = DoctrineStore(
        DOCTRINE_DIR, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, language_hint=cfg.doctrine.language_hint,
        tool_names=TOOL_NAMES, metrics=metrics,
    )

    with tempfile.TemporaryDirectory(prefix="nora-eval-") as tmp:
        identity_path = Path(tmp) / "identity.toml"
        identity_path.write_text(_IDENTITY_TOML)
        persons = load_persons(identity_path)

        audit = FakeRecordStore()
        memory = FakeMemoryStore()
        adapter = FakeAdapter()
        # Live mode swaps the scripted double for Nora's real, networked model;
        # everything else in the graph (db, memory, adapter) stays a fake, so a
        # live run measures the MODEL's behaviour against real doctrine+tools
        # without touching a real platform or store.
        runner = build_live_runner(cfg) if live else FakeAgentRunner(turns)
        # Live mode wires the canned backends (F4) so the routing gate's siblings
        # answer `ok` and a leak is a real choice, not a fallback artifact. Scripted
        # mode keeps the bare registry — its tool_calls come from the script.
        tool_registry = build_eval_tool_registry() if live else None
        # Live mode also names the room so `saved_places`/`where_is` clear the R-31
        # subset gate (the room {miles} ⊆ the eval circle {miles}); scripted mode
        # leaves the room unavailable, as before.
        roster = (
            FakeRosterSource(groups={_EVAL_GROUP_ID: (frozenset({_EVAL_MEMBER_ID}),)})
            if live else None
        )
        try:
            dispatcher, _engagement, background = build_offline_pipeline(
                cfg, adapter=adapter, audit=audit, memory=memory, persons=persons,
                doctrine_store=doctrine_store, runner=runner, metrics=metrics,
                tool_registry=tool_registry, roster_source=roster,
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
        # Live mode has no script to read tool intent from — reconstruct it from
        # what the tools actually recorded this run (see the helper's caveat).
        stop_reason = None
        if live:
            tool_calls = _tool_calls_from_metrics(metrics)
            stop_reason = _stop_reason_from_metrics(metrics)
        return Trajectory(
            tool_calls=tool_calls,
            sent=sent,
            reply=sent[-1] if sent else None,
            honcho_feeds=feeds,
            prefill_sufficient=case.prefill_sufficient,
            stop_reason=stop_reason,
        )
