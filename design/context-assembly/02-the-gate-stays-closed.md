---
type: design
title: "The Gate Stays Closed — the one occasion a registry must make harder, not easier"
description: "relevance_gate.render_context is a closed set built by a function that can only produce a closed set. A registry makes adding a block a one-line row, which is exactly the pressure that function exists to resist. The closure has to survive as a test rather than as a comment."
tags: [nora, gate, safety, blocks, r-13]
timestamp: '2026-09-22'
edges:
  - {dst: 01-the-registry.md, rel: constrains, provenance: asserted}
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
---

# The Gate Stays Closed

## What the gate's own code says

`nora/ingest/relevance_gate.py:413`, verbatim (the docstring; `408` is the
`def` line, and an earlier draft cited that):

> *"The two blocks the gate is allowed to see, and no third. Rendered here
> rather than reusing the tag path's prefill assembler because that assembler's
> job is to build the RICHEST prompt it can, and every block it learns to add
> would silently widen this one. The gate's input is a closed set, so it is
> built by a function that can only produce a closed set."*

That is not a style preference. It is a structural defence, and the thing it
defends against is precisely what `01` proposes to build: a shared assembler
that gets better at adding blocks.

## The evidence the closure rests on

From `design/culture-tier/01-the-adaptor.md §6`:

* **#144/#147 measured the gate getting WORSE when given more context.**
* **#216** — n=5, never re-taken, closed 2026-09-16 by PR #262 — reported the
  turn behind the gate false-speaking on **4 of 5** silence twins.

And the counterweight, from the same section, which must be quoted with it or
the picture is dishonest:

> *"That the gate is a new-tier's normal cost, rather than a constitutional bar,
> is settled: every tier that ever landed added a block. The first draft of this
> bundle mistook a pin for a prohibition."*

So: the gate is not sealed forever. It is sealed **until a measurement opens
it**, one block at a time, and the measurement is the eval suite rather than a
judgement in a design document.

## What this bundle therefore requires of the registry

**1. The gate's table is closed by a TEST, not by a comment.** A test asserts
the gate's registered rows are exactly `{standing, group_transcript}`. Adding a
row fails the build. That converts "please don't" into "you cannot without
deleting an assertion that names the reason" — which is the difference between
a convention and a gate, and is the same technique `_BLOCKS`' own
`test_no_block_in_this_table_belongs_in_the_cached_head` already uses.

**2. R-13 is not negotiable and is not the gate's alone.** The same function's
docstring records that its FIRST version joined raw room text with no escaping,
into a prompt that ends with the literal verdict schema — so a member whose
message contained that JSON could write the gate's own answer. Any assembler
that renders for the gate inherits that requirement. A registry that renders
blocks uniformly must escape uniformly; the day the two diverge is the day this
is reachable again.

**3. Widening is a change to the TABLE, with its own measurement.** Not a
change to the assembler, not a flag, not a config key. One row, one PR, one
eval run, one reversal condition. The row is the audit trail.

## The order-of-operations trap

There is a real temptation to unify the gate LAST, on the grounds that it is
the risky one. That is backwards for one reason: if the other five are unified
first and the gate is left hand-built, the gate becomes the only occasion whose
set is undeclared — and the argument for leaving it that way ("it's special")
is the same sentence that lets it drift unnoticed.

**The gate should be declared FIRST**, with its two rows and its closure test,
precisely because declaring it costs nothing (its set does not change) and buys
the assertion.

**"Costs nothing" is contingent, and the condition should be stated.** It holds
because `BlockKey` currently routes nothing (`00` §3) — so attaching keys to
the gate's two rows changes no behaviour. The day cache-tier routing is wired,
declaring a key for a gate block becomes a real decision rather than a label,
and this paragraph stops being free. See `03` S3, which now carries that as an
acceptance item. Unifying its RENDERER can come last or never; those are separate
questions and this bundle does not settle the second.

## Two gate tests already exist, and the new one is not redundant

`tests/unit/test_relevance_gate.py` already holds
`test_the_prompt_is_exactly_the_instruction_plus_the_two_rendered_blocks` and
`test_the_prompt_holds_exactly_two_injected_blocks`. Both count what RENDERED.

That is the gap the row-level assertion closes: **a dormant third row whose
builder returns `None` under the test's fixtures passes both of them
silently.** Counting output cannot see a registered block that happened not to
fire; only asserting the ROWS can. So the test `02` asks for is additional
rather than a duplicate, and the distinction is worth keeping in the PR that
adds it.

(Note for whoever edits the culture bundle next:
`design/culture-tier/01-the-adaptor.md §6` cites this test as
`test_the_prompt_holds_exactly_two_untrusted_blocks`. No such test exists — the
real name says `injected`, not `untrusted`. A pre-existing citation error in
that document, recorded here rather than silently fixed there.)

## What is NOT decided here

Whether culture ever reaches the gate. `design/culture-tier` sequences that
last and measured; this bundle neither advances nor blocks it. What this bundle
does is make sure that when the question is asked, the answer is a row in a
table with a test next to it, rather than an edit to a function nobody is
watching.
