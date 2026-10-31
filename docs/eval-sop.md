# Eval SOP — intercept a regression before it lands live

The standard the operator asked for: how a change is proven against Nora's real
behaviour *before* it reaches the two live groups. The eval suite is the
interceptor. It is built as the same **comprehend → measure → serialize** spine
the rest of the repo runs on (`AGENTS.md`), applied one layer up: comprehend a
user *journey*, measure the *trajectory* she produces, serialize a *verdict*.

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
held at that instant, and drives one entry point. Nothing is authored and
nothing is configurable per case (`design/evaluation/11-the-sandbox-rebuild.md`
§1.2–§1.3).

```bash
# on the deploy host, in a checkout of the branch under test (NOT the deploy checkout)
make eval-source                 # refresh nora_eval (pg_dump); ONLY when no run is in flight:
                                 # it drops every sandbox (nora-eval step 4), never between arms
set -a; source ~/workspace/nora/.env; set +a         # the deployment's keys, so the tools are production's
export NORA_EVAL_PSEUDONYM_MAP=~/.config/nora/eval-pseudonyms.txt   # resolves the scrubbed anchors (#776)
uv run python -m evals --list    # corpora, entry points, classes
uv run python -m evals --all     # every corpus; prints "N total, active, parked, unanchored" per corpus
uv run python -m evals relevance_gate --sample 3
uv run python -m evals injections identity_grounding --judge
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
