"""The engagement gate. A policy error degrades closed.

`TriggerTextPolicy` is deterministic and cannot raise (see `nora.ingest.
policy`), but `EngagePolicy.should_engage` is declared `async` ahead of the
`ClassifierPolicy` that will do network I/O and can fail. Under R-5, a gate
that lets that exception propagate would take the listener down over a
classifier outage — worse than just not engaging.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagePolicy

log = logging.getLogger("nora.ingest.gate")


async def run(*, message: NormalizedMessage, policy: EngagePolicy, metrics: Any) -> EngageDecision:
    try:
        return await policy.should_engage(message=message)
    except Exception:
        log.exception("engage policy failed; declining to engage")
        metrics.inc("nora_errors_total", stage="engage_gate", platform=message.platform)
        return EngageDecision(engage=False, reason="policy_error")
