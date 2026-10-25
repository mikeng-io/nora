# Private DM — reads across units, writes anchored, and the laundering rule

Status: proposed, **not implemented**, 2026-10-25. Amends
[00-contract](00-contract.md) §1 and §4 on one point: what a DM may **read**.
What a DM **writes**, where its private history lives, and which unit its
workspace belongs to are unchanged.

## 1. The operator's ruling

Operator, 2026-10-25, on whether a DM has a default unit or spans everywhere:
reads may span the units the person belongs to; publication into a unit is held
by the **laundering rule** («let's try laundering rule»). A personal assistant
whose person is in several units would otherwise answer from one of their
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

A DM turn may read the group context of **every unit whose policy explicitly
lists the person**, not only the default unit. Wildcard membership still grants
nothing, as in [00-contract](00-contract.md) §4.

- **Gated per room, by the existing lineage predicates.** The DM's audience is
  one authenticated person, P. An item from room R of unit Y is readable when
  the predicates that already decide room-to-room disclosure pass for an
  audience of {P}: P is a listed person of R, and the item's own requirements
  (exclusions, location restrictions, its lineage) pass, the per-item contract
  of [standing-ledger 30](../standing-ledger/30-turn-records.md) §5. A room P is
  not listed in is not readable, even when P is in its unit.
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

It may **not** cite anything whose lineage reaches a room of another unit: Y's
messages, Y's Standing or notes, Y's turn records, or **Nora's own DM replies
and tool results derived from Y**. Her replies carry the lineage of what they
were built from, so a reply that summarised Y is Y-lineaged, and the curation
validator refuses it as evidence for X.

P's own words are the one exception on purpose. If P tells Nora in the DM
something they learned in Y, that is P's disclosure, the same as P saying it
in X's group themselves. Curation still applies [00-contract](00-contract.md)
§2's publication boundary to it (confidentiality, re-identification, a
keep-private instruction vetoes), so it is P's words subject to the existing
judgement, not a blanket pass.

## 5. What this changes elsewhere

- **Unit Cognition is unchanged.** A unit's session reads its own unit and the
  curated output of its bound DMs. It never reads another unit, and the
  laundering rule means no DM can hand it another unit's content.
- **Storage** ([02-storage](02-storage.md)): publication provenance gains the
  lineage check above. The private Record keeps everything, as before; the
  rule constrains what may be **cited** into a unit, not what is kept.
- **Acceptance** ([04-build-and-acceptance](04-build-and-acceptance.md)) gains
  three cases:
  - P in units X and Y asks the DM about Y's plan: answered from Y's rooms P is
    listed in, and not from a Y room P is not in;
  - the DM then curates into X: a write citing Nora's Y-derived reply is
    refused by rule; a write citing P's own words passes to the existing
    publication judgement;
  - a person in only one unit sees no change.

## 6. This holds unless

A measured case shows a curated X write whose content is traceable to Y
through a path the lineage check does not see (for example, a paraphrase of a
Y-derived reply in P's own words that P did not write). The response is then a
stricter rule, not a looser one.
