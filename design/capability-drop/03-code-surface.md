---
type: design
title: "Capability Drop — code surface"
description: "Every file and test that touches capability, with the exact deletion/change list. Verified against the worktree at draft time."
tags: [nora, tools, schema, doctrine, registry, tests]
timestamp: '2026-08-30'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-rules-to-conduct, rel: depends_on, provenance: asserted}
---

# Code surface

All paths relative to the repo root, verified by grep against the worktree at
draft time. Three layers change: the registry (`nora/agent/registry.py`), the
doctrine tier machinery (`nora/doctrine/`), and the tests that pin both.

## 1. Registry — `nora/agent/registry.py`

| symbol | change |
|---|---|
| `ToolRegistry.capability()` (lines ~461-567) | **delete** |
| `ToolRegistry.live_only_tools()` (~413-428) | **delete** — its only runtime consumer was `capability()`; its direct test pins (`tests/unit/test_tool_registry.py::test_the_live_only_tools_are_the_ones_whose_answer_decays`, `::test_skills_is_not_live_only`, `::test_with_mcp_live_only_matches_plain`) die with it |
| `ToolRegistry.web_tier_tools()` (~430-445) | **delete** — its only runtime consumer was `capability()`; its direct test pin (`::test_web_search_and_web_fetch_are_the_web_tier_tools`) dies with it |
| `ToolRegistry.tool_names()` (~447-459) | **keep** — the doctrine loader's catalogue gate still needs the names (the gate prevents authored doctrine naming a tool) |
| `read_only_tools()` etc. | keep — read by Dreamer adaptor grants, unrelated |

**`time_shiftable` is KEPT**, even though its only renderer dies. It is a
load-bearing declaration on the tool itself (design/14 §5.1) with direct values
pinned by `tests/unit/test_tool_registry.py::test_every_tool_declares_a_time_axis`
and referenced in `tests/test_routes.py` (`arrive_by` tests) — deleting the
*renderer* (`live_only_tools`) does not delete the *declaration*. The two are
separate decisions and this bundle only makes the first.

The `capability()` docstring's own argument ("Not a schema dump") dies with it.
Its class-docstring caveat about `eval_support` ("builds three throwaway
registries purely to read budgets(), tool_names() and capability()") shortens
to two.

## 2. Tool metadata — `nora/agent/tools.py` + `nora/agent/ports.py`

| symbol | change |
|---|---|
| `NativeTool.when` field (`tools.py` ~line 277 table) | **delete field + all 48 clauses** — the value carries the docstring-redundant routing text; what genuinely survives is merged into docstrings (see `01`) |
| `ToolSpec.when` (`ports.py`) | **delete field** — its only consumer is `capability()`; the "constructor raises" gate dies with the render target |
| `ToolSpec.budget` | keep — `ReplyGuard`/`budgets()` still read it (DEC-10) |
| `NativeTool.time_shiftable`/`terminal`/`source_tier`/`cacheable`/`read_only`/`plugin` | keep — `time_shiftable` remains a load-bearing per-tool declaration even with its renderer deleted; `terminal` feeds `ReplyGuard` (DEC-32); the rest feed their own gates |

**Docstring merges (from `01-when-stays-in-schema.md`):**
- `recall_lookups`: append the doubted/guessed routing trigger to the docstring.
- `create_event`: verify the "genuinely NEW gathering" emphasis reads as
  strongly as the when-clause made it.

## 3. Doctrine tier machinery — `nora/doctrine/loader.py` + authored files

| file | change |
|---|---|
| `loader.py` `DERIVED_KINDS = ("capability", "situation")` | → `("situation",)` |
| `loader.py` `AUTHORITY_TERMINALS` (~line 67) | remove `"capability"` → `("situation", "message-content", "tool-output")` |
| `loader.py` module docstring (lines 6-28) | update the "capability sentences have no owner" argument — now only situation is derived |
| `loader.py` `check_kind_boundaries` (catalogue gate) | keep — still receives `tool_names` from composition |
| `constraints.md` front-matter `authority_below` | drop `capability` from the list |
| `identity.md` front-matter `authority_below` | drop `capability` |
| `conduct.md` front-matter `authority_below` | drop `capability`; add the two behavioural rules (see `02-rules-to-conduct.md`, draft wording) |
| `constraints.md` / `identity.md` `not_here` lines | still true — "what she can find out is generated from the tool registry" becomes "what she can find out is described by the tool schemas" (optional wording update) |

## 4. Composition / eval — `nora/app/composition.py`, `nora/app/eval_support.py`

| file | change |
|---|---|
| `composition.py` line ~948 | drop `capability=ToolRegistry().capability()` from the `DoctrineStore`/`build_doctrine` call |
| `eval_support.py` lines ~65-68 (`CAPABILITY_BLOCK`) | delete; eval assembles doctrine without the block (situation remains) |

## 5. Tests

| test | change |
|---|---|
| `tests/test_prefill.py::test_the_generated_capability_block_names_exactly_the_tools_that_exist` (~766-787) | **delete** — its subject no longer exists. Its reverse-direction assertion (`spec.when` exists in block) dies with it |
| `tests/test_prefill.py` header tests (~725-763) | keep — they pin `_context_header` naming no tool; their docstring references to "the capability section" need rewording |
| `tests/test_reminder_tools.py::test_the_three_tools_are_described_to_the_model` (~504-510) | rewrite — instead of `- remind — ... in block`, assert the docstring of each tool names its routing trigger (what the when-clause used to guarantee) |
| `tests/test_config.py::test_a_budget_below_one_refuses_to_start` (~732) | wording update ("advertised in the capability block" → "described by its schema") |
| `tests/test_doctrine_loader.py` (~340-360) | `test_a_file_claiming_a_derived_kind_is_a_startup_failure` loops over `("capability", "situation")` → `("situation",)`; `authority_below` test strings drop capability |
| `tests/conftest.py` ~line 81 | drop `capability=ToolRegistry().capability()` from the doctrine fixture |
| `tests/unit/test_tool_registry.py` | replace any `when`-presence pins with docstring-presence pins (the new gate: every registered tool's callable has a docstring). `test_the_live_only_tools...` / `test_web_search_and_web_fetch...` / `test_skills_is_not_live_only` / `test_with_mcp_live_only_matches_plain` pin the deleted helpers directly and die with them. `test_every_tool_declares_a_time_axis` (time_shiftable values) is KEPT |
| `tests/test_acceptance_rules.py` (~756-786, DEC-92) | **rewrite, not delete** — this is the acceptance test that reads `NATIVE_TOOLS["recall_lookups"].when` and asserts `actually` / `doubted`/`guessed` / `re-running`. With the `when` field deleted, the assertion target moves to the **merged docstring** (same three phrases must survive there). Its second half (conduct.md "must NOT name the tool") is unchanged in spirit but its parenthetical — "the registry is capability's source of truth" — needs rewording, and the moved closed-world/sourcing rules in conduct.md must still not introduce tool names |

## 6. Verification plan

1. `make gate` (mypy + lint) green in the worktree.
2. Full test suite green — the suite currently pins capability in ≥7 files;
   every deletion is caught by a named test, not by inspection.
3. A live smoke: one tag with the new doctrine assembled, confirm the prompt
   contains `<situation>` and the conduct rules but no `- toolname —` block.
   (`nora.doctrine.loader.DoctrineStore` renders assembled doctrine; the smoke
   asserts the assembled string.)
