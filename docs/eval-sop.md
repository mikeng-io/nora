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

# identity-grounding / injection judge gate
OMNIROUTE_API_KEY=… uv run python -m evals.run --suite adversarial
```

The `journeys` suite live-runs each journey (`evals/journeys.py`) and grades its
routing (`evals/run.py::_run_journeys_live`). `journeys_gate` hard-fails on
`HARD_ROUTING_CHECKS`; the rest is printed and advisory (see limitations).

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
  centrally). **Advisory today** (see below).
- **ConvergesInBudget** — she answered within a work-tool-call budget (excludes the
  terminal `send_reply`). Catches the runaway-search flail. **Advisory.**

## Known limitations — read before you trust a number

1. **Tool backends are unwired in the offline pipeline.** The live tier drives the
   real *model* but the *tools* (HKO weather, Tavily search, place info) report
   unavailable. A down backend makes the model try fallbacks, which inflates
   `NoSiblingLeakage` and `ConvergesInBudget` — the D3 measurement saw a carpark
   query fall into `place_info` only *because* `web_search` had failed. That is why
   only `PathMatchesIntent` is a hard gate. **To promote the other two to hard:
   wire real tool backends into the live journeys run, then move them into
   `HARD_ROUTING_CHECKS`.**
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
