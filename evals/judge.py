# evals/judge.py
"""Confidence-weighted rubric judge (spec E.4).

`parse_judge_response`/`judge_verdict`/`case_verdict`/`evaluate_raw`/`JUDGE_MODEL_TIER`
below are exact, hand-verified against every case in `tests/test_evals_rubric.py` —
transcribed as pinned by the Task 41 brief, do not vary.

`NoraOutputEvaluator`/`NoraTrajectoryEvaluator` are this task's design-guidance part
(untested by `tests/test_evals_rubric.py`). Per RK-9 (re-confirmed live against the
installed `strands-agents-evals==1.0.3`): `strands_evals`'s built-in `OutputEvaluator`
constrains its judge output to the SDK's fixed `EvaluationOutput` shape —
`{score: float, test_pass: bool, reason: str | None, label: str | None}` — with no
`criterion`/`confidence` fields, and there is no dedicated `TrajectoryEvaluator` base
class to subclass either (the SDK's `evaluators.deterministic.trajectory` module only
re-exports the same base `Evaluator` plus trajectory-shaped data types: `Session`,
`ToolCalled`, `ToolExecutionSpan`). So both classes below subclass
`strands_evals.evaluators.evaluator.Evaluator` directly and map their richer
per-criterion result onto `EvaluationOutput` at the boundary, exactly as RK-9's
fallback prescribes — the verdict arithmetic above is unchanged either way.

Neither class is invoked with a live judge model in this session (out of scope, same
boundary as Task 40's offline-only harness) — they are exercised here only for
importability/structure, and delegate all response parsing to the already-tested
functions above rather than inventing new untested parsing logic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import ClassVar, Literal

from strands import Agent
from strands.models.model import Model
from strands_evals.evaluators.deterministic.trajectory import EvaluationOutput, ToolExecutionSpan
from strands_evals.evaluators.evaluator import EvaluationData, Evaluator

from .rubric import RUBRIC

JUDGE_MODEL_TIER = "deep"    # deepseek-v4-pro; grading with the tested model is circular

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


@dataclass(frozen=True)
class JudgeItem:
    criterion: str
    score: float
    confidence: float
    rationale: str


def parse_judge_response(raw: str) -> list[JudgeItem]:
    match = _FENCE.search(raw)
    text = match.group(1) if match else raw
    data = json.loads(text)
    return [
        JudgeItem(
            criterion=item["criterion"], score=float(item["score"]),
            confidence=float(item["confidence"]), rationale=item["rationale"],
        )
        for item in data
    ]


def judge_verdict(score: float, confidence: float) -> Literal["PASS", "FAIL", "REVIEW"]:
    if score <= 0.5 and confidence >= 0.7:
        return "FAIL"
    if score >= 0.7 and confidence >= 0.7:
        return "PASS"
    return "REVIEW"


def case_verdict(results) -> Literal["PASS", "FAIL", "REVIEW"]:
    """results: iterable of (criterion_name, verdict) pairs."""
    verdicts = [v for _, v in results]
    if "FAIL" in verdicts:
        return "FAIL"
    if "REVIEW" in verdicts:
        return "REVIEW"
    return "PASS"


def evaluate_raw(raw: str) -> Literal["PASS", "FAIL", "REVIEW"]:
    """A broken/unparseable judge response is a REVIEW, never a silent PASS."""
    try:
        items = parse_judge_response(raw)
    except Exception:                      # noqa: BLE001 — malformed judge output
        return "REVIEW"
    if not items:
        return "REVIEW"
    return case_verdict([(i.criterion, judge_verdict(i.score, i.confidence)) for i in items])


# --- Design guidance (not pinned by tests/test_evals_rubric.py) ------------------

#: llm.tiers.deep's model id (config.example.toml) — the default judge model, so
#: grading never uses the same tier that produced the reply being graded.
_DEFAULT_JUDGE_MODEL_ID = "deepseek-v4-pro"

# Context tools that gather information before a reply — anything else is either
# the terminal side-effect tool (`send_reply`) or unknown to this evaluator.
_CONTEXT_TOOLS = ("load_transcript",)


def _build_judge_prompt(
    *, tag_input: str, reply_text: str, doctrine_text: str,
    source_data: str | None = None,
) -> str:
    """Build the judge-facing prompt asking for all six `RUBRIC` criteria, in the
    exact JSON array shape `parse_judge_response` expects.

    `source_data` is the SOURCE the reply had to ground against — the conversation
    transcript / tool-result list Nora was working from (a case's `transcript`).
    Without it the judge is measuring grounding BLIND: it cannot tell "correctly
    named the top entry" from "fabricated which entry is the asker", because both
    read as fluent, confident text (DEC-75, the identity-fabrication class). The
    judge missed that fabrication every trial precisely because this was absent —
    grounding the measurement is the fix (comprehend the data state, not just the
    utterance). None keeps the old shape for cases that carry no source data.
    """
    criteria_block = "\n".join(f"- {c.name}: {c.description}" for c in RUBRIC)
    source_block = ""
    if source_data:
        source_block = (
            "## Source data Nora was working from\n"
            "This is the ONLY factual ground the reply may draw on (besides the "
            "doctrine). Any specific claim of fact in the reply — a name, a rank, a "
            "number, WHICH entry is the person asking — must be traceable to this "
            "data. A confident claim that is NOT supported here (e.g. asserting a "
            "particular list entry IS the asker when nothing links them) is a "
            "FABRICATION: score `no_overreach_or_fabrication` low.\n"
            f"```\n{source_data}\n```\n\n"
        )
    return (
        "You are grading one reply from Nora, a Signal-group ambient AI doctrine, "
        "against a fixed rubric. Score every criterion below from 0.0 (fails "
        "completely) to 1.0 (fully satisfied), and state your confidence (0.0-1.0) "
        "in that score.\n\n"
        f"## Doctrine\n{doctrine_text}\n\n"
        f"{source_block}"
        f"## Tagging message (what Nora was asked)\n{tag_input}\n\n"
        f"## Nora's reply (what you are grading)\n{reply_text}\n\n"
        f"## Criteria\n{criteria_block}\n\n"
        "Return ONLY a JSON array, one object per criterion, in this exact shape "
        "(a ```json fenced block is tolerated but not required):\n"
        '[{"criterion": "<name>", "score": <0-1 float>, "confidence": <0-1 float>, '
        '"rationale": "<one sentence>"}, ...]'
    )


class NoraOutputEvaluator(Evaluator):
    """LLM-as-judge over the six `RUBRIC` criteria (spec E.4).

    Subclasses the SDK's base `Evaluator` directly (RK-9's fallback — see module
    docstring) rather than `strands_evals.OutputEvaluator`, since the latter's fixed
    `EvaluationOutput` has nowhere to carry `criterion`/`confidence`. This class owns
    the judge prompt (`_build_judge_prompt`) and delegates all response parsing and
    verdict arithmetic to the already-tested `parse_judge_response`/`judge_verdict`
    module-level functions — it does not re-implement any parsing itself.
    """

    def __init__(
        self,
        *,
        tag_input: str,
        reply_text: str,
        doctrine_text: str,
        model: Model | str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.tag_input = tag_input
        self.reply_text = reply_text
        self.doctrine_text = doctrine_text
        # JUDGE_MODEL_TIER == "deep" -> deepseek-v4-pro; never grade with the tested
        # model (circular). A bare model-id string is enough here: no live judge
        # model is invoked in this task (out of scope, same boundary as Task 40).
        self.model = model if model is not None else _DEFAULT_JUDGE_MODEL_ID

    def build_prompt(self) -> str:
        return _build_judge_prompt(
            tag_input=self.tag_input, reply_text=self.reply_text,
            doctrine_text=self.doctrine_text,
        )

    def _call_judge(self, prompt: str) -> str:
        """Invoke the judge model and return its raw text response.

        Not exercised in this session (Task 41's design-guidance scope explicitly
        excludes invoking a real/live judge model here) — kept as a thin, isolated
        seam so a real invocation can be wired in later (e.g. from `evals/run.py`)
        without touching `evaluate()`'s parsing path below.
        """
        agent = Agent(model=self.model, tools=[], callback_handler=None,
                      load_tools_from_directory=False)
        result = agent(prompt)
        return str(result)

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        raw = self._call_judge(self.build_prompt())
        try:
            items = parse_judge_response(raw)
        except Exception:                  # noqa: BLE001 — malformed judge output
            items = []
        if not items:
            return [EvaluationOutput(
                score=0.0, test_pass=False, label="REVIEW",
                reason="judge response unparseable — REVIEW, never a silent pass",
            )]
        outputs = []
        for item in items:
            verdict = judge_verdict(item.score, item.confidence)
            outputs.append(EvaluationOutput(
                score=item.score,
                test_pass=(verdict == "PASS"),
                label=verdict,
                reason=f"{item.criterion} [{verdict}, confidence={item.confidence}]: "
                       f"{item.rationale}",
            ))
        return outputs


class NoraTrajectoryEvaluator(Evaluator):
    """Deterministic (non-LLM) tool-order check over a real Strands trajectory.

    Mirrors the spirit of `evals.evaluators.NoRedundantRead`/`ToolBudget` (Task 40),
    but operates on a real Strands `Session` (via `self._trace_extractor`'s
    `ToolExecutionSpan`s) rather than the eval harness's own simplified `Trajectory`
    dataclass. Kept simple and clearly-structured, not exhaustive — Task 42's
    adversarial corpus is where real coverage pressure lands, and this task's own
    tests don't exercise this class at all.

    Checks:
    - any context tool (`load_transcript`) occurs before `send_reply`;
    - `send_reply`, if called at all, is the LAST tool call;
    - no tool is called more times than `budgets` allows (defaults mirror R-7:
      `load_transcript` <=2, `send_reply` <=1).
    """

    _DEFAULT_BUDGETS: ClassVar[dict[str, int]] = {"load_transcript": 2, "send_reply": 1}

    def __init__(self, *, budgets: dict[str, int] | None = None, name: str | None = None) -> None:
        super().__init__(name=name)
        self.budgets = dict(budgets) if budgets is not None else dict(self._DEFAULT_BUDGETS)

    @staticmethod
    def _tool_call_names(evaluation_case: EvaluationData) -> list[str] | None:
        """Flatten a real Strands `Session` trajectory into an ordered list of tool
        names, oldest-first. Returns None if the trajectory isn't a `Session` (this
        evaluator only supports real trajectories, per its docstring above).
        """
        trajectory = evaluation_case.actual_trajectory
        if trajectory is None or not hasattr(trajectory, "traces"):
            return None
        names = []
        for trace in trajectory.traces:
            for span in trace.spans:
                if isinstance(span, ToolExecutionSpan):
                    names.append(span.tool_call.name)
        return names

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        names = self._tool_call_names(evaluation_case)
        if names is None:
            return [EvaluationOutput(
                score=0.0, test_pass=False,
                reason="no Session trajectory provided (NoraTrajectoryEvaluator "
                       "requires a real Strands Session, not a simplified list)",
            )]

        outputs = []

        # 1. Context tools before send_reply.
        if "send_reply" in names:
            send_index = names.index("send_reply")
            late_context = [n for n in names[send_index + 1:] if n in _CONTEXT_TOOLS]
            ok = not late_context
            outputs.append(EvaluationOutput(
                score=1.0 if ok else 0.0, test_pass=ok,
                reason="context tools ran before send_reply" if ok else
                       f"context tool(s) {late_context} ran after send_reply",
            ))

            # 2. send_reply is the last tool call, if present.
            is_last = send_index == len(names) - 1
            outputs.append(EvaluationOutput(
                score=1.0 if is_last else 0.0, test_pass=is_last,
                reason="send_reply was the last tool call" if is_last else
                       f"send_reply was followed by {names[send_index + 1:]}",
            ))

        # 3. No tool exceeds its budget.
        for tool_name, budget in self.budgets.items():
            count = names.count(tool_name)
            ok = count <= budget
            outputs.append(EvaluationOutput(
                score=1.0 if ok else 0.0, test_pass=ok,
                reason=f"{tool_name} called {count} time(s), budget is {budget}",
            ))

        return outputs
