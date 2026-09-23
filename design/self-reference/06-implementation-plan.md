---
type: design
title: "Self-Reference — implementation (the build of DEC-193)"
description: "What was built and why it differs from 01-04: an on-demand image-generation skill (not always-loaded doctrine), the resolver's custody exemption, the operator ingest, the tests re-derived from the tree, the provider research with its sources, and the conflicts found."
tags: [nora, design, build, tests, skill, image-gen]
timestamp: '2026-09-23'
edges:
  - {src: 06-implementation-plan, dst: 04-tests-and-files, rel: realizes, provenance: asserted}
  - {src: 06-implementation-plan, dst: 01-appearance-doctrine, rel: depends_on, provenance: asserted}
  - {src: 06-implementation-plan, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
---

# Implementation (2026-09-23)

Written from the code. Where 01-04 disagree with the tree, the tree wins and
the node is corrected in the same PR (§7).

## 1. Placement: a skill, not doctrine (operator decision, 2026-09-23)

01 designed an `appearance.md` doctrine file (never created), injected every turn. The operator
reversed that before it was built: her look matters only when she makes an
image, and design/12's placement rule puts occasional, domain-specific
instruction in a skill, loaded on demand, not in the system prompt. A second
refinement the same day widened it from "appearance" to **image generation**,
with her appearance as one section of it.

So the build is `nora/skills/image-generation/SKILL.md`, and there is no
`appearance.md`.

**The trade-off, stated rather than hidden.** A skill reaches the model only
if she spends a `skills` call on it before `generate_image`. A turn that skips
the skill draws without the canon: no handle, no anchors, no in-the-picture
judgement. The description is therefore written to fire on making or editing
ANY image (not only on drawing herself), because the judgement of whether she
is in the picture lives inside it. The same cost was named for the retired
`self-description` skill (#227), which moved INTO doctrine for the opposite
reason; here the content is needed on a small fraction of turns, which is
what makes the skill the right home. Also true of a skill and recorded here:
its `authority_above` reaches nothing (#241) and `LeakGuard` does not cover
skill bodies, so she may recite her own description; nothing in it is secret.

Skills are baked into the image (`Dockerfile` copies `nora/`, and compose
bind-mounts only `nora/doctrine` and `nora/dreamer`), so the skill ships with
the image rebuild, not on `git pull`.

## 2. The skill

Sections, in order:

1. **Are you in the picture?** A judgement with its goal, never a menu of
   trigger phrases (operator rule): decide whether she herself appears in the
   picture asked for; if she does, her canonical picture keeps her
   recognisable; if not, attaching it puts her where she was not asked to be.
   Never a default. When reference photos are switched off
   (`[image_gen] allow_reference_images = false`, the template default), her
   picture cannot travel either, and she describes herself with the anchors.
2. **Whose photo is whose.** A person in the room is not her; the user's own
   photo is the user; style-merge uses both; more than one candidate photo →
   ask, never guess (03's rule, the sole cover for the operator-photo class).
3. **What you look like.** Every rule of 05 (see §6 for the audit).
4. **Writing for the model that draws it**, one subsection per configured
   model; a new model or provider adds a subsection.

**What it does not restate.** DEC-150 made the tool schema the one place a
tool is described. `generate_image`'s docstring already carries our prompt
convention (design/proposed/29 §5.2: `media_fetch` before transforming;
`[what the photos show] + [what to change] + [what to keep]`; no identifiers
in the prompt; a pure style transfer needs no description) and the three-slot
limit. The skill adds only what the docstring lacks. The docstring gained one
clause: the canonical picture is the one reference that works in any room
(without it the docstring would contradict the skill).

**Size.** 49 non-blank body lines against a 30-line default ceiling in
`tests/test_skill_content.py`; the operator ruled completeness over brevity
for a skill that loads only on image turns, so that test now carries a
per-skill ceiling (60) with the reason beside it.

## 3. Provider research (xAI), sources

Only statements the documentation makes, fetched 2026-09-23:

- Editing takes a source image with the prompt and "applies your requested
  changes"; styles are changed by "describing the desired aesthetic in your
  prompt"; edits can be chained, "using each output as the input for the
  next". <https://docs.x.ai/developers/model-capabilities/images/editing>
- Multi-image editing takes up to five source images; "by default, the output
  aspect ratio follows the first input image", overridable with
  `aspect_ratio`.
  <https://docs.x.ai/developers/model-capabilities/images/multi-image-editing>
- Parameters (`image` vs `images`, `aspect_ratio`, `resolution` 1k/2k,
  `response_format`): <https://docs.x.ai/developers/rest-api-reference/inference/images>
  and <https://docs.x.ai/developers/model-capabilities/images/generation>.
- `grok-imagine-image-quality` is retired on 2026-11-02; from then requests
  to it "are served by `grok-imagine-image-2.0` with `quality` set to `low`".
  <https://docs.x.ai/developers/migration/imagine-image-quality-nov-2>
- The docs carry **no prompting guidance** as such: the generation and
  overview pages show example prompts only.
  <https://docs.x.ai/developers/model-capabilities/imagine>

What the skill says per model is exactly those facts, turned into what she
does with them (order references by the framing wanted or name an aspect;
describe a style in words; refine by editing the last output; the quality
option buys nothing after 2026-11-02).

## 4. Conflicts between the provider and our convention (not resolved here)

1. **Reference order.** The `generate_image` docstring says of the three
   slots "the first that is set is not special". xAI says the output takes the
   first image's aspect ratio unless one is set. The docstring is right about
   the slots as a tool API and wrong about the output; the skill states the
   provider fact. Recommend the docstring say it; not changed here because the
   docstring is the tool's own surface and the change is a separate decision.
2. **`<IMAGE_n>` placeholders.** The adapter appends `<IMAGE_0>…` names to a
   multi-reference prompt, citing docs.x.ai. None of the pages above mentions
   that syntax. Neither confirmed nor refuted; the skill does not mention it.
3. **The quality model's retirement.** `config.example.toml` still names
   `grok-imagine-image-quality`; after 2026-11-02 it silently becomes
   2.0-at-low. That is a config change owed before that date, outside this
   bundle.
4. **Slot count.** The provider takes five references, the tool three
   (a tool-surface convention, 01). No conflict of behaviour; noted.

## 5. Resolver and ingest (as built)

`generate_image`'s `resolve` (`nora/agent/tools.py`): the conversation window
first, unchanged; more than one match → nothing, and the canonical row is
never a tie-breaker; zero matches → `_canonical_self`, which accepts only a
FULL 64-hex sha, reads `media_store.get`, and requires the row's
`provenance["role"]` to be exactly the string `"self"`. No new port method:
`get(sha256=)` answers the only question asked, and is already
contract-tested against the SQL store (02's `provenance->>'role'` query was not
needed). A reference that is a URL or a data URI is refused before any lookup:
`_handle_hex` keeps only hex characters, so `https://x/<sha>` would otherwise
reduce to the self handle.

`scripts/ingest_self_asset.py`: reads the single `[image <sha>]` handle out of
the shipped skill, refuses bytes that do not hash to it, `put`s the blob,
`save`s the row (`source="fetched"`, provenance
`{"role":"self","source":"operator-provided","canonical":true}`), then
`comprehend`s with the same provenance (the port's merge-upsert, so a row that
first-source-wins had already claimed as `inbound` is marked too), and reads
it back, failing unless `role == "self"`.

## 6. Tests and proof

- `tests/unit/test_canonical_self_reference.py`: canonical resolves in a room
  that never held it (bare and `[image …]` forms); every role that is not
  exactly `"self"` refused (absent, `Self`, `SELF`, `"self "`, `" self"`,
  `selfie`, `operator`, a list, `True`); a 6/12/63-char prefix never reaches
  it; a conversation blob sharing the short handle wins; two such blobs →
  nothing; the full sha still resolves beside that collision; URL / data URI
  refused before the provider; no media store → nothing; missing bytes →
  nothing; the skill's handle resolves after `ingest_self` of the real asset.
- `tests/unit/test_ingest_self_asset.py`: marks role/source; marks a
  pre-existing inbound row; refuses a sha mismatch writing nothing; refuses an
  unreadable index; idempotent; handle parsing.
- `tests/test_skill_content.py`: discoverable and triggers on any image; the
  handle equals the asset's sha256; the in-the-picture judgement is first and
  is not a phrase menu; user-photo and no-guess rules; a needle per load-
  bearing clause of 05 plus a count of eight anchors; one section per
  configured model; no restatement of the docstring; not in any assembled
  doctrine audience, and no `appearance.md`.
- `tests/test_factory.py`: the available-skill list gains `image-generation`.

04 placed the resolver tests in `tests/contract/test_image_generator_contract.py`.
That suite drives the xAI adapter against a local server with an injected
resolver; the closure under change is unreachable from it, so they are unit
tests here.

**Proof by revert**, `scripts/mutations/dec-193-self-reference.json`, all
eight RED: fallback dropped (two targets), role case-folded, role check
dropped, prefix match, URL guard dropped, ingest never marks, ingest skips the
sha check. **One equivalent mutant, recorded:** letting an ambiguous window
fall through to the canonical row cannot change a result, because the window
matches by prefix and the canonical needs a full sha, and two distinct blobs
cannot share a full sha. So "collision resolves to nothing" (02/04) is pinned
in its reachable form, the short-handle collision.

### Spec audit (05 → skill)

Every clause is carried, compressed; wording changed, no rule dropped. Four
places are softer than the spec's words and are named: the backpack's
"everyday-life scenes" is written as "errands"; "high-quality 3D/CGI
rendering" is carried as "stylized 3D, tactile and collectible" without the
word high-quality; the outfit's "casual, understated, contemporary,
functional" adjectives are carried by the item list and the no-logo rule
rather than stated; "the jeans should not produce a fashion-model
silhouette" is written "never fitted".

## 7. Deploy (operator, on norty; not done by this PR)

The skill and the resolver both ship in the image; the ingest reads the
handle from the running container's copy of the skill.

1. `git pull` on norty's checkout (after `git status -sb` is clean).
2. Build the image, then `docker compose up -d --force-recreate nora`.
3. `docker cp scripts/ingest_self_asset.py nora:/tmp/ingest_self_asset.py`
   and `docker cp assets/nora-canonical-self.png nora:/tmp/nora-canonical-self.png`.
4. `docker compose exec -T nora uv run --no-sync python /tmp/ingest_self_asset.py /tmp/nora-canonical-self.png`
   → `ok: 535e4da5… role=self source=fetched`.
5. Read back: `select provenance from media_objects where sha256 = '535e4da5…'`.

Between 2 and 4 the handle resolves to nothing (`nothing_found`), never a
wrong image. Reference images stay off unless `[image_gen]
allow_reference_images = true` in the live config; with it off, the skill
tells her to describe herself instead.

## 8. Bundle corrections made in this PR

- 01 is now the skill's appearance section, not a doctrine file; its
  authority, order and audience fields no longer apply.
- 04's test list named a `capability` doctrine section (deleted by DEC-150)
  and doctrine assembly/audience tests that no longer apply.
- 01/02 file:line citations had drifted; replaced by symbol names.
- 02/04's `provenance->>'role'` query was not needed (§5).
- 02/04's "collision resolves to nothing": see the equivalent mutant (§6).
