# Eval SOP — intercept a regression before it lands live

The standard the operator asked for: how a change is proven against Nora's real
behaviour *before* it reaches the two live groups. The eval suite is the
interceptor. It is built as the same **comprehend → measure → serialize** spine
the rest of the repo runs on (`CLAUDE.md`), applied one layer up: comprehend a
user *journey*, measure the *trajectory* she produces, serialize a *verdict*.

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
# routing gate — the four real-incident journeys through the live model
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite journeys

# production-shape effort comparison — same journeys, per-run effort override
# (config.toml untouched), DEC-50 skills layer on, and each journey starts from
# a READ-ONLY snapshot of the real Postgres transcript + Honcho peer card
# (needs the deployment env: DATABASE_URL + Honcho reachable; never writes)
OMNIROUTE_API_KEY=… DATABASE_URL=… uv run python -m evals.run --suite journeys \
    --effort low --skills --seed-production

# identity-grounding / injection judge gate
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite adversarial
```

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
2. **Measure** — add a `Journey` to `evals/journeys.py` with the real message, the
   required tool(s), and the trap. If the trap is a tool not yet in
   `SEED_INTENT_GRAPH`, add the confusability edge — every touching journey
   tightens at once.
3. **Serialize** — tier 1 pins intended-vs-regression (offline, no model); tier 2
   proves the *current* model against it. The verdict is the gate's exit code and,
   when exported, a Langfuse score.

A routing bug you fixed but did not add a journey for is a bug you will ship
again. Add the journey.
