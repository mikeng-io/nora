---
kind: spec
title: "The Dreamer's prompt substrate: versioned, hashed, hot-reloading, and stamped onto what it produces"
description: "Nora's own doctrine is versioned, gated, hashed into every trace, and hot-reloads from a bind mount. The Dreamer's model prompts are ~9,500 characters of inline Python constants with no version, no hash, no gate, and no reload — and none of the rows they produce records what produced them. A wrong lexeme is already in the record with no way to ask which prompt wrote it. This gives the Dreamer's model-calling instances the substrate the doctrine already has, and adds a provenance column to every table they write. PREREQUISITE for FactSynthesizer, because a fact is durable and prevention-shaped and an unattributable one cannot be evaluated or revoked."
tags: [nora, spec, dreamer, prompts, provenance, doctrine, prerequisite]
timestamp: '2026-08-20'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
  - docs/superpowers/specs/2026-08-15-room-language-dreamer-design.md
  - design/09-decision-index.md
---

# The Dreamer's prompt substrate

## 0. What this document is

The operator's question, on reviewing where `FactSynthesizer` would live:

> *"also about the doctrine of those dreamer agent, are they abstracted and with
> arch? i dont want everything slam into a inline text"*

and, on being shown the answer:

> *"spec it as its own item, its a prerequisite"*

The architecture it sits inside is his framing, recorded here because it is the
thing this document assumes:

> *"The 'dreamer' is the abstracted module, and the room consolidator, the room
> portrait synthesizer, and the fact synthesizer are all instances of the
> dreamer."*

**This is a prerequisite, not a nice-to-have.** §3 is the argument. It blocks
`FactSynthesizer` and nothing else.

## 1. Evidence

Measured against HEAD and the running deployment, 2026-08-20.

### 1.1 The prompts are inline constants

| file | constant | line |
|---|---|---|
| `nora/language/observer.py` | `_PROMPT_PREAMBLE` | 86 |
| `nora/language/observer.py` | `_VERIFICATION_PROMPT_TEMPLATE` | 133 |
| `nora/language/exchange_observer.py` | `_PROMPT_PREAMBLE` | 45 |

~9,500 characters of model-facing prompt text, compiled into the image.

`grep -rn "sha256\|version" nora/language/*.py` returns **nothing**. No version
field, no hash, no gate, no reload.

### 1.2 Nothing the Dreamer writes records what wrote it

Live columns, read from `nora-db-1`:

```
room_lexeme:            scope_id, canonical_form, sense, first_seen_at, last_seen_at,
                        occurrence_count, distinct_speakers, recognition_score, state, examples
room_exchange:          scope_id, act, participants, first_seen_at, last_seen_at,
                        occurrence_count, recognition_score, state, examples
room_portrait:          scope_id, narrative, lexeme_basis, exchange_basis, synthesized_at
language_observation:   …, extractor_confidence
exchange_observation:   …, extractor_confidence
```

**No prompt version. No prompt hash. No model name.** `extractor_confidence` is
the model's own self-report, which is the opposite of provenance.

### 1.3 It has already produced a wrong row, unattributably

The Dreamer glossed `big walk` as:

> 群內約定俗成嘅散步/行路活動，可能係夜晚出去行街
> *(a walking/strolling activity the group has settled on, maybe going out at
> night)*

*Big Walk* is a co-op puzzle video game by House House. The gloss is wrong, it
was rendered into every subsequent turn's `<room_language>` block, and **there is
no way to ask which prompt version produced it** — so there is no way to know
whether a later prompt edit fixed it or whether it predates the edit.

### 1.4 The asymmetry with the doctrine

| | `nora/doctrine/*.md` | Dreamer prompts |
|---|---|---|
| structured frontmatter | ✅ `name`, `version`, `kind`, `order`, `applies_to`, `authority_*`, `not_here`, `change_policy` | ❌ |
| versioned | ✅ (`voice: 14`) | ❌ |
| hashed into the trace | ✅ `doctrine_sha256` on every tag | ❌ |
| gated at load | ✅ 8 checks (`check_kind_boundaries`, `check_authority_graph`, `check_normative_register`, …) | ❌ |
| hot-reload | ✅ mtime, bind-mounted — live on merge, no rebuild | ❌ compiled into the image |
| operator-editable | ✅ | ❌ |
| stamped on output | ✅ `doctrine_versions={…}` per turn | ❌ |

This is the **pre-DEC-29 state**. DEC-29's argument was that a hand-written
catalogue goes stale and nothing catches it; the doctrine loader exists because
of it. The Dreamer's prompts never got that treatment.

## 2. Scope — exactly two instances

Only instances that **make a model call** are in scope. Measured:

| instance | model-call sites | in scope |
|---|---|---|
| `observer.py` — `LexemeObserver` | 3 | ✅ |
| `exchange_observer.py` — `ExchangePatternObserver` | 2 | ✅ |
| `portrait_synthesizer.py` — `RoomPortraitSynthesizer` | **0** | ❌ |
| `consolidator.py`, `exchange_consolidator.py` | 0 | ❌ |

`portrait_synthesizer.py` is out **by its own design**, and the reason is worth
preserving rather than accidentally overturning (its module docstring):

> "No model call anywhere … the inputs here are already model-vetted,
> already-structurally-safe output — a THIRD model call synthesizing prose from
> them would reintroduce exactly the [risk]."

It composes deterministically from already-vetted rows. It has no prompt to
version. Its `_CLOSING_FRAME` (line 41) is appended to the stored narrative
(`sentences.append(_CLOSING_FRAME)`, line 111) — **stored text, not a prompt** —
and stays where it is.

**Also out of scope:** the render-side frames that build Nora's prompt blocks
(`render.py:_CLOSING_FRAME`, `exchange_render.py:_CLOSING_FRAME`,
`ROOM_BLOCK_NOTE`). Those are prompt-block text on the tag path, a different
concern with a different lifetime, and they were just changed by the 2026-08-20
audit's A3. Versioning them is a separate question this document does not open.

So: **three prompts, two files.** `FactSynthesizer` would be the third
model-calling instance and the fourth prompt — which is the point.

## 3. Why this is a prerequisite for FactSynthesizer

Three reasons, in increasing order of how much they hurt.

**3.1 A fourth inline blob entrenches the pattern.** The cheapest moment to fix
this is before the instance that most needs it is written against the old shape.

**3.2 A fact is durable and prevention-shaped.** A wrong lexeme decorates a
prompt block until the Dreamer re-scores it. A wrong **fact** is designed to
persist, to survive the revision that clears `Standing`, and to *stop future
behaviour*. An unattributable claim that blocks behaviour cannot be revoked with
confidence, because you cannot tell which of its siblings share its defect.

**3.3 The eval is unrunnable without it.** The reflection spec's §13 asks: *does
the fact established at 02:14:43 survive the 02:16:51 silence-based reversal?*
That question is only answerable across prompt revisions if the fact rows record
which revision produced them. Without the stamp, a green eval and a stale corpus
are indistinguishable — which is the exact `nothing_found` / `unavailable`
confusion R-27 exists to prevent, one layer up.

## 4. Design

### 4.1 Prompt files

`nora/language/prompts/*.md`, one per model-calling prompt, with frontmatter:

```yaml
---
name: lexeme-extraction
version: 1
applies_to: [lexeme_observer]
description: What a candidate lexeme is, and what to return.
change_policy: operator-only
---
```

Bind-mounted the way `nora/doctrine` is, so tuning a Dreamer prompt is an
edit-and-next-tick loop rather than a rebuild. This matters more here than for
the doctrine: the Dreamer is the component whose output most needs iteration, and
it currently has the slowest loop in the system.

### 4.2 The loader

`nora/doctrine/loader.py` already does the general half — `parse_section`
(frontmatter + `sha256` over the body, line 139/191), `_fingerprint_dir` (mtime,
line 527), the cache/`_last_good` fallback, and `reload = "mtime"`. What is
doctrine-**specific** is only the gates: `check_kind_boundaries`,
`check_authority_graph`, `check_normative_register`, and `_open_tag`'s
`<section>` rendering.

So this is an **extraction, not a fork**: lift the parse/hash/reload half into
something both callers use, and keep the doctrine's gates on the doctrine side.
A copied loader is the second implementation of a shared behaviour and would need
its own contract suite under CLAUDE.md; an extraction does not.

**Open (§6.1):** whether the extraction is worth it now or whether a smaller
purpose-built loader is honest for three files. Decide by reading the two, not in
advance.

### 4.3 Provenance on every row

Add `prompt_sha256` (and, open, `model`) to:

- `room_lexeme`
- `room_exchange`
- `language_observation`
- `exchange_observation`
- the fact table, **from its first migration**

`room_portrait` is out — it has no prompt (§2). Its provenance question is
"which lexeme/exchange rows fed it", which `lexeme_basis`/`exchange_basis`
already answer.

**This is the part to keep even if §4.1 and §4.2 slip.** A column added now costs
one migration; a backfill later is impossible, because the rows that need it were
written by a prompt nobody recorded.

Schema changes go through `tests/infra/test_schema_parity.py`, **not**
`alembic check` (CLAUDE.md).

### 4.4 Metrics and logs

The Dreamer's tick should log its prompt versions the way a tag start logs
`doctrine_versions={…}` — one line, so a trace can be tied to a revision without
reading the database.

## 5. Non-goals

- **Not** versioning the render-side frames (§2).
- **Not** giving `portrait_synthesizer` a model call — it deliberately has none.
- **Not** changing any prompt's content. This is substrate only; a prompt edit
  made in the same change would be indistinguishable from a substrate bug.
- **Not** a gate suite for prompt content. The doctrine's gates encode
  doctrine-specific rules (kind boundaries, authority, RFC-2119 register) that do
  not transfer. If Dreamer prompts want gates later, that is its own decision.

## 6. Open questions

1. **Extract or purpose-build** the loader (§4.2). Read both first.
2. **`model` alongside `prompt_sha256`?** A prompt is one input; the model is
   another, and `[language].model` has changed before. Cheap to add now.
3. **Does the fact table's provenance need more than a hash** — the *source URL*
   a fact was verified against is arguably provenance too, and is what makes a
   bad fact traceable to a bad page. Likely yes, decided with the fact schema.
4. **Bind-mount or bake?** The doctrine is bind-mounted and hot-reloads, which
   means doctrine changes reach production on merge with no gate between. That is
   a real property to inherit deliberately rather than by copying.

## 7. What must be true before it ships

1. The three prompts render byte-identically before and after the move — a named
   test comparing the new loader's output to the current constants, so the
   substrate change is provably not a content change.
2. `prompt_sha256` is written on every new row by both in-scope instances, with a
   named test.
3. Schema parity green (`tests/infra/test_schema_parity.py`).
4. A Dreamer tick logs its prompt versions.
5. Hot-reload verified live: edit a prompt file, next tick picks it up, no
   restart — the same check `reload = "mtime"` gets on the doctrine.
6. Full gate green; release fragment.

## 8. Status

Design only. No code. No `design/09-decision-index.md` entry — a candidate, not a
landed decision.

**Blocks:** `FactSynthesizer` (`2026-08-20-reflection-reasoning-continuity-design.md`).
**Blocked by:** nothing.

**Related, and deliberately separate:** the same document's `SearchProvider`
finding — the Dreamer already holds a search port with both `search` and `fetch`
verbs (`nora/domain/ports.py:1139`), injected at `composition.py:876`, with
`verify_known_entities = true` live. That capability exists; only its *depth of
use* is shallow (a boolean known-entity verdict, and `fetch` used by no Dreamer
instance). That is a separate item from this one.
