---
type: design
title: "Likes: a ranking view of profiles, with evidence and uncertainty"
description: "Read likes from profile facts, traits and guesses; derive facet scores without a second preference store; keep constraints and timed origins grounded in Record; filter all inputs for the destination."
tags: [nora, recommendations, likes, evidence, decay, social-engagement, presence]
timestamp: '2026-10-25'
edges:
  - {dst: ../cognition-loop/22-social-stream.md, rel: depends_on, provenance: asserted}
  - {dst: ../unified-memory/35-persona.md, rel: depends_on, provenance: asserted}
---

# 10 · Likes

## 1. Likes come from profiles

**Likes are a read view, not another store or writer.** Recommendations reads
per-person profiles: **facts**, **traits**, **guesses (unconfirmed)**, under
[ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md).
The existing profile writers own what Nora believes about a person. Today's
code still calls these rows `PersonaLine`: citations, unit, write time,
supersession and validity are already represented
(`nora/domain/persona.py:298`, `nora/domain/persona.py:308`,
`nora/domain/persona.py:321`). The rename is a dependency, not code shipped here.

A **facet** is an attribute in a recommendation type's closed vocabulary
([30](30-recommendation-types.md)): `cuisine:japanese`, `vibe:loud`,
`genre:co-op-survival`, `platform:pc`. Step 1 adds a read-time projection from
eligible profile lines to `(person_id, facet, polarity, kind, source_time,
cites, profile_revision)`. It resolves the cited evidence once, records the
projection in the run, and creates no durable preference table. A cache, if
needed, is keyed by profile revision, destination and recommendation type;
it is disposable and never becomes another source of truth.

Facts and supported traits may contribute; guesses remain labelled unknown,
never a hard constraint or a confident dislike. Unresolvable citations or
ambiguous polarity produce unknown, not a fabricated observation. A profile
line and the evidence it cites count once, not once per summarisation.

For each `(person_id, facet)`, code derives a Beta score from the eligible,
deduplicated observations: `α = 1 + Σ positive decayed weights`,
`β = 1 + Σ negative decayed weights`. Affinity is `α / (α + β)`; support is
`α + β - 2`. Zero support means **unknown**, not neutral agreement. These
are heuristic ranking scores with support, not calibrated probabilities of
liking something. Revisions and source observations, not the rendered prose
block, make the calculation replayable.

**Hard constraints are separate from likes scores.** Step 1 also builds a
frame constraint reader over eligible original messages and message readings
for explicit restrictions and dated plans. The message-reading writer exists,
but has no retrieval consumer yet (`docs/maintainers/message-readings.md:9`);
this reader and its interval extraction are new build work. It preserves the
subject, source, assertion time, event interval, place and speech act. Only an
explicit applicable restriction removes a candidate; a question, guess or
unknown date never becomes one. Typed profile facts with a valid interval may
supply the same restriction, deduplicated by source. No constraint store is
added. `fact.valid_from` / `valid_to` still exist
(`nora/infra/sql/schema.py:2015`), but their tier and writers were deleted
(`design/09-decision-index.md:268`); those tables are not an input.

## 2. Evidence supporting likes

These are the observations a profile line may cite, not a parallel extractor
that bypasses profiles. Code assigns the starting weights; the model maps
facets and polarity with citations and cannot choose its own weight.

| kind | example | starting weight | source |
|---|---|---|---|
| **stated** | explicit liking or disliking of a facet | 3 | cited messages or message readings |
| **revealed: went** | repeated voluntary visits supporting a profile trait | 3 | `rollup_outing`; attendance alone does not establish liking |
| **revealed: played** | voluntary repeated play supporting a profile trait | 3, scaled by hours and capped | play sessions |
| **social: posted** | own post supporting an interest | 2 | `social_item` |
| **social: shared** | shared game clip or venue link | 1.5 | `social_item`, room links |
| **social: reacted** | an unambiguous positive reaction | 0.5 | `message_reaction` |
| **response** | explicit personal liking or dislike in a response | 2 | the responder's cited words ([40](40-feedback.md) §2) |
| **told by others** | another person's report of a dislike | 1 | cited messages; never a hard constraint on its own |

A group pick, attendance, agreement to a compromise or rejection of a plan is
not by itself an individual like or dislike. Silence supplies no likes
evidence. Profile writers can consider social interest and responses under
their existing evidence rules; recommendations never writes a posterior back
as a new profile fact.

The social stream is built, including perceptions and counts
([22](../cognition-loop/22-social-stream.md) §8). A shared reel is weak
interest, not proof of consumption or approval of every facet. Its recorded
revision and source remain attached to the projection.

## 3. Decay

Starting half-lives are recommendation-type data: stated and revealed 180
days, social 60 days, reactions 30 days, sized from replay. Decay uses the
observation's time, not the time its profile was rewritten. Reading another
revision of the same claim never refreshes its evidence age. The score is
recomputed at read time; the run retains the inputs and parameters it used.

## 4. The group's aggregate

Aggregate only for the frozen attendee set, using [20](20-ranker.md) §6.
Room or unit profile claims remain collective claims; they are never copied
into individual likes. No second unit preference store is needed.

## 5. Where people will be: the origin model

Logistics needs a starting distribution at event time `T`, with provenance.
Use the strongest applicable destination-eligible source:

1. **A dated explicit plan**, including its stated place, from the step-1
   constraint reader. It is new consumer work over messages/readings, not an
   existing fact-tier writer. Unknown intervals cannot fill this rung.
2. **The event's own plan**, such as a cited statement about coming from work.
3. **The existing forecast**, `forecast_window` / `forecast_hour`, built from
   historical stays with a history cutoff (`nora/domain/mobility_forecast.py:235`,
   `nora/domain/mobility_forecast.py:261`). The forecaster builds its own
   hour-of-week dwell table; it does not read `presence_place_rhythm` or the
   stored occupancy table. `presence_occupancy` is the separate stored
   hour-of-week representation (`nora/infra/sql/schema.py:517`).
   `presence_place_rhythm` is keyed only by person/place and holds circular
   mean arrival/departure and duration statistics, **no hour-of-week bins**
   (`nora/infra/sql/schema.py:538`). It can qualify a departure estimate,
   never supply a place probability. Retain forecast support and fallback
   basis; `insufficient` remains unknown.
4. **Home**, a derived place, as a labelled fallback only.

Record the rung and assumptions. A rung-4-only logistics estimate never
ranks a candidate first. A synthetic example is "assuming ada leaves work
around 19:00"; no private location is rendered unless its gate passes.

## 6. One profile owner

Recommendations reads profiles and their cited sources; it does not maintain
a competing interpretation of people. Profile writers do not ingest the
ranker's scores or explanations as evidence about a person's likes. A real
response can become evidence through its original Record message. This
prevents a recommendation from confirming its own assumptions.

## 7. Custody and disclosure

**Destination-filter every input before it can affect the run.** Fix the room
and its live audience first. Apply the source's fail-closed lineage,
exclusions and location predicates before trigger admission, framing,
candidate generation, facet projection, constraints, scoring, fairness or
explanation. Filter source observations before computing a posterior; a
previously aggregated score containing withheld evidence cannot be reused.
Missing requirements or an unreadable roster withhold the input.

This follows `design/standing-ledger/30-turn-records.md:113`: complete
requirements are captured at read time, not guessed later from a subject.
The decision and every derived result inherit **all used and cited inputs'**
restrictions, not just those mentioned in the explanation
(`design/standing-ledger/20-the-ledger.md:127`,
`design/standing-ledger/30-turn-records.md:137`). Recheck them before delivery
if the destination audience changes. Same-unit membership alone is insufficient.

If a person's location gate fails, neither their presence origin, routes nor
a score derived from them can influence the shortlist. Use permitted stated
plans, or leave the origin unknown. Hiding the reason while keeping its
influence would still disclose it through the ranking.
