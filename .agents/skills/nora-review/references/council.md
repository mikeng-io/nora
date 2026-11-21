# Review panels (council)

A review is a panel of one or more reviewers. Tier 0, the route's own review,
is the default; the orchestrator assembles it
from the lanes table (`.agents/lanes.md`) and merges the results. Every member
follows `nora-review` and `REVIEW.md`. A panel's synthesis is evidence for the
transition, never the transition's approval.

## Tiers

| tier | panel | when |
|---|---|---|
| 0 | the review tier `ORCHESTRATION.md` gives the change: none (the orchestrator reads the diff) or one independent reviewer | the default |
| 1 | several reviewers of one vendor, one lens each (application, security, data/concurrency, measurement) | a named lens one reviewer cannot cover |
| 2 | reviewers of different vendors on the same question | a named question needing a second vendor: an irreversible decision, a phase-end drift audit, an architecture verdict |
| 3 | tier 2, then one debate round where members answer each other's findings | an irreversible or high-stakes design decision |

An advisory member may share a vendor with an author: its findings are
evidence, never the independent verdict. A tier above 0 is chosen before review, naming the question it answers; a
dispute or low confidence never adds members after a round. A disputed
finding follows the existing stop rule (`ORCHESTRATION.md` «Bounded
independent review»): the orchestrator triages it against the evidence; a
supported blocker is fixed and verified, and Mike is asked only for intent,
authority or redesign.

## Modes

- **Review or audit:** findings with severity, and a verdict.
- **Re-check (finding-driven):** the window `nora-review` defines: the repair
  delta, unresolved findings and previously unavailable required evidence, even
  on an unchanged head. Four checks only: is each finding **resolved**; did the
  repair cause a **regression**; did it **drift** from the design or brief; does
  it **interact** with another fix. Nothing outside the window is re-reviewed.
- **Brainstorm or design:** competing proposals, no verdict. Round 1 gets a
  clean packet: scope, goal, hard constraints and the output shape. Never the
  orchestrator's suspected cause, preferred design, or other members' answers.
  Later rounds may challenge, merge or reject proposals.
- **Research:** evidence-backed observations with confidence and
  contradictions; no verdict.

## Standing roles at tier 1 and above

- **Devil's advocate:** argues the strongest case that the candidate is wrong,
  or that a simpler design meets the goal.
- **Integration checker:** asks whether the pieces actually connect end to
  end: entry point, wiring, the caller that uses it, the read-back that shows it.

## Synthesis

The orchestrator merges members' findings into one report: a finding two
members ground independently keeps its strongest severity; a disputed finding
names both positions and the evidence each cites, and is triaged by the
orchestrator under the same stop rule. Severity stays the reviewer's (`ORCHESTRATION.md`).
A member that is unavailable is recorded **SKIPPED**, never filled in; the
panel proceeds once its independent verdict member returns. When that member
is SKIPPED, launch the next qualifying lane (`.agents/lanes.md`); a panel left
with advisory members only has no verdict and is Blocked.
