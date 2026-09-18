# Evaluation — one Nora, many inputs

## 0. The invariant

> **Different evaluation suites. Different input. One Nora.**

## 1. The finding

**Every component the eval layer builds is built smaller than production builds
it.** Measured by AST over the call sites, 2026-09-17:

| component | production | eval | missing |
|---|---|---|---|
| `FirePath` | `composition.py:2458` **36** | `eval_support.py:1703` **21** | **15** |
| `DreamerRuntime` | `composition.py:3322` **12** | `eval_support.py:1109` **9** | 3 |
| `DreamerRuntime` | same | `eval_support.py:1359` **11** | 1 |
| `RelevanceGate` | `composition.py:2692` **9** | `eval_support.py:980` **8** | 2 (+1 eval-only) |
| `RegistryToolSource` | `composition.py:3252` **11** | `eval_support.py:1303` **9** | 2 |
| `LiveAgentRunner` | `composition.py:1414` **5** | `eval_support.py:281` **4** | 1 |

Plus `evals/harness.py:612` `build_offline_pipeline(14)` and `evals/run.py:665`
`Agent(5)`, which assemble subjects of their own.

**Seven places build a Nora. None of them is `build_dependencies`, which is what
the deployment runs** (`Dockerfile` → `nora/__main__.py:84`).

## 2. What is missing, and why it is not cosmetic

### `FirePath` — 15 collaborators, driving `proactive_act`

```
decisions                        media_join
self_card_conclusions            people_in_window_conclusions
people_in_window_speakers        tool_call_log_store
llm_state                        preview_source
participation_runner             memory_read_timeout_seconds
standing_store_timeout_seconds   shadow_channel / shadow_platform
gate_shadow_channel / gate_shadow_platform
```

`decisions`, `self_card_conclusions`, `people_in_window_*` and `media_join` are
**prompt inputs**. A suite graded on *"what the room heard"* is grading a turn
assembled from a materially smaller prompt than the deployment sends.

### `RelevanceGate` — `persons` and `media_join`, and production says what breaks

`composition.py` documents both, on the lines that pass them: without `persons`,
`person_name` is `None` on every row; without `media_join`, an image-only message
reads as an **empty body** and `render_context` drops the row — *a systematic
under-admission*.

The gate suite omits both **and adds `capabilities`**, which production
deliberately does not pass (reversed 2026-09-10 on measured numbers). So the eval
gate reads a different prompt than the deployed gate in three ways at once.

### `DreamerRuntime` — `store_timeout_seconds`, absent from BOTH eval sites

Production passes `cfg.dreamer.store_timeout_seconds`; neither eval site does, so
both silently run on the module default.

### Nothing guards any of this

`tests/architecture/test_assembler_parity.py` compares `composition.py` against
`offline.py` — both pass `FirePath(36)`, so it is green and always will be. **It
does not look at `eval_support.py`**, which is where all six divergences live.

## 3. Why DELETE and not refactor

A refactor needs a target shape the current code is an imperfect version of.
There are seven shapes and the target is in production.

> **CORRECTED 2026-09-18 — there IS a target shape for three of the four
> primitives, and it was pinned in `pyproject.toml` the whole time.**
> `strands-agents-evals` ships `Case`, the runner and the outcome types. The
> delete-not-refactor argument holds for the SUBJECT, which is the shape that
> really is only in production. For the HARNESS the target always existed and
> no node in this bundle mentioned it. `07-sdk-native` §2. Each was a reasonable
local change — somebody needed to measure a thing, the existing shape did not
fit, so they added a path beside it. Editing again is another local change
fitting what is already there.

> 因為你改，你最終只會越改越錯。

## 4. What this bundle does NOT rest on

An earlier draft supported the argument with counts from
`design/09-decision-index.md` — how many rules have an eval, how many have only a
unit test. **Those counts are withdrawn.** They were measured three times and
produced three answers; two independent reviews produced two more, disagreeing
with each other. The index is harder to count than it looks, and the counts were
decoration on a finding that does not need them.

**§1's table is the whole argument.** It is AST-measured, reproducible in one
command, and every row is a `file:line` anyone can open.

The underlying concern remains real and belongs on **#311**, not here: most
behavioural rules are guarded by tests that establish the code is wired, which is
a different claim from "she behaves". That is a separate finding and it will be
stated with numbers somebody can reproduce, or not stated.

## 5. Out of scope

**The test suite.** Unit, integration and e2e are solved outside this repo:

> 你 search 返到底人哋 unit test 點做、integration test 點做、e2e test 點做，就已經
> 完咗件事。

Its blast radius is *"do we catch a mistake before it ships"*, not runtime
behaviour. **What has no industry answer is whether an LLM agent behaves as
designed**, and that is the only thing here.

## 6. What survives the delete

- **13 corpora** (`evals/**/*.yaml`) — cases and expectations are data.
- **The graders** — **CORRECTED 2026-09-18:** only the confidence-weighted
  rubric and the absence grader survive. Content grading is the SDK's
  built-ins, and the absence grader is the one no SDK supplies
  (`08-the-sdk-surface` §4.1, §5).
- **Measured readings in corpus headers**, which are provenance.
- **`subject=` in the provenance line** (#310) — the qualifier travels with the
  number into logs, where prose does not. **Caveat:** it exists at exactly one
  print site today and covers none of the other drivers.

## 7. What this does NOT claim

- **That each divergence is a defect.** Several are deliberate and documented —
  `participation_gate=None` is right for its stated reason (*"a suite that had to
  get past a live gate call first would be measuring the gate's verdict and the
  turn's behaviour in one number"*). The defect is that seven assemblies exist,
  nothing relates them, and **nothing detects when one drifts.**
- **That the rebuild is cheap.** ~7,700 lines. `02-the-rebuild` sizes it.
- **A convention.** Written last, describing what exists. Attempted first on
  2026-09-17 (PR #307) and withdrawn the same day.

## 8. What would refute this bundle

**Diff each eval-built component against the production construction of the same
component** — gate against gate, `FirePath` against `FirePath`, Dreamer against
Dreamer — and show the divergences do not reach the model.

§1 is the kwarg diff. What it does not establish is **impact**: whether a missing
`media_join` or `decisions` changes the bytes that reach the model for the cases
these suites actually run. If it does not, the seven are one Nora wearing seven
coats and this bundle is withdrawn.

*(An earlier draft proposed diffing the six subjects' prompts against **each
other**. That check cannot fire: the gate reads `applies_to="relevance_gate"`,
the adversarial path reads `"tag"`, the Dreamer reads `cfg.dreamer.dir`, and
`RegistryToolSource` sends nothing to a model — `composition.py:2708` states the
gate/Nora split as a decision this bundle endorses. Same-component-against-
production is the comparison that can come back empty.)*
