"""Payload and handoff shaping for `bridge_queue`'s work rows — each helper
is the ONE dict shape a record crosses a boundary in.

`_person_dict`/`_decision_dict` are the handoff snapshots a `PersonRef` /
`EngageDecision` is rebuilt from on the far side of a work row or an event
snapshot — the field sets a turn payload and the ingest handoff both persist.
`_payload_trace` is the producer's W3C carrier wherever the work kind carries
it: `envelope.trace` for ingest, propagated `trace` for the kinds ingest
enqueued downstream. `_queue_wait_ms` is the enqueue→claim latency in ms: the
envelope's `queued_ms` for ingest, the `enqueued_ms` ingest stamps on
downstream obligations for the rest — a missing stamp is absent data (`None`),
never a fabricated zero. `_link` is the single-`Link` span helper; `_now_ms`
the wall-clock stamp every obligation reads.
"""

from __future__ import annotations

import time
from typing import Any

from opentelemetry.trace import Link


def _now_ms() -> int:
    return int(time.time() * 1000)


def _person_dict(person: Any) -> dict[str, Any] | None:
    """The handoff dict a `PersonRef` is rebuilt from — `None` when absent."""
    if person is None:
        return None
    return {"person_id": person.person_id, "name": person.name,
            "honcho_peer_id": person.honcho_peer_id,
            "first_name": person.first_name, "last_name": person.last_name,
            "aliases": list(person.aliases)}


def _decision_dict(decision: Any) -> dict[str, Any] | None:
    """The admitted-facts dict a turn payload/ingest snapshot persists."""
    if decision is None:
        return None
    return {"engage": decision.engage, "reason": decision.reason,
            "rationale": decision.rationale,
            "confidence": decision.confidence,
            "threshold": decision.threshold,
            "allow_episode": decision.allow_episode,
            "relevance_gate": decision.relevance_gate}


def _payload_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """The producer's W3C carrier for the work kind, `{}` when absent."""
    env = payload.get("envelope")
    trace = env.get("trace") if isinstance(env, dict) else payload.get("trace")
    return dict(trace) if isinstance(trace, dict) else {}


def _queue_wait_ms(payload: dict[str, Any]) -> int | None:
    """Enqueue→claim latency in ms, `None` when no stamp was recorded."""
    env = payload.get("envelope")
    ref: Any = None
    if isinstance(env, dict):
        ref = env.get("queued_ms")
    if ref is None:
        ref = payload.get("enqueued_ms")
    if ref is None:
        return None
    return max(0, _now_ms() - int(ref))


def _link(ctx: Any) -> list[Link]:
    return [Link(ctx)] if ctx is not None else []
