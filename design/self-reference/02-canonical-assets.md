---
type: design
title: "Self-Reference — Layer 2: canonical assets (self + operator)"
description: "Two operator-provided images marked role=self/operator in the existing provenance jsonb, resolving cross-conversation via a narrow custody exemption with pinned precedence."
tags: [nora, media, custody, resolver, image-gen]
timestamp: '2026-08-27'
edges:
  - {src: 02-canonical-assets, dst: 00-overview, rel: realizes, provenance: asserted}
---

# Layer 2 — canonical assets (self + operator)

## Storage

Two images are ingested into Nora's own media library (RustFS bytes +
`media_objects` row) exactly like any other blob she holds. The only
difference is a marker in `provenance` jsonb:

```json
{"role": "self",      "source": "operator-provided", "canonical": true}
{"role": "operator",  "source": "operator-provided", "canonical": true}
```

Using `provenance` jsonb (existing column, `schema.py:792`) avoids a schema
migration. The `source` stays `fetched` (no new enum value; marker-only).

## Custody exemption

`resolve(marker, conversation_id)` currently returns nothing when the
marker's row is not in the caller's conversation store (design/30 §5,
`ports.py:1781-1798`; the resolver closure at `tools.py:3316-3354` reads only
this conversation's transcript `media_json` via `store.recent(
conversation_id=...)`). The exemption: **if the row's `provenance.role` is
`self` or `operator`, resolve regardless of conversation.** This is safe
because:

- The blobs are Nora's own, operator-provided assets — not
  external/untrusted content, not another user's photo.
- It is a small, known, canonical set of rows — no attacker-controlled path
  can mark arbitrary content with a canonical role (the marker is written by
  the operator's ingest path, which is the same trust level as doctrine
  files themselves).
- No data leaves Nora: resolution only ever produces bytes *she already holds*
  and sends to her configured image provider.

## Precedence (pinned by contract test)

The resolver looks up the conversation window FIRST; a canonical-role row is a
FALLBACK only. A canonical handle is matched on the FULL sha256, never a
short prefix, so a conversation asset and a canonical asset can never collide
on the same marker code. The existing single-match rule (`tools.py:3342-3345`)
still applies inside each lookup source: ambiguity inside either source
resolves to nothing (safe, opaque), never to a guess.

## Files touched (this layer)

```
nora/domain/ports.py                           # ImageReference docstring: canonical-role exemption
nora/agent/tools.py                            # resolver closure: role in (self,operator) bypasses
                                               #   conversation scope; conversation-first precedence;
                                               #   full-sha match for canonical handles
nora/infra/sql/media_objects.py                # query for provenance->>'role' IN ('self','operator')
scripts/ingest_self_asset.py                   # operator one-shot: ingest design image + mark role
tests/contract/test_image_generator_contract.py # + canonical-role resolves cross-conversation;
                                               #   non-canonical still refused; collision resolves
                                               #   to nothing; external URL still refused; doctrine
                                               #   embedded handles resolve to canonical rows
```
