
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.evaluators import (
    HonchoFedOncePerReply,
    NoRedundantRead,
    NoSendWithoutTrigger,
    RepliesAtLeastOnce,
    ToolBudget,
)
from evals.harness import REPO_ROOT, TRIGGER_TEXT, Trajectory
from evals.proactive import CaseTally
from evals.run import (
    CASES_DIR,
    SUITES,
    _build_sink,
    _print_proactive_gate,
    _run_suite,
    _tally_line,
)
from evals.sink import LangfuseEvalSink, NullEvalSink
from nora.tag import is_tagged


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
    # 0.0 is «no silent failure». This asserted 1.0 on all twelve, which was
    # the CONSTANT the vacuity produced: `_run_suite` passed `script=None`, so
    # no model ran, so every tagged case was a silent failure by construction
    # and the A8 ratio the docstring calls a measurement was always 1.0. The
    # offline suites drive a scripted reply now, so this reads the plumbing:
    # tagged in, delivered out.
    assert all(s["value"] == 0.0 for s in a8_scores), (
        "a tagged case that the pipeline engaged and delivered is not a silent "
        "failure; a 1.0 here means the send never happened"
    )


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

    # The property is that the SINK cannot change the verdict, whatever the
    # verdict is. It used to pin `baseline is False` — true only because the
    # offline suite invoked no model and every trigger case failed. That is
    # the vacuity, not the property, and the suite drives a scripted reply now.
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
                           "journeys", "participation", "relevance_gate",
                           "note_candidates",
                           # The proactive adaptor's two live suites
                           # (design/dreamer-agent-loop/39 §5): their own
                           # drivers, sink-scored like note_candidates.
                           "proactive_decide", "proactive_act",
                           # The three measured-gap corpora (#227, 2026-09-17):
                           # the adversarial live path, selectable by name,
                           # deliberately NOT members of the gated
                           # `adversarial` tuple and not sink-exporting either.
                           "identity_grounding", "source_scope",
                           "being_contradicted"}



def test_no_trigger_cases_really_do_not_tag_under_the_configured_trigger():
    """**The assertion whose absence let the rot through, on the other corpus.**

    `tests/test_evals_adversarial.py` has carried this for
    `evals/adversarial/near_miss.yaml` and its docstring says why. Nothing
    covered `evals/cases/no_trigger.yaml`, and on 2026-09-09 four of its twelve
    «genuine near-misses» actually TAGGED: the corpus was authored against
    `nora.000` while the harness constant claimed to mirror a config that says
    `nora`, and the deployment says `nora` too. A suite of no-trigger cases
    that trigger measures nothing at all.
    """
    cases = yaml.safe_load(
        (CASES_DIR / "no_trigger.yaml").read_text(encoding="utf-8"),
    )
    assert cases, "the no-trigger corpus is empty"
    tagging = [c["name"] for c in cases if is_tagged(c["input"], trigger=TRIGGER_TEXT)]
    assert not tagging, (
        f"these no-trigger cases DO tag under {TRIGGER_TEXT!r}: {tagging}. "
        f"Either they are not near-misses, or the corpus was written for a "
        f"different trigger than the one any deployment runs."
    )


def test_the_harness_trigger_is_the_configured_one():
    """It is derived now, so this pins that the derivation still resolves —
    the previous value was a hand-copied literal whose own comment claimed it
    was in sync while it was two characters and a suffix away."""
    # `tomllib`, not `_load_eval_config`: the full loader refuses to build
    # without the provider API key, and this must run in the offline gate.
    conf = tomllib.loads(
        (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"),
    )
    assert conf["engagement"]["trigger"]["text"] == TRIGGER_TEXT


# ---------------------------------------------------------------------------
# the proactive gate LINE — a summary that cannot be quoted without its verdict
# ---------------------------------------------------------------------------


def test_the_proactive_gate_line_cannot_be_printed_without_its_verdict(capsys):
    """A failing act run printed

        proactive_act gate: false-positive rate 0.00 on 6 quiet case(s);
        recall 0.50; 8/10 cases majority-pass over 50 samples; errors 9

    and then, separately and later, GATE FAILED. The first half reads healthy
    on its own and that is the half that reached a PR body as the result of the
    run. The verdict is now computed BEFORE the line and printed ON it, and
    `verdict` is a required keyword argument so a caller cannot print numbers it
    has not yet decided about.

    Falsification: make `verdict` optional with a default, or move the
    `[PASS]`/`[FAIL]` marker off this line, and the first assertion reddens.
    """
    failing = [
        CaseTally(name="a01", expect="speak", verdicts=["PASS", "FAIL", "FAIL"]),
        CaseTally(name="a02", expect="silent", verdicts=["PASS", "PASS", "PASS"]),
    ]
    _print_proactive_gate("proactive_act", failing, verdict=False,
                          label="m/low", instructions=None)
    line = capsys.readouterr().out.splitlines()[0]
    assert line.startswith("proactive_act gate [FAIL]:")
    assert "recall" in line and "majority-pass" in line

    _print_proactive_gate("proactive_act", failing, verdict=True,
                          label="m/low", instructions=None)
    assert capsys.readouterr().out.splitlines()[0].startswith("proactive_act gate [PASS]:")

    with pytest.raises(TypeError):
        _print_proactive_gate("proactive_act", failing, label="m/low",  # type: ignore[call-arg]
                              instructions=None)


def test_the_gate_line_counts_outages_and_non_measurements_apart(capsys):
    """Two different words for two different things, because folding them
    together inverted this line's own promise: a run with ZERO outages reported
    `errors 9`, which reads as a broken gateway and was a quiet model."""
    _print_proactive_gate(
        "proactive_act",
        [CaseTally(name="e1", expect="either",
                   verdicts=["UNMEASURED", "UNMEASURED", "UNMEASURED"])],
        verdict=True, label="m/low", instructions=None,
    )
    line = capsys.readouterr().out.splitlines()[0]
    assert "errors 0" in line
    assert "unmeasured 3 sample(s) in 1 case(s)" in line


def test_a_case_line_reports_the_suites_own_verdict_not_majority_pass(capsys):
    """`[UNMEASURED]` is a third status and the act suite needs it: a fully
    quiet `either` case has no majority to pass, so the default
    `majority_pass` rendering would print `[FAIL]` next to a run the gate
    passed. A per-case line disagreeing with the gate is the same defect one
    level down."""
    quiet = CaseTally(name="a09", expect="either",
                      verdicts=["UNMEASURED", "UNMEASURED"])
    _tally_line("proactive_act", quiet, status="UNMEASURED")
    assert capsys.readouterr().out.startswith("[UNMEASURED] proactive_act/a09")
    # The mark for it is its own, too — never `!`, which means an outage.
    _tally_line("proactive_act", quiet)
    assert "samples=--" in capsys.readouterr().out
