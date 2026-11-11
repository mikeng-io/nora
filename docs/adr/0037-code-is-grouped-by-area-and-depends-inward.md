# 0037 · Code is grouped by area, and every area depends inward

- **Status:** accepted (operator, 2026-11-11)
- **Date:** 2026-11-11
- **Design:** [ARCHITECTURE.md «Code map»](../../ARCHITECTURE.md#code-map),
  [layer rules](../../nora/AGENTS.md).
- **Index:** none; the import contracts in `pyproject.toml` enforce it as each
  area moves.

## Context

`nora/` was grouped by mechanism: `agent/` held every tool, `app/` every
wiring, `infra/` every store, `domain/` every model. The memory subject lived
in four packages, while one package held every subject. Measured on master at
`ed678cd0`:

- 160 files exceed 800 lines (243k lines in all) and 186 functions exceed 120;
- `nora/agent/tools.py` is 18,887 lines, of which `build_tool_specs` is one
  13,503-line function with 100 parameters and 170 closures;
- `composition.py::build_dependencies` is 3,917 lines and
  `cognition_session.py::_build_tools` 1,751.

The import contracts did not prevent it. The `layered architecture` contract
lists today's packages in their existing import order, so it only proves that
nothing imports into `nora.domain`; 7 modules in `nora.platform` and 3 in
`nora.web` import `nora.infra` directly. No document named the architecture
the contracts were meant to hold.

## Decision

1. **Code is grouped by area.** An area is one subject of the business (memory,
   location, media, ...), one package `nora/<area>/`, named in the glossary
   and designed by its bundles. The list is the area table in
   ARCHITECTURE.md.
2. **An area has three layers and imports point inward.** `domain/` holds
   models, rules and the ports the area needs, with no framework imports;
   `app/` holds the use cases; `adapters/` implements the ports and holds the
   area's stores, clients, Nora's tools and context blocks.
3. **Areas meet only at package roots.** One area imports another through
   `nora.<area>`, never a submodule. Shared types live in `nora.domain`, the
   shared kernel.
4. **Each area wires itself.** `nora/<area>/wire.py` builds its adapters from
   the shared dependencies; the composition root assembles areas and wires
   nothing inside one.
5. **The runtime stays shared.** Ingest, engagement, the Strands agent, the
   platform adapters, observability and configuration serve every area and
   are not areas.
6. **DDD is used for its strategy, not its full tactics.** Areas and the
   glossary are its bounded contexts and shared vocabulary. Frozen dataclasses
   are the value objects and ports are the repositories; aggregates, domain
   event buses and per-area unit-of-work exist only where a measured need
   asks for them.
7. **Code moves one area at a time, in one integrated change per area.** New
   code goes to its area from now on. Each moved area joins the import
   contracts: the `containers` layers contract for its three layers and the
   independence contract between areas. The first move is `tools.py`, whose
   tool families go to their areas.

## Consequences

**Better.** What changes together lives together, so a change to memory touches
one package. The tool, wiring and schema monoliths split along the same seams.
An agent reads one area to change it, and the contracts state the architecture
rather than describe the imports.

**Worse.**

- A migration across about 262k lines, one area per change, with old and new
  shapes side by side until it ends.
- Moves rename import paths, so open branches conflict and `git blame` needs
  `-C` to follow a moved line.
- Some code belongs to two areas (a tool that reads memory about a place). It
  goes to the area whose rule it applies and calls the other through its root,
  which is an extra hop.
- `wire.py` per area replaces one composition function with many; the order
  areas are assembled in is now a real dependency to keep acyclic.

## Rejected

- **Keep the mechanism packages and add a folder per subject inside each**
  (a memory folder inside `agent`, `infra` and `app`). Cheaper, and it
  fixes file length, but memory still lives in four places and the coupling
  stays.
- **Group the domain by DDD building block** (`aggregates/`, `entities/`,
  `valueobjects/`). Still grouping by mechanism, one level down.
- **Full tactical DDD.** Aggregates and an event bus for every area are
  ceremony a self-hosted project does not need; added only on evidence.
- **Size ratchet alone.** It stops files growing; it does not say where the
  split goes, so files would be cut by line count instead of by subject.
