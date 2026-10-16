"""The speech formatter: a late, lite call that fits a spoken reply to the TTS
voice, between her words being final and MiniMax reading them.

**Why it is not Nora's job.** Her doctrine is written chat, and her reply
text is recorded: the audit row, the Honcho feed and the notes carry it
exactly as she wrote it. Pause markers and interjection tags are for the ear.
Putting them in her reply would put `<#0.40#>` into every store that reads
garnish a cheap model never chooses (`nora.agent.
reaction`'s docstring). So the formatter runs where the reaction picker runs,
after her reply is written, and it sees only the copy that is about to be
spoken. Nothing it returns is recorded anywhere but the audio.

**It may add, never change.** It may add pauses and interjection tags from
MiniMax's closed list, and the deterministic half (`nora.domain.
speech_markup.normalise`) removes what must not be read: emoji, markdown,
stray symbols. Whether the model kept every word is not trusted to the model:
`speech_markup.check` compares the letters and digits of its output, markup
removed, against her normalised reply, and any difference, any malformed
pause, any tag outside the list, a timeout, an error or an empty answer
speaks her normalised reply instead. A spoken reply is never delayed past
`timeout_seconds` and never dropped over formatting.

**A reply too short to pace skips the model** (`min_chars`): a pause needs a
stretch of speech on both sides, and most voice replies are a single short
clause.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from nora.domain.blocks import BlockKey, ContextBlock, assemble
from nora.domain.prompt import escape
from nora.domain.speech_markup import (
    FormatOutcome,
    adds_markup,
    check,
    normalise,
    spoken_skeleton,
)

log = logging.getLogger("nora.agent.speech_format")

AUDIENCE = "speech_format"

#: The one block the formatter reads. Declared as data: what this call carries
#: is a table, not a trace through code.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("spoken_reply", 10, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


class Completion(Protocol):
    @property
    def content(self) -> str: ...

    @property
    def finish_reason(self) -> str: ...


class ModelClient(Protocol):
    """One text completion; the composition root's `_DebriefModelClient`."""

    async def complete(self, *, system: str, prompt: str) -> Completion: ...


class PromptSource(Protocol):
    def get(self, *, applies_to: str = ...) -> Any: ...


class Recorder(Protocol):
    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...

    def observe(self, name: str, value: float, **labels: object) -> None: ...


def render_input(base: str) -> str:
    """The formatter's whole input: her normalised reply, escaped, in one
    described block. Escaped so a reply that contains a closing tag cannot
    end the block early (R-13b)."""
    order, key = _ROW["spoken_reply"]
    return assemble((
        ContextBlock(name="spoken_reply", order=order, key=key, body=escape(base)),
    )).rstrip("\n")


class SpeechFormatter:
    """Never raises and never blocks past `timeout_seconds`. Returns the text
    to synthesize: the model's when it passed the check, otherwise her reply
    normalised."""

    def __init__(
        self, *, client: ModelClient, prompts: PromptSource, metrics: Recorder,
        timeout_seconds: float, min_chars: int, max_chars: int,
    ) -> None:
        self._client = client
        self._prompts = prompts
        self._metrics = metrics
        self._timeout_seconds = timeout_seconds
        self._min_chars = min_chars
        self._max_chars = max_chars

    def _count(self, outcome: FormatOutcome) -> None:
        self._metrics.inc("nora_speech_format_total", outcome=outcome)

    async def format(self, *, text: str) -> str:
        base = normalise(text)
        size = len(spoken_skeleton(base))
        if size < self._min_chars or size > self._max_chars:
            self._count("short" if size < self._min_chars else "long")
            return base
        started = time.monotonic()
        try:
            outcome, spoken = await asyncio.wait_for(
                self._format(base), timeout=self._timeout_seconds,
            )
        except TimeoutError:
            outcome, spoken = "timeout", base
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("speech formatting failed; speaking the reply as written")
            outcome, spoken = "error", base
        self._metrics.observe(
            "nora_speech_format_seconds", time.monotonic() - started,
        )
        self._count(outcome)
        return spoken

    async def _format(self, base: str) -> tuple[FormatOutcome, str]:
        instruction = (self._prompts.get(applies_to=AUDIENCE).doctrine_text or "").strip()
        if not instruction:
            return "no_instruction", base
        got = await self._client.complete(system=instruction, prompt=render_input(base))
        if got.finish_reason == "length":
            return "truncated", base
        candidate = (got.content or "").strip()
        reason = check(base, candidate)
        if reason is not None:
            log.info("speech formatter output refused (%s); speaking the reply as written", reason)
            return reason, base
        if not adds_markup(base, candidate):
            return "unchanged", base
        return "formatted", candidate


__all__ = ["AUDIENCE", "SpeechFormatter", "render_input"]
