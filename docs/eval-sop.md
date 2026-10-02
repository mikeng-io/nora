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
uv run python -m evals --list    # corpora, entry points, classes
uv run python -m evals --all     # every corpus; prints "N total, active, parked, unanchored" per corpus
uv run python -m evals relevance_gate --sample 3
uv run python -m evals injections identity_grounding --judge
```

- `--source-url` defaults to the clone on the deploy host (from its `.env`);
  a database whose name is not `*_eval` is refused.
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
  Report the draw count with the verdict; n=3 is not a rate. Draws share one
  Honcho workspace today (#462), so they are not independent.
- Every run prints its own spend; put it in the PR with the command.

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
| `make eval-note-candidates SAMPLES=N` | `note_candidates` at N draws (2026-10-03, DEC-277: the pass it measured is deleted) |
| `make eval-adversarial-corpora` | the injection, grounding, scope and contradiction corpora, judged |

Routing ("which tool did she reach for") is the `tool_selection` corpus,
graded on the tools that actually executed; `--reasons` prints them per draw
(since `21e5405d`, 2026-09-23). A wrong route that answers fluently is
invisible to a reply grader, so a routing change is measured there, not by
whether she replied.

## Known limitations: read before you trust a number

1. **Draws are correlated** (#462): every draw reads and writes one Honcho
   workspace, so a "you asked N times" FAIL can be an artifact of the run.
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
