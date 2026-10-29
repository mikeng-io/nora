# Private DM — reads across units, writes anchored, and the laundering rule

Status: proposed, **not implemented**, 2026-10-25. Amends
[00-contract](00-contract.md) §1 and §4 on one point: what a DM may **read**.
What a DM **writes**, where its private history lives, and which unit its
workspace belongs to are unchanged.

## 1. The operator's ruling

Operator, 2026-10-25, on whether a DM has a default unit or spans everywhere:
reads may span the units the person belongs to; publication into a unit is held
by the **laundering rule** («let's try laundering rule»). An assistant to one
person who is in several units would otherwise answer from one of their
groups and be blind to the others.

## 2. What stays anchored

Everything [00-contract](00-contract.md) §4 binds to the **default unit**
stays bound to it:

- the private transcript (`dm_scope_id`, person plus default unit);
- the DM computer workspace (person plus unit);
- where curated understanding from the DM may be **published** (§4 below);
- the eligibility rule (explicit listing, inferred only when exactly one unit
  is eligible) and the `[dm] enabled` switch.

## 3. What a DM may read

> 2026-10-28 ([unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md#6a-what-this-meets-in-the-other-bundles-2026-10-28) §6a): in the memory graph this is one walk per unit that lists the owner, each through this section's per-room predicate, composed at read time; no cross-unit entity or relation is written.

A DM turn may read the group context of **every unit whose policy explicitly
lists the person**, not only the default unit. Wildcard membership still grants
nothing, as in [00-contract](00-contract.md) §4.

- **Gated per room, by the existing lineage predicates, through a new DM
  target adapter.** The DM's audience is one authenticated person, P. An item
  from room R of unit Y is readable when the predicates that already decide
  room-to-room disclosure pass for an audience of {P}: P is a listed person of
  R, and the item's own requirements (exclusions, location restrictions, its
  lineage) pass. This is the per-item contract of
  [standing-ledger 30](../standing-ledger/30-turn-records.md) §5. A room P is
  not listed in is not readable, even when P is in its unit.

  **The predicates cannot do this today, and need a new adapter.**
  `RenderRoom.resolve` resolves an entry's origin only among rooms of the
  target's own unit (`nora/domain/lineage.py:330`). `_message` refuses an
  origin that resolves to no room or to more than one
  (`nora/agent/lineage.py:98`). So an item said in Y fails against a DM target
  anchored to X, even when P is listed in both rooms. The adapter is new build
  work:

  - **One target per source unit.** For an item from unit Y, the DM turn builds
    a `RenderRoom` whose `room` is P's singleton DM audience *as seen from Y*.
    Its `rooms` are Y's policy rooms, and its `unit` is Y. The DM's anchor
    (`dm_scope_id`, default unit X) does not change. Only the predicate's frame
    of reference does.
  - **Nested lineage resolves per entry.** An item whose lineage holds entries
    from several units is readable only when *every* entry passes against the
    target for its own unit. One failing or unresolvable entry withholds the
    whole item, as `lineage.py` already does for a section.
  - **Fail closed on ambiguity.** An origin that matches rooms in more than one
    of P's units, or no room in any of them, is unreadable. A bare
    conversation id with no unit (a note's `established_in`) resolves only in
    the unit that stored it.
  - **Room-to-room is untouched.** The adapter is built only for a DM audience
    of {P}. Group targets keep today's same-unit `resolve`, so no group room
    gains another unit's content.
- **Rendered by topic, not wholesale.** A DM turn renders the default unit's
  context and, when the conversation is about another unit's affairs, that
  unit's: the same choose-from-context step the room-topology bundle gives
  `send_to`. It does not render every unit's state on every turn. When the
  topic's unit is unclear, she asks.
- **Another person's DM is never readable**, from any unit, unchanged.

## 4. The laundering rule

Reading across units creates one new path: something from unit Y, shown to P
in the DM, could become curated understanding published into unit X. Nora must
never be the carrier between two units.

**A curated write into unit X may cite only:**

1. evidence from unit X's own rooms; and
2. P's own words in the DM.

It may **not** cite anything whose lineage reaches a room of another unit:
Y's messages, Y's Standing or notes, Y's turn records, or **Nora's own DM
replies and tool results derived from Y**.

**This provenance does not exist yet and is new work, required before
cross-unit reads are enabled.** Today the `Deliverer` stores a reply's text and
turn metadata only (`nora/agent/guards.py:467`), and `note_revision_lineage`
derives a cited message's lineage from the room it was *said* in
(`nora/domain/lineage.py:349`), not from what it was built from. So a DM reply
that summarised Y currently looks like a plain DM message. The turn-record
design adds a record handle to each reply
([standing-ledger 30](../standing-ledger/30-turn-records.md) §3), and that
alone does not establish inherited lineage. The required work:

- **Code-produced, retained, transitive lineage on every DM output.** Each
  reply and each tool result records the union of the lineage of every input
  the turn read and every tool result it used, computed by code, never by the
  model. A tool result's lineage is the lineage of the rows it returned. A
  reply built from a tool result inherits that result's lineage.
- **Stored with the output**, alongside the turn record's per-item capture
  ([standing-ledger 30](../standing-ledger/30-turn-records.md) §2), so the
  curation validator reads it rather than recomputing it.
- **Missing provenance is refused.** At publication into X, a cited DM item
  whose lineage is absent, unreadable or `unresolved` is refused, the same
  as a Y-lineaged one. It never passes as P's own words.
P's own words are the one exception on purpose. If P tells Nora in the DM
something they learned in Y, that is P's disclosure, the same as P saying it
in X's group themselves. Curation still applies [00-contract](00-contract.md)
§2's publication boundary to it (confidentiality, re-identification, a
keep-private instruction vetoes), so it is P's words subject to the existing
judgement, not a blanket pass.

## 5. What this changes elsewhere

- **Group-facing unit sessions are unchanged.** An ordinary unit session reads
  its own unit and the curated output of its bound DMs. It never reads another
  unit, and the laundering rule means no published DM write can carry another
  unit's content into it.
- **Restricted curation sessions see more, and are held at citation.**
  [00-contract](00-contract.md) §3 and [03-execution](03-execution.md) §3
  let a restricted curation session read its bound **raw DM scope**. After
  this amendment, that scope can hold Y-derived replies and tool results. So
  a curation session for X *can read* Y-derived items. It cannot cite them:
  the validator refuses any proposed X write whose citations carry Y lineage or
  missing lineage. Y-derived items are rendered to that session marked with
  their lineage (for example `from_unit="y"`), so the model can see what it
  must not use. The session's other restrictions are unchanged: no group
  sends, no reminders, no direct commits.
- **Storage** ([02-storage](02-storage.md)): publication provenance gains the
  lineage check above. The private Record keeps everything, as before; the
  rule constrains what may be **cited** into a unit, not what is kept.
- **Acceptance** ([04-build-and-acceptance](04-build-and-acceptance.md)) gains
  these cases. Each one is checked against stored rows, not the model's
  account:
  - P in units X and Y asks the DM about Y's plan. It is answered from Y's
    rooms P is listed in, and not from a Y room P is not in.
  - An item whose origin is ambiguous across P's units, or resolves in none,
    is not readable.
  - An item with nested lineage, X and Y entries together, is readable only
    when both pass for their own unit's target.
  - A group room's turn in X still cannot read Y, so the adapter is not used
    for group targets.
  - The DM then curates into X. A write citing Nora's Y-derived **reply** is
    refused, and so is a write citing a **tool result** whose rows came from
    Y.
  - A reply built from **mixed sources** (X and Y) is Y-lineaged and refused.
    So is a reply built from a tool result that was itself built from Y
    (**nested**).
  - A cited DM item with **missing or unresolved provenance** is refused.
  - A write citing P's own words passes on to the existing publication
    judgement.
  - A restricted curation session for X is shown a Y-derived item marked with
    its lineage, and its proposed write citing that item is refused.
  - A person in only one unit sees no change.
## 6. This holds unless

A measured case shows a curated X write whose content is traceable to Y
through a path the lineage check does not see (for example, a paraphrase of a
Y-derived reply in P's own words that P did not write). The response is then a
stricter rule, not a looser one.
