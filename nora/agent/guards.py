"""Guardrail enforcement (spec B). ReplyGuard is constructed fresh per tag, so counters
are per-invocation by construction rather than by bookkeeping.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, ClassVar

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from ..domain.models import ConversationTarget, DeliveryStatus, NormalizedMessage
from ..obs.metrics import METRICS
from ..persona.loader import Persona

log = logging.getLogger("nora.guards")

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse whitespace so a reflowed paste still matches (leak detection only)."""
    return _WS.sub(" ", text).strip()


class LeakGuard:
    """R-6: no reply may carry the canary or a verbatim >=min_span span of persona text.

    Scope is the persona sections only — runtime_context holds config values
    (display name, trigger) that are legitimately quotable.
    """

    def __init__(self, persona: Persona, canary: str, min_span: int = 40) -> None:
        self._canary = canary
        self._min_span = min_span
        self._haystack = _normalize(persona.persona_text)

    def check(self, text: str) -> str | None:
        if not text:
            return None
        if self._canary and self._canary in text:
            return "canary"
        candidate = _normalize(text)
        if len(candidate) < self._min_span:
            return None
        for start in range(0, len(candidate) - self._min_span + 1):
            span = candidate[start:start + self._min_span]
            if span in self._haystack:
                log.error("persona_leak_blocked: verbatim span %r", span[:60])
                return "persona_span"
        return None


class Deliverer:
    """Single-shot delivery: audit-first, then send. The ONLY caller of adapter.send().

    The latch counts *attempts*, not successes (R-2): a failed send consumes the shot,
    because v2 has no auto-retry and a second attempt would be an unbounded loop.
    """

    def __init__(
        self,
        *,
        store,
        adapter,
        target: ConversationTarget,
        persona: Persona,
        canary: str,
        tag_id: str,
        nora_sender_id: str,
        metrics=METRICS,
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._target = target
        self._tag_id = tag_id
        self._nora_sender_id = nora_sender_id
        self._metrics = metrics
        self._leak = LeakGuard(persona, canary)
        self._persona_sha256 = persona.sha256
        self._attempted = False
        self._delivered = False
        self._last_text: str | None = None

    @property
    def delivered(self) -> bool:
        return self._delivered

    @property
    def attempted(self) -> bool:
        return self._attempted

    @property
    def last_text(self) -> str | None:
        return self._last_text

    async def deliver(self, text: str, *, source: str = "tool") -> bool:
        if self._attempted:                                    # R-2
            log.warning("delivery refused: already attempted (tag=%s)", self._tag_id)
            self._metrics.inc("nora_guard_vetoes_total", rule="R-2")
            return False

        if not text or not text.strip():                       # R-4
            log.warning("delivery refused: empty reply (tag=%s)", self._tag_id)
            self._metrics.inc("nora_silent_failures_total", reason="empty_reply")
            return False

        reason = self._leak.check(text)                        # R-6
        if reason:
            log.error("persona_leak_blocked: %s (tag=%s)", reason, self._tag_id)
            self._metrics.inc("nora_leak_blocks_total")
            self._metrics.inc("nora_silent_failures_total", reason="persona_leak")
            return False

        self._attempted = True
        row = NormalizedMessage(
            platform=self._target.platform,
            conversation_id=self._target.conversation_id,
            conversation_type=self._target.conversation_type,
            sender_id=self._nora_sender_id,
            sender_name=None,
            timestamp=int(time.time() * 1000),
            body=text,
            raw={"tag_id": self._tag_id, "source": source,
                 "persona_sha256": self._persona_sha256},
        )
        try:                                                   # record-first
            row_id = await self._store.append(
                message=row, is_nora=True, delivery_status=DeliveryStatus.PENDING,
            )
        except Exception:
            log.exception("audit_write_failed (tag=%s); staying silent", self._tag_id)
            self._metrics.inc("nora_silent_failures_total", reason="audit_write_failed")
            return False

        try:
            ok = await self._adapter.send(self._target, text)
        except Exception:
            log.exception("send raised (tag=%s)", self._tag_id)
            ok = False

        try:
            await self._store.set_delivery_status(
                row_id=row_id,
                status=DeliveryStatus.SENT if ok else DeliveryStatus.FAILED,
            )
        except Exception:
            log.exception("delivery_status update failed (tag=%s)", self._tag_id)

        if ok:
            self._delivered = True
            self._last_text = text
            self._metrics.inc("nora_replies_total", status="sent")
        else:
            self._metrics.inc("nora_replies_total", status="failed")
            self._metrics.inc("nora_silent_failures_total", reason="send_failed")
        return ok


class ReplyGuard(HookProvider):
    """Model-facing veto logic (R-2/R-3/R-4/R-7). Constructed fresh per tag.

    `evaluate` is pure and SDK-free so every rule is testable without strands; the
    hook adapter in _before_tool is a thin translation layer (RK-2).
    """

    BUDGETS: ClassVar[dict[str, int]] = {"read_buffer": 2, "read_peer_card": 1, "send_reply": 1}

    def __init__(self, *, conversation_id: str, deliverer=None, metrics=METRICS) -> None:
        self._conversation_id = conversation_id
        self._deliverer = deliverer
        self._metrics = metrics
        self.counts: dict[str, int] = dict.fromkeys(self.BUDGETS, 0)

    def _veto(self, rule: str, message: str) -> str:
        self._metrics.inc("nora_guard_vetoes_total", rule=rule)
        log.warning("guard veto %s: %s", rule, message)
        return message

    def record(self, tool_name: str) -> None:
        if tool_name in self.counts:
            self.counts[tool_name] += 1

    def evaluate(self, tool_name: str, tool_input: dict) -> str | None:
        if tool_name not in self.BUDGETS:
            return self._veto(
                "R-7", f"The tool {tool_name!r} does not exist. Use read_buffer, "
                       "read_peer_card or send_reply.",
            )

        target = (tool_input or {}).get("conversation_id")
        if tool_name in ("send_reply", "read_buffer"):
            if not target:
                return self._veto("R-3", "conversation_id is required and must be the "
                                         "conversation you were tagged in.")
            if target != self._conversation_id:
                log.error("cross_conversation_send_blocked: %s != %s",
                          target, self._conversation_id)
                return self._veto(
                    "R-3", "You may only act on this conversation — the one that tagged you.",
                )

        if self.counts[tool_name] >= self.BUDGETS[tool_name]:
            if tool_name == "send_reply":
                return self._veto(
                    "R-2", "You already sent your reply. Do not call send_reply again — "
                           "finish your turn.",
                )
            return self._veto(
                "R-7", f"You already called {tool_name} "
                       f"{self.counts[tool_name]} time(s) and have that context. "
                       "Answer from what you already have.",
            )

        if tool_name == "send_reply":
            text = (tool_input or {}).get("text") or ""
            if not text.strip():
                return self._veto("R-4", "Your reply text is empty. "
                                         "Send real text or end your turn.")
            if self._deliverer is not None and self._deliverer.attempted:
                return self._veto(
                    "R-2", "You already sent your reply. Do not call send_reply again — "
                           "finish your turn.",
                )
        return None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_tool(self, event) -> None:
        try:
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            tool_input = tool_use.get("input") or {}
            if not name:
                return
            message = self.evaluate(name, tool_input)
            if message:
                event.cancel_tool = message
        except Exception:
            log.exception("ReplyGuard._before_tool raised; allowing the call")

    def _after_tool(self, event) -> None:
        try:
            if getattr(event, "exception", None) is not None:
                return                       # a failed call does not consume budget
            name = (getattr(event, "tool_use", None) or {}).get("name", "")
            self.record(name)
        except Exception:
            log.exception("ReplyGuard._after_tool raised; ignoring")
