"""The `nora/agent/` extraction boundary: turn a completed Strands `Agent`
into a `TagTrace` with no SDK types surviving past this module.

Defensive throughout, for `agent_runner.py::_final_text`'s reason: a renamed
or absent SDK field degrades to an empty trace and never raises into a turn
that otherwise succeeded. This is a background-reasoning-continuity feature,
not the reply path -- it must never be the reason a tag fails.
"""

from __future__ import annotations

import logging
from typing import Any

from ..domain.models import TranscriptRow
from ..domain.tag_trace import TagTrace
from ..obs.metrics import Metrics
from .tag_trace_sanitize import cap_messages, strip_synthetic_artifacts

log = logging.getLogger("nora.agent.tag_trace")


def extract_tag_trace(
    agent: Any,
    *,
    baseline: int,
    tag_id: str,
    conversation_id: str,
    transcript: tuple[TranscriptRow, ...],
    outcome: str,
    max_chars: int,
    metrics: Metrics,
) -> TagTrace:
    try:
        raw = list(agent.messages)[baseline:]
    except Exception:  # noqa: BLE001 — defensive-throughout (see module docstring)
        log.warning(
            "tag_trace extraction: agent.messages unreadable (tag=%s); "
            "degrading to an empty trace", tag_id,
        )
        raw = []
    sanitized = strip_synthetic_artifacts(raw)
    capped, truncated = cap_messages(sanitized, max_chars=max_chars)
    if truncated:
        metrics.inc(
            "nora_tagtrace_truncations_total", value=truncated,
            what="assistant_narration",
        )
    return TagTrace(
        tag_id=tag_id, conversation_id=conversation_id,
        messages=tuple(capped), transcript=transcript, outcome=outcome,
        created_at=0,  # the runner stamps the real clock (`nora.app.agent_runner`)
    )
