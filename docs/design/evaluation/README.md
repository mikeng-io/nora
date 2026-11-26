---
class: design
subject: "how Nora is measured through her production entry points"
owns: []
area: evaluation
---

# Evaluation

Evaluation measures Nora through her production entry points. It supplies a
scenario and an isolated environment, observes execution, and judges the
result. Production owns composition, context, tools and control flow; a suite
adds cases and measurements without constructing another Nora.

**Status:** building

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fevaluation%2F%22)

## Parts and flow

```text
scenario + restored state + isolated dependencies
  -> production entry point -> Nora's runtime and tools
  -> canonical execution evidence + persisted outcomes
  -> deterministic checks and independent semantic judges
  -> report: execution, measurement validity, behaviour
```

- [Subject and sandbox](subject.md) owns production equivalence, dependency
  isolation and the boundary between replay and a complete journey.
- [Measurement and verdicts](measurement.md) owns evidence, judge visibility,
  criterion authority, comparison and reporting. SDK execution records feed
  the existing evidence recorder; judge and report views derive from it.
- [Scenario coverage](coverage.md) owns the claims different cases and
  execution modes can establish, including rehearsal without quality scoring.
- [Cognition journeys](cognition-loop.md) owns the causal sequence across wakes
  and conscious turns. It applies the common contracts rather than defining
  another harness.

## Invariants

- The baseline is the deployment's configuration, policy, model and enabled
  features. A candidate changes only the declared subject of the experiment.
- Isolation replaces external dependencies at their boundaries. It does not
  remove tools, context blocks, admission or completion work from Nora.
- The same occasion uses the same declared assembly in production and
  evaluation. Different occasions retain their different purposes and context.
- Shared code is necessary but insufficient for equivalence: actual model
  requests, tool schemas, available reads and execution paths must agree with
  the subject under test. An unfulfilled dependency makes the claim unavailable.
- A captured run is not proof of complete execution; complete execution is not
  proof of a valid measurement or correct behaviour. Each is reported separately.
- Missing evidence and instrument errors never become successful absence.
  Every attempted case, retry, exclusion and failure remains attributable.
- Production storage and person-visible services receive no evaluation effects.
  Private evidence remains private, including when a run fails.

The [eval SOP](../../operations/eval-sop.md) owns operating order and commands.
Implementation gaps do not relax these contracts or license a smaller subject.
