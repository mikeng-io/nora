"""Guardrail enforcement (spec B). ReplyGuard is constructed fresh per tag, so counters
are per-invocation by construction rather than by bookkeeping.
"""

from __future__ import annotations

import logging
import re
import time

from ..models import ConversationTarget, NormalizedMessage
from ..obs.metrics import METRICS
from ..persona.loader import Persona
from ..store import db

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
        db_path,
        adapter,
        target: ConversationTarget,
        persona: Persona,
        canary: str,
        tag_id: str,
        nora_sender_id: str,
        max_per_conversation: int = 500,
        metrics=METRICS,
    ) -> None:
        self._db_path = db_path
        self._adapter = adapter
        self._target = target
        self._tag_id = tag_id
        self._nora_sender_id = nora_sender_id
        self._max_per_conversation = max_per_conversation
        self._metrics = metrics
        self._leak = LeakGuard(persona, canary)
        self._persona_sha256 = persona.sha256
        self._attempted = False
        self._delivered = False

    @property
    def delivered(self) -> bool:
        return self._delivered

    @property
    def attempted(self) -> bool:
        return self._attempted

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
            row_id = await db.append(
                self._db_path, row, is_nora=True, delivery_status="pending",
                max_per_conversation=self._max_per_conversation,
            )
        except Exception:                                      # noqa: BLE001 — R-5
            log.exception("audit_write_failed (tag=%s); staying silent", self._tag_id)
            self._metrics.inc("nora_silent_failures_total", reason="audit_write_failed")
            return False

        try:
            ok = await self._adapter.send(self._target, text)
        except Exception:                                      # noqa: BLE001 — R-5
            log.exception("send raised (tag=%s)", self._tag_id)
            ok = False

        try:
            await db.set_delivery_status(self._db_path, row_id, "sent" if ok else "failed")
        except Exception:                                      # noqa: BLE001
            log.exception("delivery_status update failed (tag=%s)", self._tag_id)

        if ok:
            self._delivered = True
            self._metrics.inc("nora_replies_total", status="sent")
        else:
            self._metrics.inc("nora_replies_total", status="failed")
            self._metrics.inc("nora_silent_failures_total", reason="send_failed")
        return ok
