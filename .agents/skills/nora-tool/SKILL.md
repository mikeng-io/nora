---
name: nora-tool
description: Adds or changes one of Nora's model-facing tools (a NativeTool's name, docstring, level and result) to ADR 0015, with the tests that enforce it and the tool_selection routing measurement before and after. Load when a task adds, renames, splits or rewrites a tool, its description, arguments, statuses or note. Not for Nora's runtime skills (nora-runtime-skill) or the eval run itself (nora-eval).
---

# Adding or changing a tool

The source of truth is `docs/adr/0015-tool-surface-policy-v2.md` (read it in full
before writing a line), which extends `docs/adr/0004-tool-naming-is-measured.md`
and supersedes parts of `docs/adr/0007-tool-surface-policy.md`. Its §6 table says
which rule is a test and which is review. This skill is the order and the checks;
it does not restate the reasons. Every tool lives in `nora/agent/tools.py`.

## 1. Before designing

1. **Is it a tool?** A capability is a tool; a rule about calling one tool goes
   in that tool's description; judgement across several tools is a runtime skill
   or doctrine (`docs/adr/0016-skill-policy.md` §1, `nora-runtime-skill`).
2. **Search the published convention** before choosing a new shape (split or
   unify, a new verb, a new result form): vendor tool-use docs, the MCP tools
   spec, and 0015's Sources as the starting set. A good measurement of the
   problem is not evidence the structure is right (2026-09-22).
3. **Read the siblings.** `grep -n '"<domain>_' nora/agent/tools.py` lists the
   same-domain tools; copy a recent one's shape (`x_search`, `x_read`,
   `linkedin_read` were written to 0015 on 2026-09-24).

## 2. The name

1. `<domain>_<verb>`, `[a-z0-9_]`, at most 64 characters. The verb comes from
   `VERBS` in `tests/architecture/test_tool_names.py`; read it there. A new verb
   goes into that constant in a PR that says why none of the existing ones fits.
2. **Never rename an existing tool** without a `tool_selection` run that shows the
   current name causing a misroute (0004). One rename at a time. A renamed or
   removed name goes into `_RENAMED_SINCE`; never edit `_SNAPSHOT_2026_09_24`
   (its digest is pinned).
3. **A new same-domain pair** (`instagram_read` beside `instagram_search`) gets
   cases in `evals/corpora/tool_selection.yaml` naming the sibling in
   `not_tools`, so its routing is measured, not assumed.
4. Check: `uv run pytest -q tests/architecture/test_tool_names.py` passes.

## 3. The declaration

1. Add the `NativeTool(...)` entry to `NATIVE_TOOLS`: budget, `terminal`,
   `time_shiftable`, `source_tier`, `cacheable`, `read_only`,
   `side_effect_state`, `idempotency`, `retry` (0008), `output_contract`
   (`"three_state"` for a status-carrying result), and `level` (1, 2 or 3).
2. Register the closure with `_spec("<name>", <function>)`. The docstring tests
   find tools by that call; a tool registered another way escapes them.
3. Add its budget to `test_budgets_match_todays_table` in
   `tests/unit/test_tool_registry.py`, which pins the whole table.

## 4. The docstring (what the model reads on every call)

1. **First paragraph is the catalogue line**: what the tool does and when to
   call it, plus what it returns when that is not obvious. At most 40 cl100k
   tokens as `- <name> <separator> <paragraph>`. The when-clause must match one
   of `WHEN_TRIGGERS` in `tests/test_tool_surface_policy.py` ("Call this when",
   "when somebody", "use this when", ...).
2. **The level's ceiling** over what Strands sends (the docstring minus `Args:`):
   Level 1 is 80 tokens, Level 2 160 (one MUST or SHOULD), Level 3 320 (a status
   contract, one line per status). Measure it:
   ```bash
   uv run python -c "
   import tiktoken; from nora.agent.registry import ToolRegistry; from nora.agent.tools import NATIVE_TOOLS
   e=tiktoken.get_encoding('cl100k_base'); d=dict(ToolRegistry()._descriptions()); n='<name>'
   print(n, 'level', NATIVE_TOOLS[n].level, 'tokens', len(e.encode(d[n])))"
   ```
3. **Everything the model must read goes above `Args:`**; Strands drops unindented
   text after it that is not a section header.
4. **Register (RFC 2119):** MUST only for turn correctness; style guidance after
   the opening, labelled SHOULD; nothing in the opening that reads as a reason not
   to call.
5. **Fixed options are types.** An argument with a fixed set of values is a
   `Literal` or enum in the signature, not a list in prose (review, not a test).
6. **English.** At most one non-English string in the whole spec, arguments
   included, and only a trigger the tool fires on, inside 「」 or «». Example
   utterances and example argument values are removed.
7. **No em or en dash** anywhere in the spec, argument descriptions included.
8. **Run the humanizer skill** over the first paragraph and the content, where
   the session has it. The tests cover its mechanical checks either way.

## 5. The result

1. Return a dict whose `status` is `ok`, `nothing_found` or `unavailable` (R-27),
   plus a declared extension such as `refused` where the tool needs one. A wiring
   gap, a bad input, a missing backend or an exception is `unavailable`, never
   `nothing_found`: «I couldn't check» is not «there is nothing».
2. **Interpretation lives in the result.** How to read or report a status goes in
   that result's `note` (`_with_note` and a notes mapping, as `x_search` does),
   so it is paid only when the status fires. The description names the statuses;
   the result explains them. Keep a `note` short.
3. Record the outcome on the tool's metric for every return path, the way
   `x_search` calls `_record` before each return.

## 6. Tests, then the gate

```bash
uv run pytest -q tests/test_tool_surface_policy.py tests/architecture/test_tool_names.py \
  tests/unit/test_tool_registry.py tests/test_tool_contract_metadata.py \
  <the tool's own tests> > "${TMPDIR:-/tmp}/tool-tests.log" 2>&1; RC=$?
echo "RC=$RC"; grep -E 'passed|failed|error' "${TMPDIR:-/tmp}/tool-tests.log"
```
PASS is `RC=0` and a passed count. Then `make gate` through `nora-land`.

What the tests enforce today (checked 2026-09-24, working tree):
- **Enforced:** charset and `<domain>_<verb>` (`tests/architecture/test_tool_names.py`);
  the 40-token opening with a when-clause, a level on every tool, the level
  ceiling, no dashes and at most one non-English string over the whole sent
  spec, nothing lost after `Args:`, MUST/SHOULD register (all in
  `tests/test_tool_surface_policy.py`); side effects declared
  (`tests/test_tool_contract_metadata.py`) and the read/act partition
  (`tests/unit/test_tool_registry.py`).
- **Review only:** the opening says what it returns, `Literal` for fixed
  options, humanized.
- **Not yet a test:** «a Level 3 status has a result `note`» (0015 §6 names
  `test_every_listed_status_returns_a_note`; it does not exist). Check it by hand:
  every status the description names has a return path carrying a `note`.

## 7. Measure, before and after

1. **Routing**, for any change to a name, a first paragraph or a level: the
   `tool_selection` corpus on master and on the branch, narrowed by `--case`
   to what the change can move — every case naming the tool in `tools`, the
   sibling negatives naming it in `not_tools`, and one control that routes
   elsewhere (`evals/corpora/tool_selection.yaml` `metadata:`). For a
   `where_is` change:
   `python -m evals tool_selection --case route_where_is_now --case route_where_been_today --case route_routine_read_future --case route_weather_not_web --sample N --reasons`
   `--reasons` prints the tools each draw executed. Choose N for the claim and
   check the `estimate:` line — the run refuses over 60 minutes, so the whole
   corpus at `--sample 5` (~7 h an arm) can never be the recipe. How and where
   to run it (the deploy host, the eval worktree, one run at a time, n by the
   question) is `nora-eval`.
2. **Reporting**, when interpretation moves into a result: the `tool_reporting`
   corpus with `--judge`, same two arms.
3. **Check the eval feeds the tool what production does.** A tool's next step
   depends on what the previous tool RETURNED. If an eval case gets a different
   result shape than a live call would (a fake handle, a missing backend), the
   run cannot gate the change: say so in the PR and verify live. (#454: about 8
   in 10 in the eval, 0 of 2 live.)
4. **Put the numbers in the PR**: both arms, n, the cases that moved, the
   commands, and that the humanizer ran.

## 8. After deploy: one real call

A merged tool needs an image rebuild to be live (`nora-deploy`). Then:
1. Confirm the running process has it:
   `ssh nora-host 'cd ~/workspace/nora && python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora /app/.venv/bin/python -c "from nora.agent.tools import NATIVE_TOOLS; print(NATIVE_TOOLS[\"<name>\"].level)"'`
2. Find a real call in Langfuse (`nora-readback`) and read its input and result.
   Check: the live result has the status and shape your eval cases assumed. If
   it differs, the eval number does not stand; say so on the PR.

## Tool notes

- **Claude Code with the rtk hook:** long pytest or grep output can come back
  condensed. When the pass count matters, read the log file as written above,
  or run the command through `rtk proxy`.

Host exec requires the [installed secret-delivery helper](../../../docs/configuration.md#host-migration-and-installed-files).
