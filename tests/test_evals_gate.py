
from pathlib import Path
from typing import Any

from evals.evaluators import (
    HonchoFedOncePerReply,
    NoRedundantRead,
    NoSendWithoutTrigger,
    RepliesAtLeastOnce,
    ToolBudget,
)
from evals.harness import Trajectory
from evals.run import SUITES, _build_sink, _run_suite
from evals.sink import LangfuseEvalSink, NullEvalSink


def _t(tool_calls=(), sent=(), reply=None, feeds=0, prefill_sufficient=True) -> Trajectory:
    return Trajectory(tool_calls=list(tool_calls), sent=list(sent), reply=reply,
                      honcho_feeds=feeds, prefill_sufficient=prefill_sufficient)


def test_replies_at_least_once_passes_on_a_single_send():
    assert RepliesAtLeastOnce().evaluate(_t(["send_reply"], ["hi"], "hi")).passed


def test_replies_at_least_once_fails_on_zero_sends():
    assert not RepliesAtLeastOnce().evaluate(_t([], [], None)).passed


def test_replies_at_least_once_passes_on_two_sends():
    """Multi-message (DEC-42): two replies are valid, so this PASSES where the
    cap-of-1 `ExactlyOnceSendReply` failed. The upper bound (never past the cap)
    moved to `ToolBudget`, asserted in test_tool_budget_enforces_the_r7_limits."""
    assert RepliesAtLeastOnce().evaluate(_t(["send_reply", "send_reply"],
                                            ["a", "b"], "b")).passed


def test_no_send_without_trigger_passes_on_silence():
    assert NoSendWithoutTrigger().evaluate(_t()).passed


def test_no_send_without_trigger_fails_on_any_send():
    assert not NoSendWithoutTrigger().evaluate(_t(["send_reply"], ["oops"])).passed


def test_tool_budget_enforces_the_r7_limits():
    # Derived from the live budgets (doubled per operator 2026-08-08), so a
    # budget change cannot silently un-test the R-7 ceiling: exactly the budget
    # is allowed, one more is refused.
    from nora.agent.tools import NATIVE_TOOL_BUDGETS as B
    ce, gm, sr = B["load_transcript"], B["group_members"], B["send_reply"]
    assert ToolBudget().evaluate(_t(["load_transcript"] * ce + ["send_reply"])).passed
    assert not ToolBudget().evaluate(_t(["load_transcript"] * (ce + 1) + ["send_reply"])).passed
    assert not ToolBudget().evaluate(_t(["group_members"] * (gm + 1))).passed
    # send_reply's own upper bound (the cap) is ToolBudget's job now that
    # RepliesAtLeastOnce owns only the lower bound (DEC-42).
    assert ToolBudget().evaluate(_t(["send_reply"] * sr)).passed
    assert not ToolBudget().evaluate(_t(["send_reply"] * (sr + 1))).passed


def test_no_redundant_read_fails_when_prefill_was_sufficient():
    assert not NoRedundantRead().evaluate(
        _t(["load_transcript", "send_reply"], prefill_sufficient=True)).passed


def test_no_redundant_read_allows_a_read_when_prefill_was_thin():
    assert NoRedundantRead().evaluate(
        _t(["load_transcript", "send_reply"], prefill_sufficient=False)).passed


def test_honcho_fed_once_per_delivered_reply():
    assert HonchoFedOncePerReply().evaluate(_t(["send_reply"], ["hi"], "hi", feeds=1)).passed
    assert not HonchoFedOncePerReply().evaluate(
        _t(["send_reply"], ["hi"], "hi", feeds=2)).passed
    assert HonchoFedOncePerReply().evaluate(_t([], [], None, feeds=0)).passed


def test_deterministic_evaluators_are_hard_gates():
    """No confidence weighting — these are mechanical facts (spec E.4)."""
    result = RepliesAtLeastOnce().evaluate(_t([], [], None))
    assert not hasattr(result, "confidence")


# --- Phase 7 Task 3: sink wiring (DEC-6) -------------------------------------
#
# `_run_suite` below drives the SAME offline pipeline `evals/harness.py::run_case`
# always has (no live model, no network) — these tests are genuinely offline, not
# just "no Langfuse", and run fast (all 24 trigger+no_trigger cases in ~2s).

NO_TRIGGER_PATH = Path("evals/cases/no_trigger.yaml")
TRIGGER_PATH = Path("evals/cases/trigger.yaml")


class _RecordingSink:
    """Records every call — lets these tests assert on exactly what `_run_suite`
    sends, without needing a fake HTTP server (that's `tests/test_evals_sink.py`'s
    job for `LangfuseEvalSink` itself)."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str]] = []
        self.scores: list[dict[str, Any]] = []
        self._next_token = 0

    async def upsert_dataset(self, *, name: str) -> None:
        raise NotImplementedError

    async def upsert_item(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def start_run(self, *, dataset_name: str, run_name: str) -> str:
        self.runs.append((dataset_name, run_name))
        self._next_token += 1
        return f"token-{self._next_token}"

    async def record_score(
        self, *, run_id: str, item_id: str, name: str, value: float | str, data_type: str
    ) -> None:
        self.scores.append({
            "run_id": run_id, "item_id": item_id, "name": name,
            "value": value, "data_type": data_type,
        })


async def test_run_suite_scores_every_no_trigger_case_with_a_boolean(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    sink = _RecordingSink()

    ok = await _run_suite((NO_TRIGGER_PATH,), sink=sink, run_name="gate-test")

    assert ok is True  # no_trigger cases pass trivially with no live model (module docstring)
    assert sink.runs == [("nora-no-trigger", "gate-test")]
    assert {s["name"] for s in sink.scores} == {"no_send_without_trigger"}
    assert all(s["data_type"] == "BOOLEAN" and isinstance(s["value"], float)
               for s in sink.scores)
    assert all(s["item_id"].startswith("no_trigger/") for s in sink.scores)


async def test_run_suite_scores_every_deterministic_evaluator_for_trigger_cases(monkeypatch):
    """Carries the 4 trigger-side evaluators through unchanged (RepliesAtLeastOnce,
    ToolBudget, NoRedundantRead, HonchoFedOncePerReply) — one BOOLEAN score each,
    same verdict the printed [PASS]/[FAIL] line already reported."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    sink = _RecordingSink()

    await _run_suite((TRIGGER_PATH,), sink=sink, run_name="gate-test")

    names = {s["name"] for s in sink.scores}
    assert names == {
        "replies_at_least_once", "tool_budget", "no_redundant_read",
        "honcho_fed_once_per_reply", "a8_silent_failure",
    }
    assert all(s["data_type"] == "BOOLEAN" and isinstance(s["value"], float)
               for s in sink.scores)


async def test_a8_silent_failure_score_reflects_zero_send_reply_calls(monkeypatch):
    """A8 (design/09-decision-index.md): tags with no reply / total tags. Every
    trigger case here has zero send_reply calls (no live model wired into the
    offline gate), so every one is a silent failure — a8_silent_failure=1.0 for
    all 12, which is exactly what the mean of this BOOLEAN score across the run
    (computed by Langfuse itself) turns into the A8 ratio for a live run."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    sink = _RecordingSink()

    await _run_suite((TRIGGER_PATH,), sink=sink, run_name="gate-test")

    a8_scores = [s for s in sink.scores if s["name"] == "a8_silent_failure"]
    assert len(a8_scores) == 12
    assert all(s["value"] == 1.0 for s in a8_scores)


async def test_a8_silent_failure_score_is_not_recorded_for_no_trigger_cases(monkeypatch):
    """A8 is defined over TAGS (trigger cases) — a no-trigger case was never
    supposed to reply, so "silent failure" doesn't apply to it."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    sink = _RecordingSink()

    await _run_suite((NO_TRIGGER_PATH,), sink=sink, run_name="gate-test")

    assert not any(s["name"] == "a8_silent_failure" for s in sink.scores)


def test_build_sink_is_null_when_the_export_flag_is_absent(example_config):
    assert isinstance(_build_sink(example_config, export=False), NullEvalSink)


def test_build_sink_is_null_when_config_disables_langfuse_even_with_the_flag(example_config):
    """Both flag AND config must opt in — the flag alone is not enough.

    The disabled config is built explicitly rather than read from a file. An
    earlier version of this file loaded the deployment's `config.toml` and
    asserted its live value, which made a unit test of `_build_sink`'s logic
    fail the moment the operator enabled the export — a false dependency, not
    a real regression, and the case R-B (issue #113) is named for. This
    mirrors its sibling below, which overrides to True."""
    from tests.builders import override_config

    cfg = override_config(example_config, {"evals.langfuse.enabled": False})
    assert cfg.evals.langfuse.enabled is False
    assert isinstance(_build_sink(cfg, export=True), NullEvalSink)


def test_build_sink_is_langfuse_when_both_flag_and_config_enable_it(example_config):
    from tests.builders import override_config

    cfg = override_config(example_config, {"evals.langfuse.enabled": True})
    assert isinstance(_build_sink(cfg, export=True), LangfuseEvalSink)


# --- PROBE (Task 3, the important one) ---------------------------------------
#
# The eval gate's verdict must NEVER depend on Langfuse being reachable — a
# store that can fail the gate turns an observability nicety into a CI outage
# (the plan's own framing). Run the SAME suite through NullEvalSink and through
# a real LangfuseEvalSink pointed at 127.0.0.1:1 (nothing listens there;
# connection refused immediately) and require an IDENTICAL verdict — checked
# for both a passing suite (no_trigger) and a failing one (trigger), so this
# can't pass by the verdict happening to always be True regardless of the sink.


async def test_gate_verdict_is_unaffected_by_an_unreachable_langfuse_when_passing(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")

    baseline = await _run_suite((NO_TRIGGER_PATH,), sink=NullEvalSink(), run_name="baseline")

    unreachable = LangfuseEvalSink(host="http://127.0.0.1:1", headers={}, timeout=0.3)
    with_export = await _run_suite((NO_TRIGGER_PATH,), sink=unreachable, run_name="probe-run")

    assert baseline is True
    assert with_export == baseline


async def test_gate_verdict_is_unaffected_by_an_unreachable_langfuse_when_failing(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")

    baseline = await _run_suite((TRIGGER_PATH,), sink=NullEvalSink(), run_name="baseline")

    unreachable = LangfuseEvalSink(host="http://127.0.0.1:1", headers={}, timeout=0.3)
    with_export = await _run_suite((TRIGGER_PATH,), sink=unreachable, run_name="probe-run")

    assert baseline is False  # documented: trigger cases fail offline with no live model
    assert with_export == baseline


def test_export_langfuse_suite_choice_is_scoped_to_deterministic_suites():
    """Documents the scope decision (see evals/run.py module docstring): the
    `adversarial` suite's live path is not wired to the sink in this phase, and
    neither is `journeys` (D4 tier 2) — both are live suites run before deploy,
    not deterministic sink-exporting ones. The same holds for `participation`
    (design/dreamer-agent-loop/35 §9): a live suite with its own runner. This
    pins the deterministic suites still resolve to the same corpus files, and
    the full suite set."""
    assert set(SUITES) == {"trigger", "no_trigger", "all", "adversarial",
                           "journeys", "participation", "relevance_gate"}
