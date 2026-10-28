"""Step one's tool-local egress screen, paid lookup caps and fetch provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolSpec, ToolUse

from nora.agent.dream_screen import text_offence
from nora.agent.egress import _canonical
from nora.agent.fetch_watch import GraphFetched, _result_of
from nora.domain.comprehend import EGRESS_REASONS
from nora.domain.memory_graph import Source

READ_NAMES = frozenset(
    {
        "memory_search",
        "memory_read",
        "media_fetch",
        "instagram_read",
        "instagram_search",
        "x_read",
        "x_search",
        "web_search",
        "web_fetch",
    }
)


def egress_reason(text: str, private_names: Sequence[str]) -> str | None:
    # Decode before checking; percent-encoding is not an egress permission.
    while True:
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    reason = text_offence(text, content_checks=True)
    if reason:
        return reason
    folded = _canonical(text).casefold()
    for name in private_names:
        name = _canonical(name).strip().casefold()
        if not name:
            continue
        # Unicode names match as substrings. Latin names use word boundaries
        # so a private name such as Al does not block a venue called Palace.
        pattern = re.escape(name).replace(r"\ ", r"[\s+]+")
        if name.isascii():
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        if re.search(pattern, folded):
            return "private_person"
    return None


@dataclass
class LookupState:
    fetched: GraphFetched
    unit: str
    session_id: int
    records: Any
    metrics: Any
    private_names: tuple[str, ...]
    persons: tuple[str, ...] = ()
    limits: dict[str, int] = field(default_factory=lambda: {"web_search": 3, "web_fetch": 3})
    used: dict[str, int] = field(default_factory=dict)


class ComprehendRead(AgentTool):
    def __init__(self, inner: Any, state: LookupState) -> None:
        super().__init__()
        self._inner = inner
        self._state = state

    @property
    def tool_name(self) -> str:
        return str(self._inner.tool_name)

    @property
    def tool_spec(self) -> ToolSpec:
        return self._inner.tool_spec

    @property
    def tool_type(self) -> str:
        return "python"

    @staticmethod
    def _event(use: ToolUse, answer: dict[str, Any]) -> ToolResultEvent:
        envelope: dict[str, Any] = {"toolUseId": use["toolUseId"], "content": [{"json": answer}]}
        envelope["status"] = "success"
        return ToolResultEvent(envelope)  # type: ignore[arg-type]

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        state, name = self._state, self.tool_name
        args = tool_use.get("input") or {}
        if name in state.limits:
            value = args.get("query" if name == "web_search" else "url")
            reason = (
                "bad_arguments"
                if not isinstance(value, str)
                else egress_reason(value, state.private_names)
            )
            if reason is None and state.used.get(name, 0) >= state.limits[name]:
                reason = "lookup_cap"
            if reason:
                if reason not in EGRESS_REASONS:
                    raise ValueError("unknown egress refusal")
                await state.records.record_refusal(state.session_id, "egress", reason)
                state.metrics.inc("nora_comprehend_egress_refusals_total", reason=reason)
                yield self._event(
                    tool_use,
                    {
                        "status": "refused",
                        "reason": reason,
                        "note": "This lookup was refused before egress.",
                    },
                )
                return
            # No await between checking and increment: parallel tool calls share the cap.
            state.used[name] = state.used.get(name, 0) + 1
            state.metrics.inc("nora_comprehend_web_lookups_total", tool=name)
        async for event in self._inner.stream(tool_use, invocation_state, **kwargs):
            answer = _result_of(event)
            if name in ("memory_search", "memory_read"):
                # A graph preview includes notebook judgements. It is not a new
                # Record source, and cannot launder those judgements into evidence.
                yield event
                continue
            if isinstance(answer, dict) and answer.get("status") == "ok":
                if name == "web_fetch":
                    # Preserve exactly the bounded text the tool actually returns.
                    text = answer.get("text") or answer.get("content")
                    if not isinstance(text, str):
                        # A changed provider shape must never create false provenance.
                        raise ValueError("web page result has no text")
                    truncated = len(text) > 32000
                    text = text[:32000]
                    fetched_at = datetime.now(UTC)
                    key = await state.records.web_page(
                        unit=state.unit,
                        session_id=state.session_id,
                        url=args["url"],
                        fetched_at=fetched_at,
                        text=text,
                        tool_call=dict(tool_use),
                    )
                    answer = {
                        **answer,
                        "text": text,
                        "source": list(key),
                        "fetched_at": fetched_at.isoformat(),
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "truncated": truncated or bool(answer.get("truncated")),
                    }
                    answer.pop("content", None)
                else:
                    key = ("tool_result", f"{state.session_id}:{tool_use['toolUseId']}")
                    text = json.dumps(answer, ensure_ascii=False, sort_keys=True)
                    answer = {**answer, "source": list(key)}
                state.fetched.show(Source(key, "record", state.unit, state.persons, text))
                yield self._event(tool_use, answer)
            else:
                yield event
