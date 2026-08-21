---
type: design
title: "The Tool Surface — What Each Adaptor Holds, and How a Tier Contributes One"
description: "The per-adaptor tool subsets this revision proposes, and the seam by which a tier contributes read tools over its own state — the note tier's registry pattern, generalised rather than reinvented."
tags: [nora, dreamer, tools, registry, seam, adaptors]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 05-input-or-tool, rel: realizes, provenance: asserted}
  - {dst: 04-designed-vs-shipped, rel: depends_on, provenance: asserted}
---

# The Tool Surface

## 1. Two axes, kept separate

The current design collapses two questions into one list. This revision keeps
them apart, because they have different answers and different owners:

- **Capability** — does this adaptor's job require reaching this thing
  mid-reasoning? Answered by [05-input-or-tool.md](05-input-or-tool.md).
- **Permission** — may a background loop reach it at all? Answered by the
  read-only ceiling (unchanged) and by
  [08-disclosure.md](08-disclosure.md) (which blocks one path outright).

A tool appears in an adaptor's subset only when both say yes. The existing
`key_kind` narrowing stays as the mechanism for the second axis; what changes
is that it stops being the *only* thing consulted and stops standing in for a
decision nobody made ([04](04-designed-vs-shipped.md)).

## 2. The classes of read, and who needs which

| class | answers | needed by |
|---|---|---|
| **own tier** | "do we already know this, under any name? did we already fail to settle it?" | every adaptor with a store |
| **room** | "who is here, who is 你, what came before this line" | any adaptor reading messages |
| **memory** | "what does Nora already know about this person" | adaptors resolving people |
| **notes** | "is this already written down" | adaptors that would otherwise duplicate a note |
| **world** | "what does an outside source say" | adjudication |

The shipped Dreamer has **only the last class**, for **one** adaptor.

## 3. Proposed subsets

Stated as intent, not as a literal list to be copied — the ceiling is derived
and the classification test is the arbiter
([12-migration-and-governance.md](12-migration-and-governance.md)).

| adaptor | key | own tier | room | memory | notes | world |
|---|---|---|---|---|---|---|
| room language | scope | ✅ own lexicon | ✅ | ✅ | — | ✅ |
| room exchange | scope | ✅ own patterns | ✅ | ✅ | — | — |
| room portrait | scope | — | — | — | — | — |
| fact extraction (Phase A) | scope | ✅ vocabulary + negative cache | ✅ | ✅ | ✅ | — |
| fact adjudication (Phase B) | workspace | ✅ | **blocked** | **blocked** | ✅ | ✅ |

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

**The portrait keeps nothing.** Its objective is executed in code, and the
argument that a third model call would reintroduce the risk the tier removed is
still correct. Making everything an agent is not the goal.

## 4. The seam: a tier contributes its own read tools

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

**The one part that does not transfer unchanged is the key.** The note verbs
derive their workspace from `(platform, conversation_id)`. Under the Dreamer
those are an empty platform and a workspace name, so the resolution returns
nothing and the verbs answer `unavailable`. A tier read tool intended for a
workspace-keyed adaptor needs a key path that does **not** route through the
room — the Dreamer's key already *is* the workspace, so it needs supplying
directly rather than deriving.

That is a small, contained change to how the tool layer receives its key, and
it is the difference between the seam working and the seam looking like it
works while every call fails closed.

## 5. What renders, and the read-time half

`<facts>` currently renders the workspace's most recent standing facts with
**no subject filter** — its own port docstring calls this a deliberate first cut
pending entity resolution. In a room discussing one topic, Nora receives
whatever was most recently written.

Under D3 the same reader that serves the adaptors serves the turn, which makes
"decide which facts to include" a capability Nora has rather than a slice
Python guessed. This bundle does not settle whether the block becomes
query-relevant, becomes a tool she calls, or both — that is a tag-path cost
question ([13-open-items.md](13-open-items.md)). It settles only that the
capability is the same one, and should not be built twice.

## 6. What does not change

- **No write tool, ever.** The model proposes; code disposes. Every mutation
  leaves as a schema-validated proposal through a sink. A write verb pointed at
  the database is an act verb, and it would also defeat the screen — if the
  model writes directly there is no sink left to screen at.
- **The read/act split stays derived** from a field on the tool, never
  hand-listed. Every tool added here declares itself.
- **The startup refusal stays.** An adaptor declaring a tool outside the
  ceiling refuses to start, and the per-run re-check stays beside it so a
  hot-reloaded subset cannot slip through.
