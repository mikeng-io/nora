---
type: design
title: "Migration: three steps, the policy refusal last"
description: "Name uniqueness and the richer room list first, then send_to by room, then the one-per-platform refusal goes and the decision rows are amended; the live config change is the operator's."
tags: [nora, topology, migration]
timestamp: '2026-10-25'
edges:
  - {dst: 40-verification, rel: depends_on, provenance: asserted}
---

# 50 · Migration

| step | ships | gate |
|---|---|---|
| **1. Names and the room list** | the unique-name refusal, alongside the one-per-platform refusal (both hold; no config breaks); `room_list` gains `about`, `culture`, `threads` (`threads` once the standing ledger's step 2 exists); the audit of [30](30-what-reads-the-platform.md) re-run on the branch | the refusal tests of [40](40-verification.md) §3 (duplicate names, reserved network names, a missing label); the live `policy.toml` still loads (read back on the deploy host) |
| **2. `send_to` by room** | the `room` argument and resolver, `ambiguous`, same room by identity, the new docstring, the rewritten comments | routing corpus and `tool_selection` against master (§1, §2) |
| **3. The rule goes** | `_refuse_two_rooms_on_one_platform` deleted; DEC-105 and DEC-214 amended by a decision row at merge time naming the unique-name rule as the invariant; R-49 amended for the `room` address | the loader accepts two labelled Signal rooms in one unit ([40](40-verification.md) §3); the step-2 numbers hold on that test unit |

Step 3 implementation: the loader accepts two labelled Signal rooms in both
policy forms. Unique-name, reserved-network-name and missing-label refusals
remain. Steps 1 and 2 above describe the earlier migration states; their
platform-count refusal is no longer live after step 3.

Adding the second Signal room to the live `policy.toml` is a config change on
the deploy host after step 3, made by the operator; it never goes to GitHub.

## Open

| question | who decides |
|---|---|
| whether a unit can switch off rendering its shared state (the standing ledger's unit scope) in its rooms, or it is always on, gated per entry by lineage | the operator, before the standing ledger's step 2 |
| whether `send_to` should offer to post in several rooms at once ("tell everyone") | the operator; not in this bundle |
