"""Two independent judges over one wake's evidence.

Each expectation is judged against a bounded, deterministic digest of the
wake — never the frozen trajectory itself, whose model requests repeat
the whole session context and overflow any context window. The digest
carries the expectation, the wake's typed diff for every kind (created,
revised and retired entries in full; unchanged as counts only), the
state-after of the kind the expectation names, every tool call in order
with its arguments, result and refusal, the session's last assistant
output, its stop reason and tokens. It is assembled through the declared
occasion (`Judges.call` -> `prompt` -> `assemble`) like every model call.

A digest that exceeds the judge's input budget is `ERROR` with reason
`judge input over budget` naming the size — never a silent truncation.
A judge answers `met`, `not_met` or `cannot_tell` with a reason and the
entry ids it rests on; `validate_citations` turns a nonexistent citation
into `ERROR`. The served-model header is verified on every call — a
judge that is not the alias it claims makes the answer unusable.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.trajectory import result_values
from scripts.cognition_loop.verdicts import (
    JudgeAnswer,
    expectation_verdict,
    validate_citations,
)

#: The judge's context budget as a serialized size ceiling: a digest
#: larger than this never reaches the model.
JUDGE_INPUT_MAX_BYTES = 512_000

#: A judge call's stated token allowance for the run's budget: a full
#: digest (~4 bytes per token) plus its answer.
JUDGE_TOKENS_PER_CALL = JUDGE_INPUT_MAX_BYTES // 4 + 4096

INSTRUCTION = """You are judging one expectation about a Cognition session's \
resulting state. The case names a unit and a sequence of recorded wakes; \
this expectation is anchored at one wake and names a state kind.

You are given, as JSON:
- `expectation`: {"polarity": "must"|"must_not", "kind": str, "text": str}
- `diff`: the wake's typed state diff for every kind — created, revised \
(before and after) and retired entries keyed by entry id; `unchanged` is \
a count only
- `state_after`: the unit's state of the expectation's kind after the \
wake, same entry ids
- `tool_calls`: the session's tool calls in order, each with arguments, \
result status and refusal
- `final_output`: the session's last assistant output
- `stop_reason` and `usage`: how the session ended and what it spent

Answer whether the expectation holds for a `must`, or is violated for a \
`must_not`, from that evidence alone. Answer `met` only when the \
evidence shows it; `not_met` when the evidence shows the opposite; \
`cannot_tell` when the evidence is silent. Cite every entry id your \
answer rests on — ids exist exactly as `kind:id` in `diff`/`state_after`.

Return strict JSON: {"verdict": "met"|"not_met"|"cannot_tell", \
"reason": str, "entry_ids": [str]}."""


def _entry(e: Any) -> dict[str, Any]:
    return {k: v for k, v in (("id", e.entry_id), ("before", e.before),
                              ("after", e.after)) if v is not None}


def _full_diff(diff: Any) -> dict[str, Any]:
    """Every kind's diff; unchanged collapses to a count."""
    return {
        kind: {
            "created": [_entry(e) for e in d.created],
            "revised": [_entry(e) for e in d.revised],
            "retired": [_entry(e) for e in d.retired],
            "unchanged": len(d.unchanged),
        }
        for kind, d in sorted(diff.kinds.items())
    }


def _tool_calls(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tool call in order: name, arguments, result status, refusal."""
    calls = []
    for index, call in enumerate(trajectory.get("tool_calls") or []):
        result = call.get("result")
        calls.append({
            "index": index,
            "tool": call.get("tool"),
            "arguments": call.get("arguments"),
            "result": {
                "status": result.get("status") if isinstance(result, dict) else None,
                "content": result_values(result),
            },
            "refusal": call.get("refusal"),
            "error_type": call.get("error_type"),
        })
    return calls


def _final_output(trajectory: dict[str, Any]) -> str | None:
    """The last exchange's assistant output, never every request's input."""
    for request in reversed(trajectory.get("model_calls") or []):
        messages = request.get("messages") if isinstance(request, dict) else None
        for message in reversed(messages or []):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text = "".join(
                part.get("text", "")
                for part in message.get("content") or []
                if isinstance(part, dict)
            )
            if text.strip():
                return text
    return None


def judge_payload(
    expectation: Any, diff: Any, state_after: Any, trajectory: dict[str, Any],
) -> dict[str, Any]:
    """The bounded digest one judge answers against."""
    return {
        "expectation": {
            "polarity": expectation.polarity,
            "kind": expectation.kind,
            "text": expectation.text,
        },
        "diff": _full_diff(diff),
        "state_after": dict(state_after.entries(expectation.kind)),
        "tool_calls": _tool_calls(trajectory),
        "final_output": _final_output(trajectory),
        "stop_reason": trajectory.get("stop_reason"),
        "usage": trajectory.get("usage"),
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
    """One judge's answer; an over-budget digest, transport or shape
    failure is `error`, never a guess. The served-model header must name
    the model the alias routed to; the two judges must differ."""
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode())
    budget = getattr(judges, "input_budget", JUDGE_INPUT_MAX_BYTES)
    if size > budget:
        return JudgeAnswer(
            verdict="error",
            reason=(f"judge input over budget ({size:,} bytes exceeds "
                    f"{budget:,})"),
            model=model,
        )
    try:
        text = await judges.call(model, INSTRUCTION, payload)
        identities = judges.served.get(model) or []
        served = (identities[-1].get("x-omniroute-model") if identities else None)
        if not served:
            raise EvidenceError(
                f"judge alias {model} returned no served-model header")
        return replace(
            parse_answer(text), model=model, served=str(served or ""))
    except Exception as exc:  # noqa: BLE001 — a judge outage is an ERROR, not a crash
        return JudgeAnswer(
            verdict="error",
            reason=f"judge call failed: {type(exc).__name__}: {exc}",
            model=model,
        )


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
                model=answer.model, served=answer.served,
            ))
        else:
            checked.append(answer)
    served = [a.served for a in checked if a.verdict != "error"]
    if len(served) == 2 and served[0] == served[1]:
        checked = [replace(a, verdict="error",
                           reason=f"both judges were served by {a.served}; "
                                  "they are not independent") for a in checked]
    return expectation_verdict(tuple(checked))
