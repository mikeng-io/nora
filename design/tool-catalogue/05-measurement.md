# 05 — Measurement

What would prove this works, what would falsify it, and what is not yet known.

Every number in this bundle is `cl100k_base` (tiktoken), not DeepSeek's
tokenizer. They are ratios and magnitudes, not provider-exact counts — never
restate one as a DeepSeek count. §1 says what the cache cross-check does and
does not establish.

## 1. The baseline, and how to re-derive it

| | value | source |
|---|---|---|
| input tokens, one `chat` call | 35,473 | Langfuse obs `d7cdd60d4a8a87de`, trace `836eda48d8217234a73587bac33628aa` |
| cached prefix | 30,080 | same, `gen_ai.usage.cache_read_input_tokens` |
| tool schemas, 54 tools | 22,130 | build the registry, `json.dumps` each `tool_spec`, encode |
| doctrine, tag lane | 8,288 | `DoctrineStore.get(applies_to="tag")` |
| tools + doctrine | 30,418 | — |

**What the cross-check does and does not establish.** The two parts are
measured *separately from source* — the doctrine from `DoctrineStore.get()`,
the schemas from the built registry — and neither is derived by subtracting
the other. Their sum, 30,418, lands 1.1% from an independently reported
boundary (the cached prefix, 30,080).

An earlier draft claimed this "rules out wrong splits." **It does not**, and
adversarial review was right to say so: the cache figure constrains the two
*jointly*, so an error of +2,000 in one and −2,000 in the other would pass it.
What rules that out is that each part was counted directly, not that they sum
correctly. Two further limits worth stating: both parts are counted in
`cl100k_base` while the provider counts in DeepSeek's tokenizer, so a
systematic bias would shift both together; and the 338-token gap is unexplained
— it is equally consistent with the cached prefix including a slice of the
per-turn block, which would mean the layout assumption is approximate.

The decomposition is sound because each part was measured. The cache agreement
is corroboration, not proof.

Langfuse is at `<langfuse-host>`; credentials are in the operator-managed
`nora/.env` under `LANGFUSE_OTLP_HEADERS` (URL-encoded, strip the
`Authorization=Basic ` prefix and unquote).

**Usage figures are a 40-day window** (2026-08-12 → 2026-09-21, 2,000 tool
calls, 574 turns). An earlier draft of this analysis used a 3-day window and
concluded "22 of 54 tools are never called"; over 40 days only four are
(`transit`, `parking_status`, `suggest_venue`, `note_create`), and `route`
(19), `place_info` (18), `social_fetch` (13) and `where_been` (10) are live.
**Never size anything here on a short window** — a rare tool reads as dead.

## 2. The gates, in order

Each must pass before the next is built. A gate that fails stops phase 1; it
does not get its threshold moved.

**2a — Catalogue size.** Render all 54 lines; ≤ **1,400 tokens**, no line >
20. Measured today at **991 / mean 18.4**, so this is a regression guard, not
a hurdle. Cheap, no model calls — run it first.

**2b — End-to-end mechanism spike. RESOLVED 2026-09-22.**

An earlier draft made this the bundle's highest risk: *"does the provider emit
a `tool_call` for a tool absent from the `tools` array?"* That was the wrong
question — it probed the model and assumed the framework, and the framework is
the harder half. `_executor.py:449` returns `"Unknown tool: {name}"` for a name
Strands does not hold, so the draft mechanism would have dead-ended there
regardless of what the model emitted.

**Strands 1.50.2 makes the question moot.** Verified by reading the installed
package and by running the spike:

```
before:                      ['hot_tool']
  dispatch cold_tool ->      None
after register_dynamic_tool: ['cold_tool', 'hot_tool']
  dispatch cold_tool ->      DecoratedFunctionTool
```

`register_dynamic_tool` (`registry.py:562`) puts the tool in `dynamic_tools`;
`get_all_tools_config` (`:219`) merges it into the spec list; `event_loop.py`
re-reads that list on **every** model call (`:152`, `:523`); `_executor.py:142`
checks `dynamic_tools` first at dispatch. The tool appears in the next call's
`tools` array natively. Nothing unusual is asked of the provider.

**What replaces this gate:** one end-to-end turn against the real stack —
catalogue → `load_tools` → registration → the model calls the fetched tool →
`ReplyGuard` ends the turn on `send_reply`. Cheap, and it exercises the
`ValueError`-on-duplicate path (`registry.py:570`) that `04` §4 must handle.

**2c — Selection accuracy.** The one that can sink phase 1.

Does Nora pick the right tool from a one-line catalogue as often as from full
schemas? Use PR #334's `tag` entry point — it drives the deployment's own
graph through `nora/subject/obtain(spec)`, so a measured turn carries the real
prefill, every `ContextBlock`, the real registry and `ReplyGuard`. The old
suite could not have answered this: it ran a bare model against doctrine text.

Strands ships `ToolSelectionAccuracyEvaluator` for exactly this shape.

Weight the corpus toward **confusable pairs**, because that is where selection
errors concentrate and where the one-line render is thinnest:
`where_is`/`where_been`, `find_parking`/`parking_status`,
`search_memory`/`search_memory_workspace`, `note_read`/`note_search`,
`route`/`transit`.

**n≥3 per case, and never judge on one.** The 40-case Relevance Gate suite
scored 0–2 errors run-to-run on an unchanged build; one example cannot
separate a fix from noise, and judging by one is how that regression shipped.

**2d — Does she call `load_tools` at all?** The DEC-28 trap: a model read as
refusing to reach for a tool it was never shown. DEC-28's own lesson is that
this is invisible to a log grep — no tool logged its own name, so zero was
consistent with "every turn" and with "never". Measure it on the eval suite
with the counter from `04` §7, not from production logs.

**2f — Per-turn accounting. The building block exists; the wiring does not.**

**Corrected 2026-09-22, same day it was written.** An earlier version of this
section said "the instrument exists; do not build one." That was wrong, and
the way it went wrong is worth keeping: a peer named `traced_nora_task`, I
verified that it exists and that a test pins it, and I did not check whether
anything CALLS it. Verifying the artefact named is not verifying the claim
made.

`evals/scenarios.py:231` is the whole correction:

```python
experiment = Experiment(cases=cases, evaluators=[MeetsGrader()])
return experiment, nora_task(spec)          # UNTRACED
```

Every corpus runs the untraced task under a single `MeetsGrader`.
`traced_nora_task` has exactly one caller in the tree — its own unit test. **No
corpus produces a trajectory today**, so nothing can count `InferenceSpan`s
yet.

What that changes: gate 2f is **not closed**, it is **owned**, by the eval
suite's session. It is real work — a traced task path, the SDK evaluator, and
an expected-tool label (which fits in the free-form `metadata`, so no corpus
format change). Not a dict entry.

What is still true, and is why this is wiring rather than construction:

`evals/backbone.py:649` `traced_nora_task(spec)` is
`eval_task(TracedHandler())(nora_task(spec))`. `TracedHandler.after()` does
`processed.setdefault("trajectory", session)` and `nora_task`'s dict never
sets that key, so it **adds a `Session` onto the same
`output`/`environment_state` shape every other grader already reads** — no
custom evaluator, no second path. Pinned by
`tests/unit/test_eval_backbone.py:309`.

From that trajectory, per TURN and by construction:

| want | read |
|---|---|
| model calls per turn | count the `InferenceSpan`s |
| end-to-end turn latency | the single `AgentInvocationSpan`'s duration |
| which tools were selected | `ToolExecutionSpan`s — and `ToolSelectionAccuracyEvaluator` / `ToolParameterAccuracyEvaluator` / `ToolCalled` ship in the SDK and read `actual_trajectory` directly |

Constraint carried by `traced_nora_task`'s own docstring: the caller must hold
`handler` for the run's lifetime (one shared exporter) and pass
`max_workers=1` — serial only.

**The 2c baseline must be taken on BOTH sides of the switch.** Changing which
schemas are resident changes the prompt, so any number measured before PR C is
incomparable to one measured after — and the prefix cache invalidates with it,
which surfaces as cost rather than as an error. The same anchored cases run
either side, before and after, or the comparison is not one.

**A caution on the 3-vs-4 call question.** A driven case recorded in
`design/evaluation/10-the-scenario-map.md` §13 shows **four** `InferenceSpan`s
in one turn. That is a `participation` case, not a tag turn, and the tag trace
this bundle measured (`836eda48…`) ran **three** `chat` generations. So the
baseline is lane-dependent and n=1 on the participation side. **Measure the
baseline on the same lane and the same entry point as the change**, before
attributing a 4th call to `load_tools`.

**Reading `10-the-scenario-map.md`: §13 supersedes the §1 table.** The table
(line 32) records S2 coverage as "0 — `Outcome` carries no tool calls"; §13
records S2 as built 2026-09-19. The table is stale and was repeated to the
operator and onto issue #297 before being caught. `Outcome` itself
(`evals/rebuilt/domain.py:129-144`) genuinely carries no tool calls — the
trajectory is a separate channel, which is how the row reads false while
being literally true about `Outcome`.

**Ownership:** the suite belongs to another session. Nothing under `evals/`
is written here without their decision — see `§5`.

**2e — Cache.** Before/after on `cache_read_input_tokens` across ≥20 real
turns, split by whether the turn called `load_tools`. Expectation: the stable
resident prefix caches at least as well; post-`load_tools` calls may miss. An
expectation is not a result.

## 3. What success looks like

**Per-turn, not per-call.** An earlier draft's table was entirely per-call,
which is gameable in the exact way this repo keeps relearning: a regression
from 3 model calls per turn to 4 passes every per-call row while total cost
rises. Per-call rows are kept as diagnostics; **no gate passes on them.**

| | unit | today | target |
|---|---|---|---|
| **input tokens per TURN** | turn | ~106,700 (3 × ~35,500) | **≤ 75,000** |
| **model calls per turn** | turn | ~3 | **≤ 3.4 mean** |
| **end-to-end turn latency** | turn | baseline | **no worse** |
| **tool-selection accuracy** | turn | baseline from 2c | **no worse** |
| **turns ending without a reply** | turn | baseline | **no worse** |
| resident tool-spec tokens | call | 22,130 | ≤ 3,700 (diagnostic) |
| mean input per call | call | ~35,500 | ≤ 18,000 (diagnostic) |
| cold-call TTFT | call | 4,575 ms | lower (diagnostic) |

The bottom three are the ones that will look best and mean least. Cold-call
TTFT improves trivially because the prompt is smaller; what a person in the
room feels is **end-to-end turn latency**, which gets an extra round trip on
72.8% of turns and may get worse. If turn latency regresses, the change is a
regression however good the token numbers look.

The per-turn token target is deliberately ~30% rather than the ~50% the
per-call arithmetic suggests — that gap IS the extra round trip, priced in
rather than assumed away.

## 3a. The result — measured 2026-09-23/24, and the switch stays OFF

`evals/corpora/tool_selection.yaml`, 16 routing cases on one anchored Signal
room, `python -m evals tool_selection --sample N --reasons`, graded on what
EXECUTED (`ToolRouteGrader`). Per-turn numbers are the subject's own calls
only (`_model_calls(agent=…)`, after fixing a double count). Five arms, the
same build except the named variable:

| arm | schemas | catalogue line | direct-call hook | n | routing | calls/turn | uncached in/turn | cached in/turn |
|---|---|---|---|---|---|---|---|---|
| A2 | all resident | first sentence | — | 3 | 48/48 | 3.54 | 6.7k | 114.7k |
| B2 | 6 hot + catalogue | first sentence | no | 3 | 45/48 | 5.15 | 14.0k | 78.3k |
| C | 6 hot + catalogue | first sentence | yes | 3 | 46/48 | 4.73 | 13.3k | 72.1k |
| A′ | all resident | L1 (what + when) | — | 5 | **80/80** | 3.56 | 10.3k | 113.6k |
| D | 6 hot + catalogue | L1 (what + when) | yes | 5 | 79/80 | 4.92 | 19.5k | 73.0k |

**What it says.**

- Deferring saves ~36% of input tokens and moves them from CACHED to
  UNCACHED: the tools array changes after a load, so the prefix misses. The
  resident prompt already caches ~92–94%. D is cheaper than A′ only if a
  cached token costs more than ~23% of an uncached one
  (10.3 + 113.6r = 19.5 + 73.0r); at the usual cache discounts it costs more,
  and it adds ~1.4 model calls a turn. **§3's per-turn gate fails; the switch
  (`[llm] defer_tools`) stays `false`.**
- The per-call framing in §0/§3 was the error this whole section warned
  about, one level down: tokens were measured, PRICE was assumed.
- The first-sentence catalogue line was the selection defect: 2 of 55 lines
  carried a when-to-call clause, and B2/C lost `calculate`. Redefining the
  line as ADR 0007's L1 (gated by `test_the_opening_paragraph_is_an_l1`) fixed
  it (D 5/5) and was neutral with every schema resident (A′ 80/80) — ADR 0007
  §3's re-measure for 40 opening rewrites.
- With the hook she still often calls a shelved tool twice: the first call's
  arguments are guessed from the catalogue line and fail validation.
- D's one miss: `online_now` lost to the always-resident `pulse_*` MCP tools.

**What ships regardless of the switch:** one builder (`ToolRegistry.surface`,
`ToolScope`, `ToolSurface`) for every loop; the L1 opening convention and its
gate; the catalogue derived from both; `tool_selection` and the eval-spend
fixes. `load_tools` and `ShelfLoader` stay built, tested and switched off.

**When to re-measure:** the tool set grows with the per-network social split
(`instagram_read`/`instagram_search`, then X and Threads), which raises the
resident cost; or the provider's cache discount changes. Either can move
the break-even — the same corpus, both arms, one command.

## 4. After it ships

- **Re-derive the hot set quarterly**, or when a tool's share moves more than
  a point. It is a measured value; `web_search` at 24.9% is a fact about
  2026-08/09, not a constant.
- **Watch `load_tools` for tools already in the hot set** — wasted round trips
  mean the `*` marker is not being read.
- Prometheus is on norty at `127.0.0.1:9190`, ~4d retention, and Nora restarts
  2–3×/day with lazy labelling. **An absent series means "nothing since the
  last restart", never "never happened"** — do not close anything on an empty
  low-frequency counter.

## 5. Open questions — recorded, not planned

- **Does the doctrine belong in the same split?** The same lever points at the
  8,288-token doctrine block, and the Instagram workstream is already moving
  one rule out of doctrine onto a tool result — a story must be reported AS a
  story, with when it was posted, rather than restated as fact. That bundle is
  on its own branch and not on master at the time of writing. But doctrine is
  authored, operator-owned and governed by `change_policy`; `identity.md`,
  `voice.md` and `constraints.md` are `operator-only`. Separate decision,
  separate owner.
- **Doctrine compression** (identity 973, conduct 3,893) is a separate
  workstream. Two findings from this session belong to it rather than here:
  conduct.md states "empty vs unavailable" in four places (lines 42, 48, 87,
  97) and "read memory before saying you don't know" in three (46, 52, 56);
  and 68% of 40 sampled replies carry an em dash while the doctrine carries 91
  of them and `voice.md` — the file that says how to write — carries one.
- **Is one round trip the right shape?** `load_tools` takes a list, so a turn
  needing three cold tools can fetch them together — but only if the model
  anticipates all three. Whether it does is unmeasured.
- **Lane scoping** (voice lane carrying `generate_image`/`make_artifact`;
  every Signal room carrying the full non-music catalogue) is phase 2 and has
  no bundle of its own yet. Under this split its value shrinks, because an
  unloaded tool costs a catalogue line rather than a schema.

## 6. What would falsify the whole bundle

- 2c shows selection accuracy dropping outside noise on confusable pairs.
- Turn latency regresses, or mean calls per turn exceeds 3.4 — the extra round
  trip is not absorbed after all.
- Resident tool-spec tokens creep upward across a process's life, meaning
  `dynamic_tools` is outliving its turn and the registry is leaking between
  rooms.
- Measured cold tools per turn rises toward the 46-tool break-even. Nothing in
  574 turns is near it (max 7), but it is the assumption the arithmetic rests
  on, so it is worth re-checking rather than assuming.

Do not write a falsification that was not run.
