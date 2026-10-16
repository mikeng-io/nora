"""What the standing judge reads, and what `--print-input` prints.

Two renders of one real turn, and the difference between them is the point.

- `evidence` is rendered from the RECORD — the prior as the chain holds it, the
  turn's `tag_trace` row with every tool result at (nearly) full size. It is the
  same for both arms of a comparison. A judge handed the fold's own input would
  grade the master arm against a 600-character fragment of a search and the
  branch arm against 4000 characters of it, and «the finding was carried» would
  then mean two different things on two arms.
- `shown` is what the fold's model call WAS given, verbatim. It differs between
  arms by design, and printing it is how the operator proves the arms differ
  before any model is called.

Nothing here is written into the tree: the text is the room's, read from the
clone at run time and handed to the judge model the way every judged corpus
hands it a transcript.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from typing import Any

from nora.domain.external_files import render_file_links

#: How much of one tool result the JUDGE reads. Well above the fold's own
#: bound: the judge has to know what a result held to say whether the position
#: carried any of it. Past this the result is cut and says so.
_RESULT_CHARS = 16_000


def _local(ms: int, tz: tzinfo) -> str:
    """An absolute time in the DEPLOYMENT's zone, WITH the date, weekday and
    offset — the judge has to place a relative clause («tomorrow», «tonight»)
    on a calendar, so the zone the room lives in is part of the fact, and the
    machine the suite runs on is not the room. Date-first like the fold's own
    `at` (`%Y-%m-%d %H:%M`), so the two read the same way."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(tz).strftime(
        "%Y-%m-%d %H:%M (%a) UTC%z"
    )


def _hours(ms: int) -> str:
    hours = ms / 3_600_000
    return f"{hours * 60:.0f} minutes" if hours < 1 else f"{hours:.1f} hours"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]} …[cut at {limit} of {len(text)} characters]"


def _steps(messages: Iterable[Any]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "?")
        for block in message.get("content") or ():
            if not isinstance(block, Mapping):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                # The first user block is the turn's own prompt scaffolding —
                # the tag, the prefill — not a step of hers.
                who = "her own text" if role == "assistant" else "the turn's input"
                lines.append(f"[{who}] {_clip(text, _RESULT_CHARS)}")
                continue
            reasoning = block.get("reasoningContent")
            if isinstance(reasoning, Mapping):
                inner = reasoning.get("reasoningText")
                raw = inner.get("text") if isinstance(inner, Mapping) else None
                if isinstance(raw, str) and raw.strip():
                    lines.append(f"[her private reasoning] {_clip(raw, _RESULT_CHARS)}")
                continue
            use = block.get("toolUse")
            if isinstance(use, Mapping):
                args = json.dumps(use.get("input"), ensure_ascii=False)
                lines.append(f"[she called {use.get('name')}] {_clip(args, 2000)}")
                continue
            result = block.get("toolResult")
            if isinstance(result, Mapping):
                body = result.get("content")
                if isinstance(body, list):
                    body = "\n".join(
                        str(part.get("text", part)) if isinstance(part, Mapping) else str(part)
                        for part in body
                    )
                lines.append(f"[tool result] {_clip(str(body), _RESULT_CHARS)}")
    return lines


def render_evidence(turn: Any, *, display_name: str, tz: tzinfo) -> str:
    """The prior, the rows the fold read, the turn's steps — from the record.

    `turn` is a `nora.subject.standing_fold.FoldTurn`. The rows are its `seen`:
    the watermark batch at production's fold instant plus the turn's own —
    which is where a `recovered` turn's reply is, written after the turn's
    record closed.
    """
    trace = turn.trace
    prior = turn.prior
    if getattr(turn, "counterfactual_prior", False):
        prior_block = (
            "## Her position BEFORE this turn\n"
            "(none given — COUNTERFACTUAL. This turn predates the standing "
            "chain, so the position she held then is unrecoverable, and the "
            "fold was told it had no position yet. Grade the new position as a "
            "first position.)\n"
        )
    elif prior is None:
        prior_block = "## Her position BEFORE this turn\n(none held)\n"
    else:
        prior_block = (
            "## Her position BEFORE this turn\n"
            f"Written {_local(prior.updated_at, tz)} — "
            f"{_hours(trace.created_at - prior.updated_at)} before the turn.\n"
            f"```\n{prior.body.strip()}\n```\n"
        )
    rows = []
    for row in turn.seen or trace.transcript:
        who = display_name if row.is_nora else (row.person_name or row.sender_name or "?")
        # The handle, never the raw file link: the judge is a model too, and
        # grades against what the fold was shown.
        rows.append(f"[{_local(row.ts, tz)}] {who}: {render_file_links(row.body or '').strip()}")
    return (
        f"{prior_block}\n"
        f"## The turn\nAt {_local(trace.created_at, tz)}. Outcome: {trace.outcome}.\n\n"
        "### The room's messages the fold read, oldest first "
        "(the stretch since her last position, and the turn's own)\n"
        + "\n".join(rows)
        + "\n\n### Her steps on the turn: what she called, what came back, what she wrote\n"
        + "\n".join(_steps(trace.messages))
        + "\n"
    )


def render_shown(calls: Sequence[Any]) -> str:
    """Every call the fold made, as the model client received it."""
    parts = []
    for i, call in enumerate(calls, 1):
        parts.append(
            f"===== call {i}: {call.call or '(unlabelled)'} =====\n"
            f"----- system -----\n{call.system}\n"
            f"----- user -----\n{call.prompt}\n"
        )
    return "\n".join(parts)


def _field(item: Any, name: str) -> Any:
    """`item.<name>` for a `WorkingSetFinding`, or `item[name]` for the plain
    dict a `WorkingSetFinding` round-trips to once a report's `actual_
    environment_state` has gone through `EvaluationData.model_dump()`
    (`evals.__main__._print_positions`'s own read path) — one function so
    both callers render the same way."""
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name)


def render_working_set(items: Sequence[Any]) -> str:
    """`Outcome.working_set` (`evals.rebuilt.domain.WorkingSetFinding`, a
    plain dict with the same three keys, or anything else with the same three
    fields), rendered for a reader who must not mistake it for the position.

    `""` for an empty or absent working set — never a header over nothing.
    A section that always prints, even empty, is how a fold that never tried
    and a fold with a genuinely empty working set end up reading the same to
    whoever is grading; `_build_position_prompt` and `_print_positions` both
    rely on this to OMIT the section rather than print a labelled blank.

    `found_at` is left out on purpose: `WorkingSetItem`'s own docstring says
    it is the write path's clock stamp, not a property a reader grades
    against, and neither a case's `expected_output` nor the judge's rubric
    states a time — only `what` (is it the kind asked for), `source` (where
    it came from) and `bears_on` (what open plan it is for) do.
    """
    lines = []
    for item in items:
        what, source, bears_on = (_field(item, n) for n in ("what", "source", "bears_on"))
        suffix = f"; bears on: {bears_on}" if bears_on else ""
        lines.append(f"- {what} (source: {source}{suffix})")
    return "\n".join(lines)
