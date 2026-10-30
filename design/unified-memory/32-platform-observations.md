---
type: design
title: "Platform observations: entry point from unified memory"
description: "The cross-cutting observation contract lives in the platform-observations bundle; this node preserves existing graph/build links."
timestamp: '2026-10-30'
edges:
  - {dst: ../platform-observations/00-overview.md, rel: details, provenance: operator-requested}
---

# 32 · Platform observations

The operator requested a complete, accumulating design bundle on 2026-10-30.
The maintained specification has moved to
[platform-observations](../platform-observations/00-overview.md), covering metadata,
non-message events, storage/ETL, rollup, model projections and trace correlation.
This node remains the entry point for existing graph/session/build references.

- [Coverage and primary-source research](../platform-observations/01-coverage.md)
- [Typed observation, persistence and fold contract](../platform-observations/02-observation-contract.md)
- [Transcript, context and tool projections](../platform-observations/03-model-projections.md)
- [Build acceptance and accumulation](../platform-observations/04-build-and-acceptance.md)

Memory graph implementation findings remain in
[27 §17](27-the-memory-is-a-graph.md#17-implementation-findings-and-longitudinal-understanding-2026-10-30).
The [bridge bundle](../platform-bridges/00-contract.md) retains transport/recovery
ownership. No runtime change, collector activation or profile promotion is implied.
