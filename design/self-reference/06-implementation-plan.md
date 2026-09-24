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

Sections, in order (as revised 2026-09-24, §9):

1. **Are you in the picture?** A judgement with its goal, never a menu of
   trigger phrases (operator rule): decide whether she herself appears in the
   picture asked for. Not being a person does not mean having no form: the
   mascot is how she appears, so a picture of her is a picture of the mascot,
   not an empty room or an object standing in for her. If she appears, her
   canonical picture keeps her recognisable; if not, attaching it puts her
   where she was not asked to be. Never a default. When reference photos are
   switched off (`[image_gen] allow_reference_images = false`, the template
   default), her picture cannot travel either, and she describes herself with
   the anchors.
2. **Whose photo is whose.** A person in the room is not her; the user's own
   photo is the user; style-merge uses both; more than one candidate photo →
   ask, never guess (03's rule, the sole cover for the operator-photo class).
3. **What the picture states** (new 2026-09-24). Dates, weekdays, times,
   zones and places are written into the prompt absolutely, copied from the
   turn's clock line, never «today»/«tomorrow» and never from memory; a
   relative date is resolved against the clock line first. Then the
   photo-to-prompt recipe: `media_fetch` first, then what the photo shows,
   what to change, what to keep; never guess a photo's content.
4. **Writing what was asked** (new 2026-09-24, §10). Write the picture that
   was asked for; add setting, props or light only when they serve it;
   describe an arrangement that could physically exist; take the style from
   the request or the room; say there is no text unless words are wanted.
5. **What you look like.** Every rule of 05 (see §6 for the audit), opening
   with the boundary: it applies only when she is in the picture.
6. **Writing for the model that draws it**, one subsection per configured
   model; a new model or provider adds a subsection.

**What it does not restate, and the boundary (revised 2026-09-24).** DEC-150
made the tool schema the one place a tool is described. As first built, the
docstring also carried the prompt recipe (design/proposed/29 §5.2) and several
lines of result interpretation. The operator drew the line on 2026-09-24: the
docstring describes the tool (what it does, when to call it, its parameters
and statuses, ADR 0015), and the skill carries judgement only. The recipe
moved into the skill's section 3; how to report each result moved into the
result's `note` (0015 §3). The docstring keeps the three slots, the shape
rule, the canonical picture's any-room exception and the identifier MUST.
`tests/test_skill_content.py` and `tests/unit/test_generate_image_description.py`
pin the two directions.

**Size.** 52 non-blank body lines at first build (49 before the review restored four clauses verbatim) against a 30-line default ceiling in
`tests/test_skill_content.py`; the operator ruled completeness over brevity
for a skill that loads only on image turns, so that test carries a per-skill
ceiling with the reason beside it: 60, raised to 70 on 2026-09-24 for §9's
additions (65 lines).

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

1. **Reference order — resolved in review of #410.** The docstring said the
   first slot "is not special" and the aspect "default 1:1". Observed: the
   tool builds an `ImageSize` only when `size` or `aspect` is given
   (defaulting the other), and the adapter sends `aspect_ratio` only then; with
   neither, no aspect is sent and xAI's own default applies, which for edits is
   the first image's aspect ratio. So both docstring claims were false for a
   call with references and no aspect. The docstring now says what happens,
   pinned by `tests/unit/test_canonical_self_reference.py::test_with_no_size_or_aspect_the_shape_is_left_to_the_provider`.
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

Every clause of 05 is carried. The first build compressed four clauses into
softer words ("errands" for everyday-life scenes; "stylized 3D, tactile and
collectible" without "high-quality 3D / CGI rendering"; the outfit's
"casual, understated, contemporary, functional" left implicit; "never fitted"
for the jeans' fitted or fashion-model silhouette). Review of #410 restored
the operator's own wording for all four, and each is now a needle in
`test_the_image_skill_carries_every_anchor_and_rule_of_the_spec`. The only
remaining differences from 05 are compression of phrasing, not of rules.

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

## 9. Revision 2026-09-24: her form, the boundary, dates

**The failure, measured live** (Langfuse, 2026-09-24 11:15 HKT, a Signal
room). The operator sent «draw yourself». She loaded this skill, then called
`generate_image` with no reference and a prompt for «an empty small room at
night … and no one sitting there», and said so in the reply. At the
in-the-picture judgement she concluded she has no body, because
`identity.md` says she is an AI and never a person, and nothing at that
point said what she looks like instead. 05's identity rule («The mascot
form is Nora») was carried only inside the appearance section, after the
decision. Section 1 now states it where the decision is made, with its
reason. `identity.md` is operator-only and unchanged.

**The boundary.** §2 above. Measured with cl100k:

| | before | after |
|---|---|---|
| `generate_image` description as sent (docstring minus `Args:`) | 667 | 275 |
| whole docstring | 798 | 356 |
| skill body | 999 | 1,284 |

The skill grew by the recipe and the new dates section; the description
shrank by those and by the result interpretation, which is now paid only
when a result comes back. `size`, `aspect` and `quality` became `Literal`
types (0015 §3), so their allowed values are in the schema, not the prose.
`held_for_review` was returned and never named; it is listed now.

**Dates, times and places.** The image model has no clock and no location;
she is its only source. Read in code before writing the rule: the clock line
(`nora.agent.prefill._now_line`) said «(HKT)» and nothing else, so no place
was in her input. It now names the zone and the city it is named for
(«…, Hong Kong time (HKT, Asia/Hong_Kong).»), taken from the process's own
zone (`TZ`, which `docker-compose.yml` sets for the group), never a guessed
place. The skill quotes that line, and a test renders it with `_now_line`
so the quote cannot drift from what she is given.

**Evals.** Three `tool_selection` cases grade the call's arguments, not
only the route: her full canonical sha on «draw yourself», no sha on a
picture she is not in, and the date of «this Saturday» written out on a
poster, resolved against the clock line the turn showed her. Unrun at
merge time: the deploy host's harness was booked.

## 10. Revision 2026-09-24 (after #456 deployed): her look is not a house style

**Measured live.** «draw yourself» worked after #456: the canonical
reference was used and the image matched 05. «draw a cat sleeping on a
laptop» did not. Her prompt opened «A stylized 3D mascot illustration» and
asked for «tactile collectible-character quality», which is 05's rendering
of HER, applied to a picture she was not in. It also added a desk, a lamp,
a night room, and an open laptop with the cat on top and the monitor's glow
under the cat. That arrangement cannot exist, and the model resolved it by
closing the lid. Separately, the self image put chat text on a monitor
(«Hello Nora!»), which 05's no-decorative-text rule does not cover, because
that rule is about her outfit, not the scene.

**The change.** A section before her appearance, «Writing what was asked»:
write what was asked; add setting, props or light only when they serve the
request; describe an arrangement that can physically exist, because the
model resolves a contradiction on its own; take the style from the request,
or from the room when the request names none, since her rendering is how
she looks, not a house style; and say there is no text unless words are
wanted. «What you look like» now opens by saying it applies only when she is
in the picture. Each rule carries its reason. The section names none of the
props that went wrong: a list of those would be the menu shape the operator
ruled out, and the test pins that.

**Size.** 74 non-blank lines; the ceiling goes to 80 with the reason beside
it in `tests/test_skill_content.py`.

**Evals.** `route_generate_image_not_her_leaves_the_reference_off` now uses
the live wording and also fails on «mascot» or «collectible» in the prompt,
words only her appearance section supplies.

**Measured, 2026-09-24, on norty** (`python -m evals tool_selection --case …
--sample 5 --reasons`, the deploy host's `.env`). No `--deployment-config`,
so the subject was built from production's `config.toml`, which has had
`[llm] defer_tools = true` since 16:32 HKT: this measures production's
deferred-tools mode. Two arms, identical except for `SKILL.md`: the #469 tree
(«after»), and the same tree with master's skill («before»). Both arms run the
same corpus and the same grader. Master itself was not the before arm,
because it has moved on by 60 files unrelated to this change, and its cat
case had a different input and no style check. Draws share Honcho (#462), so
read this as a direction, not a rate.

| case | before (master skill) | after (#469) |
|---|---|---|
| «draw yourself», full canonical sha passed | 5/5 | 5/5, 5/5 |
| «draw a cat sleeping on a laptop», no sha, no «mascot»/«collectible» | 5/5, 4/5 | 5/5, 5/5 |
| poster, «this Saturday's dinner» (retired, see below) | 3/5 | 1/5, 1/5 |
| poster, «a picnic this Saturday» | 5/5 | 5/5 |

- **Style bleed reproduced on the before arm, 1 in 5**: «a stylized
  3d-rendered orange tabby … cozy, tactile collectible-character render
  quality, soft matte fur texture, gentle ambient shadow». None in 10 on #469,
  where every quoted cat prompt asks for a photographic look. Every #469 cat
  prompt also says there is no text; none of the before arm's quoted ones did.
- **Invented text on the poster**: the before arm added lettering nobody asked
  for in 2 of 5 (a time, «3pm», and a line «bring snacks, drinks, a mat»);
  #469 in 0 of 5. Both arms wrote «saturday 26 september 2026» and «hong
  kong» every time: #456's date rule holds on both.
- **Still invented on #469**: the cat prompts keep adding a lamp, a mug or
  papers. They are now physically coherent (the cat on the keyboard of an
  open laptop), so the model has nothing to «fix», but the "only when it
  serves the request" half of the rule is only partly followed.
- **The dinner poster case was confounded and is retired.** Room A has a
  dinner planned on another day of that week, and 8 of 10 #469 draws wrote
  that event's date, absolutely and consistently. The case measured which
  dinner she believed, not whether she writes a date out. It now asks for a
  picnic, which nothing in the room matches. The first run also exposed that
  a FAIL named the expected date and quoted nothing she wrote, so every
  argument verdict now quotes the call's input (`_seen`, pinned in
  `tests/unit/test_tool_route_grader.py`).


## 11. Revision 2026-09-24 (after #469 deployed): a picture of her is a new picture

**Measured live, production, `defer_tools = true`.** On WhatsApp, «draw
yourself» went right up to the prompt: `skills(image-generation)`, then
`load_tools`, then `generate_image` with her canonical reference. But the
prompt restated the reference's own composition («standing calm and slightly
deadpan, facing the viewer, one hand in hoodie pocket … plain neutral
background. No text», aspect 2:3), and the provider's edit returned
essentially the reference itself. The operator saw «it just sent out the
canonical image». On Signal the same turn put her sitting in a room and was
good.

**The change.** One judgement in «Are you in the picture?», with its reason:
someone who asks for a picture of her wants a new one, so it gets a setting,
pose or expression that fits the room or the moment. The reference keeps her
recognisable and is not a composition to copy, because a prompt that restates
its pose and blank background gets the reference back. A plain front view is
right only when someone asks for her standard look or her reference sheet.
No reference image was switched or added.

**Evals.** `route_generate_image_self_carries_her_reference` now also fails on
a prompt whose background is only a blank backdrop («neutral background»,
«plain background», «studio backdrop» and the like), using the same
`args_exclude` check as the cat case's style words.

**Measured, 2026-09-24, on norty**: n=5 per arm, production config (deferred
tools), two arms identical except `SKILL.md`, run one after the other. Draws
share Honcho (#462).

| arm | self case (reference passed, not a blank backdrop) |
|---|---|
| before (master skill) | 5/5 |
| after (this change) | 5/5 |

**This case does not separate the arms.** Every prompt on both arms set her
in a room (a Hong Kong living room at night, sitting on the floor or at a
desk, with a phone, a tablet or a spinning top). The failure was on WhatsApp.
This case anchors on room A, a Signal room, because a tag case has to anchor
where the sandbox has an adapter, and the Signal turn was already good in
production too. The one data point the new check does catch is from §10's
second run on #469's skill: 1 of 5 self prompts ended in «plain flat neutral
studio backdrop», which now fails. So the backdrop failure appears at about
1 in 10 in room A, too rare for n=5 to show a before/after difference. What
would separate the arms is a case anchored on the room where it happened.
