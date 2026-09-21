---
type: design
title: "Self-Reference — Layer 2: canonical asset (self only)"
description: "One operator-provided image marked role=self in the existing provenance jsonb, resolving cross-conversation via a narrow custody exemption with pinned precedence."
tags: [nora, media, custody, resolver, image-gen]
timestamp: '2026-08-27'
edges:
  - {src: 02-canonical-assets, dst: 00-overview, rel: realizes, provenance: asserted}
---

# Layer 2 — canonical asset (self only)

## Scope of the canonical set: ONE asset

**v1 pins exactly one canonical asset: Nora's own image (`role: self`). There is
no `operator` asset** — the operator decided on 2026-09-16 that v1 covers Nora
only (`「no need operator, just nora」`). The consequence is recorded honestly in
`03-reference-rules.md`: the 2026-08-27 failure class (Nora misidentifying which
user-supplied photo was the operator's) is then covered **only by the
deterministic ask rule**, not by a pinned handle. That is the accepted v1
trade-off, and it keeps the custody exemption at its narrowest — exactly one
role value, one row.

The `role` marker is still the extension point: adding an `operator` asset later
is a new DEC plus an ingest, not a redesign.

## Source of the canonical image

The canonical `self` asset source material lives IN this bundle, as two files
with two distinct roles:

- **`assets/nora-canonical-self.png`** (1024×1536) — **THE ASSET.** A single
  clean full-body front-facing studio image, produced from the reference sheet
  (2026-09-16) and accepted by the operator as the canonical self image. This
  is what gets ingested.
- **`assets/nora-character-reference-sheet.png`** (1536×1024) — **THE
  DESCRIPTIVE SOURCE, not an asset.** Multi-panel (angles, expressions, eye
  directions, closeups, turnaround, sitting poses, swatches); it is what the
  doctrine text is written from, and it is deliberately NOT ingested as a
  reference image — a multi-panel image muddles identity in generation.

At build time the ingest one-shot (`scripts/ingest_self_asset.py`) ingests the
canonical single image into Nora's media library, marks `role: self`, and the
doctrine's `<SELF_HANDLE>` is pinned to the ingested row's full sha256 as
normal. Source sha256 at acceptance: `4b8825fe61b874bbcee85b28ce740fb2da1b10c1c7e5ba406fbca4c4e9b45e09`.

## Storage

The image is ingested into Nora's own media library (RustFS bytes +
`media_objects` row) exactly like any other blob she holds. The only
difference is a marker in `provenance` jsonb:

```json
{"role": "self", "source": "operator-provided", "canonical": true}
```

Using `provenance` jsonb (existing column, `schema.py:792`) avoids a schema
migration. The `source` stays `fetched` (no new enum value; marker-only).

## Custody exemption

`resolve(marker, conversation_id)` currently returns nothing when the
marker's row is not in the caller's conversation store (design/30 §5,
`ports.py:1781-1798`; the resolver closure at `tools.py:3316-3354` reads only
this conversation's transcript `media_json` via `store.recent(
conversation_id=...)`). The exemption: **if the row's `provenance.role` is
`self`, resolve regardless of conversation.** This is safe because:

- The blob is Nora's own, operator-provided asset — not external/untrusted
  content, not a user's photo.
- It is a single, known, canonical row — no attacker-controlled path can mark
  arbitrary content with a canonical role (the marker is written by the
  operator's ingest path, which is the same trust level as doctrine files
  themselves).
- No data leaves Nora: resolution only ever produces bytes *she already holds*
  and sends to her configured image provider.

The exemption is written as an **exact role match** (`role = 'self'`), never a
prefix or a set that could be widened by accident.

## Precedence (pinned by contract test)

The resolver looks up the conversation window FIRST; the canonical-role row is a
FALLBACK only. A canonical handle is matched on the FULL sha256, never a
short prefix, so a conversation asset and the canonical asset can never collide
on the same marker code. The existing single-match rule (`tools.py:3342-3345`)
still applies inside each lookup source: ambiguity inside either source
resolves to nothing (safe, opaque), never to a guess.

## Files touched (this layer)

```
nora/domain/ports.py                           # ImageReference docstring: canonical-role exemption
nora/agent/tools.py                            # resolver closure: role == 'self' bypasses
                                               #   conversation scope; conversation-first precedence;
                                               #   full-sha match for the canonical handle
nora/infra/sql/media_objects.py                # query for provenance->>'role' = 'self'
scripts/ingest_self_asset.py                   # operator one-shot: ingest design image + mark role
tests/contract/test_image_generator_contract.py # + canonical-role resolves cross-conversation;
                                               #   non-canonical still refused; collision resolves
                                               #   to nothing; external URL still refused; doctrine
                                               #   embedded handle resolves to the canonical row
```
