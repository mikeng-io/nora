"""The no-delivery assertion, run before the first wake.

A composed subject may not reach the world: no delivery worker wired
(dispatcher, fire path, social poller, bridge runtime, computer
lifecycle), and every adapter's sender a landed one — a real Signal,
Discord, Instagram or any other platform sender is a refusal, never a
hope it stays silent.
"""

from __future__ import annotations

from typing import Any

from nora.subject.eval_evidence import EvidenceError

#: Every delivery component a subject can compose. Live means wired.
DELIVERY_WORKERS = (
    "bridge_runtime", "fire_path", "dispatcher",
    "social_poller", "computer_lifecycle",
)


def egress_report(subject: Any) -> dict[str, Any]:
    deps = subject.deps
    senders: dict[str, str] = {}
    for platform, adapter in getattr(deps.registry, "adapters", {}).items():
        sender = getattr(adapter, "_sender", None) or getattr(adapter, "sender", None)
        if sender is not None:
            senders[platform] = type(sender).__name__
    return {
        "workers": {w: getattr(deps, w, None) is not None for w in DELIVERY_WORKERS},
        "senders": senders,
    }


def assert_no_egress(subject: Any) -> dict[str, Any]:
    """Refuse the run when any egress path is wired live."""
    report = egress_report(subject)
    wired = sorted(w for w, live in report["workers"].items() if live)
    if wired:
        raise EvidenceError(
            f"delivery components are wired into the subject: "
            f"{', '.join(wired)}; the eval composes a subject that can "
            "never deliver"
        )
    real = {
        platform: sender for platform, sender in report["senders"].items()
        if not sender.startswith("Landed")
    }
    if real:
        raise EvidenceError(
            f"real platform senders are wired: {real}; every adapter's "
            "sends must land nowhere"
        )
    return report
