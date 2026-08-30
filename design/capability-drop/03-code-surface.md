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

**The loader has its OWN capability machinery beyond the kind tuple — this
was the gap the first two review runs surfaced.** Deleting
`ToolRegistry.capability()` alone leaves `DoctrineStore` still carrying a
`capability:` parameter, still appending a generated section in `_scan`, and
still enforcing the `DERIVED_KINDS`/`AUTHORITY_TERMINALS` tuples — the block
would survive as an empty shell. The full loader surface:

| file | change |
|---|---|
| `loader.py` `DERIVED_KINDS = ("capability", "situation")` (line 60) | → `("situation",)` |
| `loader.py` `AUTHORITY_TERMINALS = ("capability", "situation", …)` (line 67) | → `("situation", "message-content", "tool-output")` |
| `loader.py` `CAPABILITY_ORDER = 35` (line 332) | **delete** |
| `loader.py` `CAPABILITY_DESCRIPTION` (line 333) | **delete** |
| `loader.py` `_capability_section()` (line 339) | **delete** — the one-section-with-no-file constructor |
| `loader.py` `DoctrineStore.__init__` `capability: str = ""` param (line 496) | **delete param + `self._capability`** — the "empty value assembles no capability" default becomes simply "no capability exists" |
| `loader.py` `_scan` line 557-558 (`if self._capability: sections.append(...)`) | **delete** — nothing appends a generated section; `_scan` returns only authored files |
| `loader.py` check_kind_boundaries error strings ("Capability is generated from the registry") | reword to "tools are described by their schemas" — the gate itself survives (it prevents authored doctrine naming tools) |
| `loader.py` module docstring (lines 6-28) | update the "capability sentences have no owner" argument — now only `situation` is derived |
| `constraints.md` front-matter `authority_below` | drop `capability` from the list |
| `identity.md` front-matter `authority_below` | drop `capability` |
| `conduct.md` front-matter `authority_below` | drop `capability`; add the two behavioural rules (see `02-rules-to-conduct.md`, draft wording) |
| `voice.md` front-matter `authority_below` (line 9: `[silence, conduct, capability, situation, …]`) | **drop `capability`** — missed by the first two reviews; the authority chain is wider than constraints/identity/conduct |
| `silence.md` front-matter `authority_below` (line 9: `[capability, situation, …]`) | **drop `capability`** — same miss |
| `nora/skills/navigation/SKILL.md` front-matter `authority_above: [capability]` (line 7) | **drop `capability`** — the skill's authority edge pointed at the dying tier; authority_above becomes empty (`[]`) or points at situation |
| `constraints.md` / `identity.md` `not_here` lines | still true — "what she can find out is generated from the tool registry" becomes "what she can find out is described by the tool schemas" (optional wording update) |

## 4. Composition / eval — `nora/app/composition.py`, `nora/app/eval_support.py`

| file | change |
|---|---|
| `composition.py` line ~948 | drop `capability=ToolRegistry().capability()` from the `DoctrineStore`/`build_doctrine` call |
| `eval_support.py` lines ~65-68 (`CAPABILITY_BLOCK`) | delete; eval assembles doctrine without the block (situation remains) |
| `evals/run.py` lines 74, 401 + `evals/harness.py` lines 37, 269 | **delete the `CAPABILITY_BLOCK` import and the `capability=` kwarg** — both import from `nora.app.eval_support` and pass the block to their DoctrineStore; without this, the eval harness keeps assembling a doctrine the production path no longer has (the exact "silently missing a fifth of it" failure `eval_support` was built to prevent, now inverted) |

## 5. Tests

| test | change |
|---|---|
| `tests/test_prefill.py::test_the_generated_capability_block_names_exactly_the_tools_that_exist` (~766-787) | **delete** — its subject no longer exists. Its reverse-direction assertion (`spec.when` exists in block) dies with it |
| `tests/test_prefill.py` header tests (~725-763) | keep — they pin `_context_header` naming no tool; their docstring references to "the capability section" need rewording |
| `tests/test_reminder_tools.py::test_the_three_tools_are_described_to_the_model` (~504-510) | rewrite — instead of `- remind — ... in block`, assert the docstring of each tool names its routing trigger (what the when-clause used to guarantee) |
| `tests/test_config.py::test_a_budget_below_one_refuses_to_start` (~732) | wording update ("advertised in the capability block" → "described by its schema") |
| `tests/test_doctrine_loader.py::test_a_file_claiming_a_derived_kind_is_a_startup_failure` (~347-360) | loops over `("capability", "situation")` → `("situation",)`; `authority_below` test strings drop capability |
| `tests/test_doctrine_loader.py::test_the_generated_capability_section_joins_the_assembly_without_a_file` (~363-369) | **delete** — relies on `_store(doctrine_dir, capability=...)` (the param being removed) |
| `tests/test_doctrine_loader.py` (~372+, "One capability section, and it is the generated one. A file at order 35") | **delete** — pins `CAPABILITY_ORDER = 35` collision, a constant being removed |
| `tests/test_doctrine_assembly.py::test_the_capability_section_is_generated_and_sits_between_conduct_and_constraints` (~32-49) | **delete** — pins `capability=ToolRegistry().capability()` and the generated section's position |
| `tests/test_doctrine_assembly.py::test_without_a_generated_block_there_is_no_capability_section_at_all` (~52) | **keep as the new invariant, reworded** — "no capability section at all" is now simply the always-true state; the test becomes `_store(doctrine_dir).get()` produces no capability section (which it now does without the `capability=` arg) |
| `tests/test_composition.py::test_the_doctrine_the_root_builds_carries_the_generated_capability` (~156-171) | **delete** — pins `'<section kind="capability"'` in the root-built doctrine text |
| `tests/web/test_api.py` (~105-115, `/app/v1/doctrine` returns `[identity, voice, silence, conduct, capability, constraints]`) | **update** — expected section list drops `"capability"` |
| `tests/web/test_doctrine.py` (~8-11, `build_doctrine_payload` section names) | **update** — expected list drops `"capability"` |
| `tests/test_doctrine_content.py` (~63-68, 217-220, docstrings referencing "the section is gone and what replaces it is generated (`ToolRegistry.capability()`)") | **wording update** — the "what replaces it" phrase now reads "tools are described by their schemas"; the tests themselves (`test_every_shipped_file_says_what_it_must_not_contain`) are unaffected, docstrings only |
| `tests/test_doctrine_audiences.py` (~1-6, module docstring "received only the generated capability section") | **wording update** — docstring only; the audience tests themselves are unaffected |
| `nora/app/fire.py` (~56, module docstring "a `LeakGuard` corpus shrunk to the capability block") | **wording update** — docstring only; with the block gone, the sentence's historical point stands but the reference dies |
| `nora/skills/loader.py` (~6, module docstring "the generated capability block") | **wording update** — docstring only; the loader itself is unaffected |
| `nora/agent/prefill.py` (~178-183, `_context_header` docstring: "`ToolSpec.when` is that field, and the catalogue is now `ToolRegistry.capability()`… Situation, not capability.") | **wording update** — the DEC-29 history sentence dies with its referents; the surviving claim ("Situation, not capability") becomes "Situation, not catalogue" |
| `tests/unit/test_engagement_pipeline.py` (~1609, "and `capability()` are all still the production ones") | wording update — drop the `capability()` reference |
| `tests/conftest.py` ~line 81 | drop `capability=ToolRegistry().capability()` from the doctrine fixture |
| `tests/unit/test_tool_registry.py` | replace any `when`-presence pins with docstring-presence pins (the new gate: every registered tool's callable has a docstring). `test_the_live_only_tools...` / `test_web_search_and_web_fetch...` / `test_skills_is_not_live_only` / `test_with_mcp_live_only_matches_plain` pin the deleted helpers directly and die with them. `test_every_tool_declares_a_time_axis` (time_shiftable values) is KEPT |
| `tests/test_acceptance_rules.py` (~756-786, DEC-92) | **rewrite, not delete** — this is the acceptance test that reads `NATIVE_TOOLS["recall_lookups"].when` and asserts `actually` / `doubted`/`guessed` / `re-running`. With the `when` field deleted, the assertion target moves to the **merged docstring** (same three phrases must survive there). Its second half (conduct.md "must NOT name the tool") is unchanged in spirit but its parenthetical — "the registry is capability's source of truth" — needs rewording, and the moved closed-world/sourcing rules in conduct.md must still not introduce tool names |
| `tests/test_tools.py::test_read_peer_card_is_gone_from_the_table_and_the_capability_block` (~159-172) | **rewrite** — the second half asserts `"read_peer_card" not in ToolRegistry().capability()`; with `capability()` deleted, the surviving assertion is `"read_peer_card" not in NATIVE_TOOLS` (R-7 default-deny), the capability half dies |

## 6. Verification plan

1. `make gate` (mypy + lint) green in the worktree.
2. Full test suite green — the suite currently pins capability in ≥7 files;
   every deletion is caught by a named test, not by inspection.
3. A live smoke: one tag with the new doctrine assembled, confirm the prompt
   contains `<situation>` and the conduct rules but no `- toolname —` block.
   (`nora.doctrine.loader.DoctrineStore` renders assembled doctrine; the smoke
   asserts the assembled string.)
