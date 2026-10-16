"""The tag-response path: admission, context assembly,
invocation, delivery and feedback for a tag `nora.ingest` already decided to
engage. `nora.engagement.pipeline.EngagementPipeline` sequences the stages;
import it from there directly, the way `nora.ingest.pipeline.IngestPipeline`
is — this package's own `__init__` re-exports only `TranscriptInjection`,
matching `nora.ingest`'s convention of not re-exporting its pipeline class.

`nora.ingest` decides WHETHER to engage; `nora.engagement` decides HOW —
assembling the `TagContext` a reply needs and admitting a tag (debounce,
in-flight, self-tag) before it ever reaches a model.

May import: `nora.domain`, `nora.persons`, `nora.tag`, `nora.obs.metrics`.
May NOT import: `nora.config`, `strands` — `Deliverer`/`ReplyGuard` stay in
`nora/agent/` because they are `strands.HookProvider`s, and pulling them into
`engagement/` would drag strands across the boundary
`tests/test_boundaries.py::test_strands_import_boundary` enforces.
"""

from nora.engagement.context import TranscriptInjection

__all__ = ["TranscriptInjection"]
