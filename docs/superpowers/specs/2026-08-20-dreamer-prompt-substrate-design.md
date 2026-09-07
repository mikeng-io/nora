---
kind: spec
title: "The Dreamer's instruction substrate: versioned, hashed, hot-reloading, and stamped onto what it produces"
description: "Nora's own doctrine is versioned, gated, hashed into every trace, and hot-reloads from a bind mount. The Dreamer's model-facing text is ~9,500 characters of inline Python constants with no version, no hash, no gate, and no reload — and none of the rows they produce records what produced them. A wrong lexeme is already in the record with no way to ask which text wrote it. Under the adaptor architecture the unit to version is no longer three string constants: it is the Dreamer's OWN doctrine as a read-only research agent, plus one objective per adaptor — doctrine-shaped, not constant-shaped. Every row an adaptor writes carries an instruction_sha256. PREREQUISITE for the fact adaptor, because a fact is durable and prevention-shaped and an unattributable one cannot be evaluated or revoked."
tags: [nora, spec, dreamer, adaptor, objective, provenance, doctrine, prerequisite]
timestamp: '2026-08-20'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md
  - docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
  - docs/superpowers/specs/2026-08-15-room-language-dreamer-design.md
  - design/09-decision-index.md
---

> **PARTLY DEPRECATED 2026-09-07 — every reference below to the knowledge tier (the "fact tier") or to the language tier describes code that has since been deleted.** Those parts are retained as a record of the reasoning, not as a description of the system; the rest of this document still stands.
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.


# The Dreamer's instruction substrate

## 0. What this document is

The operator's question, on reviewing where the fact adaptor would live:

> *"also about the doctrine of those dreamer agent, are they abstracted and with
> arch? i dont want everything slam into a inline text"*

and, on being shown the answer:

> *"spec it as its own item, its a prerequisite"*

The architecture it sits inside is his framing, recorded here because it is the
thing this document assumes:

> *"The 'dreamer' is the abstracted module, and the room consolidator, the room
> portrait synthesizer, and the fact synthesizer are all instances of the
> dreamer."*

**This is a prerequisite, not a nice-to-have.** §3 is the argument. It blocks the
fact adaptor and nothing else.

### 0.1 Revision note — this document was rescoped

The first draft was written for a **one-shot prompt world**, hours before that
world was settled out of existence. Its frame was "three prompts, two files":
version the three inline string constants that the three hand-written extractors
hand to a bare `complete()`, and exclude `RoomPortraitSynthesizer` as an outlier
with no prompt to version.

`2026-08-20-dreamer-as-an-agent-design.md` — **the keystone** — replaces that
frame. The quote above is realised there: the Dreamer becomes the runtime (loop,
tools, budget, and **its own doctrine**), and each synthesizer becomes an
**adaptor** supplying five things — objective, inputs, output schema, tool
subset, sink. Counting inline constants stops describing anything.

So the unit this document versions changed, and the sections that named the old
unit are rewritten: §0.1 (this), §2, §3.1, §4.1, §4.2, §4.3's column name, §4.4,
§5, §6, §7.1, §8. **§1 is untouched** — the evidence is about rows already in the
database and about a wrong gloss already rendered into every later turn, and none
of that depends on which architecture wrote them. §3.2 and §3.3 are untouched;
§3.3 was the strongest reason before and still is.

The **filename keeps the word `prompt`** even though the title and the column no
longer do. It is the identifier the keystone already points at — in its
`derives_from`, in its §8, and in its §10 "Blocks" line — and a rename would
silently break those references to save a word.

The earlier scoping is left visible in §2 rather than quietly deleted, per
`design/proposed/README.md`: a wrong turn stays readable so it is not retaken.
Where this document and the keystone disagree, **the keystone wins**; where the
keystone leaves something open (its §9), this document leaves it open too, except
for the one question the keystone explicitly delegated here (its §9.5, answered
in §2.1 and §4.1).

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

## 2. Scope — what needs a versioned artifact

**Correction. This section was written for a one-shot world.** Its earlier form
counted model-call *sites*:

> ~~**Scope — exactly two instances.** Only instances that make a model call are
> in scope … `portrait_synthesizer.py` is out **by its own design** … So:
> **three prompts, two files.** `FactSynthesizer` would be the third
> model-calling instance and the fourth prompt — which is the point.~~

That count is an artifact of the architecture that existed on the morning of
2026-08-20 — a clock calling three hand-written extractors, each holding an
inline constant and getting one shot at a bare `complete()`. Under the keystone
there is no `complete()` call for a fourth constant to sit in front of, so "how
many prompts" has no answer. It is left visible because it is why this document
originally said *three prompts, two files*, and a reader arriving from that
phrasing should see it superseded rather than wonder whether it was an edit slip.

### 2.1 Two kinds of artifact

Under the keystone, model-facing text lives in exactly two kinds of artifact:

| artifact | how many | what it says |
|---|---|---|
| **the Dreamer's doctrine** | one, shared by every adaptor | who the Dreamer is and what it may not do: a read-only research agent; the read-verbs-in / act-verbs-out line (keystone §4); what to do with a source that does not cover the claim; when to stop; and that «I could not check» is a result to report, not a gap to fill — R-27 one layer down |
| **an adaptor's objective** | one per adaptor | the research goal, in words, and nothing else. The other four adaptor fields — inputs, output schema, tool subset, sink — are structured, not prose, and are not this document's concern |

This answers the keystone's open question §9.5 (*"Does the Dreamer get its own
doctrine files, or a single objective file per adaptor?"*), which it explicitly
delegated here: **both**, because the two have different lifetimes. The doctrine
is what is true of every Dreamer run and changes when the runtime's constraints
change; an objective is what one adaptor is for and changes when that one job is
retuned. Folding the doctrine into each objective would copy the read/act line
four times, which is the hand-maintained-catalogue shape DEC-29 already showed
goes stale with nothing to catch it.

### 2.2 The adaptors

| adaptor | objective artifact | reaches a model | rows carry the stamp (§4.3) |
|---|---|---|---|
| room language (`LexemeObserver`) | ✅ | ✅ | ✅ |
| room exchange (`ExchangePatternObserver`) | ✅ | ✅ | ✅ |
| room portrait (`RoomPortraitSynthesizer`) | ✅ — executed in code | ❌ | ❌, see §4.3 |
| facts (the fact adaptor) | ✅, from its first commit | ✅ | ✅, from its first migration |

**`RoomPortraitSynthesizer` is no longer an excluded outlier.** It is an adaptor
like the others, whose tool subset is empty and whose objective is executed in
code (keystone §3). The argument that keeps it deterministic is untouched and
still load-bearing (its module docstring):

> "No model call anywhere … the inputs here are already model-vetted,
> already-structurally-safe output — a THIRD model call synthesizing prose from
> them would reintroduce exactly the [risk]."

What changed is only its relationship to *this* document. It used to be out of
scope because it was not the same kind of thing. It is now in scope as an
adaptor — it has an objective, and that objective is worth writing down whether
or not a model ever reads it — and out of §4.3 for a narrower and better reason
given there.

### 2.3 The current constants are not the objectives

There is no 1:1 mapping from §1.1's three constants to the artifacts above, and
assuming one is how this lands wrong. Those ~9,500 characters are raw material
that splits three ways:

- text that is true of **every** Dreamer run (be careful, what counts as
  evidence, do not assert what you could not check) → the **doctrine**, once;
- text that specifies the **return shape** → the adaptor's **output schema**
  field, which under the keystone is structured and validated, not prose;
- what is left — what this adaptor is trying to find out → its **objective**.

So the move is a **re-partition**, not a relocation. That has a direct
consequence for §7.1, where the old "renders byte-identically" check no longer
has a target.

### 2.4 Still out of scope: the render-side frames

Unchanged from the first draft, and unaffected by the keystone. The frames that
build Nora's prompt blocks (`render.py:_CLOSING_FRAME`,
`exchange_render.py:_CLOSING_FRAME`, `ROOM_BLOCK_NOTE`) are prompt-block text on
the **tag** path — a different concern with a different lifetime, and they were
just changed by the 2026-08-20 audit's A3. Versioning them is a separate question
this document does not open. `portrait_synthesizer.py`'s own `_CLOSING_FRAME`
(line 41), appended to the stored narrative at line 111, is **stored text, not an
instruction**, and stays where it is.

## 3. Why this is a prerequisite for the fact adaptor

Three reasons, in increasing order of how much they hurt.

**3.1 The fact adaptor is the first objective written after the runtime exists,
so it sets the precedent.** The first draft's phrasing — *"a fourth inline blob
entrenches the pattern"* — counted blobs, which the keystone made meaningless.
The point survives in a stronger form. Every other objective is a **migration**
of text that already runs; the fact adaptor's is written from nothing. It is also
the one that will be tuned hardest: the largest tool subset, the deepest loop,
and the only objective whose failure mode has already been observed end-to-end
(keystone §2 — the search that was answered correctly, then overturned by an
article silent on the claim, then repudiated by one that addressed nothing).
Whatever it is written as becomes what "an objective" means in this codebase. The
cheapest moment to decide that an objective is a versioned artifact rather than a
string in a `.py` is before the first one is written from scratch.

**3.2 A fact is durable and prevention-shaped.** A wrong lexeme decorates a
prompt block until the Dreamer re-scores it. A wrong **fact** is designed to
persist, to survive the revision that clears `Standing`, and to *stop future
behaviour*. An unattributable claim that blocks behaviour cannot be revoked with
confidence, because you cannot tell which of its siblings share its defect.

**3.3 The eval is unrunnable without it.** The reflection spec's §13 asks: *does
the fact established at 02:14:43 survive the 02:16:51 silence-based reversal?*
That question is only answerable across revisions if the fact rows record which
revision produced them. Without the stamp, a green eval and a stale corpus are
indistinguishable — which is the exact `nothing_found` / `unavailable` confusion
R-27 exists to prevent, one layer up.

This reason is **unchanged by the keystone, and harder to satisfy under it.** A
one-shot's output is a function of one string; an adaptor's output is a function
of the doctrine, the objective, and the loop that ran between them. There is
correspondingly more to record, not less — see §6.5, which this document leaves
open rather than settling.

## 4. Design

### 4.1 Where the artifacts live

A text-only tree, bind-mounted the way `nora/doctrine` is:

```
nora/dreamer/
  doctrine/            the Dreamer's own doctrine — who it is, what it may not do
  objectives/          one file per adaptor
```

No Python in it. The keystone's §5 puts the **runtime** in `nora/agent/` (the
package already allowed `strands`) and the **adaptors** in `nora/language/`
(ports only, no `strands`, no `nora.config`); a directory of Markdown is neither,
and `lint-imports` has nothing to say about it. The runtime reads the doctrine,
an adaptor names its objective, and composition wires them — the same split the
Dreamer already uses for its model client.

Frontmatter, following `nora/doctrine`'s shape:

```yaml
---
name: room-language
kind: objective
version: 1
applies_to: [room_language_adaptor]
description: What this room's words mean.
change_policy: operator-only
---
```

`kind` here is descriptive only. The doctrine's `check_kind_boundaries` gate
encodes doctrine-specific rules and does **not** transfer (§5).

Bind-mounting matters more here than for the doctrine, and more under the
keystone than it did before: the Dreamer is the component whose output most needs
iteration, it has the slowest loop in the system, and an objective that steers a
tool loop will need more revisions than a one-shot extraction prompt did. An
edit-and-next-tick loop instead of a rebuild is the difference between tuning it
and not tuning it.

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

**The keystone moves the balance here.** The first draft left open whether a
smaller purpose-built loader was honest *for three flat files*. There are no
longer three flat files: there are two artifact kinds, a per-run **assembly** step
(the doctrine plus the running adaptor's objective), and a hash over the assembled
result — which is precisely what `loader.py` already does on the tag path
(`doctrine_text`, one `sha256` over it at line 625, per-section versions
alongside). The question stays open in the terms the first draft set — decide by
reading the two, not in advance — but record that the argument for extraction got
stronger, not weaker.

### 4.3 Provenance on every row — the part to keep

**Renamed: `prompt_sha256` → `instruction_sha256`.** The keystone calls the
column `prompt_sha256` (its §8) and says it survives unchanged. The *substance*
survives unchanged; the name does not, and the name is worth one paragraph.

Under a tool loop there is no *a* prompt. The runtime sends many messages across
many turns, and what they have in common is not a string but an **instruction
set**: the Dreamer's doctrine plus that adaptor's objective. `prompt_sha256`
names a unit that no longer exists, and invites hashing whichever string happened
to go out first — provenance bound to a proxy, which is the failure this document
exists to remove. `instruction_sha256` is **one hash over the assembled
instruction text**, doctrine body + objective body in the order the runtime
assembles them. That is not a new mechanism: it is exactly `doctrine_sha256`'s
shape on the tag path, where `loader.py:625` hashes the assembled `doctrine_text`
and per-part versions ride alongside it as `doctrine_versions={…}`. Same shape,
same vocabulary, one word changed.

Considered and rejected: `objective_sha256` — it names half the input, so a
doctrine edit that changed the behaviour of every adaptor at once would leave no
trace anywhere, which is the worst case this column exists to catch.

Add `instruction_sha256` (and, open, `model`) to:

- `room_lexeme`
- `room_exchange`
- `language_observation`
- `exchange_observation`
- the fact table, **from its first migration**

`room_portrait` stays out, for a narrower and better reason than before. It used
to be out because it was not in scope. It is now an adaptor (§2.2) and *still*
gets no `instruction_sha256`, because its objective is executed in code: no
instruction text governed the row, and stamping a hash of text that did not
produce the output is provenance bound to the wrong property — the exact defect
this document exists to remove. Its provenance question is unchanged — "which
lexeme/exchange rows fed it" — and `lexeme_basis`/`exchange_basis` already answer
it; those rows now carry their own stamps, so the chain resolves. If the portrait
adaptor ever gains a model call, it gains the column in the same change.

**This is the part to keep even if §4.1 and §4.2 slip.** A column added now costs
one migration; a backfill later is impossible, because the rows that need it were
written by instructions nobody recorded. Nothing about the keystone changes that
arithmetic — four of the five tables above are live and accumulating rows today.

Schema changes go through `tests/infra/test_schema_parity.py`, **not**
`alembic check` (CLAUDE.md).

### 4.4 Metrics and logs

The Dreamer's tick should log its instruction versions the way a tag start logs
`doctrine_versions={…}` — the Dreamer doctrine's version, plus the objective
version of each adaptor that ran on that tick, in one line, so a trace can be
tied to a revision without reading the database.

## 5. Non-goals

- **Not** versioning the render-side frames (§2.4).
- **Not** giving the portrait adaptor a model call. It is an adaptor now, but one
  with an empty tool subset and an objective executed in code, and the keystone
  keeps it that way deliberately (its §3: *"Making everything an agent is not the
  goal; making everything the same interface is."*).
- **Not** changing what any instruction *says*. This is substrate only. The
  re-partition of §2.3 is mechanical; a wording improvement made in the same
  change would be indistinguishable from a substrate bug. §7.1 turns that into a
  procedure.
- **Not** a gate suite for instruction content. The doctrine's gates encode
  doctrine-specific rules (kind boundaries, authority, RFC-2119 register) that do
  not transfer to an objective. If Dreamer objectives want gates later, that is
  its own decision.
- **Not** resolving anything the keystone left open. Its §9 lists five; §9.5 was
  delegated here and is answered in §2.1/§4.1. The other four — which model the
  Dreamer runs, per-adaptor scheduling, whether the fact adaptor shares the
  watermark, and per-adaptor failure isolation — are untouched by this document
  and stay open **there**.

## 6. Open questions

1. **Extract or purpose-build** the loader (§4.2). Read both first. The keystone
   moved the balance toward extraction; it did not settle it.
2. **`model` alongside `instruction_sha256`?** The instructions are one input; the
   model is another, and `[language].model` has changed before. The keystone's
   §9.1 — which model a research loop should run — is open, which means it *will*
   change: rows written either side of that change should be distinguishable.
   Cheap to add now.
3. **Does the fact table's provenance need more than a hash** — the *source URL* a
   fact was verified against is arguably provenance too, and is what makes a bad
   fact traceable to a bad page. Under the keystone there is a second candidate:
   the loop's own Langfuse trace, which *is* the derivation. Likely yes to
   something; decided with the fact schema.
4. **Bind-mount or bake?** The doctrine is bind-mounted and hot-reloads, which
   means doctrine changes reach production on merge with no gate between. That is
   a real property to inherit deliberately rather than by copying — and the
   keystone raises the stakes, because the artifact now steers a component holding
   `web_fetch`.
5. **Does the stamp need to cover the tool subset and the loop budget?** Two runs
   over identical instruction text, one with `web_fetch` available and one
   without, can write different rows; so can two runs with different turn caps.
   The tool-subset and budget fields are the keystone's (its §3, §6) and how they
   are versioned is not settled there. **Left open — this document does not invent
   an answer.** Note only that §4.3's column is necessary either way: if the
   answer is "yes, more", it is a second column, not a different one.

## 7. What must be true before it ships

1. **The re-partition is separable from any content change.** The first draft
   asked that *"the three prompts render byte-identically before and after the
   move"*; §2.3 removes the target — the text is being split three ways, so there
   is no byte-identical artifact to compare against. The *reason* for that check
   survives, as two steps: **(a)** lift each constant into its artifact file
   verbatim, with a named test comparing the loader's output to the current
   constants byte-for-byte; **(b)** re-partition into doctrine / objective /
   output schema as its own change, evaluated on its own. One change that both
   moves and re-partitions cannot be told apart from a substrate bug.
2. `instruction_sha256` is written on every new row by every adaptor that reaches
   a model, with a named test per adaptor.
3. Schema parity green (`tests/infra/test_schema_parity.py`).
4. A Dreamer tick logs the doctrine version and each adaptor's objective version
   (§4.4).
5. Hot-reload verified live: edit an objective file, next tick picks it up, no
   restart — the same check `reload = "mtime"` gets on the doctrine.
6. Full gate green; release fragment.

## 8. Status

Design only. No code. No `design/09-decision-index.md` entry — a candidate, not a
landed decision.

**Blocks:** the fact adaptor
(`2026-08-20-reflection-reasoning-continuity-design.md`).

**Blocked by:** `2026-08-20-dreamer-as-an-agent-design.md` — for §4.1, §4.2 and
§4.4, which describe artifacts of a runtime that does not exist yet. **§4.3 is
not blocked by it.** The provenance column can be added to the four live tables at
any time, against the extractors as they are today, and the argument for doing it
early (a backfill is impossible) does not depend on the runtime landing. If only
one part of this document ships, that is the part.

**Related, and deliberately separate:** the reflection spec's `SearchProvider`
finding — the Dreamer already holds a search port with both `search` and `fetch`
verbs (`nora/domain/ports.py:1139`), injected at `composition.py:876`, with
`verify_known_entities = true` live. That capability exists; only its *depth of
use* is shallow (a boolean known-entity verdict, and `fetch` used by no Dreamer
instance).

**Absorbed by the keystone.** That finding is now recorded as the keystone's §1
and answered by its §4 — `web_search` and `web_fetch` are both in the Dreamer's
tool set, and the shallow use is the thing the runtime exists to fix. It stays
here as the record of where the finding was made and when; the decision is no
longer this document's to make.
