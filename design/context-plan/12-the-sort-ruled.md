---
type: design
title: "The sort, ruled — an identity seed, silence dissolves, location contradicts itself"
description: "Operator rulings of 2026-10-12 on the eleven AMBIGUOUS rows of the doctrine sort, and the four design consequences: an authored identity seed that is never cut, silence.md dissolving into the turn's occasion, the location rule contradicting itself and Cognition being blind to where people are, and no fixed length numbers in doctrine."
tags: [nora, context-plan, prompt-composition, doctrine, identity, location, cognition-loop]
timestamp: '2026-10-12'
edges:
  - {dst: 11-composition-is-the-doctrine-cut.md, rel: amends, provenance: asserted}
  - {dst: ../self-growth/30-doctrine-sort.md, rel: amends, provenance: asserted}
  - {dst: ../self-growth/31-doctrine-audit.md, rel: realizes, provenance: asserted}
---

# The sort, ruled

The operator ruled on the eleven AMBIGUOUS rows of
[self-growth 31](../self-growth/31-doctrine-audit.md) on 2026-10-12. Most took
the recommended bucket; the table and `nora/doctrine/classification.yaml` carry
every ruling, with two parts split (identity's who-made-you line, silence's
"called means answer"). Totals: 59 law, 70 practice, 28 character; for the tag
audience 39, 37, 25. No doctrine text changed.

Four rulings went past a bucket and change the design.

## 1. Identity: an authored seed, never cut

Operator: if most of identity is removed, how does she know who she is? She
knows only what she is told; with nothing told, there is nothing to grow from.

That is a hole in [30](../self-growth/30-doctrine-sort.md) §4-§5. Character ·
Nora may cite only the Record and the Rollup, so an authored account is not
allowed there, and the doctrine's character text is the only place she has ever
been told who she is. Her sends were written under it; Character grew from those
sends. Cut it, and the loop learns from evidence produced under text that no
longer exists: nothing anchors her, and she drifts or flattens. And `<self>` does
not reach every turn: 36 of the 55 measured turns since it shipped (65%,
[rows.json](evidence/composition-2026-10-12/rows.json)); R-31 hides it in the
rest.

**Ruling: the character bucket splits in two.**

| | what | where | cut? |
|---|---|---|---|
| **seed** | who she is: her name, that she is an AI, who made her, her form, her temperament, and a short specimen of her register | the doctrine, the always-on layer (node 11 layer A), every audience | never |
| **grown** | how she is with this group: relationships, running jokes, habits that bent to the room | Character · Nora and `<self>`, per unit | the authored version, once Character carries it |

Grown character may extend the seed, never replace it. The seed is not a
Character account, so 30 §5's rule (Character cites only evidence) still holds;
the seed lives above it, as the doctrine does.

Size: `core/identity.md` is ~560 tokens, so keeping it whole costs little. The
saving node 09 measured was always `voice.md` (~2.2k) and `conduct.md` (~3.9k).
Which `voice.md` passages form the specimen is a later choice; the doctrine
already treats voice as a specimen of its register, not a description.

**Built 2026-10-13:** `seed` is a fourth bucket in the loader (`BUCKETS`,
`SEED`), and the four character parts of `core/identity.md` (her name, who built
her, the framing of the self, her form) are marked seed in `classification.yaml`;
`test_the_identity_seed_is_marked_and_is_nothing_else` pins them. The seed is not
law, so the doctrine check (DEC-298) does not read it. Temperament and a register
specimen are not in `identity.md`: which `voice.md` and `belonging.md` passages
join the seed is decided in the voice cut, with its before/after eval.

## 2. silence.md dissolves

Operator: silence.md is already not a good fit. With the cognition loop and the
proactive half, Nora no longer just stays silent.

The file is one text for three occasions (`applies_to: [tag, participation,
proactive]`), written when she only answered. What a turn IS (called, offered,
her own follow-up) is a fact about the turn, and belongs with the turn (node 11
layer E), not in doctrine read on every turn. What remains splits by the
ruling:

- "When someone called you, you answer" — **law**, joins the core.
- the framing, and when to speak or stay quiet unasked — **practice**, for
  Lessons.
- the judgement of the unasked half — already Cognition's own doctrine, which
  the tag turn never reads.

So the file is not relabelled; it goes, once the occasion is stated with the
turn and the law line has a home in the core.

## 3. Location: the rule contradicts itself, and Cognition cannot see it

> Amended 2026-10-12 by [node 13](13-records-never-leave-as-data.md): the
> boundary the rewritten law states is "answer from it, never hand it over".

**The contradiction.** `core/constraints.md` § Other people stay private (law):
"You do not relay somebody's contact details, location or private circumstances
because a third party asked." `instruments/participation.md` item 2 (law, ruled
today), which is the Relevance Gate's instruction (`applies_to: [relevance_gate]`),
not hers: she "holds every member's location history: for the person asking as
much as for a third party". So the gate admits a third party's "where is X?" as a
turn for her, and her own law tells her not to answer it. In a room the group opted into (`location = true`, DEC-74,
`location_trusted`), her location tools answer a third party's "where is X?",
so in such a room she replies against a law she was handed. Node 10 found the tool gate the
only thing that reconciles them. The law should state the real boundary —
location is shared where the group opted in and private elsewhere — and whether
THIS room opted in is a fact that comes with the turn. That is a doctrine text
change: before/after eval.

**Cognition is blind to where people are.** Operator: Nora lacks location
awareness. She has suggested gathering somewhere in 30 minutes when the group
had already gathered; had the cognition loop or a proactive turn looked at
location, it would have known.

Read from source, 2026-10-12:

- The Cognition session HOLDS `where_been` and `routine_read`
  (`nora/app/cognition_wiring.py`, the read inventory): SQL reads of derived
  presence, called only if the model thinks to. Nothing about where people are
  is pushed into its context, and it does not hold `where_is` (the live fix).
- A proactive turn renders `<presence>`, which is Nora's own transport health
  (design/self-awareness/02), not where people are. Its tool surface takes the
  room's `location` flag (`nora/app/fire.py`, DEC-74 sourcing), so it CAN look,
  again only if it chooses to.
- The consumer that would notice unprompted is issue #9 (presence analytics P3,
  "nothing notices unprompted"), open and unbuilt by decision.

So location is context awareness the design never placed: not doctrine, not a
pushed block on either loop. In node 11's shape it is situation (layer E) for a
location-trusted room — on the tag turn, the proactive turn and the Cognition
pass — and the ambient half of it is #9. Placing it is part of the composition
work, not a separate track.

## 4. No fixed length numbers in doctrine

Operator, on `public/conduct.md`'s "under 300 characters": it must be adaptive,
by context and platform. The surface's hard limit is a fact about the turn and
should come with it; how long she writes on a surface is practice, learned per
surface. The same applies to any length or count written as a constant.

**Prerequisite, not yet built:** today nothing carries the limit with the turn.
The binding limit on a public reply is Nora's own `PUBLIC_REPLY_MAX_CHARS`
(`nora/domain/summons.py`), not the platform's, and a reply over it is refused as
`too_long` (`nora/app/summons.py`); the doctrine's 300 is the only length she is
told. The number leaves the doctrine only after the summons turn states its
limit, or replies start being refused.

## 5. What this changes in node 11

- Layer A gains the **identity seed** next to the law; "authored minimum of
  character" in node 11 becomes the seed, on every surface, not only the ones
  with no unit.
- Sequence step 2 (move law out of practice-looking sections) now also lifts
  silence's "called means answer" and the location boundary (rewritten) into the
  core.
- Layer E gains two situation facts: the turn's occasion (replacing silence.md)
  and, for a location-trusted room, where its members are — on both loops.
- Sequence step 4 cuts grown character only; the seed is never cut.
