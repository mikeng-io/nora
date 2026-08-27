# Nora Self-Reference — canonical visual identity for self-aware image generation

**Status:** Proposed (nothing built)
**Date:** 2026-08-27
**DEC:** 139 (indexed at the bottom of this document)
**Branch:** `feat/self-reference-design` (worktree `~/workspace/nora-wt-self-ref`)

## 1. What this is

Nora gains a **canonical visual identity**: a doctrine definition of what she
looks like (`appearance.md`), a **canonical self asset** (her design/character
reference image, stored in her own media library), and a **self-reference
injection rule** so that when she is asked to draw herself, she uses her own
reference image instead of guessing from transcript context.

This fixes a live, observed failure: on 2026-08-27 Miles asked Nora to merge his
avatar photo (`1fce23`) with a rainy Tin Shui Chi scene. Nora's reasoning
(recorded in Langfuse) said *"combining his avatar (the 牛來 image ef0606)"* —
she confused a 牛來 meme from earlier in the transcript with Miles's avatar,
because she had **no authoritative description of what the images in her
library are**. The result (`eee623`) was Miles's avatar character rendered as a
cow mascot. The reference mechanism worked; the *identity model* did not.

Note the precise shape of the failure: Nora did not misidentify *herself* —
she misidentified **which of two user-supplied images was Miles's avatar**. So
this DEC must give Nora two canonical anchors, not one: her own appearance AND
the operator's avatar (role: `operator`), plus a deterministic no-guess rule
for the ambiguous case (see §4 Layer 3).

## 2. Why this matters

The current system gives Nora:

- `identity.md` — who she is (a companion agent, name, register)
- `voice.md` — how she sounds (language, register, teasing ceiling)
- `conduct.md` — when she acts (tag rules, presence, silence)

...but **nothing about what she looks like**. Every time a task involves her
own appearance — "draw yourself", "make an avatar", "merge my photo with your
style" — she has no source of truth, so she improvises from transcript
fragments. That improvisation produced a cow. It will produce a different
wrong guess every time, and the failure mode is silent: the image looks
plausible, and Miles has to *look at it* to discover it is wrong.

This is the same shape of problem `identity.md` solved for *behaviour* — a
canonical, injected, authoritative definition — now applied to *appearance*.

## 3. Design constraints

1. **Provider-agnostic, per DEC-137.** The self-reference mechanism must not
   depend on xAI; it rides the existing `ImageGenerator` port and the existing
   `ImageReference` custody model.
2. **Custody must not weaken (DEC-24, R-21).** The existing rule — a reference
   code resolves *inside this conversation only*, to a blob Nora already holds
   — is a security boundary against external-content injection. The self asset
   is an intentional, narrow exception: Nora's own asset, not user-supplied
   content, so the exfiltration surface is zero.
3. **Keep it simple (Miles).** The smallest change that fixes the observed
   failure. Do not build a general "asset registry" abstraction; a single
   canonical self asset with a `role` marker suffices.
4. **Doctrine is the injection path.** `loader.py` already auto-discovers
   `*.md` in `nora/doctrine/` and injects them into the system prompt by
   `applies_to` audience. A new `appearance.md` with `kind: identity` needs
   **zero code changes** to reach the model every turn.
5. **No tool-level intent detection.** Detecting "draw me/yourself" in the
   prompt is unreliable (pronouns are ambiguous — "draw me" could be Miles
   referring to himself). Instead: teach Nora *when* to use her self asset via
   doctrine, and let her vision-capable judgment decide. If she guesses wrong,
   the outcome is a normal wrong image, not a security failure.

## 4. Solution — three layers

```
Layer 1: appearance.md (doctrine, zero code)
  ┌───────────────────────────────────────────────┐
  │ nora/doctrine/appearance.md                    │
  │   kind: identity, order: 15, applies_to: [tag] │
  │   • Textual description of Nora's visual form  │
  │   • "This is what you look like"               │
  │   • Recognition rule: your canonical self      │
  │     reference handle is <SELF_HANDLE>;         │
  │     when asked to draw yourself, use it        │
  └───────────────────┬───────────────────────────┘
                      │ loader auto-discovery (existing)
                      ▼
              system prompt, every turn
              → Nora knows her own appearance

Layer 2: canonical assets (small code, no migration)
  ┌───────────────────────────────────────────────┐
  │ media_objects: provenance jsonb +=             │
  │   {"role": "self"}      — Nora's design image  │
  │   {"role": "operator"}  — Miles's canonical avatar│
  │ RustFS: both already stored (bytes Nora holds) │
  │ resolve(): role in ('self','operator') →       │
  │   cross-conversation custody exemption         │
  │   (narrow, documented, operator-provided only) │
  └───────────────────┬───────────────────────────┘
                      ▼
        canonical handles resolve in ANY conversation
        → she can reference herself / Miles anywhere

Layer 3: reference rules (doctrine text only)
  ┌───────────────────────────────────────────────┐
  │ appearance.md instructs:                      │
  │   "When asked to draw yourself / your avatar, │
  │    set reference=<SELF_HANDLE>"               │
  │   "When the user sends a photo of themselves, │
  │    that is the USER, not you — use their      │
  │    photo as reference, not yours"             │
  │   "Miles's canonical avatar is <OPERATOR_HANDLE>"│
  │   "If the user asks to merge their photo with │
  │    your style, use BOTH references"           │
  │   "If two user photos could both be the       │
  │    requested subject, ASK which one —         │
  │    never guess"                               │
  └───────────────────────────────────────────────┘
```

### Layer 1 — appearance.md (doctrine file)

New authored file in `nora/doctrine/`, `kind: identity` (same kind as
`identity.md`/`voice.md`, so it inherits the existing authority model and
audience filtering). Contents:

1. **Visual description** — derived from the canonical character-design image
   (Miles's Codex-generated setting image): hair, eyes, outfit, accessories,
   setting (Tron-style data centre, neon grid), aesthetic (sharp, capable,
   slightly dishevelled). Written in the same descriptive register as the
   existing doctrine — it is *identity text for the model*, not prose.
2. **Self-recognition rule** — "Your canonical self reference is
   `[image <SELF_HANDLE>]`; when the task is to draw yourself or your avatar,
   reference it. A photo of Miles is NOT you."
3. **Conflict rule** — when Miles supplies his own reference image AND asks for
   "your style", use both; the reference slot budget is a **tool-surface
   convention** (three params `reference`/`reference_2`/`reference_3`,
   `tools.py:3265-3266`) rather than a provider limit — the adapter sends N
   images via `images[]`. The "self wins over a second user image" rule is
   doctrine guidance, labelled advisory not mechanical.

**Authority fields (loader-enforced, `loader.py:51-54,459-472`).**
`appearance.md` is a peer of `identity`/`voice` — same tier, names neither.
`authority_above: [constraints]`, `authority_below: [tool-output]` (both
terminals, no reciprocity), plus a substantive `not_here` (len > 40, per
`test_doctrine_content.py:71-73`). Explicit here so the build does not
rediscover the loader by error.

`order: 15` places it between `identity` and `voice` in the assembly.

**Files touched:** `nora/doctrine/appearance.md` (new), `tests/doctrine`
exact-order assertions updated (see Files-touched in §6 — this includes
`test_doctrine_assembly.py`, which is NOT listed in the diagram above and
whose exact section lists break on the new file).

### Layer 2 — canonical assets (self + operator)

**Storage.** Two images are ingested into Nora's own media library (RustFS
bytes + `media_objects` row) exactly like any other blob she holds. The only
difference is a marker in `provenance` jsonb:

```json
{"role": "self",      "source": "operator-provided", "canonical": true}
{"role": "operator",  "source": "operator-provided", "canonical": true}
```

Using `provenance` jsonb (existing column) avoids a schema migration. The
`source` stays `fetched` (no new enum value; marker-only).

**Custody exemption.** `resolve(marker, conversation_id)` currently returns
nothing when the marker's row is not in the caller's conversation store
(design/30 §5, `ports.py:1781-1798`). The exemption: **if the row's
`provenance.role` is `self` or `operator`, resolve regardless of
conversation.** This is safe because:

- The blob is Nora's own, operator-provided asset — not external/untrusted
  content, not another user's photo.
- It is a small, known, canonical set of rows — no attacker-controlled path
  can mark arbitrary content with a canonical role (the marker is written by
  the operator's ingest path, which is the same trust level as doctrine
  files themselves).
- No data leaves Nora: resolution only ever produces bytes *she already holds*
  and sends to her configured image provider.

**Precedence (pinned by contract test).** The resolver looks up the
conversation window FIRST; a canonical-role row is a FALLBACK only. A
canonical handle is matched on the FULL sha256, never a short prefix, so a
conversation asset and a canonical asset can never collide on the same
marker code. The existing single-match rule (`tools.py:3342-3345`) still
applies inside each lookup source: ambiguity inside either source resolves to
nothing (safe, opaque), never to a guess.

**Files touched:** `nora/domain/ports.py` (resolve contract wording + the
conversation-scope note), the resolver closure in `nora/agent/tools.py`,
`nora/infra/sql/media_objects.py` (query honoring `provenance->>'role' IN
('self','operator')`), `tests/contract/test_image_generator_contract.py` (new
cases: canonical role resolves cross-conversation; non-canonical still
refused; collision between conversation asset and canonical asset resolves to
nothing; external URL still refused), ingest path CLI/script to mark the
canonical assets, and a contract test asserting the doctrine's embedded
handles resolve to `role IN ('self','operator')` rows (a re-ingest that
changes a sha fails loudly at test time instead of silently producing a dead
handle).

### Layer 3 — reference rules (doctrine text)

No code. The *rules* live in `appearance.md`; the *mechanism* already exists
(reference marker → resolve → `/v1/images/edits` JSON, fixed in the xAI JSON
adapter earlier today). Nora has vision; she sees the reference image and the
prompt; the doctrine tells her which is herself, which is Miles, and which
photos are neither. The failure mode of a wrong guess is a wrong image —
normal model error, not a security or custody issue.

**Deterministic no-guess rule (the reproduce-prevent for the live
incident).** When more than one user-supplied photo could be the requested
subject, Nora ASKS which one — she never guesses. This is what the 2026-08-27
failure actually calls for: the transcript held two user photos (牛來 meme
`ef0606`, avatar `1fce23`) and she guessed wrong. With the canonical
operator avatar pinned, the common case resolves instantly; the ask rule
covers the residue.

## 5. Open questions (operator decision needed)

1. **Which image is canonical for `self`?** Two candidates:
   - The Codex character-design image sent to Norty this session (in Hermes
     cache as `img_b8b045cca7dc.jpeg`, described by vision analysis — Tron
     data-centre setting, neon grid).
   - The `1fce23` image Nora holds (the one Miles sent her on Signal, used as
     reference in the failing cow run).
   These may be the same character or different; the canonical asset must be
   the *design reference* — the one that defines the look, not an example
   render. **Decision needed: confirm which image becomes the self asset.**
2. **Which image is canonical for `operator`?** The avatar Miles asked Nora to
   merge in the failing run is `1fce23` — unless Miles has a different
   canonical avatar in mind. **Decision needed: confirm the operator asset.**
   (Default proposal: `self` = Codex design image, `operator` = `1fce23`.)
3. **Doctrine `appearance.md` text should be written from the canonical
   image.** If Miles picks a different image than the one Norty has seen, the
   description must be re-derived from that image before the doctrine is
   finalized.
4. **Standing observation (unrelated but flagged):** Miles reported not being
   sure whether the Standing injection exists / whether reasoning content is
   retained. This session verified *reasoning content is present* in Langfuse.
   The Standing question is a separate diagnostic, tracked outside this DEC —
   this design does not touch `nora/domain/standing.py`.
5. **Should `appearance.md` also be injected for `heartbeat` audiences?**
   Default: `[tag]` only (image generation happens in tagged turns). If
   heartbeat turns can also generate images, widen.

## 6. Files touched (when built)

```
nora/doctrine/appearance.md                    # new — kind: identity, order 15
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
tests/test_doctrine_content.py                 # authored set + exact-order now include appearance.md
tests/test_doctrine_assembly.py                # exact section-list assertions include appearance.md
tests/test_doctrine_audiences.py               # appearance is tag-only; excluded from AUTHORED,
                                               #   mirroring silence
config.toml                                    # (none expected — asset handle goes in doctrine, not config)
```

## 7. What this deliberately does NOT do

- **No new tool.** `generate_image` stays; only its reference resolution
  gains the canonical-role exemption.
- **No "asset registry" abstraction.** Two canonical assets (self, operator),
  marker in existing jsonb. If video/pets/other character assets appear
  later, the `role` pattern extends naturally — that is a future DEC, not
  this one.
- **No auto-inject on pronoun detection.** (See §4 Layer 3 rationale.)
- **No change to the provider-agnostic contract.** The adapter does not learn
  about "self" or "operator"; it just receives a reference handle like any
  other.
- **No new schema.** `provenance` jsonb carries the role marker; no
  migration, no new enum value for `source`.

## 8. DEC-139 (index row)

| ID | Statement | Enforcing test |
|---|---|---|
| DEC-139 | **Nora has a canonical visual identity: a doctrine `appearance.md` (kind: identity) describing her look and the operator's avatar, plus two canonical assets in her media library (marked `provenance.role` = `self` and `operator`), which resolve as image references in ANY conversation (narrow custody exemption, operator-provided assets only) so she can reference her own design image when asked to draw herself and Miles's avatar when asked to draw him; when more than one user photo could be the requested subject she ASKS rather than guessing; doctrine text, not tool-level intent detection, decides when canonical assets are used.** | `tests/contract/test_image_generator_contract.py` (canonical-role resolves cross-conversation; a non-canonical row in another conversation is still refused; collision between conversation asset and canonical asset resolves to nothing; external URL still refused; doctrine-embedded handles resolve to canonical rows), `tests/test_doctrine_content.py` (appearance.md present, kind=identity, no constraint keywords), `tests/test_doctrine_assembly.py` (exact section lists include appearance), `tests/test_doctrine_audiences.py` (applies_to includes tag; appearance excluded from AUTHORED mirroring silence), vision-verified smoke: "draw yourself" with no user reference yields an image consistent with the canonical design |
