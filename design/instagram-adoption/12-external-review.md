---
type: design
title: "External Review — What An Outside Architect Said, And What It Changes"
description: "A pre-merge advisory review by an external model, given the abstract design and the measurements but no code. Two of its four arguments are accepted outright; one requires an operator decision because it reverses a stated preference."
tags: [nora, instagram, review, consent, retention, tiers]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 07-social-boundary-and-scope.md, rel: challenges, provenance: asserted}
  - {dst: 06-retention-and-custody.md, rel: challenges, provenance: asserted}
---

# External Review

Commissioned before merge, 2026-09-22. The reviewer was given the abstract
design, the decisions, and the measurements — **no repository access**, because
repo context anchors an outside reader on the implementation instead of the
choice. Four questions, four answers. Recorded here because three of them are
better arguments than the ones this bundle made for itself.

## ACCEPTED — the reach ladder is backwards

> "Tier 1 (a *known person*, someone the operator deliberately vetted) is
> stricter than Tier 0 (a stranger who wandered into a room). That's backwards
> by any consent logic — it's only forward by convenience."

This is correct and this bundle did not see it. `07` justified Tier 0 as *the
subject opted in by joining a room with her*. That reasoning covers **being
heard**: a speaker controls their own utterance and chose to make it in a room
the agent is in. It does not reach a profile or a story, which are published
to a **different audience under a different expectation**, and which the
subject may not connect to the agent at all — on Instagram nothing marks the
account as an agent (`07` §the non-technical one).

The reviewer's structural tell is the one that convinces: a ladder where the
*vetted* person needs a flag and the *unvetted* one does not is not a consent
model, it is a convenience model wearing one.

**Recommended change:** Tier 0 authorizes **messaging only**. A profile or
story read requires the Tier 1 per-person flag **regardless of room
membership**.

**This is NOT applied, and needs the operator.** It reverses a stated
preference — *"the first priority is reading the people in that circle"*
(2026-09-21) — and the whole point of Tier 0 was that the circle needs no
per-person setup. Reversing an operator decision while they sleep is not a
call this session gets to make. What it costs if accepted: every person whose
story Nora may read needs one line in the person index, which is the friction
Tier 0 existed to remove.

## ACCEPTED — the retention policy is false as written

> "The regret isn't the leak; it's that you shipped a retention policy you
> knew was false and will have to re-litigate the entire memory store to honor
> a deletion request."

`06` already documents that three mechanisms make recollection durable and
that the bytes-level rule cannot reach them. What it did not do is draw the
conclusion: **a stored conclusion derived from an ephemeral read is
indistinguishable, in the store, from something a person said.** There is no
lineage. "Show me everything you retain about X" cannot be answered correctly,
and a deletion request becomes archaeology.

The reviewer's structural alternatives, none of which this bundle proposed:

1. **Provenance class on every memory write** — `room-speech` vs
   `ephemeral-read` — so ephemeral-derived conclusions can be expired or
   tombstoned, and deletion is a query rather than an excavation. This means
   intercepting the automatic feed on every reply, "which is the actual work
   decision 5 is avoiding".
2. **No conclusions from ephemeral reads at all** — the tool result reaches
   the current reply's context and is excluded from the memory-write path.
3. **Failing both, change the policy to match the architecture** and re-derive
   consent from the truth, rather than keeping a rule the system contradicts.

**Applied here:** `06`'s mitigation is relabelled. Attribution phrasing is a
**UX improvement, not a bound** — and this bundle's own measured evidence
convicts it, since instruction rewrites in this system made a model worse
three times running, once unsafely. Option 1 is recorded as the real fix and
is out of this bundle's scope: it changes a shared memory path.

## ACCEPTED — deferring the correlation decision IS deciding it

> "An unresolved decision attached to a live capability isn't deferred — it's
> decided, in favor of the capability."

And the asymmetry, which is the part that stings:

> "for backend routing you demanded a *recorded measurement* before allowing
> an exception; for correlation you demand nothing before allowing the
> capability."

`07` recorded the correlation gap as an open operator decision. That is
adequate only while the capability is **off**, which today it is — the social
backend is disabled and there is no bridge route for it to call. It stops
being adequate the moment story reads work, because the dossier then requires
no new code, only a question.

**Applied:** `07` now states that the correlation decision **gates** the story
capability rather than accompanying it — composition across provenance classes
is denied until the operator decides, and that gate must exist before the
first Tier-0 story read, not after.

## NOTED — one session for both axes fuses availability

> "You've fused your availability domains to dodge a security checkpoint …
> browser automation plus native API calls from one token is exactly what
> anomaly detection looks for."

`08` argued one session because a second login is the configuration most
likely to provoke a checkpoint, and `05` measured that the browser profile
already holds the same account. The reviewer's point stands anyway and is not
contradicted by ours: the *cost* of the checkpoint we avoided is ten minutes;
the cost of the correlated loss is messaging and social together, possibly the
account. Recorded as a known trade rather than an oversight — `01`'s
degradation requirement exists precisely because that loss is expected.

## What the reviewer thought was RIGHT

> "Decision 2 is the *least* likely to be regretted — routing by measured
> impossibility with a written exception list is the one decision here with a
> forcing function against drift."

Recorded because a review that only finds faults has not been read carefully.
