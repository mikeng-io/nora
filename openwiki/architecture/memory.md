---
type: architecture
title: Records, memory and Persona
description: Ownership of source records, derived memory, scoped retrieval and typed Persona accounts during an incomplete Honcho cutover.
tags: [memory, persona, retrieval, provenance]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:28:42.613Z
sources:
  - id: openwiki-source-7aa209ee4f993345d7092214
    resource: repo://config.example.toml
  - id: openwiki-source-8c4e9c2c6b400d0ee6982682
    resource: repo://nora/agent/memory_search.py
  - id: openwiki-source-f732aae31c1f25710cf2df5f
    resource: repo://nora/engagement/context.py
  - id: openwiki-source-41ead1feab657e9d72f50b7b
    resource: repo://nora/infra/sql/audit.py
  - id: openwiki-source-90491938d950163b951d76c8
    resource: repo://nora/infra/sql/persona.py
generated: { by: "codex", at: "2026-10-20T00:28:42.613Z" }
---

Nora's continuity is distributed across stores with different jobs. A source record, a retrieved interpretation and a current account of a person are not interchangeable evidence.

## Source records and derived context

PostgreSQL records normalized observations and Nora's sends. The record append stores message content, resolved identity, delivery state and media references together. Memory-index projection is attempted in the write transaction; projection failure queues repair without discarding the message. The record remains the source against which interpretations can be checked.

Honcho supplies interpreted memory while the unified-memory migration proceeds. Its presence in composition is a continuing dependency, not proof that a new SQL index has replaced every reader. Likewise, a page describing Persona does not mean a deployment has enabled its writer or render switch.

## Retrieval is scoped before traversal

`MemorySearch.search` derives a scope from the reading room and policy before querying the index. The unit is the read boundary; the roster, listed people, disclosure permissions and social exclusions further constrain access. Traversal cannot use an out-of-scope node as a bridge to another result.

The search combines exact references, dense or lexical seeds, person and media seeds, then bounded graph expansion. It ranks the resulting evidence, with pinned exact hits and age decay. Embedding failure can fall back to lexical search, but the answer is marked `partial`. Missing scope or an exception is `unavailable`, rather than an apparently successful empty result. A complete empty search is `nothing_found`.

`[memory_index] enabled` selects the indexed memory tool surface instead of the older Honcho tools. It is a tool-surface switch, not a declaration that every background dependency on Honcho has disappeared. Embedding has a separate switch and worker lifecycle.

## Typed Persona accounts

Persona represents accounts for people, rooms, units and Nora herself. Changes append typed lines with provenance and supersession rather than replacing history. Current state comes from chain heads: rows with no successor. Reading every row still labelled current would revive superseded information.

At context assembly, Persona reads use bounded, fail-soft account reads. A failed account is omitted; it does not become an assertion that the person has no relevant facts. Room disclosure checks run before the view reaches the renderer. Authored identity is rendered separately from inferred account lines.

The writer and render switches are independent: shadow writers can produce accounts while prompts still use older context. `[persona] render` takes effect only with Persona enabled. This separation supports comparison and rollback, and explains why “new memory exists” and “all turns use it” are different claims.

## Where to investigate a problem

For a missing observation, inspect record ingestion first. For missing retrieval, distinguish scope refusal, projection backlog, embeddings and an honest empty result. For a wrong personal claim, inspect its cited source and supersession chain before changing model instructions.

`tests/unit/test_memory_search.py` covers scope and failure status. `tests/unit/test_memory_routing.py` covers workspace bindings. These are deterministic checks, separate from semantic recall or live model acceptance.

See [continuity](../concepts/continuity.md), [cognition](ambient-cognition.md) and [configuration](../operations/configuration.md).
