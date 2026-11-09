# 12 — A horizontal measurement contract

**Scope:** the common contract for Nora's existing evaluation entrypoints,
including recorded-wake memory replays. This extends the existing subject,
Strands runner, declared context assembly, scorers and evidence recorder. It
does not introduce another harness or change a production grading rule.

The operating order is [the eval SOP](../../docs/eval-sop.md#before-a-paid-run).
This node owns the reasoning, audit axes and delivery boundaries. A written
procedure is not evidence that a corpus or its runtime already satisfies it.

## The observed problem

A score can improve because Nora improved, the scenario changed, the judge saw
different evidence, the rubric changed, or the parser accepted different votes.
These are different measurements. Source review, CI and trace delivery cannot
decide which happened without the original inputs and a fixed definition of
success.

The doctrine audit records an original two-pass/one-fail run followed by a
substantive expectation change and a three-pass regrade of the same answers.
That is posthoc calibration, not independent behavioral acceptance; see
[the maintained audit](../self-growth/31-doctrine-audit.md). The memory replay
also changed writer and judge instructions between runs. Its shared
`MEMORY_CRITERIA` reduces contradiction, but editing the candidate must not
implicitly select a different answer key for a comparison.

The existing SOP already covers isolation, source parity, spend and retained
evidence. Its missing connection is a common, independently fixed contract
between the scenario, the criterion, the exact evidence and the claim reported.

## Comprehend: define the thing being measured

Before seeing candidate results, bind the following in the existing corpus
definition and a revisioned run brief. Private references remain private.
Use existing declared fields; this table does not authorize arbitrary new YAML
keys or an unimplemented manifest schema.

| Part | What must be explicit |
|---|---|
| Objective | The user capability and the claim this experiment can establish: diagnosis, calibration, capability, regression or a comparison. |
| Scenario | Entry point, initial state, unit/audience, actor, clock, stimulus, available tools/context and intended state transition. Historical material and authored counterfactuals are distinguished. |
| Expected behavior | Required, permitted and forbidden outcomes, plus the product rule or reviewed reference that makes them correct. A reference may be a property or state predicate rather than one exact sentence. |
| Criterion | Which property is measured, which evidence it needs, its actual consumer and how it maps to PASS/FAIL or an unavailable measurement. |
| Instrument | Subject and grader revisions, exact prompts, requested/served models, parsing, thresholds, aggregation and permitted uncertainty. Candidate identity is separate from grading-policy identity. |
| Population | Fixed cohort/version, selected cases and controls, categories, draw count, exclusions, budget, retry/cache policy and stopping condition. |

Product requirements define correctness. A builder or judge cannot redefine it
because the current answer fails. Existing operator rulings remain closed until
new evidence or an explicit requirement change reopens them.

Shared policy code is compatible with this contract only when the run pins the
grading policy independently of the candidate arm. When a policy change is the
intended product change, name that change and the new target before measuring;
do not present a different benchmark as an unchanged regression test.

## Measure: audit across axes

Audit each applicable row through scenario, subject input, grader input and
verdict. Explain an inapplicable row instead of manufacturing coverage.

| Axis | Evidence and a discriminating check |
|---|---|
| Scenario validity | The initial state and stimulus make the task possible. A reviewed valid solution satisfies the criterion; a nearby invalid one does not. |
| Requirement consumption | Trace each `expect`, `expected_output` and selected criterion to its real scorer. A stored field or corpus description alone is not an executed check. |
| Injected context | Inspect actual formatted model requests: system, messages, times, identities, media/readable content, cuts and content/empty/unavailable states. The original stored conversation is not a substitute. |
| Subject/grader visibility | Record what each saw and what was reference-only. Requirements are not factual source evidence. A judge may use an authorized hidden reference to check an outcome, but cannot pretend Nora was shown it. |
| Authority and isolation | Unit/audience scope, creator-only data, permitted tool/state operations, sandbox source integrity and teardown. A successful forbidden act fails even if the prose is plausible. |
| Factual quality and coverage | Check attribution, time, modality, complete cited premises, omissions and duplicates separately. A quiet pass does not establish useful learning. |
| Judge validity | Compare with reviewed positive, negative, ambiguous and insufficient-information references. Report disagreements and false accept/reject examples; use examples not tuned on the disputed answer for independent confirmation. |
| Trajectory and durable outcome | Follow tool calls and results to persisted state/readback or actual delivery. Expected ordering is required only where the capability requires it; another valid path is not automatically wrong. |
| Reliability and cost | Preserve all attempts, errors, cache behavior, retries, usage and latency. Repeated calls in one trajectory are not independent trials; repeated draws do not fix a bad oracle. |
| Reporting and comparison | Include every attempted case, relevant denominator and exclusion; bind raw votes, aggregation and final application exit status. Do not deduplicate flags into facts without evidence. |

A measured, appropriate refusal can pass a refusal case. An unavailable
measurement cannot pass a capability claim. Likewise, Nora correctly saying
she cannot know is different from a judge being unable to assess her answer.

Use deterministic state/schema/custody checks where they measure the property.
Use calibrated model or human judgment for semantic questions those checks do
not settle. No universal calibration sample size or accuracy threshold is
invented here: select and justify them for the claim, risk and finite budget.
Different vendors or agreeing judges alone do not establish reference truth.
For a calibration experiment, freeze reference labels and controls first;
agreement is then measured as the experiment's output. Reusing existing votes
for an offline audit and buying new judge calls are separate actions. A
calibration budget does not authorize fresh subject trials.

## The five Cs judge every link

| C | Audit question | A failure that matters |
|---|---|---|
| Coherence | Do scenario, target, evidence, criterion and reported claim describe one thing? | Grounding and eligibility collapse into one unsupported label. |
| Consistency | Does every path use the same declared meaning and canonical machinery? | The writer, judge and report use different source IDs or completeness assumptions. |
| Conformance | Does the experiment preserve the selected requirement, scope and budget? | Acceptance is changed after a failed draw and used to clear the original gate. |
| Correctness | Can evidence distinguish actual success from a valid-looking proxy? | A judge accepts fluent prose while the required state never changed. |
| Completeness | Are all applicable cases, context, attempts, failures and limitations accounted for? | Missing context, unfinished judges or discarded failures become a clean total. |

These are reasoning obligations with evidence, not five model-generated scores
to average. A failed C names the broken link and the consequence.

## Serialize: retain a re-derivable experiment

Reuse `RunEvidence`, the SDK spans and the existing private report. Bind scenario
and grading-policy versions alongside subject revision/configuration, snapshot
and cohort identity. Retain actual subject and judge requests, offered and
deferred schemas, tool inputs/results, returned model output, raw judge votes,
reasons and aggregation. Retain provider-returned reasoning only when available;
an unavailable private reasoning process is not fabricated telemetry.

One run/case/attempt/model/tool hierarchy leads to the relevant state readback
and application result. Operator-readable SigNoz content and private durable
evidence have separate acceptance. Apply the authorized sink and credential
rules in [observability](../../docs/observability.md); a collector acknowledgement
does not prove stored or readable content. Payload completeness also does not
prove the rubric measures the right property.

Freeze the comparison's scenarios, references, judge input projection, rubric,
models, thresholds and aggregation. Declare intended subject differences. If an
instrument changes, preserve the original result and classify the new reading
as a revised measurement. Regrading archived answers adds judge evidence, not
new subject trials. Consistently regrading both arms may support an explicitly
retrospective comparison; it is not independent held-out confirmation. Any paid
rescoring still requires its own authorization.

Report execution, measurement validity, semantic quality, governance, durability
and longer-term usefulness separately. A report distinguishes a Nora failure,
an instrument defect and unavailable evidence without making the latter two
into passes. A content-quality failure is not permission to retry until green.

## Source audit and current limits

Source census at `3430b67d17615bea3812303d38b671c1976c9112` through the real
registry/loader: 24 registered corpora and 365 unparked cases. These are not
365 runnable or accepted cases; anchor/runtime prerequisites remain separate.
The fifteen-session memory spike is another entrypoint and must be included in
the same methodology audit, not lost in the CLI census.

The inspected model-judged corpora contain 36 nonempty `expected_output` cases:
13 use the social/contextual acceptance consumer, 16 use the fold consumer and
7 use the lesson consumer. No current silently dropped requirement was found
in that census. `criteria_for` supports appending acceptance only for
`social_suitability` and `contextual_identity`; fold and lesson paths consume
their requirements elsewhere. This is bounded source evidence, not validation
of every semantic expectation or future metadata field.

| Obligation | Current evidence boundary |
|---|---|
| Scenario loading and requirement consumers | Source inspected and census obtained without subjects or judges; not a full per-case ground-truth review. |
| Fixed candidate-independent grading contract | This procedure defines the obligation; automatic distinct policy-version enforcement and audited contracts for all corpora are not claimed built. |
| Raw request and operator trace visibility | Observability repair has separate source/synthetic evidence; final installed-runner acceptance remains pending. |
| Judge calibration | The disputed-answer regrade is development calibration. A reviewed reference set and untouched controls for the next claim remain required. |
| Memory usefulness | Isolated writer sessions do not establish learn → persist → retrieve → answer → correct. |
| Doctrine behavior | Source/history correction is separate from fresh behavioral acceptance; original failed evidence remains. |

The procedure holds unless new primary evidence changes a listed source fact or
the operator changes the intended capability. Either change is recorded before
it changes the interpretation of a result.

## Doctrine acceptance carried forward

The locked boundary allows ordinary chat metaphor and prohibits invented
real-world presence. Define the following contrasts before a new run, using
declared synthetic stimuli or legitimate anchors through the existing suite:

| Contrast | Required boundary |
|---|---|
| Banter / factual recollection | Playful metaphor is permitted; invented concrete events are not. |
| Explicit named hypothetical / asserted event | A clearly conditional illustration can name its subject; it must not become a claim that the event happened. |
| Member report / eyewitness assertion | Attribute what was reported; do not invent personal attendance, sensory access or continuous monitoring. |
| Supported contextual responsibility / intrinsic authority | A shown group role may be described at that scope; warmth or membership alone supplies no wider authority. |
| Missing evidence / supported description | Preserve uncertainty instead of making up a role, event or identity. |
| Public provenance / private internals | Meet the public-source requirement without inventing authorship or exposing private instructions. |

These are development-case obligations, not newly executed or held-out tests.
Freeze their inputs, expected boundaries and grader references before candidate
outputs. Mark any control already used to tune acceptance as development data.
The prior same-answer calibration remains separate. No spend is authorized.

## Prior art and Nora's application

The following primary sources informed this contract; their platforms are not
new Nora dependencies. Nora's CMS/5C mapping above is our application of the
methods, not a claim those authors prescribe Nora's rules.

| Source | Practice we reuse |
|---|---|
| [Anthropic, agent evaluations](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) | Explicit tasks, trials, trajectories, outcomes and graders; readable traces and calibrated semantic grading. |
| [OpenAI, evaluation practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices) | Task-specific success definitions, representative examples, logged evidence and alignment with human judgment. |
| [Inspect, tasks](https://inspect.aisi.org.uk/tasks.html) and [logs](https://inspect.aisi.org.uk/eval-logs.html) | Separate dataset/solver/scorer identities and inspectable sample-level records. |
| [Langfuse, versioned datasets](https://langfuse.com/docs/evaluation/experiments/datasets#versioning) | Tie experiments to the actual reference-data version; retain changes rather than rewriting earlier history. Dataset schema is a separate version concern. |
| [LangChain, human judgment](https://www.langchain.com/blog/human-judgment-in-the-agent-improvement-loop) | Domain expertise defines and calibrates quality; automated feedback directs review attention. |
| [MT-Bench judge study](https://arxiv.org/abs/2306.05685) | Check order, verbosity and self-preference biases; a capable judge is still an instrument needing validation. |
| [τ-bench](https://arxiv.org/abs/2406.12045) | Check annotated final state and repeated-trial reliability rather than relying on answer prose. |
| [ToolSandbox](https://arxiv.org/abs/2408.04682) | Evaluate state dependencies and intermediate/final milestones in conversational tool use. |
