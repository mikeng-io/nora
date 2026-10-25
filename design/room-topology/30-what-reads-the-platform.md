---
type: design
title: "What reads the platform: the audit of every place that leaned on one room per platform"
description: "Each code path that resolves a unit's room by network or compares platforms, read in the tree, and whether it changes."
tags: [nora, topology, audit, relay, policy, room-tools]
timestamp: '2026-10-25'
edges:
  - {dst: 10-topology, rel: details, provenance: asserted}
---

# 30 · What reads the platform

Read in the tree on 2026-10-25 (`git grep` for the rule's wording and for
platform comparisons in `nora/agent/`, `nora/app/`, `nora/language/` and
`nora/engagement/`). Step 1 of [50](50-migration.md) re-runs this search on
the branch it ships from, because a path added since would be missed by a list.

| path | what it does | changes |
|---|---|---|
| `nora/policy.py` `_refuse_two_rooms_on_one_platform` | refuses a unit with two rooms on one platform | **replaced** by the unique-name refusal ([10](10-topology.md) §3) |
| `nora/agent/relay.py` `Relay._find` | the unit's room on a platform; refuses `ambiguous_room` on two | **changes**: resolves a room by name, then a unique network ([20](20-addressing.md) §1) |
| `nora/agent/relay.py` `send`, `same_room` | refuses when the platforms match | **changes**: same room by identity ([20](20-addressing.md) §4) |
| `nora/agent/tools.py` `send_to` | `platform` Literal; the "one room on each network" docstring | **changes**: `room` argument, new docstring ([20](20-addressing.md) §6) |
| `nora/agent/tools.py` `_find_room` | name first, then a network only when unique | unchanged; becomes the shared resolver |
| `nora/agent/tools.py` `room_list` | a room's name, network, state, activity | **gains** `about`, `culture`, `threads` ([20](20-addressing.md) §2) |
| `nora/agent/memory_search.py` | finds this room in the unit by platform and ids | unchanged: matches on ids too |
| `nora/app/documents_service.py`, `nora/agent/video_desk.py` | match a room by platform and conversation id | unchanged: an exact room, not a network |
| `nora/policy.py` `voice`, `music_channel` | at most one per guild | unchanged ([10](10-topology.md) §2) |
| `nora/policy.py` `home_rooms` | at most one per unit | unchanged |
| `nora/app/composition.py` note-room join | `(conversation id, unit)` to a room | unchanged: keyed by id, not by network |

The comments that state the old rule as a reason (`nora/policy.py` around
`voice` and `music_channel`, `nora/agent/relay.py`'s module docstring) are
rewritten with the change, so no comment keeps asserting an invariant the code
no longer holds.
