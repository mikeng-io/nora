# Eval SOP — intercept a regression before it lands live

The standard the operator asked for: how a change is proven against Nora's real
behaviour *before* it reaches the two live groups. The eval suite is the
interceptor. It is built as the same **comprehend → measure → serialize** spine
the rest of the repo runs on (`AGENTS.md`), applied one layer up: comprehend a
user *journey*, measure the *trajectory* she produces, serialize a *verdict*.

## Before a paid run

Apply this order to every entrypoint, including memory-graph replay, diagnostic
and rescore commands. The [horizontal measurement contract](../design/evaluation/12-horizontal-measurement-contract.md)
defines the axes, five-C checks and current delivery limits. Reuse the existing
runner, corpus fields, private evidence and trace hierarchy.

1. **Comprehend the objective and scenario.** Name the capability and whether
   this is diagnosis, calibration, regression, capability measurement or a
   comparison. Bind the scenario's entrypoint, initial state, clock, actor,
   audience, stimulus and available tools/context. Distinguish historical
   sources from explicitly authored stimuli. State required, permitted and
   forbidden outcomes from product requirements or reviewed references.
2. **Fix the grading contract before candidate results.** Pin the cases,
   references, rubric, judge-input projection, models, parsing, thresholds and
   aggregation independently of the subject arm. Trace every expected property
   to its actual consumer. In the current reply path, appended case acceptance
   binds only social suitability/contextual identity; folds and lessons use
   their own consumers. A field's presence does not prove it is measured.
   Name the product/domain owner, evaluation author, independent evaluation
   reviewer and run orchestrator. The reviewer checks the rubric against reviewed
   valid/invalid references and calibration evidence; another model's agreement
   is not ground truth. Existing product rulings govern routine work. Bring only
   an unresolved value choice or changed acceptable behavior back to the owner.
   Define required semantic coverage separately from factual support and storage
   eligibility. For shared images/stickers, preserve the conveyed information;
   extra grounded visual detail is permitted, and its omission alone does not
   fail coverage unless the scenario turns on that detail. Fix the reference
   units before outputs; do not add obligations after seeing a candidate answer.
3. **Measure the instrument before the subject.** Inspect an accepted reference,
   a nearby invalid one and relevant ambiguous/insufficient-information controls.
   Calibrate semantic judges against reviewed labels, preserve disagreement and
   keep independent confirmation examples separate from examples used to tune
   the grader. Select calibration size and tolerances for the claim; do not
   treat agreeing vendors, one canary or valid JSON as quality calibration.
   A declared calibration run freezes its reference labels and controls before
   judge calls; calibration agreement is its output, not a precondition it must
   already have measured. New judge calls need a separate explicit budget and
   do not authorize a subject benchmark. Retained votes can be audited offline.
4. **Inspect context and observable outcomes.** Read the formatted subject and
   judge requests, including system instructions, source scope, identities,
   clock, media, cut/unavailable states, offered schemas and tool results.
   Requirements are not factual evidence. Check that available tools can reach
   required state and that readback measures the claimed result. A fresh synthetic
   export and backend API readback precede paid work where visibility was
   repaired; an HTTP success or a private file alone is insufficient. Frontend
   checks are required when the acceptance claim concerns frontend behavior.
5. **Serialize a fixed run brief and bounded execution.** Use the existing
   private evidence/receipt to bind the subject, grading policy, cohort/snapshot,
   controls, draws, source/config versions, budget, retries/cache behavior and
   stop condition. Declare intended differences between arms. Preserve every
   attempted case, error and exclusion. A completed budgeted run does not leave
   open-ended credit for reruns or rescoring.
6. **Read back and adjudicate.** Apply Coherence, Consistency, Conformance,
   Correctness and Completeness to scenario → criterion → evidence → verdict
   → report. Separate execution, valid measurement, content quality, governance
   and durable usefulness. Report a Nora defect, an instrument defect or missing
   evidence honestly; the last two are not passes. Correct abstention may pass
   an insufficient-information case, but does not prove recall on an answerable
   one.

If a rubric, expectation or judge evidence changes after observing a failure,
preserve the original result and record the substantive change and its reason.
Regrading unchanged answers is **posthoc calibration**, not fresh subject trials
or independent behavioral acceptance. A corrected rubric can support a clearly
labelled retrospective comparison when applied consistently to both arms; it
cannot silently turn a failed behavioral gate into success. New paid grading
still needs authorization. A candidate's prompt must not silently rewrite its
answer key.

These are pre-run obligations, not a claim every check is automated. A missing
obligation stays unmet/unavailable for the selected claim; do not add a parallel
harness or run models to discover a cause already visible in retained evidence.

### Bind the selected measurement

Paid standard evaluations and memory replay, rescore and diagnostic entries
require `--grading-contract <private.json>` and
`--grading-contract-sha256 <expected-sha256>`. Select the reference independently
of the candidate; never regenerate its normative text from the current writer
merely to make preflight pass. Inspection, listing and source export remain
zero-dispatch operations. The standard zero-dispatch commands refuse unused
contract flags; memory inspection validates an explicitly supplied contract.

Memory replay, rescore and decision diagnostics accept `--stop-after-tokens N`.
With that flag, they share one measured input-plus-output budget across all model
calls in that invocation, also capped at 1000 requests and 600,000 output tokens.
Without it, the non-inspection legacy path installs no measured budget.
Inspection-only always installs a zero-request budget, with or without the cutoff.
Neither path grants
permission to spend. Once usage reaches the cutoff, subsequent calls refuse; the last
admitted call may overrun it. Unknown usage blocks later spending. A stopped run
is ERROR, with counters in `run_budget.json`, the report and trace. Inspection
and replay sample flags remain refused on scoring-only modes. Selecting a cutoff
does not authorize a run; each paid experiment still needs its own finite budget.

The version-one JSON object has these fields:

| Field | Meaning |
|---|---|
| `version`, `entrypoint`, `purpose` | Version `1`; `eval`, `replay`, `rescore` or `diagnostic`; the declared measurement claim. A rescore cannot be fresh confirmation. |
| `judges`, `selection` | Exact requested aliases and selection parameters. Standard selection binds corpora, case filter and sample counts. Replay selection binds seed, strata settings, smoke mode and requested cohort size. Archive modes bind the original evidence-file fingerprint. |
| `implementation` | SHA256 of each path in `evals.grading_contract.IMPLEMENTATION_FILES`: the selected grader, assembly, parser and aggregation source plus dependency lock. A changed implementation needs a newly declared measurement. |
| `reference_doctrine` | Fixed normative doctrine for the reply judge, independent of the subject's doctrine. Empty for memory/archive entries. |
| `instructions` | Full fixed `memory_support` and `memory_coverage` instructions for memory grading; empty for standard evals and decision diagnostics. |
| `cases` | Exact case roster. Each reference contains `input_sha256`, `expected_output` and `units`. |
| `sample_sha256` | The frozen `sample.json` file fingerprint for replay. |

Standard case keys are `corpus:case-name`. Hash `case_scenario(case)`: the loaded
SDK case, excluding its generated execution `session_id`. Bind the fixed
`expected_output` through the existing grader-only channel; a nonempty obligation
with no real consumer refuses before subject execution. Memory keys are the
recorded wake IDs. Hash `memory_scenario(inspection)`: shown agenda and NEW source
custody after the existing transport normalization, excluding work bookkeeping
and the candidate's system instruction. Each required
unit has an `id`, semantic `claim` and integer `sources` from shown NEW rows.
Standard cases use empty `units`; memory cases use empty `expected_output`.

Author the normative reference first. Materializing its implementation hashes
and calculating its final file hash is mechanical; it is not product approval
or independent reference validation. Retain that exact reviewed file and pass
its previously selected hash to each intended arm. Do not replace it after a
refusal without recording which scenario or measurement changed and why.

The private capture records the exact contract text and fingerprint before
model work. Run metadata/report receipts identify the binding; formatted SDK
requests retain the actual dynamic evidence and output independently. Memory
grading records support, eligibility and finite-unit coverage separately under
`fixed-reference-v1`. Every required unit needs a verdict; covered units must
identify saved statements or successful shown reads. Unknown/missing verdicts
or unavailable read evidence are ERROR, not PASS. Unlisted possible omissions
remain unscored diagnostics. Preserve old reports under their original
instrument; report comparison refuses differing contract identities.

For fixed-reference memory scoring, the request also carries the parser's
output contract: support and eligibility fields with nonempty reasons, and a
coverage array containing exactly the supplied unit ids. Empty required units
require `{"coverage":[],"diagnostic_misses":[]}`. Output-format text is part of
the pinned implementation; a format change creates a new measurement identity.
It does not change reference labels or excuse malformed output. Zero-paid
preflight checks the actual formatted request and injects valid empty/populated
responses, malformed shapes, truncation and transport failures through the real
consumer. It verifies refusal, per-judge attribution, stop behavior and capture.
Generated valid responses prove wiring only; they cannot prove that a live judge
understands either the interface or the normative policy.

Judge output should emit each field once. The shared reply, memory and coverage
parsers accept repeated fields only when their canonical JSON values agree,
including nested value types. Equivalent repeats preserve the same value;
conflicting repeats are ERROR/REVIEW, never a first/last-value vote selection.
Raw output retains every occurrence for inspection.
This parser contract applies to a newly bound measurement. Preserve reports
accepted by an older parser; do not retrospectively clear or fail them.
A valid prompt template and a successful canary do not guarantee a provider's
format reliability. Fixed-reference memory calls use the existing text request
protocol and validate the response against strict local JSON schemas. Local schema
validation does not require a provider-native `response_format` capability and
does not add a second model call or an SDK tool loop.
Validation and scoring use the same fact parser: a leading complete JSON document
or fence is read, with trailing explanation ignored; conflicting fields are rejected.
Evidence is mandatory for `covered` rows. For `missed`/`unsure`, `covered_by`
may be absent or null. `diagnostic_misses` is an optional array whose entries are
retained unscored. Local schema checks follow these same consumer rules; an unused
null placeholder or diagnostic annotation must not decide a verdict. Scored meanings,
reason/proof validation and reference labels are unchanged.
Check the actual route's structured-output support before claiming enforcement;
plain JSON mode, a mock request or a requested field alone is not proof that a
schema is enforced. Provider-native enforcement is separate from local validation;
a route lacking it can still use the ordinary protocol with strict local checks.
Never silently retry, discard local validation or repair provider text into a verdict. Keep model/route
changes and new paid probes explicitly authorized, with the same fixed semantic
references.

Binding proves that the selected instrument was used. It does not establish
that reference labels are complete, that a semantic judge is calibrated, that
SigNoz has retained the complete queryable payload, or that a model run is authorized.
Those remain the preceding SOP's separate evidence obligations.

## Judge-only development calibration

`python -m evals.judge_controls --job FILE --job-sha256 SHA --config FILE`
validates a private frozen job without model dispatch. `--execute --output DIR`
uses the existing memory support/eligibility/coverage and reply graders under
one shared measured budget and `eval_host_lock()` (the same inner lock as the
official suite); it runs no subject. The operator wrapper may hold the separate
outer `/tmp/nora-eval.lock` without the command taking that lock again.
Execution requires separate operator spending approval. The job fingerprint is
an artifact binding, never permission or proof that its answer key is correct.

The job pins source head and tracked diff, config, opaque ordered controls,
a separate answer key, both lane-specific grading contracts, selected judge
aliases and finite token/request/output/time limits. Optional
`budget.per_call_output_tokens` selects the output allowance for both judge
paths (default4096); `output_tokens` is its sum across planned requests. The
measured input-plus-output cutoff remains independent. Inputs are projected through
the actual consumers; control labels and expected verdicts never enter requests.
Memory uses a `rescore` calibration contract, and reply controls use an `eval`
diagnosis contract. Both contracts select the exact lane and control-file hash.
Freeze their reference policy independently of candidate writer instructions.

Every selected judge must match each clear control's expected vector. A bad
answer correctly rejected is a successful discrimination; wrong agreement is a
failure. Preserve every judge separately, without voting away disagreements.
Missing/unsure/REVIEW/error is ERROR, never PASS. Stop on unavailable measurement,
unknown usage, exhausted budget or evidence failure. Retain semantic mismatches
and complete the already-authorized finite controls; do not retry or retune.
The final `report.json`, together with its validated capture receipt, owns the
job outcome. The trace's `run_finished` means capture completion and leaves its
exit code unset; it cannot assert success before the final report is written.
The report's PASS concerns only this finite generated development check, not
subject behavior, heldout accuracy or population reliability.

## The rebuilt suite (2026-09-21) — where and how it runs

> The previous harness (`evals.run`, fake stores, hand-authored journeys,
> the intent graph) was deleted 2026-09-18 to 2026-09-21. Its commands and
> their reasoning were removed from this file on 2026-09-24 and resolve in
> `git log` only. The procedure, step by step with the checks, is the
> `nora-eval` agent skill (`.agents/skills/nora-eval/SKILL.md`).

The suite runs **on the deploy host**, against the deployment's **clone**.
Every corpus case names a real room at a real instant; the runner composes
the deployment's own Nora (its `config.toml`, `policy.toml`, `identity.toml`)
against a sandbox database of the case's own, seeds it with what that room
held at that instant, and drives one entry point. Declared stimuli and
counterfactuals may be authored; they must not be presented as recorded history.
The subject uses the corpus's canonical assembly rather than a bespoke
per-case dependency graph (`design/evaluation/11-the-sandbox-rebuild.md`
§1.2–§1.3 and its later amendments).

```bash
# on the deploy host, in a checkout of the branch under test (NOT the deploy checkout)
make eval-source                 # refresh nora_eval (pg_dump); ONLY when no run is in flight:
                                 # it drops every sandbox (nora-eval step 4), never between arms
set -a; source ~/workspace/nora/.env; set +a         # the deployment's keys, so the tools are production's
export NORA_EVAL_PSEUDONYM_MAP=~/.config/nora/eval-pseudonyms.txt   # resolves the scrubbed anchors (#776)
GRADING=(--grading-contract "$GRADING_CONTRACT" --grading-contract-sha256 "$GRADING_CONTRACT_SHA256")
uv run python -m evals --list    # corpora, entry points, classes
# Alternative selections: each needs its matching reviewed contract/hash, not one file for all three.
uv run python -m evals --all "${GRADING[@]}"
uv run python -m evals relevance_gate --sample 3 "${GRADING[@]}"
uv run python -m evals injections identity_grounding --judge "${GRADING[@]}"
```

- `--source-url` defaults to the clone on the deploy host (from its `.env`);
  a database whose name is not `*_eval` is refused.
- The corpora are pseudonymised (the scrub, `0bd34346d`): a case's room id,
  person and names are pseudonyms, while the clone and the deployment's
  `policy.toml`/`identity.toml` carry the real values. `NORA_EVAL_PSEUDONYM_MAP`
  names a private map on the deploy host only (`~/.config/nora/eval-pseudonyms.txt`,
  mode 600, outside every checkout): one literal `pseudonym==>real` per line.
  `evals/pseudonyms.py` resolves each case in memory when it loads (anchor,
  person, input, expectation, metadata; never the case name) and the run
  header prints `pseudonym map: N rule(s)`. Unset, nothing is resolved and a
  pseudonymised anchor refuses with "policy names no conversation", naming the
  variable; set to a file that does not exist, the run refuses. A new case
  whose pseudonym is not in the map refuses with its room withheld: add its
  line to the host file, never a real value to the tree. With the map in
  effect an eval LOG holds real names and room content: it stays on the host,
  and only case names, PASS/FAIL/ERROR, counts and spend leave it (never into
  an issue, PR or commit).
- `--deployment-config` defaults to the deployment's `config.toml`; the run
  refuses to start if the rebuilt `config.evaluation.toml` differs from it
  outside `ALLOWED_TO_DIFFER`.
- Read the census. A parked case is counted, never run; an unanchored case
  ERRORs. ERROR is never PASS.
- The branch under test is a separate checkout: fetch and check out the ref
  first, or the run measures old code and reads like a success.
- One run at a time on the host (`pgrep -af "[p]ython.*-m evals"` first).
  `make eval-source` drops every sandbox, so it kills a run in flight.
- A case reads PASS or FAIL only when every draw agrees, otherwise UNSTABLE.
  Report the draw count with the verdict; n=3 is not a rate. Each draw gets a
  private Honcho workspace, created and cleaned up by
  `nora/subject/memory_workspace.py`; model draws still do not establish a
  population error rate.
- Every run prints its own spend; put it in the PR with the command.

## Durable run evidence

The CLI holds the existing host eval lock for the whole run. Before composing
subjects or calling models it opens a private, unique run directory and prints
`private eval evidence: ...`. The default is
`${XDG_STATE_HOME:-~/.local/state}/nora/evals`; `NORA_EVAL_EXCHANGES` selects an
existing private evidence root instead (and retains its legacy exchange dumps).
Directories must be owned by the runner with mode 0700; `events.jsonl` is 0600.
These files contain actual prompts, tool arguments/results and room content.
They stay on the eval host, outside Git, PRs and chat. Transport authorization
headers and credential files are not recorded. Tool payloads can still contain
sensitive material; access and retention follow the same private-log boundary.

The append-only JSONL file fsyncs each record. It contains the held lock identity,
source revision/diff and configuration hashes, run selection, case/draw inputs,
actual admitted/resident/deferred tool names, formatted OpenAI request bodies,
existing OTel spans (including model/tool outputs and usage), enabled application
logs, raw judge votes, typed case results and complete corpus reports. It uses
the existing provider/SDK spans. The CLI also requires the configured owner-local
SigNoz sink; [evaluation visibility](observability.md#evaluation-visibility)
defines the explicit content policy and operator acceptance.
Request bodies and tool-surface events are persisted before model dispatch;
raw model paths retain their existing SDK span contents rather than gaining a
second model wrapper. Run/case IDs and trace/span IDs correlate the records.


The memory-graph command shares this automatic operator export: replay,
`--rescore` and `--decision-judge` all require the configured owner-local sink
before execution. Stderr prints the trace ID; stdout retains the JSON report.
Replay cases group one selected wake's writer and both judges with the frozen
sample and snapshot provenance. No paid replay/rescore is authorized by the
source tests or a previous synthetic trace.

Read the printed run directory privately. `run_finished` carries the CLI exit
code; it does not turn a failed case into PASS. A started case/run without a
terminal record is incomplete, including a process killed before cleanup.
Span start records survive interruption; unfinished spans cannot promise a
complete response. Failure to write evidence invalidates the run and stops new
work; inability to create the artifact refuses the run. `--list` and
`--print-input` remain inspection commands without model execution. Collector
availability and private evidence retention are separate operational checks;
this local artifact does not prove delivery to SigNoz or semantic acceptance.

## Anchoring the Cognition session corpus

The thirteen scenarios include two code guarantees and eleven parked model cases
(three held out). The committed corpus stays parked. Anchors are materialised
only in the deploy host's eval checkout, never committed or copied off-host.
No live data or host access is needed to build or test these scripts.

Run the following **on the host**, from its eval checkout at the PR head, with
the deployment environment loaded privately. Hold the existing eval lock for
this sequence; do not refresh the snapshot between finding and replaying.

```bash
exec 9>/tmp/nora-eval.lock
flock -w 900 9 || exit 75
umask 077
set -a; source ~/workspace/nora/.env; set +a
export NORA_EVAL_PSEUDONYM_MAP=~/.config/nora/eval-pseudonyms.txt
EVAL_SOURCE_URL="postgresql+asyncpg://nora:${POSTGRES_PASSWORD}@127.0.0.1:5435/nora_eval"
uv run python -m scripts.find_cognition_anchors \
  --source-url "$EVAL_SOURCE_URL" > /tmp/cognition-candidates.jsonl
uv run python -m scripts.confirm_cognition_anchors \
  --source-url "$EVAL_SOURCE_URL" --config ~/workspace/nora/config.toml \
  --candidates /tmp/cognition-candidates.jsonl > /tmp/cognition-confirmed.jsonl
uv run python -m scripts.write_cognition_anchors \
  --source-url "$EVAL_SOURCE_URL" --confirmed /tmp/cognition-confirmed.jsonl
uv run python -m evals cognition_session --sample 3 \
  --grading-contract "$GRADING_CONTRACT" --grading-contract-sha256 "$GRADING_CONTRACT_SHA256" \
  > /tmp/cognition-eval.log 2>&1
echo "RC=$?" > /tmp/cognition-eval.rc
```

`--config` is the deployment's own `config.toml`, verbatim: the confirmer
rebuilds `config.evaluation.toml` against it (the same drift-checked
`materialise` `python -m evals` runs) and resolves the `eval_judge` route
through `nora.subject.obtain.model_endpoint`, so a `host.docker.internal` or
`honcho` name is rewritten to loopback exactly as a subject's own endpoints
are. The production route gets one plain POST at temperature zero, without
`response_format` and without retries. Never show agents the model inputs,
raw responses or private logs.

The finder joins message-shape matches to `recorded_wake` stimulus batches,
skipping incomplete batches. It emits only wake ids, explicit `room_*` aliases,
scenario numbers and strength counts, at most twenty wakes per room/scenario.
It has no heuristic for scenarios 8 and 10; those stay parked unless the
operator supplies a real wake candidate privately in the same JSONL shape.
An empty result means no heuristic candidate, never proof of no instance.

The confirmer restores every store at the session's recorded execution time
(`started_at_ms`, or the wake's write time without a session), not the wake's
pending watermark `at_ms` — once per wake, with
every candidate on the same wake sharing the restore — composes the same
subject a `cognition_session` draw runs (the corpus's own overrides, the
full-restore set and the eval workspace), and stops after its normal context
assembly, before its model call (`CognitionSessionRunner.inspect`).
It judges the system prompt and rendered agenda/grounding the session would
see, plus open notes (including their then-current bodies), open events/plans
(staged, proposed and confirmed) and current/disputed profile lines
from the restored unit, with the wake time and room explicit. Today's state
is never substituted. `match` confirms the input situation, not a successful
session trajectory. Errors, missing state, refusals and truncated/wrong-shape
answers are `unavailable`, never `no_match`.

The writer refuses outside the Linux deploy-host layout, revalidates each
confirmed wake/room, then replaces `parked` with top-level `snapshot` and
`metadata.wake` in **that checkout only**. Unmatched scenarios remain parked.
Review anchor-specific argument checks privately before treating the replay as
acceptance evidence: the existing generic checks do not establish all semantic
properties. No baseline or confirmed anchor is claimed by this PR.

## Cloud development and private evaluation

The proposed GitOps path would handle deployment; it would not give a cloud
checkout the data needed for these evaluations. The current harness needs the
deployment configuration,
identity/policy and private pseudonym map, a frozen database clone, disposable
writable case databases, model access and the services enabled by that corpus.
Graph retrieval also needs compatible embedding state/model access. Source
reads are read-only, but the run itself is not: it creates databases and
private Honcho workspaces and spends model calls.

A minimal next step is an isolated evaluation worker beside the deployment, running the
existing CLI from a separate checkout. An operator prepares a snapshot on a
separate evaluation Postgres instance and supplies only the required private
files and scoped service credentials. The existing sandbox code creates case
and template databases beside the source, so moving just the source URL while
keeping a production database account is not sufficient isolation. Snapshot
refresh stays a separate operator operation: the current refresh script drops
sandboxes and terminates their sessions. Do not run it from a cloud PR job.

A trusted host-side launcher can accept an approved commit and named corpus,
run one bounded job, and return case names, verdicts, counts and spend. Keep raw
logs, prompts, identities and snapshot data on the private worker. Run only
reviewed code there: a sandbox in the eval harness is not a security boundary
against malicious Python. Public PR tests stay on disposable hosted runners
with synthetic data and no private credentials. Restrict worker network access
to the services needed by its selected corpus; the existing `.env` sourcing
example is not a least-privilege worker credential design.

This worker/launcher is a proposal, not an installed service. Start with one
manual, operator-approved job on a disposable worker before adding a queue.
Interactive access is still useful when reproducing a failure that needs raw
traces or live service diagnosis; grant time-limited access to the evaluation
worker when needed, separately from deployment authority. It should not imply
cloud SSH access to the production shell or Docker socket.

## What gates a change

**In `make gate`, offline, no model:** the deterministic evaluators
(`tests/unit/test_the_deterministic_evaluators.py`) and the rubric
(`tests/test_evals_rubric.py`). Green or the change does not merge.

**Live, on the deploy host, before a behavioural change ships** (a prompt,
the doctrine, a tool's text, a gate, routing): the corpora that the change can
move, before and after, same `--sample`. The make targets are shortcuts:

| target | runs |
|---|---|
| `make eval-gate` | `relevance_gate` |
| `make eval-participation` | `participation`, the flip gate (below) |
| `make eval-adversarial-corpora` | the injection, grounding, scope and contradiction corpora, judged |

Routing ("which tool did she reach for") is the `tool_selection` corpus,
graded on the tools that actually executed; `--reasons` prints them per draw
(since `21e5405d`, 2026-09-23). A wrong route that answers fluently is
invisible to a reply grader, so a routing change is measured there, not by
whether she replied.

## Known limitations: read before you trust a number

1. **Repeated draws do not establish a population error rate.** Each draw has
   its own Honcho workspace, but the draws still share a corpus, model and
   evaluation setup. Report the sample count and its scope.
2. **An eval whose tool results differ from production measures a different
   decision.** 2026-09-24: an image handle the sandbox could not resolve made a
   tool fail in the eval and pushed the model on to the right next step (about
   8/10); live, the same tool succeeded and the model stopped there (0/2). The
   next step depends on what the last tool RETURNED. Check a changed tool
   against a live call too.
3. **A case must anchor on a room whose platform adapter the sandbox has.**
   Discord is off in the sandbox; a case anchored there errors before the
   model is called.
4. **A narrow corpus overstates detection**, and a judge detects the
   fabrications its corpus teaches it to. Widen the corpus before trusting a
   rate; read a green adversarial run as "not caught by this judge".

## The loop: when the live rooms surface a new failure

1. **Comprehend.** Name what went wrong and what the right behaviour was.
   Read the model's INPUT for that turn (the Langfuse trace) before touching
   its instruction.
2. **Measure.** Add the case to the corpus that owns that behaviour (a
   routing miss to `tool_selection`), and read it offline (`--print-input`,
   the case's anchor and expectation) to confirm the input shows the failure
   and to name its cause. No run yet: its failing number comes from the
   before arm of step 3's run, per `.agents/skills/nora-eval/SKILL.md` "An
   eval confirms a fix; it never finds one".
3. **Serialize.** Fix, then run the same selection on both arms (the case,
   plus its corpus if the fix can move other cases there), and put both
   numbers, the draw count and the spend in the PR.

A failure you fixed but did not add a case for is a failure you will ship
again.


## The participation suite — the flip gate (design/dreamer-agent-loop/35 §9)

`make eval-participation` runs the 15-case participation corpus, including
relevance-Yes cases, their knowledge-cut twins, and the pure-No cases (S6-S10) — the S1 and S4 pairs were
deleted with the knowledge tier, since their `-known` half had nothing left to
ground on. Each
case is `transcript + standing seed + incoming -> expected verdict (speak /
silence)`, driven through the REAL participation path (real
`ParticipationTrigger`, real `FirePath.speak`, real policy opt-in, real model).

**This is the flip gate.** Zero false-speaks on the knowledge-cut twins and
zero speaks on S6-S10 is the hard bar the design makes load-bearing — a model
that answers a twin from parametric memory has failed, however correct the
answer is in the world.

**Reading the output:** a turn that never successfully invoked the model
(timeout, model error, budget cut) is an ERROR, not a correct silence. A
live-model outage during a gate run must read as an outage, not as "zero
false-speaks". The judge on spoke cases is advisory (printed, never bound):
binding a stochastic judge would make the flip gate flaky. It runs on the
deploy host, like every live corpus.

**The loop:** a participation failure you fixed but did not add a case for is a
failure you will ship again. Add the case (or tighten the twin).

### Context exposure experiment

The existing tag replay accepts `--context-arm full|rules|flash` (default `full`)
with `--context-report /tmp/context-arm.json`. Select one tag corpus. Runtime
never imports the filter: the eval temporarily patches the existing prefill
assembly call and restores it when the run ends. Retrieval still assembles the
whole `TagContext`. Culture, Standing, identity, notes, transcript depth and tool
admission are unchanged. Only `peer_history`, `documents`, `computer` and
`subagents` can be withheld.
When history is withheld, the experiment clears the room's `about` short-circuit
flag so the tool can retrieve it; retrieved history is not necessarily exposed.

`rules` retains full context except for explicit arithmetic and simple split-bill
questions. `flash` calls `ollamacloud/deepseek-v4.1-flash` through the host's
OmniRoute endpoint, using `OMNIROUTE_API_KEY`. Its exact system instruction and
input block table live in `nora/subject/context_filter.py`. Input uses the current
message, tagger name, peer card, Culture, Standing, last eight transcript rows
with ages, and candidate previews bounded to 600 characters. The selector emits
only optional block names and the unchanged full transcript window. Invalid
output or a transport failure retains full context, and makes the experiment
unsuccessful even if Nora's fallback reply passes.

Receipts bind exposure sizes, selected/withheld blocks, selector latency, complete
turn latency, provider/cache identity and selector usage to each replay draw.
They contain no room text. Existing prefill `blocks_rendered` still describes
assembled material; `nora.context_filter.withheld` identifies the experiment's
additional exposure restriction. Default full mode makes no selector call.

Compare the same anchored cases and deployment model across all three arms.
Use `--reasons` for tool routing. One draw is a smoke check, not an accuracy rate.
Tool-route grades do not establish reply quality; judged corpora remain subject
to the open judge-timeout defect. Static Nora instructions and tool schemas are
unchanged, but per-turn context changes can still affect provider caching.
