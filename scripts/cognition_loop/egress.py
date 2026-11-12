"""The no-delivery assertion, run before the first wake.

A composed subject may not reach the world: no live egress worker wired
(bridge runtime, fire path, social poller, computer lifecycle), and every
adapter's sender a landed one — a real Signal, Discord, Instagram or any
other platform sender is a refusal, never a hope it stays silent.

Discovery itself is fail-closed: a deps whose adapter registry cannot be
read, an adapter whose sender cannot be identified wherever the adapter
keeps it, or a sender that is not `Landed*` refuses the run rather than
reporting nothing. `dispatcher` is a required inbound entry point —
always present on a composed subject — so it is reported, never refused:
the only send it could drive goes through the senders checked here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nora.subject.eval_evidence import EvidenceError

#: The egress loops a subject can compose — each optional, each live when
#: wired. `dispatcher` is not here: it is a required inbound entry point,
#: never a delivery worker, and always present on a real subject.
DELIVERY_WORKERS = (
    "bridge_runtime", "fire_path",
    "social_poller", "computer_lifecycle",
)


def _adapter_senders(adapter: Any) -> dict[str, str]:
    """Every attribute whose value is a sender, by type name."""
    try:
        members = dict(vars(adapter))
    except TypeError:
        members = {
            name: getattr(adapter, name) for name in dir(adapter)
            if "sender" in name.lower()}
    return {
        name: type(value).__name__
        for name, value in members.items()
        if type(value).__name__.endswith("Sender")
    }


def egress_report(subject: Any) -> dict[str, Any]:
    deps = subject.deps
    adapters = getattr(getattr(deps, "registry", None), "adapters", None)
    senders: dict[str, dict[str, str]] | None = None
    if isinstance(adapters, Mapping):
        senders = {p: _adapter_senders(a) for p, a in adapters.items()}
    return {
        "workers": {w: getattr(deps, w, None) is not None for w in DELIVERY_WORKERS},
        "senders": senders,
    }


def assert_no_egress(subject: Any) -> dict[str, Any]:
    """Refuse the run when any egress path is wired live — or cannot be
    shown not to be."""
    report = egress_report(subject)
    wired = sorted(w for w, live in report["workers"].items() if live)
    if wired:
        raise EvidenceError(
            f"delivery components are wired into the subject: "
            f"{', '.join(wired)}; the eval composes a subject that can "
            "never deliver"
        )
    if report["senders"] is None:
        raise EvidenceError(
            "the subject's platform adapters cannot be enumerated; "
            "a send surface the check cannot read is a refusal"
        )
    unidentified = sorted(
        p for p, found in report["senders"].items() if not found)
    if unidentified:
        raise EvidenceError(
            f"adapter sender(s) cannot be identified: {unidentified}; "
            "an adapter keeping its sends where the check cannot see "
            "them is a refusal"
        )
    real = {
        platform: found for platform, found in report["senders"].items()
        if any(not name.startswith("Landed") for name in found.values())
    }
    if real:
        raise EvidenceError(
            f"real platform senders are wired: {real}; every adapter's "
            "sends must land nowhere"
        )
    return report
