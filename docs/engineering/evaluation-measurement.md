---
class: engineering
subject: "what a measurement is and how a verdict is reached"
owns:
  - reviewed contrast: "a calibration example used to decide whether a judge can distinguish accepted from rejected behaviour for a criterion"
  - run budget: "the declared per-run ceiling on model-gateway spend"
  - grading contract: "the frozen binding of cases, criteria, instructions and judges a run measures against"
  - single-arm acceptance run: "an acceptance measurement of one arm against fixed criteria and a reviewed grading contract"
---

# Measurement and verdicts

## One experiment and one evidence source

A scenario, criterion, evidence projection and reported claim describe the same
capability. The existing evidence recorder, `RunEvidence`, retains SDK-native
invocations alongside canonical application outcomes. The SDK owns its case,
experiment and invocation machinery; Nora supplies isolation, domain checks
and projections the SDK cannot express. A custom measurement does not justify
a second runtime, event history or independently assembled report trajectory.

One run/case/attempt/model/tool hierarchy connects actual requests and responses,
offered schemas, tool arguments and domain outcomes to persisted revisions,
delivery receipts and terminal settlement. Framework success is not an accepted
write; a tool response alone is not proof of persistence or delivery.

Evidence includes effects that disappear from the current view: creation then
retirement, cancellation, replacement and refused writes. Current active-entry
readers cannot establish a complete history of a wake's changes. Canonical
revisions and receipts supply that history; evaluation does not invent it.

Reports, debuggers and judges derive views from this evidence. Every projection
preserves source identity, scope, time, uncertainty and the premises its claim
needs. Lossless factoring may remove repeated values; dropping required evidence
or silently truncating cannot produce a valid smaller measurement.

## Criteria and judge visibility

Product requirements and reviewed references define expected behaviour before
candidate outputs are inspected. The evaluation author maps each requirement
to a criterion and an actual consumer. Independent review checks valid, invalid,
ambiguous and insufficient-information controls; the operator owns unresolved
product choices. Another model's agreement is not ground truth.

A criterion accepts equivalent valid outcomes. It cannot demand an exact phrase,
storage kind, tool order or number of writes unless the product requires it.
Required semantic coverage, factual support and storage eligibility are separate
properties. A grounded extra detail is not automatically required; a permitted
kind of claim is not automatically supported. Attribution, identity, time,
modality and audience qualifications remain substantive.

Shared media is judged on conveyed information. Grounded incidental visual
detail is permitted but required only when the task turns on it. Explicitly
identified people and depicted activities can be retained as attributed media
content; appearance or sharing alone establishes no identity, ownership, habit,
residence, location or gender. Missing media cannot ground a guessed description.
Explicit storage exclusions still apply to supported content.

Judges see the exact criterion and the bounded evidence needed to decide it,
including earlier turns where the claim spans them. Evidence shown to Nora,
post-execution outcomes and authorized reference-only material are distinguished.
A requirement is not factual evidence, and a judge cannot assume Nora saw a
hidden reference. Missing premises yield an unavailable judgement rather than
an inferred absence.

Semantic judges are independent of the subject. Model identity, raw answers,
reasons, cited evidence and parsing remain inspectable. Citations resolve to
what the judge was actually shown. Structural failures cannot be cleared by
fluent prose or an agreeing judge. Permission, disclosure and fabricated-presence
failures cannot average away.

Reviewed contrasts calibrate false accepts and rejects. They are fixed before
the run, disjoint from the scored cases and free of production rows. A judged
criterion's behavioural verdict counts only after both independent judges pass
calibration against them. Calibration examples remain distinct from held-out
confirmation. A judge disagreement is REVIEW. A criterion whose judges have not
passed calibration reports behaviour UNAVAILABLE, not a score.

Learning claims follow [Cognition's evidence chain](../design/cognition/learning.md).
Persistence, exposure, declared dependence and later success do not alone
establish use. Adaptation compares the same recorded decision with and without
its learned rows, under matched repeated draws and unchanged-arm controls.
An independent semantic comparison must separate a changed decision from
ordinary replay instability. This measures influence at that decision, not
the whole acquisition-to-correction journey; an unreproducible input is
unavailable.

## Three results

- **Execution** states whether the specified path ran and settled, including
  admission, tool work and completion hooks. A legitimate silent outcome needs
  the entry point's positive completion signal.
- **Measurement validity** states whether the subject was equivalent to the
  declared target, the required path was exercised, and evidence reached its
  intended consumers intact. Event pairing or collector acknowledgement alone
  establishes none of those semantic properties.
- **Behaviour** states whether a valid measurement supports the criterion.
  PASS requires its required checks; FAIL is an observed violation; REVIEW
  preserves disagreement or insufficient grounds. An instrument ERROR cannot
  become PASS or an ordinary quality score.

Missing provider-returned reasoning is recorded as missing, never invented.
It invalidates only a claim that needs it; observable calls and state outcomes
can establish other properties without access to private model reasoning.

## Comparison and cost

The experiment binds subject and grader identities separately: scenario,
reference, source snapshot, model, instructions, configuration, projection,
parser and aggregation. Intended differences are explicit. Changing the oracle
creates a revised measurement; it does not clear the original result. Rejudging
both frozen arms can support a retrospective comparison, not fresh subject
trials or independent held-out confirmation.

Draws, consecutive wakes and model/tool calls are different units. Repeated
draws retain unique identities; disagreement is UNSTABLE and errors remain in
the denominator. One draw is a smoke observation, not a reliable rate.

A run budget is declared once per run at the model gateway. Before spend, the
run states an estimate in requests. The budget covers the actual dispatch path,
including SDK serialization, and counts every subject request, judge request,
retry and output reservation. Payload size and total wire size are different
bounds. Exhaustion turns each remaining draw into an ERROR, never a skip.
Spend authority is given per run; a budget with room left is not permission to
retry or rescore. Unknown usage remains unknown. Latency, tokens, cache use,
external work and database read cost accompany quality; a cheaper failed path
is not an improvement. Paid rescoring and retries need their own authority.

Every single-arm acceptance run measures one arm against fixed criteria and a
reviewed grading contract. It reports, per corpus, execution, measurement
validity and behaviour with their counts and spend. It claims no improvement
over another arm.

## Privacy and observability

Full content lives in private artifacts and authorized sinks. Public reports
carry fixed causes, counts and opaque references, never production rows or
credentials. Failure retains available evidence without expanding disclosure.
Backend readback establishes retention and queryability; a frontend claim needs
frontend evidence. Neither establishes the criterion's truth by itself.

[Observability](../operations/observability.md) owns sink and transport mechanics;
[the eval SOP](../operations/eval-sop.md) owns execution and adjudication procedure.
