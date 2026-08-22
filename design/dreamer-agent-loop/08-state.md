---
type: design
title: "State — What the Agent May Reach, and How"
description: "Knowable before the call is an input; discovered during reasoning must be a tool. The rule that replaces the blanket refusal, the tool surface it implies, and the seam by which a tier contributes read access over its own state."
tags: [nora, dreamer, tools, state, registry, seam, decision]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
  - {dst: 04-designed-vs-shipped, rel: motivated_by, provenance: asserted}
---

# State — What the Agent May Reach, and How

> Two halves of one seam. **§1–§6** settle *when* a thing must be reachable
> mid-reasoning rather than pre-rendered; **§7–§12** settle *what* is reachable
> and *how* a tier contributes it. The first is the generative rule; the second
> is its application.

## 1. The rule

> **If the selection is knowable *before* the call, it is an input.**
> **If the selection depends on what the model concludes *mid-reasoning*, it
> must be a tool.**

That is the whole of it. It is a test on the *selection*, not on the subject
matter, and not on whether the data is "about the world" or "about ourselves".

## 2. Why the existing rule is not wrong, only misapplied

Fact-tier §4.2 refused a fact-read tool because *"selection stays where it can
be capped and counted."* For the case it was written about, that is correct and
this bundle does not disturb it: the adjudicator's pending candidates are
chosen by a deterministic filter that is code precisely so the cost decision is
auditable. Handing that selection to the model would move a cost decision into
a prompt.

The failure is generalisation. §4.2's stated rationale — *"read-only about the
world rather than about ourselves"* — is a claim about **subject matter**, and
subject matter is the wrong axis. It sweeps in every tool that reads the room,
which the same document's §4 grants, ten lines above.

Under the rule above, the two cases separate cleanly and for a reason that
holds:

- **Pending candidates for this workspace** — knowable before the call, capped,
  countable, and the cap *is* the cost decision. **Input.** Unchanged.
- **"What do we already call this thing?"** — not answerable until the model
  has read the batch and formed a hypothesis about what "this thing" is. No
  amount of pre-computation can select it, because the selector does not exist
  until the reasoning has happened. **Tool.**

## 3. Why pre-selection cannot substitute

The tempting alternative is to widen the pre-rendered input: fetch more facts,
render more context, pass the transcript window in. It fails on three counts.

**It cannot be targeted.** Python does not know which subject the model will
land on, so it must either guess or send everything. Guessing is what the
current renderer does — it matches on literal `subject_id` — and it is why a
contradiction between 「big walk」 and `Big Walk` is invisible.

**It scales the wrong thing.** Widening the input grows every prompt on every
tick, including the overwhelming majority that need none of it. A tool call
costs a round trip only when the model decides it needs one.

**It cannot ask twice.** The distinguishing act of research is asking
*differently* after the first answer disappoints — the keystone's own
motivating case is precisely a model that could not do this. A pre-rendered
input is one question, asked before the model has seen anything.

## 4. Worked application

| need | knowable before? | verdict |
|---|---|---|
| the pending candidate set for this workspace | yes, and the cap is the cost decision | **input** (unchanged) |
| the standing facts for those candidates' literal subjects | yes | **input**, but insufficient alone — see below |
| "is this the same thing under a different name?" | no | **tool** |
| "have we already tried and failed to settle this?" | no | **tool** |
| "what does this room mean by this word?" | no | **tool** |
| "who is 你 in this message?" | no | **tool** |
| the current Standing for this conversation, before revising it | yes — exactly one row, known in advance | **input** |
| "have I concluded something like this before, elsewhere?" | no | **tool** |

The standing-facts row is the instructive one. It stays an input *and* a tool
becomes necessary beside it: the pre-fetch answers "what do we believe about
the subjects as named", and the tool answers "is there something here under
another name". Neither replaces the other.

## 5. What this decides, and what it does not

**Decides.** Fact-tier §4.2's narrow answer stands; its general rationale is
superseded. Read-of-ourselves is no longer a category that is refused on
principle. The tool surface that follows is in
[08-state.md](08-state.md).

**Does not decide.** Whether a given tool is *safe* to grant is a separate
question with a separate answer — this rule establishes only that the
capability is needed. Disclosure is in [11-disclosure.md](11-disclosure.md),
and it blocks one of the two paths regardless of anything decided here.

**Does not decide.** The write side. The keystone's refusal of any write verb —
*"the model proposes; code disposes"* — is untouched and must stay untouched. A
read tool is a read tool; every mutation continues to leave as a
schema-validated proposal through a sink.

## 6. Precedent, and a caution

The note tier already implements this rule without naming it. Its index carries
every note's title and description on every turn — *awareness*, knowable in
advance, an input. Its `note_search` reaches note **bodies** — *retrieval by
content*, not knowable in advance, a tool. Its own spec says searching titles
*"would duplicate what is already in the prompt on every turn and would earn
nothing."* That is this rule, derived independently, for a different tier.

The caution is that the note tier pairs its read tool with a **read-before-write
latch**: a revision is vetoed unless the read succeeded in the same turn. The
lesson is not that every read needs a latch — the Dreamer has no write verb to
latch against. It is that granting a read tool was accompanied by a mechanical
guarantee, not by a hope that the model would use it well. Any grant made under
this rule should be able to name its equivalent.

---

# The Surface

## 7. Two axes, kept separate

The current design collapses two questions into one list. This revision keeps
them apart, because they have different answers and different owners:

- **Capability** — does this adaptor's job require reaching this thing
  mid-reasoning? Answered by [08-state.md](08-state.md).
- **Permission** — may a background loop reach it at all? Answered by the
  read-only ceiling (unchanged) and by
  [11-disclosure.md](11-disclosure.md) (which blocks one path outright).

A tool appears in an adaptor's subset only when both say yes. The existing
`key_kind` narrowing stays as the mechanism for the second axis; what changes
is that it stops being the *only* thing consulted and stops standing in for a
decision nobody made ([04](04-designed-vs-shipped.md)).

## 8. The classes of read, and who needs which

| class | answers | needed by |
|---|---|---|
| **own tier** | "do we already know this, under any name? did we already fail to settle it?" | every adaptor with a store |
| **room** | "who is here, who is 你, what came before this line" | any adaptor reading messages |
| **memory** | "what does Nora already know about this person" | adaptors resolving people |
| **notes** | "is this already written down" | adaptors that would otherwise duplicate a note |
| **world** | "what does an outside source say" | adjudication |

The shipped Dreamer has **only the last class**, for **one** adaptor.

## 9. Proposed subsets

Stated as intent, not as a literal list to be copied — the ceiling is derived
and the classification test is the arbiter
([14-migration-and-governance.md](14-migration-and-governance.md)).

| adaptor | key | own tier | room | memory | notes | world |
|---|---|---|---|---|---|---|
| room language | scope | ✅ own lexicon | ✅ | ✅ | — | ✅ |
| room exchange | scope | ✅ own patterns | ✅ | ✅ | — | — |
| room portrait | scope | — | — | — | — | — |
| fact extraction (Phase A) | scope | ✅ vocabulary + negative cache | ✅ | ✅ | ✅ | — |
| fact adjudication (Phase B) | workspace | ✅ | **blocked** | **blocked** | ✅ | ✅ |
| note curator | workspace | ✅ own notebook | — | — | ✅ | — |
| note revisitor | workspace | ✅ | — | ⚠️ **contingent** | ✅ | ✅ |

Three things to read off it:

**Phase A gains tools, and its cost argument is answered rather than
dismissed.** The fact-tier spec refused them because *"a research loop per
message would be unbounded cost."* That remains true and this does not propose
one. What Phase A needs is not research — it is *grounding*: what does this
room call this thing, and have we seen it before. Those are local reads, cheap,
and bounded by a per-tool budget. The distinction the original argument missed
is between a **web research loop** (genuinely unbounded, paid, slow) and a
**local lookup** (one indexed query, one round trip).

**Phase A must not deduplicate against existing candidates.** The candidate
store upserts and increments an occurrence count, and the deterministic filter
ranks on recurrence. An extractor that suppressed "we already have this" would
destroy the signal that decides what is worth adjudicating. The grant is for
*naming consistency* and for reading the **negative cache** — "we already tried
and could not settle this" — not for dedup. This is the sharpest constraint on
the grant and the easiest to get wrong.

**The revisitor's memory column is contingent, not granted.** Memory-derived
content about a person, written into a workspace-keyed note body that renders in
every room of that workspace, is the same carry
[11-disclosure.md](11-disclosure.md) §4.3 blocks — arriving through memory rather
than through a room read. It waits on the same condition.

**The two note adaptors are in the table because they are Dreamer adaptors**,
specced in detail and unbuilt — and because every seam in this bundle applies to
them the moment they land. The revisitor is additionally **spec-blocked**: its
subset as written contains an act verb ([11-disclosure.md](11-disclosure.md)
§4.1). Its remaining tools are all reads and all uncontroversial; it is the one
adaptor whose declared needs already match what this seam proposes.

**The portrait keeps nothing.** Its objective is executed in code, and the
argument that a third model call would reintroduce the risk the tier removed is
still correct. Making everything an agent is not the goal.

## 10. The seam: a tier contributes its own read tools

**Decision (D3):** a tier does not get a bespoke mechanism. It contributes
read tools the way the note tier already does — the store is a process-lifetime
collaborator on the tool registry; the key is resolved per call.

Three properties make it the right seam rather than merely an available one:

1. **It already serves both paths.** The Dreamer's tool source holds the *same*
   registry instance the tag path uses. A tier's reader added once is reachable
   from a background adaptor and from Nora's own turn, with no second wiring.
   That matters directly: "which facts to include" at reply time is the same
   capability as "what do we already believe" at adjudication time.
2. **It respects the layer contract.** A tier package may not import the agent
   package. Tools therefore live in the agent layer closed over an injected
   port, which is exactly what the note verbs do. No boundary moves.
3. **It is proven in-tree, including the failure modes.** The note tier's own
   read tools already demonstrate the resolution trap described below.

**The one part that does not transfer unchanged is the key, and it is the part
most easily got wrong.** The note verbs take a *title* or a *query*, never a
workspace — they **derive** the workspace from `(platform, conversation_id)`,
which means they need a room. Under the Dreamer those are an empty platform and
a workspace name, the resolution returns nothing, and the verbs answer
`unavailable`.

So the existing verbs are **not** a worked example of a workspace-keyed read
tool; the *store port* beneath them is. A tier read tool for a workspace-keyed
adaptor needs a key path that does not route through the room at all — the
Dreamer's key already *is* the workspace, so it must be supplied rather than
derived. That is a small change to how the tool layer receives its key, and it
is the difference between the seam working and every call failing closed while
looking wired.

That is a small, contained change to how the tool layer receives its key, and
it is the difference between the seam working and the seam looking like it
works while every call fails closed.

## 11. What renders, and the read-time half

`<facts>` currently renders the workspace's most recent standing facts with
**no subject filter** — its own port docstring calls this a deliberate first cut
pending entity resolution. In a room discussing one topic, Nora receives
whatever was most recently written.

Under D3 the same reader that serves the adaptors serves the turn, which makes
"decide which facts to include" a capability Nora has rather than a slice
Python guessed. This bundle does not settle whether the block becomes
query-relevant, becomes a tool she calls, or both — that is a tag-path cost
question ([16-open-items.md](16-open-items.md)). It settles only that the
capability is the same one, and should not be built twice.

## 11.1 Two writers, and no lock

The bundle asks what stops a background pass and a live turn clobbering each
other, and does not answer it. The note tier does, with **no lock at all**:

- **A read-before-write latch.** A revision is vetoed unless the read succeeded
  in the same turn — *"appending a revision without the body in front of her
  rewrites structure she cannot see."* The write costs two calls, and *"the
  second one is what makes the first mandatory."*
- **A compare-and-swap on the head.** The write echoes the version the read
  returned. If it moved — the background pass merged it mid-turn, or a parallel
  turn in another room did — the write is **refused as `unavailable`**, never
  applied on top.

Three properties generalise:

1. **The primitive between a background pass and a live turn is a version echo,
   not a lock.** A lock held across a model call is held for seconds against a
   human waiting; a version echo costs nothing and its failure is a legible tool
   result.
2. **A stale-write refusal is `unavailable`, never `nothing_found`** — the
   three-state discipline at the storage boundary rather than the instrument.
3. **The refusal names its remedy** — *"this changed under you, read it again"*.
   A refusal the model cannot act on is a dead end.

## 11.2 Who owns an operation is decided by who can see enough to do it

> *the tag path may only touch a note it can see the whole of this turn. The
> Dreamer is the only thing that may act across notes.*

And the derivation, which is the part worth keeping:

> *merging **cannot** be in-slot, and this is a statement about **what she can
> see** rather than a policy choice: it needs every note's full body, which is
> neither in the prompt nor affordable to load mid-turn.*

**Which actor owns an operation follows from whose view is wide enough to
perform it correctly, and destructive operations belong to whoever can see the
whole.** The corollary is the one this bundle must not forget: the fast path is
permitted to be wrong-by-addition *because the background pass is the cleanup* —
which only holds if the background pass actually runs. It has not, for weeks.

## 11.3 A granted tool whose argument cannot be constructed is inert

A hole found during implementation: the index carried titles, the read verb took
an id, and the search covered bodies — so *"she could see every note and name
none of them."*

**A tool is unreachable unless its arguments are constructible from what is
already in the prompt.** This is the sibling of the key-derivation trap in §10 —
there the key was underivable, here the handle was unnameable — and both make a
wired grant inert while looking wired.

**The design rule for every grant in §9: name the prompt-resident datum its
argument comes from.** Two more requirements ride along, both from the fix:
addressing is by the name a person would say, not an id; and **ambiguity refuses
rather than picking** — guessing which object was meant is how a write lands on
the wrong one.


## 11.4 Across tiers, the same rule — not N-by-M exposure

Tiers already reference each other: the revisit adaptor's inputs include the
facts reached through its note's entities, and `note_entity` carries a live FK
into the fact tier's entity table.

The tempting generalisation is *every adaptor exposes its state to every other*.
That is N×M grants, each a disclosure decision and a budget line, and it is not
what the connection needs.

**The rule in §1 already decides it, unchanged, across a tier boundary:**

| need | knowable before? | verdict |
|---|---|---|
| the anchor facts for this note (derivable from its entity ids) | yes | **input** |
| "does any note bear on this claim?" | no — not pre-selectable | **tool** |
| "what does this room call this thing?" | no | **tool** |
| the standing position for this conversation | yes — one known row | **input** |

Two riders, both load-bearing:

- **Cross-tier reads are where [11-disclosure.md](11-disclosure.md) bites
  hardest**, because what comes back is not a screened restatement but another
  tier's model-authored prose. A note body reached from the fact tier carries
  the same problem §4.3 raises in the other direction.
- **The useful cross-tier read is mostly the same question**: *what is this
  thing, and what do we already believe about it?* That is one capability over
  several stores, not several capabilities — which is why it is being
  investigated as one thing rather than granted store by store
  ([16-open-items.md](16-open-items.md)).

## 12. What does not change

- **No write tool, ever.** The model proposes; code disposes. Every mutation
  leaves as a schema-validated proposal through a sink. A write verb pointed at
  the database is an act verb, and it would also defeat the screen — if the
  model writes directly there is no sink left to screen at.
- **The read/act split stays derived** from a field on the tool, never
  hand-listed. Every tool added here declares itself.
- **The startup refusal stays.** An adaptor declaring a tool outside the
  ceiling refuses to start, and the per-run re-check stays beside it so a
  hot-reloaded subset cannot slip through.
