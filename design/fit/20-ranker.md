---
type: design
title: "The ranker: trigger, frame, generate, filter, score, aggregate, diversify, explain"
description: "The eight stages, what is the model's and what is code's at each, the score as a weighted sum of normalised features with unknowns kept unknown, group aggregation as average-without-misery plus a fairness credit, the logistics feature, MMR for the shortlist, and the decision it writes."
tags: [nora, fit, ranking, group-recommendation, mmr, logistics, fairness]
timestamp: '2026-10-25'
edges:
  - {dst: 10-preference-model, rel: depends_on, provenance: asserted}
  - {dst: ../standing-ledger/20-the-ledger.md, rel: depends_on, provenance: asserted}
---

# 20 · The ranker

One run of the ranker answers one **frame**: a recipe, the people it is for, a
time, a place constraint, and the question ("where on the 31st", "what to play
this weekend"). Every stage records what it did into the run, and the run is a
turn record ([standing-ledger 30](../standing-ledger/30-turn-records.md)), so
any proposal can be replayed and audited.

## 1. Trigger

A run starts from a Cognition stimulus or from a person asking.

| trigger | fires when | computed by |
|---|---|---|
| **interest burst** | evidence for one facet cluster in a unit, over a short window, rises well above its baseline (an EWMA with a z-score threshold; a Poisson rate test is the alternative where counts are tiny) | code, over evidence rows |
| **undecided plan** | an event or note has an open slot (no venue) and its lead time crosses the recipe's urgency curve (earlier for scarce slots: a Saturday, a holiday) | code, over events and notes |
| **outside change** | a candidate the unit already showed interest in changes (a release, a sale, a venue opening) | the recipe's watch source |
| **asked** | someone asks her for a suggestion | the tag turn |

A trigger raises a stimulus. Cognition decides whether to act on it, so the
proposal cap and the bandit ([40](40-feedback.md)) sit at one place.

## 2. Frame

Who it is for (an event's RSVPs; otherwise the room's members who are part of
the interest), when, and where from. **Each person's origin** comes from the
origin model ([10](10-preference-model.md) §5) with its rung. The frame is
written down before any candidate is looked at, so later stages cannot
quietly change the question.

## 3. Generate

The recipe's candidate sources ([30](30-recipes.md)), run by a sub-agent: a
places search along the attendees' routes, not around one centre point; a
games catalogue query seeded by the titles that triggered the burst. **The
model extracts each candidate's facets** into the recipe's vocabulary, once
per candidate, cached with an embedding.

## 4. Filter

Hard constraints remove a candidate outright: closed at `T`, too few seats or
too few players supported, over a stated budget, a stated dietary rule, already
owned by the group, a place someone has ruled out. Each removal is recorded
with the constraint that removed it.

## 5. Score

For each surviving candidate, each **feature** is normalised to `[0, 1]`:

| feature | computed from | by |
|---|---|---|
| **preference fit** | the group aggregate (§6) over the candidate's facets | code |
| **similarity** | cosine between the candidate's embedding and the attendees' positive seeds (the reels, games and places their evidence cites) | code |
| **logistics** | §7 | code |
| **timing** | open hours against `T`, release or sale timing, lead time | code |
| **cost** | against stated budgets or the unit's usual band | code |
| **novelty** | not already proposed recently (the decision log), not where they always go, unless the recipe prefers the familiar | code |
| **judged fit** | the model's judgement where a facet cannot be computed ("is this the kind of place this group likes for a party"), with its reasons | model, cited |

`score = Σ wᵢ · featureᵢ`, with the weights from the recipe.

**Unknown stays unknown.** A feature whose inputs are missing is not filled
with a default: it is marked unknown, its weight is redistributed over the
known features, and the candidate carries the count of unknowns. A candidate
whose top-weighted feature is unknown is never ranked first, and the proposal
says what it assumed.

## 6. Group aggregation

For each candidate facet, per-person affinities are combined by **average
without misery** (Masthoff): the mean affinity of the attendees, with the
candidate removed if any attendee's affinity on a facet the recipe marks
vetoable falls below the misery threshold with enough confidence. Plain
averaging lets one person's strong dislike be outvoted; least misery alone
lets one lukewarm person decide everything.

**Fairness over time.** A small credit goes to attendees whose preferences lost
in the unit's recent closed decisions (read from the decision log), so the
same person is not always the one accommodating. People are weighted equally
otherwise: tie strength chooses who is likely to come, never whose taste
counts more.

## 7. Logistics

For each attendee and candidate, travel time from the attendee's origin
distribution to the candidate at `T` (`nora/infra/routes/directions.py`,
transit where that is how they travel; `presence_corridors` speeds where a
habitual route exists), and, when the frame knows where they go after, the
**detour** against their usual route home.

`logistics = 1 - normalise(λ · max + (1 - λ) · mean)` over attendees: the
worst case for fairness and the mean for efficiency, `λ` from the recipe
(0.6 to start). A person whose origin came from rung 4 contributes with a
widened range, not a point.

## 8. Diversify and explain

The shortlist is chosen by **maximal marginal relevance**: repeatedly take the
candidate with the best `score − μ · similarity to the ones already chosen`,
so three suggestions are three different kinds of answer, not three ramen
shops. Size 2 or 3, from the recipe.

**The explanation is the top features**, not a story: for each option, the two
or three features that contributed most, rendered with their evidence where
the evidence passes for the room ([10](10-preference-model.md) §7). The model
writes the sentence; the reasons it may use are the recorded ones.

## 9. Output

The run writes a **Standing decision**
([standing-ledger 20](../standing-ledger/20-the-ledger.md) §2): `Options` are
the shortlist with scores and reasons, citing `turn(run)`; `Criteria` are the
frame's constraints and the recipe's weights; `Blocked on` names any unknown
that matters. A fire turn posts it, or the tag turn answers with it when
someone asked. The decision stays open until the group chooses, and the choice
closes it ([40](40-feedback.md)).
