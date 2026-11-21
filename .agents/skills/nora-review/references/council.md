# Review panels (council)

A review is a panel of one or more reviewers. Triage picks the panel's tier
(`nora-work/references/lifecycle.md` «Triage»); the orchestrator assembles it
from the lanes table (`.agents/lanes.md`) and merges the results. Every member
follows `nora-review` and `REVIEW.md`. A panel's synthesis is evidence for the
transition, never the transition's approval.

## Tiers

| tier | panel | when |
|---|---|---|
| 0 | one independent reviewer | the default: light and standard routes |
| 1 | several reviewers of one vendor, one lens each (application, security, data/concurrency, measurement) | a change across several areas where one reviewer cannot hold every lens |
| 2 | reviewers of different vendors on the same question | the full route, a phase-end drift audit, an architecture verdict |
| 3 | tier 2, then one debate round where members answer each other's findings | an irreversible or high-stakes design decision |

Start at the lowest tier the route allows. Raise it one step only when a
round's reviewers dispute a finding or report low confidence; a raised tier is
recorded with its reason. A tier is never raised to buy another round.

## Modes

- **Review or audit:** findings with severity, and a verdict.
- **Re-check (finding-driven):** the input is the prior findings and the repair
  delta. Four checks only: is each finding **resolved**; did the repair cause a
  **regression**; did it **drift** from the design or brief; does it
  **interact** with another fix. Nothing outside the delta is re-reviewed.
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
names both positions and the evidence each cites, and goes to the next tier or
to Mike when it blocks. Severity stays the reviewer's (`ORCHESTRATION.md`).
A member that is unavailable is recorded **SKIPPED**, never filled in; the
panel proceeds when at least one member returns. A tier-2 panel with every
other vendor SKIPPED is a tier-0 review and is reported as one.
