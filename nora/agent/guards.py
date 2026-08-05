"""Guardrail enforcement (spec B). ReplyGuard is constructed fresh per tag, so counters
are per-invocation by construction rather than by bookkeeping.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from ..domain.models import ConversationTarget, DeliveryStatus, NormalizedMessage
from ..domain.ports import SendResult
from ..obs.metrics import Metrics
from ..persona.loader import Persona
from .egress import LeakGuard, scan_egress

log = logging.getLogger("nora.guards")





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
        metrics: Metrics,
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
            self._metrics.inc(
                "nora_silent_failures_total", reason="empty_reply", platform=self._target.platform,
            )
            return False

        reason = self._leak.check(text)                        # R-6
        if reason:
            log.error("persona_leak_blocked: %s (tag=%s)", reason, self._tag_id)
            self._metrics.inc("nora_leak_blocks_total")
            self._metrics.inc(
                "nora_silent_failures_total", reason="persona_leak", platform=self._target.platform,
            )
            return False

        # R-18 — see `nora.agent.egress` for why this exists and what it
        # deliberately does not match.
        kind = scan_egress(text)
        if kind:
            log.error("egress_blocked: %s (tag=%s)", kind, self._tag_id)
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            self._metrics.inc(
                "nora_silent_failures_total", reason="egress_block",
                platform=self._target.platform,
            )
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
            self._metrics.inc(
                "nora_silent_failures_total", reason="audit_write_failed",
                platform=self._target.platform,
            )
            return False

        try:
            result = await self._adapter.send(target=self._target, text=text)
        except Exception:
            log.exception("send raised (tag=%s)", self._tag_id)
            result = SendResult(ok=False)

        try:
            await self._store.set_delivery_outcome(
                row_id=row_id,
                status=DeliveryStatus.SENT if result.ok else DeliveryStatus.FAILED,
                ts=result.timestamp,          # the platform's stamp, not ours
            )
        except Exception:
            log.exception("delivery_status update failed (tag=%s)", self._tag_id)

        if result.ok:
            self._delivered = True
            self._last_text = text
            self._metrics.inc("nora_replies_total", status="sent", platform=self._target.platform)
        else:
            self._metrics.inc("nora_replies_total", status="failed", platform=self._target.platform)
            self._metrics.inc(
                "nora_silent_failures_total", reason="send_failed", platform=self._target.platform,
            )
        return result.ok


def _list_tool_names(names: Any) -> str:
    """`"read_buffer, read_peer_card or send_reply"` — sorted so the R-7
    unknown-tool message is deterministic and, incidentally, matches the
    natural reading order of today's three native tools."""
    ordered = sorted(names)
    if len(ordered) <= 1:
        return ", ".join(ordered)
    return ", ".join(ordered[:-1]) + " or " + ordered[-1]


class ReplyGuard(HookProvider):
    """Model-facing veto logic (R-2/R-3/R-4/R-7). Constructed fresh per tag.

    `evaluate` is pure and SDK-free so every rule is testable without strands; the
    hook adapter in _before_tool is a thin translation layer (RK-2).

    `budgets` is DEC-10: the per-tool call-count ceiling R-7 enforces used to be
    a hardcoded `BUDGETS` class attribute, which meant any tool not in that
    literal table (a fourth native tool, `end_episode`) was vetoed before it
    ever ran. It is now injected — `nora.agent.registry.ToolRegistry.budgets()`
    via `nora.engagement.stages.deliver.build` — so the number a tool costs
    lives with the tool, not with the guard. R-7's OTHER half, unknown-tool
    default-deny, is unchanged: a tool absent from `budgets` is still vetoed.
    """

    def __init__(
        self, *, conversation_id: str, budgets: Mapping[str, int], deliverer=None,
        metrics: Metrics,
    ) -> None:
        self._conversation_id = conversation_id
        self._budgets = dict(budgets)
        self._deliverer = deliverer
        self._metrics = metrics
        self.counts: dict[str, int] = dict.fromkeys(self._budgets, 0)

    def _veto(self, rule: str, message: str) -> str:
        self._metrics.inc("nora_guard_vetoes_total", rule=rule)
        log.warning("guard veto %s: %s", rule, message)
        return message

    def record(self, tool_name: str) -> None:
        if tool_name in self.counts:
            self.counts[tool_name] += 1

    def evaluate(self, tool_name: str, tool_input: dict) -> str | None:
        if tool_name not in self._budgets:
            return self._veto(
                "R-7", f"The tool {tool_name!r} does not exist. "
                       f"Use {_list_tool_names(self._budgets)}.",
            )

        # R-3 — scope, now held BY CONSTRUCTION rather than by veto. Both
        # tools ignore this argument: `read_buffer` reads the closure value
        # (Task 1's cross-group leak fix) and `send_reply` calls
        # `deliverer.deliver(text)`, whose `Deliverer` holds its target bound
        # from construction. The argument is INERT, so vetoing on it protected
        # nothing while making a weak model unable to speak at all — on the
        # first real tag the model echoed a wrong group id three times, R-3
        # cancelled each, and the turn ended `no_send_call`. A mismatch is
        # still a model reaching outside its scope, so it is counted.
        target = (tool_input or {}).get("conversation_id")
        if tool_name in ("send_reply", "read_buffer") and target != self._conversation_id:
            log.warning(
                "tool scope mismatch on %s: model supplied %r, using the bound %r",
                tool_name, target, self._conversation_id,
            )
            self._metrics.inc("nora_tool_scope_violations_total", tool=tool_name)

        if self.counts[tool_name] >= self._budgets[tool_name]:
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
        registry.add_callback(BeforeModelCallEvent, self._before_model)

    def _before_model(self, event: BeforeModelCallEvent) -> None:
        """End the turn as soon as the reply is out.

        `tool_choice="required"` (see `nora.agent.factory`) is the only reason
        this model calls `send_reply` at all — but it applies to EVERY turn, so
        once the reply has gone the model is still forced to call something and
        the loop cannot terminate. It ran until the invocation timeout killed
        it, which cost ~25s per tag AND resolved the indicator wrongly: the
        timeout path exits before `delivered` is set, so a turn that had
        actually succeeded showed the group ⚠️. One cause, two symptoms, both
        observed live.

        Cancelling here is the narrow fix. R-2 already refuses a second send,
        so nothing after the first delivery can change what the group sees —
        the remaining turns were pure cost.
        """
        if self._deliverer is not None and self._deliverer.delivered:
            event.cancel = "Reply already sent — the turn is finished."

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
