"""The tag-response path (spec §4.4): context assembly, admission, and — in
later tasks — invocation, delivery and feedback for a tag `nora.ingest`
already decided to engage.

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
