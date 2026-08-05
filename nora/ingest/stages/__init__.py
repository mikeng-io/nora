"""The four ingest stages (spec §4.3): audit, identity, memory_feed, gate.

Each module has exactly one public `async def run(...)`. `nora.ingest.pipeline.
IngestPipeline` sequences them; this package holds no orchestration logic of its
own — anything that looks like a decision belongs in the stage that owns it.

Parsing (the former stage 1) is not here: `nora.domain.ports.PlatformAdapter`
deliberately has no `parse()`, so each adapter parses its own transport
internally and only ever hands `IngestPipeline.run` an already-normalized
`NormalizedMessage` — see `nora.platform.signal.adapter.SignalAdapter.listen`
and Trap B in `docs/superpowers/plans/2026-08-05-nora-phase4-platforms.md`.
"""

from __future__ import annotations
