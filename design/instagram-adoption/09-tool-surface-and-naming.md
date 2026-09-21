---
type: design
title: "The Social Tool Surface — One Verb, A Sparse Matrix, And A Subject Not An Address"
description: "Why social_fetch stays one unified tool as Threads and others arrive, why per-network tools would reintroduce address-keyed thinking, and the one change that actually matters: target becomes a subject."
tags: [nora, social, tools, naming, vocabulary, threads]
timestamp: '2026-09-22'
edges:
  - {dst: 08-social-adapter-architecture.md, rel: parent, provenance: asserted}
  - {dst: 07-social-boundary-and-scope.md, rel: constrained-by, provenance: asserted}
  - {dst: ../09-decision-index.md, rel: constrained-by, provenance: asserted}
---

# The Social Tool Surface

## Recommendation

**One tool — `social_fetch` — however many networks arrive.** Per-network
tools (`instagram_fetch`, `x_fetch`, `threads_fetch`) should not be built.

The naming question turns out not to be about naming.

## Why: per-network tools force the platform to be an INPUT

The distinction the operator drew about URLs versus the graph applies here
exactly. Person-keyed reads mean Nora asks *"what is this person's latest
story"* about someone she already knows. Which network that person is on is a
**fact in the person index**, not a decision:

```
subject: a person Nora knows
   └─ person index ──> instagram igid 221731317
                       x handle    @…
                       threads     …
```

A tool named `instagram_fetch` moves the network into the call signature and
makes the model supply it. That is address-keyed thinking wearing a different
hat, and the model is the worst-placed component to decide it — it would be
inferring a network from a name, which is precisely the guess the person index
exists to remove.

With one tool, the network is something the call **resolves to**, and can even
be omitted when the subject is a known person. With N tools it is something
the model must assert before it can call anything.

## Why: the security boundary is per-vocabulary, not per-tool

DEC-151 built one tool with a CLOSED `(platform, what)` vocabulary and no
free-form passthrough, and R-86 pins read-only at three layers — the tool's
vocabulary, the host-side allowlist, the `read_only=True` declaration.

N tools means that pin must hold N times, and the host `READ_ONLY_ALLOWLIST`
stops being a single source of truth about what Nora may read. The failure
mode is not dramatic: it is that in a year, `threads_fetch` exists with its
own slightly different allowlist and nobody can answer "what social reads can
Nora perform" without reading three files. One table answering that question
is worth more than per-network ergonomics.

## The sparse matrix is already solved, so it is not an argument for splitting

The honest objection to a unified tool is that networks have genuinely
different verbs — X has threads, Instagram has stories — and a shared `what`
vocabulary drifts toward the lowest common denominator or becomes a matrix
where most cells are invalid.

But the matrix is **already sparse and already handled**. Today:

|  | post | profile | posts | search | story |
|---|---|---|---|---|---|
| **x** | yes | yes | yes | yes | — |
| **instagram** | **no** (`unavailable`) | yes | yes | yes | *new row* |
| **threads** | — | — | — | — | — |

`instagram/post` is absent and answers `unavailable`, which is R-27-correct: a
pair that is not in the allowlist is refused without a read, and the model
learns it could not be done rather than that nothing was found. Adding Threads
is adding a row whose cells fill in as backends support them — the same
mechanism, no new shape.

**A verb is admitted to the vocabulary when at least one network can serve
it**, not when all can. `story` is admitted because Instagram serves it, even
though X has no equivalent today.

## What should actually change: `target` becomes a subject

This is the change worth making, and it is orthogonal to naming.

Today `target` is an **address** — a handle or URL supplied by whoever is
asking. That is what makes the current tool URL-keyed, and it is the thing the
operator identified as the real gap.

It should become a **subject**, which is one of:

| subject kind | example | tier (`07`) | resolution |
|---|---|---|---|
| a person Nora knows | a member of this room | 0 or 1 | person index → network + id |
| a handle someone typed | `@someone` said in the room | 2 | used as given, never inferred |
| an address | a link pasted in the room | 2 | used as given |

When the subject is a person, `platform` may be **omitted** and resolved from
the index — the graph model working. When it is a handle or address, the
network is explicit because there is nothing to resolve it from.

This is where the tier ladder attaches to the tool surface: **the subject kind
determines the tier, and the tier determines whether the read is permitted at
all.** A unified tool has one place to enforce that. N tools have N.

## Naming, briefly

`social_fetch` is the right name and should not change. It names the axis
(social, as opposed to messaging), not the network, which is what makes it
survive Threads arriving. Renaming it per network would also break DEC-151's
recorded decision and its acceptance tests for no gain.

If a future network needs a verb no other has — a Threads-specific concept
with no analogue — it is a new `what` value valid for one platform, which the
sparse matrix already expresses.

## What this forbids

- **No per-network social tool**, now or when Threads arrives.
- **No free-form network string.** `platform` stays a closed enum; an unknown
  value is refused, not passed through.
- **No inferring a network from a name.** If the subject is not a known person
  and no network was given, the answer is `ambiguous`, not a guess.
- **No verb admitted because one backend might grow it.** A `what` value
  enters the vocabulary when a backend serves it, with the allowlist row that
  proves it.
