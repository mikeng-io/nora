# Evaluation

Different suites exercise different inputs through one Nora. A case is typed
input and expectation; the subject is obtained through production composition,
never reassembled for evaluation. Verdicts are measured against real execution
and remain re-derivable from their evidence.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fevaluation%2F%22)

## Parts

- **Corpora** are data: an entry point, a real room and instant, an arriving
  message and the property it must produce. Adding a scenario adds no driver.
- **The subject layer** obtains Nora with the deployment's own configuration,
  policy, model and features, inside isolated storage and external workspaces.
- **The SDK harness** drives cases, captures the SDK-native `Session` and grades
  content and tool trajectories. Nora does not implement another runner, report
  or trajectory format.
- **Absence and outcome grading** distinguish intended silence, a wrong answer
  and a run that never happened. Entry-point-specific fail-closed signals make
  that distinction where an empty output alone cannot.
- **Anchored sandboxes** reconstruct what production held at an instant, or
  declare a bounded counterfactual that no real moment could contain.
- **Evidence capture** preserves actual requests, responses, tools, state,
  outcomes, source identity and cost. Judges read the same bounded evidence
  the subject saw, with private content kept private.

The subjects are [the subject and sandbox](subject.md), [measurement and
verdicts](measurement.md), and [scenario coverage](coverage.md).

## Invariants

- The eval layer composes nothing. Production owns the graph; evaluation owns
  only the case, isolation, drive and measurement.
- The deployment's own model, configuration and feature flags are the baseline.
  An example configuration or a reduced prompt is a different subject.
- ERROR is not FAIL and is never PASS. Missing, truncated, unmatched or
  ungradable evidence is an instrument error, not a behavioural success.
- A populated SDK `Session` comes from the SDK mapper. No hand-built trajectory
  or second record shape replaces it.
- An expected absence is not an empty string. It needs a positive execution
  signal showing the intended path completed without producing the thing.
- One stochastic draw is a smoke check, not a rate. Disagreement across draws is
  UNSTABLE, not a majority verdict.
- A judge may remove a pass, never grant one against the case's structural
  expectation, and never grade its own writer.
- Shared source and template databases are never migrated or mutated by a run.
  Schema or source divergence refuses early rather than becoming a subtle wrong
  answer later.
