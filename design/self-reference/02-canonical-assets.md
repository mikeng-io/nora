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

## Current 2D Bauhinia source — 2026-10-26

The later [production ruling](09-mini08-balanced.md#production-setting-supersession--2026-10-26)
requires 2D anime Bauhinia for every non-3D use. The stable canonical path now
contains the supplied single-image Mini production master. Its identifying PNG
metadata is stripped without re-encoding image data; the runtime skill pins the
actual sanitized bytes by the full handle. The appearance section, identity
summary and existing anchor assertions have moved together. Recognition,
reference selection, room/date, prompt and privacy rules remain in force.

The simplified-hair [v2 references](../../assets/modeling/bauhinia/v2/README.md)
supplement the retained adult anchor and historical v1 sheets. Stylized 3D PNGs
are Meshy-only input candidates, not consumer art or geometry proof. There is
still one canonical self handle. Parent browser acceptance passed for the current 2D artwork. Live media ingest and
deployment remain separate; reach and cost remain UNMEASURED.

## Earlier option08 repository source — 2026-10-26

The operator approved [option08 Balanced](09-mini08-balanced.md) as canonical
and requested matching reference, T-pose, setting and public repository art.
At that earlier refresh, the canonical path received the approved option08 PNG, sanitized of
generation metadata while retaining encoded IDAT pixels. The runtime skill
pins its sanitized full digest; live ingest and release are not performed
by repository synchronization. The other source paths keep their roles;
the social files are repository references, not proof of external publication.
The [Mini turnaround](../../assets/nora-mini-turnaround.png) adds front, side
and back views beside the [front T-pose](../../assets/nora-mini-tpose.png) and
[character sheet](../../assets/nora-character-reference-sheet.png). These are
reference-only inputs; neither a validated model nor a rig.
The human Bauhinia anchor stays unchanged. Current dimensions, digests,
parents and consumers are in `assets/visual-assets.json`.

## September source history (superseded image bytes)

The canonical `self` asset source material lives IN this bundle, with distinct roles:

- **`assets/nora-canonical-self.png`** (1024×1536) — **THE ASSET.** A single
  clean full-body front-facing studio render of the mascot form, supplied by
  the operator on 2026-09-23 as the image to reference whenever Nora appears
  in a picture herself. This is what gets ingested. It replaces the
  2026-09-16 human-form image at the same path.
- **`assets/nora-character-reference-sheet.png`** (1254×1254) — **THE
  DESCRIPTIVE SOURCE, not an asset.** Multi-panel "Mini Form" sheet
  (turnaround, expressions, poses, details, colour palette, size reference);
  with `05-visual-identity-spec.md` it is what the skill's appearance section is written
  from, and it is deliberately NOT ingested as a reference image — a
  multi-panel image muddles identity in generation.
- **`assets/nora-social-avatar.png`** (1374×1145) and
  **`assets/nora-social-banner.png`** (2056×765) — **REFERENCE ONLY, not
  assets.** Her current profile picture and banner on every social network
  (operator, 2026-09-23). Kept so the look she presents publicly is on file
  beside the canon; not ingested, because each places her in a scene (desk,
  laptop, cat) that a generation would copy.
- **`assets/nora-mini-icon.png`**, **`assets/nora-mini-tpose.png`** and
  **`assets/nora-embodied-tpose.png`** — **REFERENCE ONLY, not assets**
  (operator, 2026-09-26): a cut-out icon of the native form and the two
  T-pose inputs recorded in September, one per form. The old photoreal
  embodied T-pose is now historical; `07-native-and-embodied-form.md` preserves
  that framing and points to its dated supersession.
- **`assets/nora-human-bauhinia-reference.png`** — **HUMAN VISUAL ANCHOR ONLY,
  not ingested.** Operator-approved 2026-10-25;
  `08-bauhinia-human-form.md` governs its appearance and limits. It does not
  replace the Mini canonical self asset, descriptive sheet or public avatars.

In the September implementation the ingest one-shot (`scripts/ingest_self_asset.py`) ingests the
canonical single image into Nora's media library, marks `role: self`, and the
skill's `<SELF_HANDLE>` (the image-generation skill, 06) is pinned to the ingested row's full sha256 as
normal. Source sha256 at acceptance (2026-09-23): `535e4da56b933c9c30ea28182ba651246ce9bc65608ebdf92363e6342ab8567c`. The 2026-09-16 image (`4b8825fe…`) is superseded.

## Storage

The image is ingested into Nora's own media library (RustFS bytes +
`media_objects` row) exactly like any other blob she holds. The only
difference is a marker in `provenance` jsonb:

```json
{"role": "self", "source": "operator-provided", "canonical": true}
```

Using `provenance` jsonb (existing column on `media_objects`) avoids a schema
migration. The `source` stays `fetched` (no new enum value; marker-only).

## Custody exemption

`resolve(marker, conversation_id)` currently returns nothing when the
marker's row is not in the caller's conversation store (design/30 §5,
the `ImageReference` docstring in `nora/domain/ports.py`; the `resolve` closure inside
`generate_image` in `nora/agent/tools.py` reads only
this conversation's transcript `media_json` via `store.recent(
conversation_id=...)`). The exemption: **if the row's `provenance.role` is
`self`, resolve regardless of conversation.** This is safe because:

- The blob is Nora's own, operator-provided asset — not external/untrusted
  content, not a user's photo.
- It is a single, known, canonical row — no attacker-controlled path can mark
  arbitrary content with a canonical role (the marker is written by the
  operator's ingest path, which is the same trust level as the skill and doctrine files
  themselves).
- No data leaves Nora: resolution only ever produces bytes *she already holds*
  and sends to her configured image provider.

The exemption is written as an **exact role match** (`role = 'self'`), never a
prefix or a set that could be widened by accident.

## Precedence (pinned by contract test)

The resolver looks up the conversation window FIRST; the canonical-role row is a
FALLBACK only. A canonical handle is matched on the FULL sha256, never a
short prefix, so a conversation asset and the canonical asset can never collide
on the same marker code. The existing single-match rule (the `len(matches)` check in `resolve`)
still applies inside each lookup source: ambiguity inside either source
resolves to nothing (safe, opaque), never to a guess.

## As built (2026-09-23, see 06)

- No `provenance->>'role'` query was added to `nora/infra/sql/media_objects.py`:
  the resolver reads the existing `MediaObjectStore.get(sha256=)` and checks
  the role in code, exact string equality. One read, already contract-tested.
- "Collision resolves to nothing" is reachable only as a SHORT-handle
  collision: two distinct blobs cannot share a full sha, and the canonical
  needs a full sha. The pinned forms: a conversation blob sharing the
  canonical's short handle wins; two such blobs resolve to nothing.
- A URL or data URI reference is refused before any lookup (new; the hex
  extraction would otherwise turn a URL wrapping the sha into the handle).
- The resolver tests are unit tests (`tests/unit/test_canonical_self_reference.py`),
  not in the adapter's contract suite, which cannot reach the closure.

## Files touched (as built)

```
nora/domain/ports.py                           # ImageReference docstring: the exemption
nora/agent/tools.py                            # resolve: conversation first, then _canonical_self
                                               #   (full sha, role == "self"); URL/data refusal
scripts/ingest_self_asset.py                   # operator one-shot, checked against the skill's handle
tests/unit/test_canonical_self_reference.py    # the exemption and every negative
tests/unit/test_ingest_self_asset.py           # the ingest
```
