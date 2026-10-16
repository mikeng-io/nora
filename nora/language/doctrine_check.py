"""`DoctrineCheck` — one instrument call: does this lesson loosen or contradict
a LAW rule?

It is handed the lesson and the law subset that reaches a unit's turns
(`LAW_SURFACES`, DEC-291), asks a model that is NOT the Lessons session, and
returns a `CheckVerdict` read strictly by `parse_verdict`. Its instruction is
`nora/dreamer/objectives/doctrine-check.md`, read per call (bind-mounted, so an
edit reaches the next check without a rebuild). A failed call, a timeout, a
cut-off or an answer that does not parse is `error`: the caller refuses the
lesson and writes no tension row.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from nora.domain.blocks import BlockKey, ContextBlock, assemble, render_elements
from nora.domain.doctrine_check import (
    CHECK_OUTCOMES,
    LAW_SURFACES,
    CheckVerdict,
    LawRule,
    parse_verdict,
)
from nora.domain.prompt import escape
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = ["CHECK_OBJECTIVE", "DoctrineCheck", "render_check_input"]

#: The instruction's objective name (`nora/dreamer/objectives/`).
CHECK_OBJECTIVE = "doctrine-check"

#: What the check reads, in order.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("law_rules", 10, "turn"),
    ("lesson_to_check", 20, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {name: (order, key) for name, order, key in _BLOCKS}


class _Completion(Protocol):
    @property
    def content(self) -> str: ...

    @property
    def finish_reason(self) -> str: ...


class _Client(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(self, *, system: str, prompt: str) -> _Completion: ...


class _Rule(Protocol):
    """`nora.doctrine.loader.Rule` (frozen), read-only here."""

    @property
    def file(self) -> str: ...

    @property
    def line(self) -> int: ...

    @property
    def text(self) -> str: ...


def render_check_input(*, lesson: str, rules: Sequence[LawRule]) -> str:
    """The numbered law, then the lesson. A rule's number is its position here,
    which is what the answer names."""
    return assemble([
        ContextBlock(name="law_rules", order=_ROW["law_rules"][0],
                     key=_ROW["law_rules"][1], body=render_elements(
            list(rules), tag="rule",
            attrs=lambda r, i: (("n", str(i)), ("file", r.file)),
            body=lambda r: r.text,
        )),
        # ESCAPED: the lesson is model-written from members' messages, and a
        # `</lesson_to_check>` in it would otherwise close the block and let
        # it forge a verdict. The law is escaped by
        # `render_elements`.
        ContextBlock(name="lesson_to_check", order=_ROW["lesson_to_check"][0],
                     key=_ROW["lesson_to_check"][1], body=escape(lesson)),
    ], sep="\n\n")


class DoctrineCheck:
    def __init__(
        self, *, client: _Client, law: Callable[[], Sequence[_Rule]],
        instruction: Callable[[], str], timeout_seconds: float,
        metrics: Metrics | None = None,
    ) -> None:
        """`law` is `DoctrineStore.law_rules`; only `LAW_SURFACES` are read.
        `instruction` returns the objective body."""
        self._client = client
        self._law = law
        self._instruction = instruction
        self._timeout = timeout_seconds
        self._metrics = metrics

    @property
    def model(self) -> str:
        return self._client.model

    def rules(self) -> tuple[LawRule, ...]:
        return tuple(
            LawRule(file=r.file, line=r.line, text=r.text) for r in self._law()
            if r.file.startswith(LAW_SURFACES)
        )

    async def judge(self, lesson: str) -> CheckVerdict:
        verdict = await self._judge(lesson)
        if verdict.outcome == "error":
            log.warning("doctrine check: error (%s); the lesson is refused", verdict.why)
        self._count(verdict.outcome)
        return verdict

    async def _judge(self, lesson: str) -> CheckVerdict:
        try:
            rules = self.rules()
            system = self._instruction().strip()
        except Exception as exc:  # a doctrine that will not classify, a missing file
            log.exception("doctrine check: could not read the law or its instruction")
            return CheckVerdict("error", why=type(exc).__name__)
        if not rules or not system:
            return CheckVerdict("error", why="no law or no instruction to check against")
        try:
            got = await asyncio.wait_for(
                self._client.complete(
                    system=system, prompt=render_check_input(lesson=lesson, rules=rules)),
                timeout=self._timeout,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return CheckVerdict("error", why="timeout")
        except Exception as exc:
            log.warning("doctrine check: the call failed: %s", exc, exc_info=True)
            return CheckVerdict("error", why=type(exc).__name__)
        return parse_verdict(
            content=(got.content or "").strip(), finish_reason=got.finish_reason or "",
            rules=rules,
        )

    def _count(self, outcome: str) -> None:
        if outcome not in CHECK_OUTCOMES:
            raise ValueError(f"nora_doctrine_check_total{{{outcome}}} is outside its vocabulary")
        if self._metrics is not None:
            self._metrics.inc("nora_doctrine_check_total", outcome=outcome)
