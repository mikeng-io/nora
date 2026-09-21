# Eval SOP — intercept a regression before it lands live

The standard the operator asked for: how a change is proven against Nora's real
behaviour *before* it reaches the two live groups. The eval suite is the
interceptor. It is built as the same **comprehend → measure → serialize** spine
the rest of the repo runs on (`AGENTS.md`), applied one layer up: comprehend a
user *journey*, measure the *trajectory* she produces, serialize a *verdict*.

## The rebuilt suite (2026-09-21) — where and how it runs

> Everything below this section describes the PREVIOUS harness (`evals.run`,
> fake stores, hand-authored journeys). It is kept for the reasoning; the
> commands no longer exist. The current suite is `python -m evals`.

The suite runs **on the deploy host**, against the deployment's **clone**.
Every corpus case names a real room at a real instant; the runner composes
the deployment's own Nora (its `config.toml`, `policy.toml`, `identity.toml`)
against a sandbox database of the case's own, seeds it with what that room
held at that instant, and drives one entry point. Nothing is authored and
nothing is configurable per case (`design/evaluation/11-the-sandbox-rebuild.md`
§1.2–§1.3).

```bash
# on the deploy host, in a checkout of the branch under test (NOT the deploy checkout)
make eval-source                 # refresh nora_eval from production (pg_dump); do this first
set -a; source /home/norty/workspace/nora/.env; set +a   # the deployment's keys, so the tools are production's
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

## The two tiers

A change is gated in two tiers, cheapest first. Tier 1 is deterministic and runs
in CI on every commit; tier 2 needs the network and the real model and is run
deliberately before a deploy.

### Tier 1 — deterministic, offline, always-on (CI)

No model, no network. Grades **mechanics** and pins **routing ground truth**
against scripted trajectories.

```bash
uv run pytest -q tests/test_evals_gate.py tests/test_evals_intent.py \
                 tests/test_evals_journeys.py tests/test_evals_rubric.py
```

- **Mechanics** (`evals/evaluators.py`): she replies when triggered, stays within
  tool budgets, never over-reads a sufficient pre-fill, feeds Honcho once per
  reply, never speaks unprompted.
- **Routing ground truth** (the intent graph, D2): for each seed journey the
  *intended* tool path passes all three routing checks and the *regression* path
  (the trap tool a near-identical question would take) fails. This is what keeps
  the graph honest without spending a token.

Tier 1 is green or the change does not merge.

### Tier 2 — live, before deploy (network-marked)

Drives Nora's **real model** through the real pipeline (fake db/memory/adapter)
and grades what she *actually did*. Excluded from CI by `addopts = "-m 'not
network'"`; run it against a reachable gateway with the LLM key set.

```bash
# routing gate — the six seed journeys and their six paraphrases, live
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite journeys

# production-shape effort comparison — same journeys, per-run effort override
# (config.toml untouched), DEC-50 skills layer on, and each journey starts from
# a READ-ONLY snapshot of the real Postgres transcript + Honcho peer card
# (needs the deployment env: DATABASE_URL + Honcho reachable; never writes)
OMNIROUTE_API_KEY=… DATABASE_URL=… uv run python -m evals.run --suite journeys \
    --effort low --skills --seed-production

# identity-grounding / injection judge gate
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite adversarial

# the three corpora that MEASURE a doctrine gap instead of gating one (#227).
# Same live path, same fail-closed grading, their own suite names — and they are
# never part of `--suite adversarial`, so a red reading here cannot block a
# deploy. `--repeat N` runs the whole corpus N independent times (verdict cache
# off), binds the strict majority per case, and prints per-case `k/N` plus the
# per-run spread. Read the spread: one green window is not a rate, which is what
# the 40-case relevance-gate regression shipped on.
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite being_contradicted --repeat 5
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite source_scope --repeat 5
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite identity_grounding --repeat 5

# the note-candidates scenario suite (design 37 §7, issue #118): ten batches
# through the REAL DreamerRuntime + NoteCandidateAdaptor over an in-memory
# notebook, N samples each, graded on the notebook (a note created, a head
# revised, or nothing) — never on the envelope text and never by a judge.
# `--dreamer-dir` measures a prompt variant held as files; the run reports
# the instruction hash it measured, so a verdict is bound to a prompt.
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite note_candidates --samples 5
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite note_candidates \
    --dreamer-dir /path/to/variant   # holds doctrine/ and objectives/

# the Relevance Gate's four measurements (design 36): determinism first
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite relevance_gate

# the SAME 40 cases through the Agent SDK's structured output instead of a raw
# json_object completion. `--via-sdk` swaps the TRANSPORT and nothing else —
# same instruction, same schema, same parser — so the two runs are comparable
# and the difference is attributable. This is the reading that would reopen
# ADR 0011: production is the raw arm because the SDK arm held 1 of 6 safety
# cases against its 6 of 6 (2026-09-11, n=5, issue #186), and if a re-run ever
# shows the SDK arm holding 6/6 the decision is back on the table.
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite relevance_gate --via-sdk

# the proactive adaptor's two suites (design 39 §5, DEC-162): DECIDE grades
# which rows the real DreamerRuntime + real tool source wrote and in which
# due windows (a quiet case FAILS on a refused `remind` attempt, and a remind
# without a memory read FAILS); ACT grades whether the fire turn spoke, with
# `must_cite`, or stayed silent. Both majority-quiet; the false-positive rate
# is the primary number. `--model` names the deployed alias from a dev box.
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite proactive_decide --samples 3
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite proactive_act
```

### Running a live suite from a dev machine

The gateway listens on the deployment's localhost only, so a run from the
MacBook needs a tunnel and the deployed model's ALIAS:

```bash
ssh -f -N -L 20128:127.0.0.1:20128 norty
export OMNIROUTE_API_KEY="$(ssh norty 'grep ^OMNIROUTE_API_KEY= ~/workspace/nora/.env | cut -d= -f2- | tr -d "\""')"
uv run python -m evals.run --suite note_candidates --samples 10 \
    --model nora-dreamer --effort none
pkill -f 'ssh -f -N -L 20128'
```

`--model` is not optional in this shape. `config.example.toml` names the
provider id and the deployment routes through an alias (`nora`,
`nora-dreamer`), so a run without it measures a different model than
production and its verdict is about nothing anyone deploys.

The adversarial suites take the same treatment and need one more flag, because
they drive TWO models: `--model nora` is the reply tier and `--judge-model
nora-judge` the grader (the self-grade guard compares what will actually be
called, so it still refuses a run where the two are the same id). Add
`--judge-samples 3` to match the deployment's `[evals] judge_samples` — the
committed template says 1, and a single judge sample is the noise A2 exists to
kill. The whole invocation, from the Mac, with the tunnel up:

```bash
uv run python -m evals.run --suite being_contradicted --repeat 5 --no-cache \
    --model nora --judge-model nora-judge --judge-samples 3
```

Every run prints its own provenance first — corpora, both model ids, judge
samples, the doctrine sha256 and the base url — so a rate quoted from a log can
be re-derived instead of trusted. The key is read
from the deployment's `.env` through ssh rather than stored anywhere local —
same rule as every other secret (`AGENTS.md`, "Reaching the deployment").

Output is block-buffered under a redirect: a backgrounded run prints nothing
until it exits, so wait on a sentinel (`until grep -q GATE "$log"`) rather
than tailing.

The `journeys` suite live-runs each journey (`evals/journeys.py`) and grades its
routing (`evals/run.py::_run_journeys_live`). `journeys_gate` hard-fails on
`HARD_ROUTING_CHECKS`; the rest is printed and advisory (see limitations).

Production-shape knobs (all optional, all journeys-only):

- `--effort {none,low,medium,high}` — per-run `reasoning_effort` override. The
  mechanism that makes an ADR re-check possible without rewriting `config.toml`
  between runs; the shipped config value stays untouched for a plain gate.
- `--skills` — turns the DEC-50 on-demand skill layer ON, exactly as production
  composes it (`cfg.doctrine.dir.parent / "skills"`), so the `<available_skills>`
  block + `skills` activation tool are present in the prompt.
- `--seed-production` — snapshots the real Postgres transcript (newest 12 rows)
  + the eval person's Honcho peer card, read-only, and injects them into the
  harness's FAKE stores; the model's prompt therefore starts from what
  production would assemble while no eval write ever reaches the live stores.
  Raises loudly if `DATABASE_URL` is unset, rather than silently measuring an
  empty snapshot.

## The routing checks (the intent graph)

The "same same but different" problem: two short, near-identical questions must
route to *different* tools ("外面凍唔凍?" → `weather`, "我而家喺邊?" → `where_is`),
and a model that confuses them fails *silently* — the reply reads fine, the
source is wrong. A frozen-reply judge cannot see that; the tool path can.

- **PathMatchesIntent** — she reached the tool the journey requires. **Hard gate.**
  Backend-independent: "did she reach for the right tool at all" is true or false
  whether or not that tool's backend was up.
- **NoSiblingLeakage** — she did *not* reach a plausible-but-wrong neighbour, where
  "neighbour" is an edge in the `IntentGraph` (tool *confusability*, declared once,
  centrally). **Hard for a fully-backed journey** (F4, see below).
- **ConvergesInBudget** — she answered within a work-tool-call budget (excludes the
  terminal `send_reply`). Catches the runaway-search flail. **Hard for a
  fully-backed journey.**

Each journey runs `[evals] journey_samples` (default 3) times and the gate binds
the strict-MAJORITY per-journey verdict (F8a); ties fail closed. And `JOURNEYS`
now pairs each tuning incident with a **held-out paraphrase** (same intent, none
of the doctrine's trigger words), so passing the seed but failing its paraphrase
exposes memorization rather than routing (F2).

## Known limitations — read before you trust a number

1. **Backend confound — RESOLVED for backed tools (F4).** The live tier drives the
   real *model*; the eval used to leave every *tool* backend unwired, so a tool
   returning `unavailable` drove fallback calls that made `[web_search →
   unavailable → place_info]` grade identically to `[place_info]` — a leak
   indistinguishable from a fallback. `nora.app.eval_support.build_eval_tool_registry`
   now wires **canned** providers for `weather`, `web_search`, `place_info`,
   `route`, `transit`, `where_is` and `saved_places` (`BACKED_TOOLS`), so each
   answers `ok` and a leak is a real choice. `NoSiblingLeakage`/`ConvergesInBudget`
   are therefore **hard for any journey whose tools (required + siblings) are all
   backed** (`hard_checks_for`), which today is all of them; a journey touching an
   unbacked tool (a future MCP tool) keeps those two advisory automatically. The
   presence tools clear the R-31 subset gate because the eval `Circle` roster and
   the harness room are the same single person. **CAVEAT — backed ≠ answers the
   query (DEC-78):** a canned tool returns `ok` but the same fixed data for every
   query, so for an ask whose specific fact it cannot supply (real-time carpark
   vacancy), a retry-happy model re-calls to its budget. That inflates
   `ConvergesInBudget`/`NoSiblingLeakage` for that journey even though its
   `PathMatchesIntent` (which tool she reached for) is correct. Read the routing
   check, not the budget/leak check, when the canned data cannot answer the ask.
2. **Judge detection on diverse fabrications is ~3/5, not 3/3** (DEC-75, C1). The
   adversarial gate's *mechanism* and controls are clean; the judge's *detection*
   of invented-data fabrication on a diverse corpus is the open frontier. Do not
   read a green adversarial run as "she never fabricates" — read it as "she did
   not fabricate in a way this judge caught."
3. **Measurement is corpus-dependent.** A narrow corpus overstates detection (the
   all-rank-2 identity corpus read as 3/3 until C1 diversified it). Widen the
   corpus before trusting a rate.

## The loop — when the live groups surface a new failure

This is the SOP, not a one-time setup:

1. **Comprehend** — a real turn went wrong (weather web-searched, "我邊度返工"
   answered with live GPS). Name the journey and its *correct* tool path.
2. **Measure** — **THIS STEP HAS NO INSTRUMENT as of 2026-09-18.** The routing
   tier went with the old eval layer and the rebuilt one has no counterpart:
   its `Outcome` carries no tool calls and no corpus is a journey, so "which
   tool did she reach for" cannot currently be graded. DEC-76 and DEC-84 are
   marked NOT ENFORCED in `design/09-decision-index.md` for exactly this.

   Until it is rebuilt, a routing failure is caught by a reply-grader that
   `design/09-decision-index.md` DEC-75/DEC-76 both record as BLIND to it — a
   wrong route answers fluently from the wrong source. Adding a corpus case
   still measures whether she replied; it does not measure where she looked.
3. **Serialize** — tier 1 pins intended-vs-regression (offline, no model); tier 2
   proves the *current* model against it. The verdict is the gate's exit code and,
   when exported, a Langfuse score.

A routing bug you fixed but did not add a journey for is a bug you will ship
again. Add the journey.


## The participation suite — the flip gate (design/dreamer-agent-loop/35 §9)

`make eval-participation` runs the 11-case participation suite: 3 relevance-Yes,
their 3 knowledge-cut twins, and 5 pure-No (S6-S10) — the S1 and S4 pairs were
deleted with the knowledge tier, since their `-known` half had nothing left to
ground on. Each
case is `transcript + standing seed + incoming -> expected verdict (speak /
silence)`, driven through the REAL participation path (real
`ParticipationTrigger`, real `FirePath.speak`, real policy opt-in, real model).

**This is the flip gate.** Zero false-speaks on the knowledge-cut twins and
zero speaks on S6-S10 is the hard bar the design makes load-bearing — a model
that answers a twin from parametric memory has failed, however correct the
answer is in the world.

**Reading the output:** the verdict is graded on `Trajectory.sent` (the
room-visible truth — a called-but-vetoed `send_reply` is not a message the
room saw). The summary prints a `stop_reason` distribution: a turn that never
successfully invoked the model (timeout, llm_error, budget cut) is an ERROR,
not a correct silence — if the distribution shows `invocation_timeout` or
`llm_error` on the silence-expected cases, the run is NOT green even if the
verdicts print PASS. A live-model outage during a gate run must read as an
outage, not as "zero false-speaks".

**Where it runs:** the live gate needs the configured model endpoint reachable
(`[llm] base_url`). The deployment's `host.docker.internal:20128` gateway is
only reachable from the deploy host; on a dev box, point `[llm] base_url` at
the local OmniRoute (`127.0.0.1:20128`) before running. The judge on spoke
cases is advisory (printed, never bound) — binding a stochastic judge would
make the flip gate flaky.

**The loop:** a participation failure you fixed but did not add a case for is a
failure you will ship again. Add the case (or tighten the twin).
