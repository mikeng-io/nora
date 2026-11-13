---
name: nora-structure
description: Places Nora code by area and layer (ADR 0037). Finds the area that owns a subject and the layer a piece belongs in, moves an area into nora/<area>/{domain,app,adapters} with its wire.py and import contracts, and splits an oversized file or function along those seams. Load before adding a module, moving code or splitting a file. Not for design bundles (nora-design) or tool text (nora-tool).
---

# nora-structure

The map is ARCHITECTURE.md «Code map»; the layer rules are `nora/AGENTS.md`;
the reasons are ADR 0037. This skill is the order and the check after each step.

## 1. Find the area

Read the area table. The subject's design bundle names it; the glossary names
its words. Check: one area owns the rule this code applies. A subject no area
owns is an architecture change: `nora-design` first, then the area table.
Runtime code (ingest, engagement, the agent, platforms, obs, config) is not an
area and stays in its package.

## 2. Find the layer

| the code | layer |
|---|---|
| a model, a rule, an invariant, a port | `domain/` |
| a use case that coordinates domain objects through ports | `app/` |
| SQL, HTTP, a Strands tool, a context block, a renderer | `adapters/` |
| building the adapters from shared dependencies | `wire.py` |

Check: `grep -rn "<the idea>" nora/<area>` finds no existing helper doing it.

## 3. Moving an area (one integrated change)

1. Create `nora/<area>/{domain,app,adapters}/__init__.py` and `wire.py`. The
   package root exports `wire` and the area's public types, nothing else.
2. Move whole files with `git mv`; split mixed files by layer. Update every
   import; leave no re-export at the old path.
3. Point the composition root at `nora.<area>.wire` and delete what it replaces.
4. Add the area to `pyproject.toml`:
   - its name to `containers` in the `every area depends inward` layers
     contract (`layers = ["wire", "adapters", "app", "domain"]`);
   - a `protected` contract: `protected_modules` the area's three layers and
     `wire`, `allowed_importers = ["nora.<area>"]`;
   - `nora.<area>.domain` to the source modules of `domain is framework-free`.

   The first area to move creates the layers contract.
5. Check:
   ```bash
   uv run lint-imports > "$LOG/imports.log" 2>&1; echo "RC=$?"
   uv run python -m scripts.code_size --write && git diff --stat tests/architecture/code_size_baseline.json
   ```
   PASS is `RC=0` with every contract kept, and a baseline that only lost entries.
   A tool move also dumps the tool specs before and after: they are identical.

## 4. Splitting a file or a function

Split by area, then by layer, then by step, never by line count. A function
over 120 lines becomes named steps in the same layer; a builder of many things
becomes one builder per area. Check: `code_size --write` drops the entry and
nothing new appears in the baseline.

## 5. Finish

The PR's «Architecture» line says `none` for a move (the design does not
change) or names the bundle when an area is added.
