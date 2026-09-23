---
type: design
title: "Self-Reference — implementation plan (build of DEC-193)"
description: "The concrete build: files, functions, the test list re-derived from the code as it stands on 2026-09-23 (not copied from 04), the audiences decision for appearance.md, and where the earlier nodes were wrong against the code."
tags: [nora, design, build, tests]
timestamp: '2026-09-23'
edges:
  - {src: 06-implementation-plan, dst: 04-tests-and-files, rel: realizes, provenance: asserted}
  - {src: 06-implementation-plan, dst: 01-appearance-doctrine, rel: depends_on, provenance: asserted}
  - {src: 06-implementation-plan, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
---

# Implementation plan (2026-09-23)

Written before the code, from the code. Where 01/02/04 disagree with the tree,
the tree wins and the node is corrected in the same PR (§"Bundle corrections").

## 1. Doctrine: `nora/doctrine/appearance.md`

- `kind: identity`, `order: 15` (between `identity` 10 and `voice` 20; no
  other file holds 15).
- **Audiences: `[tag, heartbeat, participation, proactive]`, not `[tag]`.**
  04 Q4's default assumed image generation happens only on tagged turns. The
  code says otherwise: `build_tool_specs` returns `generate_image` in the ONE
  tool tuple every occasion builds (`nora/agent/tools.py`, the `return (...)`
  at the end of `build_tool_specs`), and `nora/app/fire.py` builds the heartbeat,
  participation and proactive turns through the same `ToolRegistry.build`. Only
  the Dreamer's loops are narrowed (`read_only_tools()`), and `generate_image`
  is `read_only=False`, so the Dreamer never sees it and is not an audience.
  A reminder that says «draw me a card at 8» fires on the heartbeat audience and
  can call the tool; that turn needs to know what she looks like as much as a
  tag does. So appearance.md is occasion-neutral, like identity.md, and it
  joins `AUTHORED` in `tests/test_doctrine_audiences.py` rather than being
  excluded from it the way `silence` is.
- **Authority: a true peer of identity/voice.** The loader's own docstring
  (`check_authority_graph`) defines "peer" as byte-identical authority fields
  that do not name each other. 01 proposed `authority_below: [tool-output]`
  only, which would leave appearance UNRANKED against `silence`/`conduct` —
  i.e. not on the identity rung at all. So:
  `authority_above: [constraints]`,
  `authority_below: [silence, conduct, participation-conduct, situation, message-content, tool-output]`,
  and the symmetry the loader enforces adds `appearance` to
  `constraints.authority_below` and to `authority_above` of `silence`,
  `conduct`, `participation-conduct`. Authority fields are not rendered, so
  none of those four files' served sha moves and no version bumps.
- Register constraints the loader enforces on `kind: identity`: no RFC 2119
  uppercase keyword, no snake_case identifier, no backticked tool name. The
  doctrine carries no em dash (the 2026-09-22 pass), so none here either.
- Content: the spec (05) compressed without dropping a rule, plus the
  self-recognition rule with the full-sha handle, the user-reference rule, the
  no-guess ask rule and the conflict/slot-order guidance (01 §Contents, 03).

## 2. Resolver: `generate_image`'s `resolve` closure (`nora/agent/tools.py`)

Order, conversation-first:

1. Read the conversation window exactly as today. One match: that blob. More
   than one: `None` (ambiguous), and the canonical fallback is NOT consulted.
2. Zero matches: the canonical fallback, and only if the marker is a FULL
   64-hex sha256. `media_store.get(sha256=marker)`; the row must exist, its
   `sha256` must equal the marker, and `provenance["role"]` must be exactly the
   string `"self"` (no case-folding, no strip, no prefix, no set).
3. Anything else, including an unreadable conversation or an absent
   `media_store`: `None`.

**No new port method.** 02 named a `provenance->>'role' = 'self'` query in
`nora/infra/sql/media_objects.py`; the existing `MediaObjectStore.get(sha256=)`
already answers the only question the resolver asks (is THIS full sha the
self row), exactly, in one indexed read, and is already contract-tested
against the SQL store. A second read path would be a second implementation
to keep in step for no new question. 02 is corrected.

**External references refused at the tool, before any lookup.** `_handle_hex`
reduces any string to its hex characters, so `https://x/<canonical sha>`
reduces to the canonical sha and, with the exemption, would resolve. Nothing
would be fetched (resolution only returns bytes she holds), but the port's
docstring promises a URL is refused, so a code containing `://` or starting
`data:` is `nothing_found` before resolution. The `ImageReference` docstring
gains the exemption and the tool docstring gains one sentence, because it
currently says every reference must already be in this conversation, which
would contradict the doctrine.

## 3. Ingest: `scripts/ingest_self_asset.py`

`ingest_self(*, data, expected_sha256, blob_store, media_store) -> MediaObject`:
refuse if `sha256(data) != expected_sha256` (the doctrine's handle, read from
`appearance.md`, so the two can never disagree silently); `blob_store.put`;
`media_store.save(MediaObject(source="fetched", provenance=SELF_PROVENANCE, ...))`;
then `media_store.comprehend(..., provenance=SELF_PROVENANCE)`, which is an
upsert that MERGES provenance, so a row that already existed (the operator
once sent the same bytes into a room, first-source-wins kept it `inbound`) is
marked too; then `get` and verify `role == "self"`, or fail. No new store
method. `main()` reads config, builds the SAME blob store the daemon uses
(`nora.app.composition._build_blob_store`) and `SqlMediaObjectStore`, and
exits non-zero on any refusal.

## 4. Tests (re-derived from the tree, not from 04)

Doctrine, existing files:

- `tests/test_doctrine_content.py`: `EXPECTED` gains `appearance: (identity,
  15, (tag, heartbeat, participation, proactive))`; the strict assembly list
  becomes `[identity, appearance, voice, silence, conduct, constraints]`; the
  authority and peer tests include appearance; a new content test pins the
  spec's substance (8 anchors, proportion rule, earphones, backpack optional,
  no human form) and that the embedded handle equals the asset's sha256.
- `tests/test_doctrine_assembly.py`: the shipped list, same six names. **No
  `capability` entry** (04 listed one; DEC-150 deleted the tier).
- `tests/test_doctrine_audiences.py`: `AUTHORED` gains `appearance`.
- `tests/test_doctrine_version_parity.py`: `PINNED["appearance"] = (1, sha)`.
- `tests/web/test_doctrine.py`: the tag-audience list (04 did not know it
  existed).
- `tests/unit/test_fire_path.py`: the proactive occasion-neutral list.

New, `tests/unit/test_canonical_self_reference.py` (fakes only; the resolver
is exercised through `build_tool_specs` and a generator fake that calls the
resolver the way the real adapter does):

- the canonical full sha resolves from a conversation that never held it;
- the doctrine-embedded handle resolves after `ingest_self` of the real asset;
- a non-canonical row in another conversation is refused, for every role that
  is not exactly `"self"` (absent, `Self`, `SELF`, `self `, `selfie`,
  `operator`, a list);
- a short or truncated prefix of the canonical sha never reaches it;
- collision: a conversation blob sharing the canonical's short handle wins
  (one match) or resolves to nothing (two), never the canonical;
- an external URL (including one wrapping the canonical sha) and a data URI
  are refused before the provider is called;
- no `media_store` wired: the canonical sha resolves to nothing.

New, `tests/unit/test_ingest_self_asset.py`: marks role/source/provenance;
marks a pre-existing inbound row; refuses a sha mismatch and writes nothing;
re-run is idempotent.

04 put the resolver tests in `tests/contract/test_image_generator_contract.py`.
That suite exercises the xAI ADAPTER against a local server and takes the
resolver as an injected callable; the closure under change is not reachable
from it, so tests placed there would pin a fake. They go in `tests/unit/`.

## 5. Proof by revert

`scripts/mutations/dec-193-self-reference.json`: drop the canonical fallback,
widen the role match, match the canonical by prefix, drop the URL guard. Each
must turn a named test red; run after the first commit.

## 6. Deploy (operator, on norty; not done by this PR)

The doctrine hot-reloads on pull, the resolver needs the image. The ingest
uses only store verbs the running image already has, so:
`git pull` → copy the script and the PNG into the running `nora` container
and run the ingest (it reads the pulled `appearance.md` for the handle) →
build the image → `up -d --force-recreate` → read back. Between pull and
recreate the doctrine names a handle the old resolver refuses cross-room,
which degrades to `nothing_found`, never a wrong image.

## Bundle corrections made in this PR

- 04 §Enforcing tests listed a `capability` section (deleted by DEC-150) and
  "appearance excluded from AUTHORED, mirroring silence" (wrong once the
  audiences are all four).
- 01 authority: `[tool-output]` alone does not make a peer; corrected.
- 01/02 file:line citations had drifted; replaced by symbol names.
- 02/04 "the media query in `media_objects.py`": not needed (§2).
- 02/04 "collision resolves to nothing": with full-sha canonical matching and
  content addressing, two distinct blobs cannot share a full-sha marker, so the
  pinned form is the short-handle collision above.
