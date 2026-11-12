"""Two independent judges over one wake's evidence.

Each expectation is judged against that wake's diff, the state after it,
and its trajectory — never the case file's prose alone. A judge answers
`met`, `not_met` or `cannot_tell` with a reason and the entry ids it
rests on; `validate_citations` turns a nonexistent citation into
`ERROR`. The served-model header is verified on every call — a judge
that is not the alias it claims makes the answer unusable.
"""

from __future__ import annotations

import json
from typing import Any

from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.verdicts import (
    JudgeAnswer,
    expectation_verdict,
    validate_citations,
)

INSTRUCTION = """You are judging one expectation about a Cognition session's \
resulting state. The case names a unit and a sequence of recorded wakes; \
this expectation is anchored at one wake and names a state kind.

You are given, as JSON:
- `expectation`: {"polarity": "must"|"must_not", "kind": str, "text": str}
- `diff`: the wake's typed state diff for every kind — created, revised \
(before and after), retired, unchanged entries keyed by entry id
- `state_after`: the unit's state after the wake, same entry ids
- `trajectory`: the session's prompt blocks, model calls, tool calls \
(arguments, result, refusal), usage and stop reason

Answer whether the expectation holds for a `must`, or is violated for a \
`must_not`, from that evidence alone. Answer `met` only when the \
evidence shows it; `not_met` when the evidence shows the opposite; \
`cannot_tell` when the evidence is silent. Cite every entry id your \
answer rests on — ids exist exactly as `kind:id` in `diff`/`state_after`.

Return strict JSON: {"verdict": "met"|"not_met"|"cannot_tell", \
"reason": str, "entry_ids": [str]}."""


def judge_payload(
    expectation: Any, diff: Any, state_after: Any, trajectory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "expectation": {
            "polarity": expectation.polarity,
            "kind": expectation.kind,
            "text": expectation.text,
        },
        "diff": _kind_diff(diff, expectation.kind),
        "state_after": state_after.entries(expectation.kind),
        "trajectory": trajectory,
    }


def _kind_diff(diff: Any, kind: str) -> dict[str, Any]:
    d = diff.kinds.get(kind)
    if d is None:
        return {}
    return {
        status: [
            {k: v for k, v in (("id", e.entry_id), ("before", e.before),
                               ("after", e.after)) if v is not None}
            for e in entries
        ]
        for status, entries in (
            ("created", d.created), ("revised", d.revised),
            ("retired", d.retired), ("unchanged", d.unchanged),
        )
    }


def parse_answer(text: str) -> JudgeAnswer:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise EvidenceError("a judge answer is a JSON object")
    verdict = str(raw.get("verdict", ""))
    ids = raw.get("entry_ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise EvidenceError("a judge's entry_ids must be a list of strings")
    return JudgeAnswer(
        verdict=verdict, reason=str(raw.get("reason", "")),
        entry_ids=tuple(ids),
    )


async def judge_one(
    judges: Any, model: str, payload: dict[str, Any],
) -> JudgeAnswer:
    """One judge's answer; transport or shape failure is `error`, never a
    guess. The served-model header must name the alias's routed model."""
    try:
        text = await judges.call(model, INSTRUCTION, payload)
        identities = judges.served.get(model) or []
        served = (identities[-1].get("x-omniroute-model") if identities else None)
        expected = judges._endpoint(model)[2]
        if served != expected:
            raise EvidenceError(
                f"judge alias {model} was served by {served}, expected {expected}"
            )
        return parse_answer(text)
    except Exception:  # noqa: BLE001 — a judge outage is an ERROR, not a crash
        return JudgeAnswer(verdict="error", reason="judge call failed")


async def judge_expectation(
    judges: Any, models: tuple[str, str], expectation: Any,
    diff: Any, state_after: Any, trajectory: dict[str, Any],
) -> Any:
    """Both judges over one expectation; citations checked against the
    wake's evidence ids; the roll-up stays fail-closed."""
    known = diff.entry_ids() | {
        f"{expectation.kind}:{eid}" for eid in state_after.entries(expectation.kind)
    }
    answers = [
        await judge_one(judges, model, judge_payload(
            expectation, diff, state_after, trajectory,
        ))
        for model in models
    ]
    checked = []
    for answer in answers:
        bogus = validate_citations(answer, known)
        if bogus is not None:
            checked.append(JudgeAnswer(
                verdict="error",
                reason=f"cites a nonexistent entry {bogus}",
                entry_ids=answer.entry_ids,
            ))
        else:
            checked.append(answer)
    return expectation_verdict(tuple(checked))
