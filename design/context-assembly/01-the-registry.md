---
type: design
title: "The Registry — an occasion declares its blocks, the assembler renders them"
description: "Generalise prefill's `_BLOCKS` from one table to a table per occasion, keyed by occasion, holding name, order and BlockKey. A builder is registered against a name once; the assembler walks the occasion's rows. Adding a tier becomes a row per occasion that wants it, and «what does proactive carry» becomes something you read."
tags: [nora, context, registry, assembler, blocks]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 02-the-gate-stays-closed.md, rel: constrained-by, provenance: asserted}
---

# The Registry

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## The shape, and why it is prefill's shape rather than a new one

`prefill._BLOCKS` already is the thing this bundle wants, for one occasion:

```python
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("self_card", 10, "person"),
    ("peer_card", 20, "person"),
    ...
    ("group_transcript", 70, "room"),
)
```

Name, order, cache tier. It is read at the top of the file, it is data, and a
reviewer can check a claim against it in seconds. The proposal is not to invent
an abstraction; it is to give the other five occasions the same table and one
assembler that walks whichever table applies.

```
    OCCASION                       REGISTRY                      ASSEMBLER
    ────────                       ────────                      ─────────
    tag                       ┐                              ┐
    participation             │    occasion -> (              │  for name, order, key
    proactive                 ├──►     (name, order, key),    ├─►   in rows(occasion):
    note_candidates           │         ...                   │      build = BUILDERS[name]
    note_curator              │    )                          │      emit build(ctx)
    culture                   │                               │
    relevance_gate            ┘    BUILDERS: name -> fn       ┘  ordered, keyed, once
```

Three properties fall out, and they are the whole point:

1. **An occasion's set is readable.** «Does proactive carry `standing`?» is a
   grep of one table, not a trace through a 1400-line module.
2. **A block has ONE builder.** `self_card` is registered once. Today it is two
   functions with two input types that happen to share a tag name, and nothing
   compares their output.
3. **`BlockKey` applies everywhere.** The cache-tier discipline `_BLOCKS`'
   comments argue for stops being a property of one occasion.

## The hard part: the context types differ, and that is REAL

This is the thing that makes it a design rather than a refactor. The six
occasions do not share an input:

| occasion | what its builders receive |
|---|---|
| tag / participation | `TagContext` |
| proactive | its own per-pass context, across rooms |
| note curator | a note plus its evidence rows |
| note candidates | a Dreamer batch |
| culture | the previous account plus the room's messages |
| relevance gate | `(transcript, standing)` — two positional values |

A registry keyed on `name -> fn(ctx)` needs `ctx` to mean something. Three ways
out, and this bundle does not pretend the choice is obvious:

**(a) One context object every builder takes**, with optional fields. Simplest
registry, worst honesty: a builder would accept a context carrying fields that
are structurally absent for its occasion, and «is `peer_card` None because
nobody is tagged, or because this occasion never fills it?» becomes
unanswerable at the type level. This is the shape that lets a block silently
render empty forever — the failure `13`'s `Roster(available=False)` reasoning
exists to prevent, one layer up.

**(b) A protocol per block, structural typing.** Each builder declares what it
needs (`needs_tagger`, `needs_transcript`); an occasion's table is only valid if
its context satisfies every row's protocol. Honest, checkable statically, and
the most work. It also makes an invalid table a TYPE error rather than a
runtime `None`, which is the direction R-27 points.

**(c) Register per (occasion, name), builders stay occasion-specific.** Least
change: the registry unifies DECLARATION only, not the builders. «What does
proactive carry» becomes readable immediately; `self_card` remains two
functions. Delivers property 1 and 3, not 2.

**(d) Per-occasion literal unions, builders untouched.** Each occasion's block
names are a `Literal[...]` rather than bare strings, so `mypy` refuses a table
that registers a name the occasion has no builder for. Most of (b)'s static
checking at a fraction of (b)'s cost, and it composes with (c) rather than
replacing it. **Added after review**; the first draft considered three options
and this is a real fourth. It does not subsume (b) — a literal union catches a
name that is not registered, not a builder handed a context missing the field
it needs — so it is a cheap first guard rather than the honest end state.

**Recommended: (c) first, then (b) for the four names that are genuinely
duplicated, with (d) as the cheap guard over (c).** (c) is behaviour-preserving and therefore measurable against the
existing suite on day one, which (a) and (b) are not. It also front-loads the
value the operator actually asked for — a place to add culture as a row — while
leaving the builder merge as a separate, separately-measured change. (a) is
recorded to be rejected, not considered: it buys the tidiest diagram and costs
the one property that makes an absent block debuggable.

**Said bluntly, because the first draft buried it in a parenthesis: (c) alone
does not fix `00`'s claim #1.** "The same name is not the same block" stays
exactly as true after S1 as before — `self_card` remains two functions from two
input types. What (c) buys is that the discrepancy becomes VISIBLE in a table
instead of invisible across two modules. Fixing it is S3, and S3 is the step
this bundle schedules least concretely. An operator reading only the
recommendation should not come away thinking the duplication is handled.

## What a duplicated name must prove before it is merged

`self_card`, `notes`, `standing`, `group_transcript`. Before any two are
collapsed into one builder, the two current outputs have to be **diffed on real
inputs**, because "same name" is not evidence of "same content" and this
codebase has already been bitten by assuming it (`byte-faithful is not
meaning-faithful`). A merge that changes what reaches the model is a prompt
change on the hottest path and needs the eval suite; a merge proven to change
nothing does not. Which of the four is which is **unmeasured** and is the first
thing `03` sequences.

## What the registry must NOT become

* **Not a way to give every occasion every block.** The gate's set is closed for
  a measured reason (`02`), and a registry that makes adding a row easy makes
  adding it to the gate easy too. The gate's table must be structurally
  incapable of growing without the decision in `02` being taken — a test, not a
  convention.
* **Not a place for conditional logic.** A row says a block belongs; whether it
  renders is the builder's answer (`None` renders nothing, as today). An
  occasion table with `if` in it is control flow wearing a table's clothes and
  has bought nothing.
* **Not a second source of truth for order.** `prefill` currently holds both
  `_BLOCKS` and `_ROW`, derived from it, precisely because a hand-kept second
  copy drifted — its own comment records that the duplicate shipped
  `<loop_decisions>` and `<people_in_window>` with no entry at all. Whatever the
  registry exposes, it derives.
