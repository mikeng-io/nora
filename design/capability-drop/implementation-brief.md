# Implementation Brief: Capability Drop (DEC-150)

## Context

The Nora repo has a generated `kind: capability` doctrine section rendered by
`ToolRegistry.capability()` from the `NATIVE_TOOLS.when` clauses. Design
decision DEC-150 (bundle at `design/capability-drop/`, approved) deletes it:
the tool schemas (docstring-derived) already carry 85% of the when-clause
content, and the two behavioural rules the block carried that schemas cannot
(closed-world, sourcing) move to conduct.md. **This brief is the closed
decision — implement exactly this, do not redesign. If evidence conflicts with
a listed invariant, STOP and return BLOCKED.**

Design bundle (read these first):
- `design/capability-drop/00-overview.md`
- `design/capability-drop/01-when-stays-in-schema.md`
- `design/capability-drop/02-rules-to-conduct.md`
- `design/capability-drop/03-code-surface.md` — the authoritative per-file change list

## Invariants (non-negotiable)

1. Master untouched — work in this worktree branch `feat/capability-drop-design` only.
2. The `skills` plugin tool (DEC-50) is NOT affected — `<available_skills>` stays.
3. `time_shiftable` field is KEPT (per-tool declaration, pinned by tests) — only its
   renderer `live_only_tools()` dies.
4. `tool_names()` is KEPT — the doctrine catalogue gate needs it.
5. The DEC-92 evidence framing survives: `recall_lookups` docstring must contain
   `actually`, `doubted`/`guessed`, `re-running` (acceptance test pins these).
6. No tool name may appear in any authored doctrine file (conduct.md closed-world +
   sourcing rules use by-category wording).

## Changes (from 03-code-surface.md — verify each against it)

### A. Registry — `nora/agent/registry.py`
- Delete `ToolRegistry.capability()`, `ToolRegistry.live_only_tools()`,
  `ToolRegistry.web_tier_tools()`.
- Keep `tool_names()`, `read_only_tools()`, `budgets()`, `terminal_tools()`,
  `cacheable_tools()`, `specs()`.
- Update docstrings referencing the deleted methods (eval_support caveat: "three
  throwaway registries" → "two").

### B. Tool metadata — `nora/agent/tools.py`, `nora/agent/ports.py`
- Delete `NativeTool.when` field + all 48 `when` clauses (second positional arg)
  in `NATIVE_TOOLS`.
- Delete `ToolSpec.when` field in `ports.py`.
- `_spec()` stops passing `when=declared.when`.
- **Docstring merges:**
  - `recall_lookups`: replace the "reusing … costs nothing; re-searching costs a
    live call" cost framing with the evidence framing, keeping `re-running`:
    "…your record of what you ACTUALLY checked: reach for it before re-running
    place_info/route/web_search/weather/transit, and whenever an earlier answer
    of yours is doubted or you cannot tell whether you checked something or
    guessed it." (Must contain `actually`, `doubted`/`guessed`, `re-running`.)
  - `create_event`: verify "genuinely NEW gathering" emphasis still reads strongly.
- Keep `time_shiftable`, `terminal`, `source_tier`, `cacheable`, `read_only`,
  `plugin`, `budget`.

### C. Doctrine loader — `nora/doctrine/loader.py`
- `DERIVED_KINDS = ("capability", "situation")` → `("situation",)`.
- `AUTHORITY_TERMINALS` → `("situation", "message-content", "tool-output")`.
- Delete `CAPABILITY_ORDER`, `CAPABILITY_DESCRIPTION`, `_capability_section()`.
- `DoctrineStore.__init__`: delete `capability: str = ""` param + `self._capability`.
- `_scan`: delete `if self._capability: sections.append(...)`.
- `check_kind_boundaries` error strings: "Capability is generated from the
  registry" → "tools are described by their schemas" (gate itself survives).
- Module docstring: update the capability/situation derived-kinds argument.
- `check_authority_graph` keeps working — authored files no longer list
  capability below them (section D), so nothing references the dead tier.

### D. Doctrine authored files
- `constraints.md`, `identity.md`, `conduct.md`, `voice.md`, `silence.md`:
  remove `capability` from front-matter `authority_below`.
- `nora/skills/navigation/SKILL.md`: remove `capability` from `authority_above`
  (becomes `[]` or `[situation]` — check the skill loader's authority rules).
- `conduct.md`: add the two behavioural rules from
  `design/capability-drop/02-rules-to-conduct.md` §3 draft wording (closed-world
  + sourcing), by-category phrasing, NO tool names.
- `constraints.md`/`identity.md` `not_here` lines: "what she can find out is
  generated from the tool registry" → "what she can find out is described by the
  tool schemas" (optional wording, keep the spirit).

### E. Composition / eval — `composition.py`, `eval_support.py`, `evals/`
- `nora/app/composition.py` ~948: drop `capability=ToolRegistry().capability()`.
- `nora/app/eval_support.py` 65-68: delete `CAPABILITY_BLOCK`.
- `evals/run.py` 74,401 + `evals/harness.py` 37,269: delete `CAPABILITY_BLOCK`
  import + `capability=` kwarg.

### F. Tests (disposition per 03-code-surface.md §5 — apply EXACTLY)
- DELETE: `tests/test_prefill.py::test_the_generated_capability_block_names_exactly_the_tools_that_exist`
- DELETE: `tests/test_doctrine_loader.py::test_the_generated_capability_section_joins_the_assembly_without_a_file`
- DELETE: the order-35 collision test in `test_doctrine_loader.py`
- DELETE: `tests/test_doctrine_assembly.py::test_the_capability_section_is_generated_and_sits_between_conduct_and_constraints`
- DELETE: `tests/test_composition.py::test_the_doctrine_the_root_builds_carries_the_generated_capability`
- REWRITE: `tests/test_reminder_tools.py::test_the_three_tools_are_described_to_the_model`
  → assert each tool's docstring names its routing trigger.
- REWRITE: `tests/test_acceptance_rules.py` DEC-92 test — assertion target moves
  from `NATIVE_TOOLS["recall_lookups"].when` to the merged docstring (same three
  phrases); parenthetical "the registry is capability's source of truth" reworded.
- REWRITE: `tests/test_tools.py::test_read_peer_card_is_gone_from_the_table_and_the_capability_block`
  → surviving assertion: `"read_peer_card" not in NATIVE_TOOLS`.
- UPDATE: `tests/web/test_api.py` + `tests/web/test_doctrine.py` expected section
  lists drop `"capability"`.
- UPDATE: `tests/conftest.py` drop `capability=ToolRegistry().capability()`.
- UPDATE (wording only): `tests/unit/test_tool_registry.py` (delete live_only/
  web_tier pins + when pins, keep time_shiftable pins), `tests/test_config.py`,
  `tests/unit/test_engagement_pipeline.py`, `tests/test_prefill.py` header tests,
  `tests/test_doctrine_loader.py` derived-kind loop → `("situation",)`,
  `tests/test_doctrine_content.py` + `tests/test_doctrine_audiences.py` docstrings,
  `tests/test_doctrine_assembly.py::test_without_a_generated_block...` (keep as
  always-true invariant, reworded).
- KEEP: `test_every_tool_declares_a_time_axis` (time_shiftable values).

## Verification (must all pass before you finish)

1. `uv run pytest tests/ -x -q` — full suite green (in worktree).
2. `uv run ruff check nora/ tests/` and mypy (the repo's gate: `make gate` if defined).
3. Grep proof:
   - `grep -rn "capability()" nora/ --include="*.py"` → no matches except incidental wording.
   - `grep -rn "spec.when\|\.when\b" nora/agent/ --include="*.py"` → no matches
     (Event.when etc. in domain/infra are unrelated — leave them).
   - `grep -rn "capability" nora/doctrine/*.md` → no `authority_below`/`authority_above`
     reference, no tool-name list; conduct.md contains the two new behavioural rules.
4. A smoke assemble: `uv run python -c "from nora.doctrine.loader import DoctrineStore; from nora.agent.registry import ToolRegistry; from nora.obs.metrics import Metrics; s=DoctrineStore(__import__('pathlib').Path('nora/doctrine'), tool_names=ToolRegistry().tool_names(), metrics=Metrics()).get(applies_to='tag'); print(s.doctrine_text[:200])"` — the assembled text has NO `<section kind="capability"` and no `- <tool> — <when>` lines.

## Acceptance criteria (definition of done)

- All the above greps return the expected results.
- Full test suite green.
- `git diff master` shows ONLY the intended deletions/moves above — no unrelated changes.
- Report back: list of files changed, tests deleted/rewritten, and the smoke output.
  If anything in section B/C/D contradicts the live code (e.g. a `when` consumer
  you find that this brief missed), STOP and return BLOCKED with the evidence —
  do not redesign the brief.
