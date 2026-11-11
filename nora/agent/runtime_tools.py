from __future__ import annotations

from typing import Any

from nora.agent.arithmetic import evaluate as evaluate_expression
from nora.agent.clock import local_now, state_time
from nora.agent.security import scan_identifiers
from nora.agent.surface import LOAD_TOOLS_UNAVAILABLE_NOTE, load_onto
from nora.agent.toolkit.results import _record
from nora.agent.toolkit.spec import _spec


def _make_now(deps):
    clock = deps.clock
    metrics = deps.metrics

    async def now() -> str:
        """The current date and time, in this deployment's time zone. Call this
        before answering anything that depends on when it is.

        That includes how long until something or since a message was sent,
        whether a place is still open,
        and what "tonight" or "yesterday" means. Returns the weekday, date, time,
        timezone and the zone's city.
        """
        # The SAME clock read and formatter as the turn's clock line
        # (`nora.agent.clock`), so a mid-turn answer states exactly its facts. Uses
        # the injected `clock` when there is one, read the way the fire path reads it
        # (`self._clock().astimezone()`): an eval sandbox freezes the turn at a past
        # instant, and a `now` read from the wall would disagree with it. In
        # production both are the wall.
        moment = clock().astimezone() if clock is not None else local_now()
        # The clock reading stays in the line: it is a fact about Nora's own host,
        # not about the group, and it tells an operator the container's timezone is
        # what they think it is.
        _record(metrics, "now", "ok", at=moment.isoformat(timespec="seconds"))
        return state_time(moment)

    return now


def _make_calculate(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics

    async def calculate(expression: str) -> dict:
        """Work out a number exactly. Use this for ALL arithmetic, however easy
        it looks: call it whenever a number needs computing.

        Never state a computed number that did not come from here, unless
        another tool already printed it (a command on your computer): report
        that one as it is, without recomputing it. It checks the arithmetic,
        not the question: divide by 5 when six people ate and you get an exact answer
        to the wrong sum, so count first.

        Returns `status`: ok or refused, with a `note` on how to report it.

        Args:
            expression: The whole calculation as one expression: numbers,
                brackets, and + - * / // % **. No names, functions, units or
                currency signs.
        """
        # The evaluator is pure and documented never to raise (see
        # `nora.agent.arithmetic`), so there is no try/except here and no
        # `unavailable` status: it holds none of this tag's collaborators, so
        # there is nothing that can be unreachable. A bare `except` would only
        # be able to invent a state that cannot happen.
        out = evaluate_expression(expression)
        # Same hole `_asked_about` exists for, in the one other place a
        # MODEL-SUPPLIED string is handed straight back. `+85290000101 + 0` is
        # real arithmetic — a unary plus on an integer — so a model can put a
        # phone number here exactly as it does in `lookup_person(name=...)`,
        # and an identifier inside a tool result costs the whole reply at R-18.
        # Only the ECHO is recoverable: the RESULT of that expression is
        # `85290000101`, which is a number and nothing distinguishes it from
        # arithmetic, here or at egress.
        if scan_identifiers(expression, content_checks=content_checks) is not None:
            out.pop("expression", None)
        # ADR 0015 §3: how to report the answer rides in the result. A refusal
        # already says what to fix (`arithmetic`'s own sentence); the tool adds the
        # retry rule. An `ok` gets a note only when it has a `split`.
        if out["status"] == "refused":
            out["note"] = (
                f"{out.get('note', '')} Fix the expression and call calculate "
                "again; never answer from your own arithmetic."
            ).strip()
        elif "split" in out:
            out["note"] = (
                "The division does not come out evenly. Say the leftover out loud, "
                "and use settles_as for who pays the extra cent; never drop it or "
                "multiply it back yourself."
            )
        # COUNTS and status, never the expression (`_log_call`'s rule):
        # `data/nora.log` is a worse place for a model-supplied string than a tool
        # result, because nothing scans it on the way out. The expression still
        # reaches the TRACE, in the result above (DEC-31): `1485/6` is checkable,
        # `247.5` is not.
        _record(
            metrics,
            "calculate",
            out["status"],
            chars=len(expression or ""),
            split="split" in out,
        )
        return out

    return calculate


def _make_load_tools(deps):
    shelf = deps.shelf

    async def load_tools(names: list[str], tool_context: Any) -> dict:
        """Load catalogue tools by name so you can call them. Call this before
        using any tool your catalogue lists without a *.

        Tools marked * are already loaded. Ask for every tool you expect to need
        in one call (up to 4), then call them as usual on your next step.

        Returns `status`: ok, nothing_found or unavailable, with the names
        `loaded`, `already_loaded`, `unknown` and `refused`, and a `note` on how
        to report it.

        Args:
            names: Tool names exactly as your catalogue spells them.
        """
        if shelf is None:
            return {
                "status": "unavailable",
                "loaded": [],
                "already_loaded": [],
                "unknown": [],
                "refused": [],
                "note": LOAD_TOOLS_UNAVAILABLE_NOTE,
            }
        return load_onto(getattr(tool_context, "agent", None), shelf, names)

    return load_tools


def build(deps):
    now = _make_now(deps)
    calculate = _make_calculate(deps)
    load_tools = _make_load_tools(deps)
    return (
        _spec("now", now),
        _spec("calculate", calculate),
        _spec("load_tools", load_tools),
    )
