"""The ambient path: every message, deterministic, no Agent SDK — ever (D1/R-1).

The stages (scope, identity, media, audit, memory-feed, engage-gate) run in
`IngestPipeline`. Its only branch is the gate, and the gate's output is an
`EngageDecision`, not a bool, so a future classifier can carry a rationale and
a confidence without changing the seam.

`EngagePolicy` lives here rather than in `engagement/` because the gate runs on
the INGEST pipeline — placing it the other way round inverts the dependency.

May import: `nora.domain`, `nora.tag`, `nora.obs.metrics`.
May NOT import: `nora.config`, `strands`.
"""

from nora.ingest.policy import TriggerTextPolicy

__all__ = ["TriggerTextPolicy"]
