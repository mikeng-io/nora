---
type: contract
title: "The Note Dreamer — Two Adaptors, and the Verb That Blocked One"
description: "The curator acts across notes; the revisitor reaches a person. The revisitor was spec-blocked by a rule that refuses any act verb — the fix is distinguishing a verb that DELIVERS from one that ENQUEUES, because the delivery path re-applies every guard."
tags: [nora, note-tier, dreamer, curator, revisitor, reminders, skills]
timestamp: '2026-08-24'
edges:
  - {dst: 15-roadmap, rel: realizes, provenance: asserted}
  - {dst: 17-failure-classes, rel: depends_on, provenance: asserted}
  - {dst: 13-doctrine, rel: extends, provenance: asserted}
---

# The Note Dreamer

## 1. Two adaptors, and neither exists

The note tier ships its tag-path half — the index, the four tools, the
read-before-write latch. **Its background half is two adaptors and there are
zero objective files for either**: `nora/dreamer/objectives/` holds only
`fact-*` and `room-*`.

| adaptor | job | state |
|---|---|---|
| **curator** | merge, retire, promote, coherence-rewrite — **acts ACROSS notes**, which the tag path may never do | unblocked, unbuilt |
| **revisitor** | reaches a person about a note whose moment has come | **was spec-blocked** — §3 |

The split is the tier's own rule: the tag path may only touch a note it read
**this turn**, so anything cross-note is the Dreamer's by construction. That is
not a convenience — it is what stops one turn silently reorganising a notebook
it only partly saw.

## 2. The curator: merge is keyed on the ANCHOR

The one rule that carries the design: **merge is keyed on the anchor, never on
topical similarity, so two weddings stay two notes.**

Topical similarity would merge them — same vocabulary, same shape, same kind of
uncertainty — and the merge is unrecoverable in the direction that matters: two
notes wrongly merged have already lost which revision belonged to which
occasion. This is [21-substrate-contract.md](21-substrate-contract.md) §5's
default-to-split, one tier over, for the same reason.

**And it is why the entity registry matters here too.** An anchor is
`(anchor_date, entity_set)`, so anchor-keyed merge needs resolved entities —
which is why `note_entity` is empty and why populating it
([24-embedding-seam.md](24-embedding-seam.md) §8) is the curator's real
precondition, not its flag.

## 3. The revisitor was blocked by a rule that is too broad

**The block, stated exactly:** the revisitor's specced tool subset contains an
**act verb**, the startup check refuses any adaptor holding one, and that verb
is *the entire mechanism by which its own spec has a background pass reach a
person*. Removing it leaves the section with no implementation path. So the
adaptor is not merely unbuilt — it is **unbuildable as specced**.

**The resolution is a distinction the rule never drew:**

> **No adaptor may hold a verb that DELIVERS. An adaptor may hold a verb that
> ENQUEUES for delivery.**

The note tier's own design already says the safe route: *"Nothing on the
background clock speaks: a revisit reaches a person only by **scheduling a
reminder**, which fires on the delivery path."*

**Why enqueueing is categorically different from speaking.** A reminder fired on
the delivery path passes every guard a reply passes — the reply guard, the
disclosure subset, the never-silent fallback, the per-requester caps. The
background pass writes a row; **the delivery path decides whether anyone is
spoken to, and in what words.** A verb that delivers would bypass all of it,
which is what the rule was written to prevent, and it prevented the safe case
along with the unsafe one.

This also fits what [15-roadmap.md](15-roadmap.md) §6.1 already establishes:
reminders and events are **the only route by which background work reaches a
human**, and their per-requester caps are therefore *the de facto budget on how
much any pass may nudge somebody* — machinery that exists rather than a new
counter.

**So the revisitor holds `schedule_reminder` and nothing that speaks**, and the
startup check is amended to refuse delivery verbs specifically rather than act
verbs generally. That unblocks the adaptor without widening what background work
may do.

### 3.1 One revisit adaptor, many notes

**The note supplies the objective; the adaptor supplies the procedure.** One
revisitor serves every note — there is no per-note adaptor, and a note's own
`closing_condition` is what tells the pass whether its moment has come.

This is [06-trigger.md](06-trigger.md)'s D2 at the row level: the *condition*
travels with the data, the *mechanism* is shared, and adding a note never adds
an adaptor.

## 4. Are the Dreamer's adaptors skills?

They look alike — both are operator-authored instruction files, versioned,
bind-mounted, and refused from any other source. The answer is **no**, and the
reason is provenance.

| | a skill | an adaptor's objective |
|---|---|---|
| selected by | **the model, by name, mid-turn** | **code, at construction** |
| bound to | nothing — it is a capability offered | one adaptor, one key kind, one sink |
| provenance | which skill loaded is a model choice | **which objective ran is a code fact** |

**That last row decides it.** Every fact and note this bundle writes carries the
hash of the instruction that produced it, and a re-derivation must be able to
say *which objective ran* without trusting the producer. If the Dreamer chose
its own objective by name, the provenance stamp would record a **choice**
instead of a **fact**, and the audit trail would depend on the thing being
audited.

Skills are for a capability Nora *reaches for*. Objectives are for work a
scheduler *dispatches*. Collapsing them would make the second inherit the
first's non-determinism for no gain.

## 5. Three instruction corpora, not one

The question *"is the instruction the base of the Dreamer only?"* has a concrete
answer: **there are three, and they have different lifetimes and audiences.**

| corpus | audience | changes when |
|---|---|---|
| `nora/doctrine/` | **Nora, on the tag path** — who she is when she replies | her character or constraints change |
| `nora/dreamer/doctrine/` | **every adaptor** — who the Dreamer is on any run | the research posture changes |
| `nora/dreamer/objectives/*.md` | **one adaptor each** — what this pass is for | that pass's job changes |

A fourth, `nora/skills/`, is not doctrine at all — it is a capability the model
activates by name, governed by R-29's authority boundary.

**The superseded claim, recorded because it is still cited:** an earlier spec
held *one doctrine, shared by every adaptor*. That is what
[00-overview.md](00-overview.md) supersedes and what D8 replaces with
shape-aware doctrine. The Dreamer's doctrine says who the researcher is; the
objective says what this run is for. **One file cannot do both without becoming
the union of every pass's instructions**, which is how a shared prompt acquires
a clause for a tier that is not running.

## 5.1 The field convention, which was purpose without shape

The tier documents **how** the caps are enforced and never **what the fields
should say**. The enforcement half is right and worth keeping: title and
description are validated in `__post_init__`, **not in the renderer**, because
*"a renderer that truncates loses content silently, and the whole tier is built
on the index being a complete listing of what exists."*

The content half says only that the index is *"enough to know a note exists;
nowhere near enough to revise it."* That is a purpose, not a convention — and a
model given a purpose invents a shape per note.

**The convention follows from the note's own schema.** A note is
`(anchor, uncertainty, closing condition, body)`, so:

| field | carries | cap |
|---|---|---|
| **title** | the **anchor, in words** — the occasion this note is about | 60 |
| **description** | the **uncertainty and its closing condition** — what is undecided, and what would settle it | 200 |
| **body** | the **arrangement** — the thing that has to survive | §5.2 |

**Why the description is uncertainty-plus-closing-condition and not a summary:**
the index exists so Nora can decide *whether to read the body on this turn*.
"Dinner on the 9th" tells her a note exists. "Three restaurants proposed, nobody
has picked one" tells her whether it **bears on what is being said right now** —
which is the only question the index is asked.

A summary answers *what is this about*, which the title already did.

## 5.2 The body has no cap at all

`note_revision.body` is constrained only by `length(btrim(body)) > 0`. **There
is no upper bound**, in the domain or the database.

That is a different risk from the injected fields, and it is easy to miss
because the body is **not** injected — it is read on demand. The exposure is the
**curator**, which reads bodies per tick against a cap that is *"capped in
config and counted when the cap bites."* A read budget in rows is only a budget
if a row has a bounded size; one unbounded body can consume a whole tick's
allowance, and the tier's own rule then reports it as *nothing to do* — the
`unavailable`-as-`nothing_found` failure at the pass level, which that same spec
names in advance.

**Decided, on §5.1's pattern:** `[notes] max_body_chars`, operating value
**8000**, database ceiling **32000**. Eight thousand is roughly a long page —
enough for an arrangement with its history, and small enough that the curator's
per-tick row budget means something.

## 5.3 Does Nora need a note SKILL?

**No — and the reason is a chicken-and-egg the skill mechanism cannot escape.**

A skill is activated **by name, by the model, mid-turn**. So it can only help
once she has already decided she needs it:

> **A skill you must know you need before you can load it cannot teach you to
> recognise the need.**

Splitting the question along that line answers it:

| what | where it belongs | why |
|---|---|---|
| **recognition** — *this conversation has an undecided plan with no home* | **doctrine**, always present | it must be true before she reaches for anything. A skill here is unreachable by construction |
| **procedure** — *how to write the title, revise versus create* | **the tool descriptions** | they are already in front of her whenever the tool is |
| **the read-before-write rule** | **the guard, which already enforces it** | the veto *names the remedy* — "read it first" — so the procedure is taught by the failure, at the moment it matters |

That last row is the repo's own pattern and it is why a procedure skill would be
redundant: **the latch does not document the rule, it makes the rule
unavoidable**, and it explains itself on the one path where explanation is
needed.

What doctrine owes is therefore one or two lines of *recognition*, not a manual.
[13-doctrine.md](13-doctrine.md)'s shape-awareness is what makes that affordable
— the tag path's doctrine carries it; no adaptor pays for it.

## 5.4 THE COLD START — the tier is inside the trap §5.3 names

**Found 2026-08-27 by reading the log, not the design.** In the tier's entire
life — enabled `ba661c9`, 2026-08-21 19:45 HKT — `/app/data/nora.log` holds
**exactly one** note-tool line:

```
{"tool": "note_search", "status": "nothing_found", "ts": "2026-08-21T13:22:37Z"}
```

Zero `note_create`, zero `note_read`, zero `note_update`, ever. **The create path
is not broken. It has never been called**, so the failure is upstream of the
store and no store-side counter could have found it.

**Why she never writes the first note:**

- `render_notes` returns `""` when nothing is standing
  (`nora/language/note_render.py:85-87`), so `_notes_block` emits nothing. With
  zero notes **the `<notes>` block is absent from every prompt.**
- `nora/doctrine/` contains **no note recognition at all** — verified by grep;
  the only corpus match is the phrase "developer note" in `constraints.md`.
- So she has four tools whose descriptions she would read only if she had already
  gone looking.

**§5.3 already made this argument and then left the tier inside it.** That section
assigns recognition to doctrine, "always present", because *a skill you must know
you need before you can load it cannot teach you to recognise the need.* **The
same sentence is true of the index block:**

> **A block that only appears once a note exists cannot teach her to write the
> first one.**

The lone `note_search` is the counter-evidence that matters: the tools ARE
reachable and the workspace DOES resolve. She reached for one unprompted, once,
and found nothing — which is the tier working exactly as built, and never
starting.

**So the cold start is the tier's first defect, ahead of the curator.** A curator
that merges and retires notes has nothing to operate on until the first one
exists, and building it first would measure nothing.

**And the denominator was smaller than it looked.** An earlier framing of this —
*"eight days enabled and zero notes"* — overstated the evidence. Since the tier
went on the log holds **18 `send_reply` calls**. Eighteen authored turns with no
block and no doctrine line is a much weaker indictment than eight days implies,
and it is the honest number to reason from.

## 6. What each owes its instrument

**The tier declares TEN metrics and ships ZERO.** Verified 2026-08-24: ten
`nora_note_*` names appear in its spec, and `nora/obs/metric_specs.py` contains
none of them. This is the tier [09-measurement.md](09-measurement.md) §3 means
by *"one tier declares ten and ships none"* — now named.

That is why the curator's own justification is currently unmeasurable: the
notebook only grows, and `nora_note_index_entries` — the metric `config.toml`
calls **"the tell"** — is not merely uncounted, it **does not exist**.

| reading | why |
|---|---|
| notes merged, **by anchor match kind** | §2's rule is only observed if a merge that fired on something other than an anchor is visible |
| notes retired versus notes grown | the notebook only grows today, and `nora_note_index_entries` — named in config as *"the tell"* — **has no call site**. Without it the curator's whole justification is unmeasurable |
| reminders scheduled by the revisitor, and how many **fired** | scheduled-but-never-fired is the revisitor silently doing nothing, which looks identical to a quiet notebook |
| revisits per person per window | §3's budget is the reminder caps, so the reading that matters is *how often background work nudged somebody*, not how often it ran |

## Status / Checkpoint

2026-08-27 — **§5.4: the cold start.** The log shows ONE note-tool call in the
tier's life; the create path has never been invoked. With zero notes the
`<notes>` block is absent from every prompt and doctrine carries no recognition
rule, so §5.3's own argument — a thing you must already know you need cannot
teach you the need — applies to the index block itself. §6.1 records why the
tier ships THREE counters rather than the ten it declares.

2026-08-24 — written after the operator asked whether the note dreamer has a
design, whether all dreamers should be skills, and whether the instruction
corpus is the Dreamer's alone. It **unblocks the revisitor**, which
[15-roadmap.md](15-roadmap.md) recorded as spec-blocked, by drawing the
deliver/enqueue distinction the startup check was missing.
