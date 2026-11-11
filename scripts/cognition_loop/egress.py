"""The no-delivery assertion, run before the first wake.

A composed subject carries the same fields `__main__` runs — dispatcher,
fire path, adapters — but nothing starts their loops here, and the
wires that would reach the world must be the neutralised ones: no
bridge runtime, and no real platform sender behind an adapter. Anything
else wired is a refusal, never a hope it stays silent.
"""

from __future__ import annotations

from typing import Any

from nora.subject.eval_evidence import EvidenceError


def egress_report(subject: Any) -> dict[str, Any]:
    deps = subject.deps
    senders: dict[str, str] = {}
    for platform, adapter in getattr(deps.registry, "adapters", {}).items():
        sender = getattr(adapter, "_sender", None)
        if sender is not None:
            senders[platform] = type(sender).__name__
    return {
        "bridge_runtime": deps.bridge_runtime is not None,
        "fire_path": deps.fire_path is not None,
        "dispatcher": deps.dispatcher is not None,
        "social_poller": getattr(deps, "social_poller", None) is not None,
        "senders": senders,
    }


def assert_no_egress(subject: Any) -> dict[str, Any]:
    """Refuse the run when any egress path is wired live."""
    deps = subject.deps
    report = egress_report(subject)
    if deps.bridge_runtime is not None:
        raise EvidenceError(
            "a bridge runtime is wired into the subject; the eval composes "
            "direct adapters only, so nothing here may reach a bridge"
        )
    for platform, sender in report["senders"].items():
        if platform == "signal" and not sender.startswith("LandedSignalSender"):
            raise EvidenceError(
                f"the signal adapter sends through {sender}; the eval sends "
                "must land nowhere"
            )
    return report
