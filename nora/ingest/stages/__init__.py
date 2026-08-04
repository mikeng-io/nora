"""The five ingest stages (spec §4.3): parse, audit, identity, memory_feed, gate.

Each module has exactly one public `async def run(...)`. `nora.ingest.pipeline.
IngestPipeline` sequences them; this package holds no orchestration logic of its
own — anything that looks like a decision belongs in the stage that owns it.
"""

from __future__ import annotations
