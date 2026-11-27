"""Verify the physical case boundary while retaining production workers."""

from __future__ import annotations

from typing import Any

from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError

DELIVERY_WORKERS = (
    "bridge_runtime", "fire_path", "social_poller", "computer_lifecycle",
)


def egress_report(subject: Any) -> dict[str, Any]:
    environment = getattr(subject, "environment", None)
    return {
        "workers": {name: getattr(subject.deps, name, None) is not None
                    for name in DELIVERY_WORKERS},
        "isolated": (isinstance(environment, IsolatedServices)
                     and environment.guarded
                     and environment.database_url == subject.database_url),
        "denied": len(environment.denied) if isinstance(environment, IsolatedServices) else None,
    }


def assert_no_egress(subject: Any) -> dict[str, Any]:
    report = egress_report(subject)
    if not report["isolated"]:
        raise EvidenceError("the subject has no active case-owned network/storage boundary")
    subject.environment.check()
    return report
