"""The S2 routing grader — was the RIGHT tool reached, graded on what EXECUTED.

`ToolRouteGrader` reads the tool spans (`NORA_TOOLS`, `NORA_TOOL_INPUTS`,
`NORA_TOOL_RESULTS`), never what the reply claims; its three verdicts are
pinned in `tests/unit/test_tool_route_grader.py`. It lives apart from
`evals.backbone` because it is the grader a case's metadata drives, not part
of the subject spine.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import EvaluationData, EvaluationOutput

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ERROR,
    NORA_SHOWN_NOW,
    NORA_TOOL_INPUTS,
    NORA_TOOL_RESULTS,
    NORA_TOOLS,
    PASS,
    _texts_in,
    date_shown,
    state_of,
    states_the_date,
    this_weekday,
)


def _arguments_in(obj: Any) -> Iterator[dict[str, Any]]:
    """Every `arguments` dict inside a recorded tool-call input, depth first."""
    if isinstance(obj, dict):
        if isinstance(obj.get("arguments"), dict):
            yield obj["arguments"]
        for value in obj.values():
            yield from _arguments_in(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _arguments_in(value)


def _last_call_args(raw: str) -> dict[str, Any] | None:
    """The last call's `arguments` from a tool input text, or None."""
    for chunk in reversed(raw.split("\n")):
        try:
            obj = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if (found := next(_arguments_in(obj), None)) is not None:
            return found
    return None


#: How much of a call's input a verdict quotes, PASS or FAIL: a reason that
#: names only what was expected cannot be read. 900 characters, because a
#: poster prompt states its date near the end.
_SEEN_CHARS = 900


def _seen(texts: list[str]) -> str:
    """What the call actually carried, quoted for a FAIL reason."""
    if not texts:
        return "(no call)"
    return " | ".join(f"«{t[:_SEEN_CHARS]}»" for t in texts)


class ToolRouteGrader(Evaluator):
    """Did the turn reach for the RIGHT tool?

    `metadata.tools` names the acceptable tools — any one of them executing
    is a PASS, because «what is the weather» may honestly be answered by
    `weather` and a case should not pretend there is one right verb when
    there are two. `metadata.not_tools` names the confusable sibling that
    must NOT run — the half of a confusable pair that is the actual error
    (`where_been` for a «where is she NOW» question). Both are read from what
    EXECUTED (`NORA_TOOLS`), never from what the reply says.

    `metadata.required_tools` optionally names tools that must ALL execute,
    in addition to the ANY choice in `metadata.tools`.

    `load_tools` is plumbing and is never the answer to anything: it is
    dropped before either check, so a deferred turn and a resident one are
    graded on the same question.

    `metadata.before` is an ORDER criterion: `{instagram_read: [media_fetch]}`
    says `media_fetch` must not run before the first `instagram_read`, and
    may run after it. Some errors are an order, not a choice: calling `media_fetch` on a
    shared reel's cover, reading one frame and stopping, is a defect that a case
    whose `media_fetch` finds nothing then points her at the link, so without
    the order it grades PASS. `NORA_TOOLS` is in
    start order (`_tools_called`), which is what makes this readable at all. A
    `before` that is not a mapping of tool to a list of tools is an ERROR: a
    criterion the grader cannot read is not one it applied.

    ERROR, never FAIL, when the case did not run or recorded no tools state:
    a broken instrument is not a wrong answer.

    **What the call carried**, read from `NORA_TOOL_INPUTS` and
    checked only once routing has passed, each keyed by tool name:

    - `args_include: {tool: [text, ...]}`: some call of `tool` carries every
      text (case-insensitive), e.g. her canonical picture's sha on «draw
      yourself».
    - `args_exclude: {tool: [text, ...]}`: no call of `tool` carries any.
    - `args_date: {tool: <weekday>}`: some call of `tool` writes out the
      date of «this <weekday>», resolved against the clock line she was
      SHOWN (`NORA_SHOWN_NOW`).
    - `args_last: {tool: {arg: expected}}`: the LAST call of `tool` carries
      each argument as expected — booleans compare by truthiness (a missing
      argument is falsy), anything else by equality. `tools` alone cannot
      see a turn that ended on `send_reply(more=true)` or a `heads_up`: the
      send executed, yet the delivery it promised never ran. A named tool
      that never ran FAILs; an unreadable last input is ERROR.

    Inputs not captured, or no clock line for `args_date`, is ERROR: the
    instrument could not see, which is not the same as she did not pass it.
    """

    _PLUMBING = frozenset({"load_tools"})

    def _args(self, data: EvaluationData, md: Mapping[str, Any]) -> EvaluationOutput | None:
        """The argument checks, or None when the case states none."""
        include = md.get("args_include") or {}
        exclude = md.get("args_exclude") or {}
        dated = md.get("args_date") or {}
        last = md.get("args_last") or {}
        if not (include or exclude or dated or last):
            return None
        if not isinstance(last, dict) or not all(
            isinstance(v, dict) for v in last.values()):
            return self._out(
                ERROR, 0.0, f"metadata.args_last must map a tool to args, got {last!r}")
        inputs = state_of(data, NORA_TOOL_INPUTS)
        if inputs is None:
            return self._out(ERROR, 0.0, "no tool inputs recorded")
        calls: dict[str, list[str]] = {}
        for name, text in inputs:
            calls.setdefault(str(name), []).append(str(text))
        for tool in {*include, *exclude, *dated, *last}:
            if tool in calls and not any(calls[tool]):
                return self._out(ERROR, 0.0, f"{tool} ran but its input was not captured")
        for tool, needles in exclude.items():
            for text in calls.get(tool, ()):
                hit = [n for n in needles if str(n).lower() in text.lower()]
                if hit:
                    return self._out(
                        FAIL, 0.0, f"{tool} was called with {hit}: {_seen([text])}")
        for tool, needles in include.items():
            if not any(all(str(n).lower() in text.lower() for n in needles)
                       for text in calls.get(tool, ())):
                return self._out(
                    FAIL, 0.0,
                    f"no {tool} call carried all of {list(needles)}: {_seen(calls.get(tool, []))}",
                )
        for tool, expected in last.items():
            if tool not in calls:
                return self._out(FAIL, 0.0, f"args_last names {tool}, which never ran")
            args = _last_call_args(calls[tool][-1])
            if args is None:
                return self._out(
                    ERROR, 0.0, f"the last {tool} input was not captured or is unreadable")
            for key, want in expected.items():
                got = args.get(key)
                if (bool(got) == want) if isinstance(want, bool) else (got == want):
                    continue
                return self._out(
                    FAIL, 0.0,
                    f"the last {tool} call carried {key}={got!r}, expected {want!r}")
        if dated:
            clock = state_of(data, NORA_SHOWN_NOW)
            today = date_shown(clock) if clock else None
            if today is None:
                return self._out(ERROR, 0.0, "no clock line was shown, so no date to expect")
            for tool, weekday in dated.items():
                day = this_weekday(today, str(weekday))
                if not any(states_the_date(text, day) for text in calls.get(tool, ())):
                    return self._out(
                        FAIL, 0.0,
                        f"no {tool} call wrote out {day:%A %-d %B %Y} (from {clock!r}): "
                        f"{_seen(calls.get(tool, []))}",
                    )
        carried = [t for tool in {*include, *exclude, *dated, *last} for t in calls.get(tool, [])]
        return self._out(PASS, 1.0, f"the call carried what the case asks: {_seen(carried)}")

    def evaluate(self, data: EvaluationData) -> list[EvaluationOutput]:
        error = state_of(data, NORA_ERROR)
        called = state_of(data, NORA_TOOLS)
        md = data.metadata or {}
        want = frozenset(md.get("tools") or ())
        avoid = frozenset(md.get("not_tools") or ())
        required = frozenset(md.get("required_tools") or ())
        before = md.get("before") or {}
        if error:
            return [self._out(ERROR, 0.0, f"the case did not run: {error}")]
        if called is None:
            return [self._out(ERROR, 0.0, "no tools state recorded")]
        if not want:
            return [self._out(ERROR, 0.0, "case names no metadata.tools to expect")]
        if not isinstance(before, dict) or not all(
            isinstance(v, (list, tuple)) for v in before.values()
        ):
            return [self._out(
                ERROR, 0.0, f"metadata.before must map a tool to a list, got {before!r}",
            )]
        ran = [name for name in called if name not in self._PLUMBING]
        wrong = sorted(avoid & set(ran))
        if wrong:
            return [self._out(FAIL, 0.0, f"called the sibling {wrong}; ran {ran}")]
        missing = sorted(required - set(ran))
        if missing:
            return [self._out(FAIL, 0.0, f"missing required tools {missing}; ran {ran}")]
        for first, not_before in before.items():
            if first not in ran:
                continue
            early = sorted(set(ran[:ran.index(first)]) & set(not_before))
            if early:
                return [self._out(
                    FAIL, 0.0, f"called {early} before {first}; ran {ran}",
                )]
        if want & set(ran):
            carried = self._args(data, md)
            if carried is not None and carried.label != PASS:
                return [carried]
            if md.get("journey"):
                verified = self._journey(data, ran)
                if verified is not None:
                    return [verified]
            routed = f"called {sorted(want & set(ran))}; ran {ran}"
            if carried is not None:
                routed = f"{routed}; {carried.reason}"
            return [self._out(PASS, 1.0, routed)]
        return [self._out(FAIL, 0.0, f"expected one of {sorted(want)}; ran {ran}")]

    async def evaluate_async(self, data: EvaluationData) -> list[EvaluationOutput]:
        return self.evaluate(data)

    def _journey(self, data: EvaluationData, ran: list[str]) -> EvaluationOutput | None:
        """Correlate successful calls to one owned capture and its exported bytes."""
        results = state_of(data, NORA_TOOL_RESULTS)
        inputs = state_of(data, NORA_TOOL_INPUTS)
        if results is None or inputs is None:
            return self._out(ERROR, 0.0, "journey inputs/results unavailable")

        tools = ("computer_browse", "computer_capture", "computer_export")
        ordered_inputs = [(name, text) for name, text in inputs if name in tools]
        ordered_results = [(name, text) for name, text in results if name in tools]
        expected = [name for name in ran if name in tools]
        if ([name for name, _ in ordered_inputs] != expected
                or [name for name, _ in ordered_results] != expected):
            return self._out(ERROR, 0.0, "journey call inputs/results do not align")
        successful: dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]] = {
            tool: [] for tool in tools
        }
        # Both extractors retain every tool span in start order, including missing evidence.
        for index, ((name, raw_input), (_, raw_result)) in enumerate(
            zip(ordered_inputs, ordered_results, strict=True)
        ):
            try:
                args = json.loads(raw_input)
            except (ValueError, TypeError):
                args = None
            if not isinstance(args, dict):
                return self._out(ERROR, 0.0, f"{name} input unavailable")
            result = None
            for fragment in _texts_in(raw_result):
                try:
                    value = json.loads(fragment)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict) and isinstance(value.get("status"), str):
                    result = value
                    break
            if result is None or not result["status"]:
                return self._out(ERROR, 0.0, f"{name} result unavailable")
            if result["status"] == "ok":
                successful[name].append((index, args, result))

        for browse_index, _, browse in successful["computer_browse"]:
            handle, target = browse.get("task"), browse.get("tab")
            if not handle or not target:
                continue
            for capture_index, args, capture in successful["computer_capture"]:
                if capture_index <= browse_index:
                    continue
                digest = capture.get("sha256")
                if (args.get("task") != handle or capture.get("tab") != target
                        or not capture.get("path") or not isinstance(digest, str)
                        or len(digest) != 64
                        or any(c not in "0123456789abcdef" for c in digest)):
                    continue
                for export_index, args, exported in successful["computer_export"]:
                    if export_index <= capture_index:
                        continue
                    if (args.get("path") != capture["path"]
                            or exported.get("sha256") != digest):
                        continue
                    destination = args.get("to", "chat")
                    receipt = ((destination == "keep" and exported.get("kept") is True)
                               or (destination == "chat" and exported.get("sent") is True)
                               or (destination == "link" and exported.get("artifact_id")
                                   and exported.get("url")))
                    if receipt:
                        return None
        return self._out(FAIL, 0.0,
                         "no successful same-task/target capture and matching export receipt")

    @staticmethod
    def _out(verdict: str, score: float, reason: str) -> EvaluationOutput:
        return EvaluationOutput(
            score=score, test_pass=(verdict == PASS), reason=reason, label=verdict,
        )
