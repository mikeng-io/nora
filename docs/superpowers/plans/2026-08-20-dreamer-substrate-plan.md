> **PARTLY DEPRECATED 2026-09-07 — every reference below to the knowledge tier (the "fact tier") or to the language tier describes code that has since been deleted.** Those parts are retained as a record of the reasoning, not as a description of the system; the rest of this document still stands.
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.

# Dreamer Substrate — Phases 1–3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Dreamer from a clock calling three hand-written extractors into a
**runtime** — a read-only research agent with a versioned, hot-reloading instruction
substrate — and turn each synthesizer into an **adaptor** of it, supplying five things:
objective, inputs, output schema, tool subset, sink. Phase 1 builds the substrate,
Phase 2 builds the runtime, Phase 3 migrates the two instances that already call a model.

**Architecture:** Three layers, each shippable on its own.

- **Phase 1 — substrate.** A shared parse/hash/reload core extracted out of
  `nora/doctrine/loader.py` into a new `nora/instructions/` package; a text-only
  `nora/dreamer/` tree (`doctrine/`, `objectives/`) bind-mounted the way `nora/doctrine`
  already is; one `instruction_sha256` over the **assembled** doctrine + objective,
  stamped on every row an adaptor writes. No behaviour change beyond where the text
  is read from.
- **Phase 2 — runtime.** `nora/agent/dreamer.py` — the only package besides
  `nora.compute` allowed to import `strands`. A read/act split expressed as one new
  `NativeTool` field and one derived frozenset, exactly as `terminal_tools()` /
  `cacheable_tools()` / `live_only_tools()` / `web_tier_tools()` already are.
  Per-adaptor budgets with a bounded wall-clock timeout. Screening before every sink
  write. Adaptors declare their own key, so a workspace-keyed adaptor can join a
  scope-keyed loop.
- **Phase 3 — migration.** `LexemeObserver` and `ExchangePatternObserver` become
  adaptors, one at a time, dual-pathed behind a flag, with the old path deleted only
  after the new one has been watched on live traffic.

**Tech Stack:** Python 3.13, Strands 1.50.2 (via `nora.agent` only), OpenAI-compatible
client through OmniRoute, SQLAlchemy 2 async + asyncpg, Alembic, pydantic, PyYAML,
pytest + pytest-asyncio. **No new dependency** (see Task 2.5 for the deliberate
decision not to add `jsonschema`).

**Specs** (all three, and they disagree in places — §"Spec defects found while
sequencing" at the end of this plan records where, and what this plan does about it):

- `docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md` — **the keystone.**
  Runtime/adaptor split, tool set, code location, budget, screening.
- `docs/superpowers/specs/2026-08-20-dreamer-prompt-substrate-design.md` — the
  instruction substrate: files, loader, `instruction_sha256`.
- `docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md` — where
  the fact adaptor fits (§5.3, §8.2). **Phase 4, not this plan.**
- `docs/superpowers/specs/2026-08-20-prompt-architecture-audit-findings.md` — the
  measured state of the prompt path. F14 (`room_language.sense` is an open
  model-authored persistence channel) is what Phase 2's screening closes.

**Prior art in the same subsystem, and the house style these tasks copy:**
`docs/superpowers/plans/2026-08-15-dreamer-language-consolidator.md` (Plan 1),
`docs/superpowers/plans/2026-08-19-presence-analytics-tier3-rhythm-plan.md`.

---

## Scope

**In:** Phases 1, 2, 3 as above.

**Out, and deliberately so:**

- **Phase 0b** — `instruction_sha256` on the four live tables (`room_lexeme`,
  `room_exchange`, `language_observation`, `exchange_observation`). Being implemented
  concurrently on another branch. **This plan depends on it and does not repeat it.**
  Task 1.7 is the first task that touches those columns and it must not start until
  0b is merged (see "Cross-branch dependency" below).
- **The fact adaptor** (`2026-08-20-reflection-reasoning-continuity-design.md`). It is
  Phase 4. Phase 2's port design is shaped by it — two sink verbs, adaptor-declared
  keys — but no fact code, no fact table, no fact store decision is in scope here. The
  store question belongs to the workstream holding
  `2026-08-20-fact-store-research-handoff.md`.
- **`RoomPortraitSynthesizer`.** The keystone (§3) makes it an adaptor with an empty
  tool subset and an objective executed in code. Migrating it moves no model call and
  buys nothing this plan needs, so it stays a `portrait=` keyword on `dream_loop` until
  the adaptor port has two real users. Task 2.2 leaves room for it; nothing more.
- **The tag path.** `Standing`, the debrief, `TagTrace`, the propose/dispose split,
  `build_prefill`, the render-side frames (substrate §2.4). Untouched.
- **The audit's A1–A11.** Separate work.

---

## Cross-branch dependency (read before starting)

Phase 0b is landing on another branch **in the same checkout family**. Two rules:

1. **Never run two agents against one checkout** (CLAUDE.md). Work in a worktree.
2. **Re-read the four tables' real columns before Task 1.7** —
   `docker exec nora-db-1 psql -c '\d room_lexeme'` — rather than assuming what 0b
   shipped. 0b may have chosen to populate `instruction_sha256` from a hash of the
   inline constants as an interim; Task 1.7 then *repoints* the stamp at the loader's
   assembled hash rather than introducing it. Either way the column is 0b's and the
   value is Phase 1's.

If 0b has not merged when Phase 1 reaches Task 1.7, **Tasks 1.1–1.6 still ship**;
1.7 waits. Nothing else in this plan blocks on it.

---

## Decision gates

Where a task cannot be written without an answer this plan does not have, it stops at a
gate. **A gate is not a TODO — it is a task that produces a decision, with the evidence
the decision needs, before the dependent task starts.** Three of the five come straight
from the keystone's §9.

| gate | question | keystone | blocks | this plan's position |
|---|---|---|---|---|
| **D1** | Extract the parse/hash/reload core from `nora/doctrine/loader.py`, or purpose-build a second loader? | substrate §4.2, §6.1 | Task 1.2 | **DECIDED — extract, with a narrower carve than §4.2 implies.** Evidence and the carve line are in Task 1.2. Reversible: if the carve exceeds ~120 lines or forces one doctrine test to change, stop and purpose-build. |
| **D2** | **Which model does the Dreamer run?** A research loop is a different workload from one-shot extraction. | §9.1 | flipping `[dreamer].enabled = true` in production (Task 3.5) | **OPEN. Do not pick one.** Task 2.9 is the measurement that answers it. Everything up to 3.5 is buildable with `[dreamer].model` defaulting to `[language].model`, which reproduces today's tier; the gate is on the *cutover*, not on the code. |
| **D3** | **Per-adaptor failure isolation.** `dream_loop`'s try/except is per scope; per adaptor is a second axis and does not exist today. | §9.4 | Task 2.8 | **OPEN.** Task 2.8 cannot be written without it — the new loop shape must decide whether a failing adaptor increments *the scope's* backoff counter. The precedent that argues both ways is in Task 2.8's own body (`runtime.py`'s portrait branch already isolates one participant; `_dream_once`'s consolidator loop deliberately does not). |
| **D4** | Does the instruction stamp need to cover the **tool subset and the budget** as well as the text? | substrate §6.5 | nothing in this plan | **OPEN, and deliberately not resolved.** Substrate §6.5's own note holds: if the answer is "yes, more", it is a *second* column, not a different one, so Phase 1's stamp is correct either way. Recorded here so it is not silently answered by omission. |
| **D5** | `model` alongside `instruction_sha256` on the stamped rows? | substrate §6.2 | Task 1.7 | **OPEN, and it interacts with D2.** D2 *will* change the model, so rows written either side of it should be distinguishable. Task 1.7 stops and asks; adding the column costs one migration now and is impossible to backfill later. |

**Not gates, because the brief settles them:** §9.2 (per-adaptor scheduling) is a
**requirement** — `_dream_once` hands every consolidator one per-scope batch off one
shared watermark and cannot host a workspace-keyed adaptor unchanged. §9.3 is answered
**no** — the fact adaptor does not take the shared watermark;
`RoomPortraitSynthesizer` (`nora/app/runtime.py:352`) is the standing precedent for a
dream-loop participant with no watermark, lag or quiet condition of its own.

---

## Global Constraints

- **The Dreamer is LIVE.** `config.toml:594` has `[language] enabled = true`; the loop
  logs `dream loop started: tick every 300s over 4 scope(s)` and its output renders
  into `<room_language>`/`<room_exchange>`/`<room_portrait>` on every tag. This is a
  migration of a running subsystem, not greenfield. Every task must leave the loop
  working; every phase must be revertible by a flag, not by a rebuild.
- **Gates, all four, before every commit** (CLAUDE.md):
  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  ```
  Plus `uv run python scripts/check_release_fragments.py --base origin/master` before
  merge, and for any schema change `tests/infra/test_schema_parity.py` (which runs
  inside `pytest`) — **never `alembic check`**.
- **A fresh worktree needs the evals extra**: `uv sync --extra evals`, or pytest dies at
  COLLECTION on three files and the failure looks like broken code.
- **`strands` may be imported only from `nora/agent/**` and `nora/compute/**`**
  (`tests/test_boundaries.py:81::test_strands_import_boundary`). The Dreamer runtime is
  therefore in `nora/agent/`; the adaptors stay in `nora/language/` holding ports only.
- **`nora/agent/factory.py` is the ONLY module that constructs Strands objects.** A
  second agent gets a second constructor *there*, never an `Agent(...)` call somewhere
  else.
- **Layer contract** (`pyproject.toml:149-167`), higher may import lower:
  `app > ingest|engagement > agent|platform > doctrine > language > skills > obs >
  infra|testing > domain`. Consequences this plan lives with:
  - `nora.language` may **not** import `nora.agent` (so an adaptor names its tool
    subset by **string**, and the runtime resolves the names — the same inversion
    `ToolRegistry.tool_names()` already does for the doctrine loader), and may not
    import `nora.doctrine` or `nora.config`.
  - The screen (`nora.agent.egress.scan_identifiers`, `nora.agent.security`) is
    unreachable from `nora.language`. **The runtime screens, immediately before the
    sink write.** "Screening at the sink" (keystone §7) is satisfied by *the write
    path*, not by code living inside the sink.
- **A new `nora/` subpackage costs five edits, every time:** the `layers` contract, the
  "only app and `__main__` read config" `source_modules` list, the "evals may only
  reach `nora.app` and `nora.testing`" `forbidden_modules` list (all in
  `pyproject.toml`), a package-table row in `ARCHITECTURE.md`
  (`tests/test_architecture_doc.py::test_architecture_doc_covers_every_package`), and —
  if it reads a `.md` — the allowlist in
  `tests/test_boundaries.py:18::test_doctrine_read_boundary`.
- **Every port method is keyword-only after `self`** (`tests/test_ports.py:80`) **and
  carries `@runtime_checkable`** (`tests/test_ports.py:62`). Both gates enumerate every
  Protocol in `nora.domain.ports` automatically.
- **A port never gains a second implementation, including a test fake, without a
  contract suite both pass** (`tests/contract/`).
- **Production classes take collaborators through `__init__` only** — no `*_fn` hooks,
  no `_for_test` params, no module-level mutable singletons.
- **No act verb, ever, and no write verb.** The runtime writes the schema-validated
  return. Enforced by a named test (Task 2.1), not by a comment.
- **`nora/doctrine` and `config.toml` on `master` are bind-mount sources for the running
  container.** An edit on `master` is a deploy. Every change here starts in a worktree
  on its own branch. `nora/dreamer/` inherits exactly this property the moment Task 1.6
  mounts it — that is deliberate (substrate §4.1) and it is why Task 1.4's text is
  reviewed as text, not as code.
- **Release fragment required** under `release-fragments/unreleased/` before merge. Each
  phase writes its own.
- **Migration head is `0022_presence_trips_sequence`** as of 2026-08-20. This plan adds
  **no migration of its own** — the only schema change it needs is Phase 0b's, on
  another branch. If Task 1.7's D5 lands "yes, add `model`", that is one hand-written
  migration; re-check `ls nora/infra/sql/migrations/versions/` first, another session
  is committing to this repo.
- **Highest DEC in `design/09-decision-index.md` is DEC-124** at plan time. Re-check
  before writing an entry.

---

## File Structure

```
nora/instructions/__init__.py               NEW   the shared substrate package
nora/instructions/frontmatter.py            NEW   split + parse + body sha256; the ONLY
                                                   new module that reads a .md
nora/instructions/reload.py                 NEW   mtime fingerprint, cache, last-good
nora/instructions/dreamer.py                NEW   DreamerInstructions: doctrine +
                                                   objectives -> assembled text + sha256
nora/doctrine/loader.py                     EDIT  re-expressed on the shared core; the
                                                   gates and the assembly stay here

nora/dreamer/doctrine/dreamer.md            NEW   text only, no Python in this tree
nora/dreamer/objectives/room-language.md    NEW
nora/dreamer/objectives/room-language-verification.md  NEW
nora/dreamer/objectives/room-exchange.md    NEW

nora/agent/tools.py                         EDIT  NativeTool.read_only + one flag per row
nora/agent/registry.py                      EDIT  ToolRegistry.read_only_tools()
nora/agent/factory.py                       EDIT  tool_choice param; create_dream_agent;
                                                   final_text promoted out of nora/app
nora/agent/guards.py                        EDIT  DreamGuard (default-deny, no terminal)
nora/agent/dreamer.py                       NEW   the runtime — one public entry point
nora/agent/dream_screen.py                  NEW   the sink screen (R-18/R-28 at the write)

nora/domain/ports.py                        EDIT  DreamAdaptor, DreamSink
nora/language/observer.py                   EDIT  objective injected, then adaptor
nora/language/exchange_observer.py          EDIT  same
nora/language/adaptors.py                   NEW   the two adaptors' five fields
nora/infra/sql/language.py                  EDIT  sink implementation (propose)
nora/infra/sql/exchange.py                  EDIT  sink implementation (propose)
nora/app/runtime.py                         EDIT  dream_loop schedules per adaptor
nora/app/composition.py                     EDIT  wiring
nora/app/agent_runner.py                    EDIT  imports the promoted final_text
nora/config.py                              EDIT  [dreamer] section
nora/obs/metric_specs.py                    EDIT  the Dreamer runtime's metrics
config.toml / config.example.toml           EDIT  [dreamer]
docker-compose.yml                          EDIT  ./nora/dreamer:/app/nora/dreamer:ro
pyproject.toml                              EDIT  package-data + 3 import contracts
ARCHITECTURE.md                             EDIT  package table row for instructions/
design/09-decision-index.md                 EDIT  one entry per phase
release-fragments/unreleased/*.json         NEW   one per phase

tests/unit/test_instructions_frontmatter.py NEW
tests/unit/test_instructions_reload.py      NEW
tests/unit/test_dreamer_instructions.py     NEW
tests/test_doctrine_assembly.py             EDIT  the golden-assembly safety net
tests/test_boundaries.py                    EDIT  .md reader allowlist
tests/unit/test_tool_registry.py            EDIT  read_only_tools()
tests/contract/test_dream_adaptor_contract.py NEW
tests/contract/test_dream_sink_contract.py  NEW
tests/unit/test_dreamer_runtime.py          NEW
tests/unit/test_dream_screen.py             NEW
tests/unit/test_runtime.py                  EDIT  per-adaptor scheduling
tests/fixtures/dreamer_objectives_pre_move/ NEW   the verbatim pre-move constants
```

---

# PHASE 1 — the instruction substrate

**What Phase 1 ships:** the three inline constants stop being inline; the Dreamer gains
its own doctrine; every row an adaptor writes carries the hash of the instruction text
that produced it; an operator can edit an objective and see it take effect on the next
tick without a rebuild.

**What Phase 1 deliberately does NOT ship:** the three-way re-partition of substrate
§2.3. Two of its three targets — the **output-schema field** and the assembled
**Dreamer doctrine + objective** an agent loop actually consumes — are artifacts of a
runtime that does not exist until Phase 2. Attempting the re-partition here would mean
splitting text against a shape nobody can run, which is exactly the "indistinguishable
from a substrate bug" failure §7.1 exists to prevent. **Phase 1 does §7.1 step (a)
only** — a verbatim lift with a byte-for-byte test. Step (b) is Tasks 3.2 and 3.4, one
per adaptor, each its own commit, each evaluated on its own.

---

### Task 1.1: A golden-assembly safety net before touching the doctrine loader

`nora/doctrine/loader.py` assembles Nora's live system prompt and hot-reloads it from a
bind mount. Task 1.2 refactors its guts. Before that happens there must be a test that
turns **red on any change to the assembled bytes**, and the existing suite does not have
one: `tests/test_doctrine_assembly.py` asserts *properties* (wrapped in
`<nora_doctrine>`, bodies verbatim, sha excludes runtime context) rather than the exact
output.

The golden is taken over the **test fixture corpus**, never over the shipped doctrine —
`nora/doctrine/*.md` is operator-edited and hot-reloaded, so a golden over it would be
red every time the operator writes a better sentence.

**Files:**
- Test: `tests/test_doctrine_assembly.py`
- New: `tests/fixtures/doctrine_assembly_golden.txt`

**Interfaces:** none. Test-only.

- [ ] **Step 1: Write the golden test**

  Add to `tests/test_doctrine_assembly.py`, reusing the file's existing
  `write()`/`doctrine_dir` fixture shape:

  ```python
  def test_the_assembled_doctrine_is_byte_stable(doctrine_dir):
      """The safety net for the loader extraction (Phase 1 Task 1.2).

      Properties-only assertions cannot catch a refactor that changes a single
      newline in `_assemble` — and that newline lands in the live system prompt
      and in `doctrine_sha256`, which every trace's provenance is bound to. The
      golden is over the FIXTURE corpus, never the shipped doctrine: those files
      are operator-edited and bind-mounted, so a golden over them would be red on
      every legitimate edit.
      """
      store = _store(doctrine_dir)
      golden = (FIXTURES / "doctrine_assembly_golden.txt").read_text(encoding="utf-8")
      assert store.get(applies_to="tag").doctrine_text == golden
  ```

- [ ] **Step 2: Generate the golden from HEAD and commit it**

  Write the current output to the fixture file with a throwaway script, then read the
  file back and eyeball it: it must open with `<nora_doctrine>`, carry one
  `<section kind=… name=… description=…>` per fixture file in `order` sequence, and
  close with `</nora_doctrine>`. **Do not generate it after Task 1.2** — a golden
  captured after the refactor proves nothing.

- [ ] **Step 3: Prove it can fail**

  Temporarily change `_assemble`'s join from `"\n"` to `"\n\n"`, run the test, watch it
  fail, revert. *Never read an instrument's output as evidence without first checking it
  can measure the thing.*

  Run: `uv run pytest tests/test_doctrine_assembly.py -q` → PASS after the revert.

- [ ] **Step 4: Commit**

  ```bash
  git add tests/test_doctrine_assembly.py tests/fixtures/doctrine_assembly_golden.txt
  git commit -m "test(doctrine): pin the assembled bytes before the loader extraction"
  ```

---

### Task 1.2: Extract the shared parse/hash/reload core — `nora/instructions/`

**D1 is decided here: extract, with a narrower carve than substrate §4.2 implies.**

The evidence, measured against `nora/doctrine/loader.py` (631 lines) and
`nora/skills/loader.py`:

- `_FRONTMATTER_RE` is **already duplicated byte-identically** in
  `nora/skills/loader.py:65`. A third copy would be the second duplication of the same
  regex, and the Dreamer's would be the first one that also needs the *reload* half.
- The reload state machine — `_fingerprint_dir`, `_maybe_rescan`, `_on_reload_failure`,
  `_cache`/`_last_good` — is ~50 lines and mentions doctrine only in a metric name and a
  log string. `SkillStore` has **no reload at all**, so this behaviour is currently
  unshared and a fork would be its first second implementation. Under CLAUDE.md that
  buys a contract suite; and its failure mode (silently serving a stale instruction set
  after a bad edit) is precisely what `_on_reload_failure` was written to get right.
- Almost nothing pins the internals: no test outside the module reaches `_scan`,
  `_fingerprint`, `_last_good`, `_assemble`, `_open_tag` or `_capability_section`. The
  constructor signature and `Doctrine`'s field names are what is pinned.

**The carve line — what does NOT move:** the kind taxonomy, all three gates
(`check_kind_boundaries`, `check_normative_register`, `check_authority_graph`),
`_open_tag`/`_assemble`/`<nora_doctrine>`/`<runtime_context>`, `_capability_section`,
the `applies_to` audience filter, the `order`-with-filename tiebreak, and the `Doctrine`
value type. Substrate §4.2 says "lift the parse/hash/reload half"; **`parse_section`
itself does not lift** — 60% of it is doctrine-specific validation over
doctrine-specific required keys, and `DoctrineSection` is a doctrine record. What lifts
is the *skeleton*: frontmatter split, YAML load, mapping check, required-key check,
name-charset check, body `sha256`, leftovers into `metadata`.

**Abort condition, and it is real:** if the carve exceeds ~120 lines of moved code, or
if any existing doctrine test has to change, stop, revert, and purpose-build a second
loader instead. A refactor of the live system-prompt assembler that needs its own tests
edited is no longer a refactor.

**Files:**
- New: `nora/instructions/__init__.py`, `nora/instructions/frontmatter.py`,
  `nora/instructions/reload.py`
- Modify: `nora/doctrine/loader.py`
- Modify: `pyproject.toml` (three import contracts), `ARCHITECTURE.md`,
  `tests/test_boundaries.py`
- Test: `tests/unit/test_instructions_frontmatter.py`,
  `tests/unit/test_instructions_reload.py`

**Interfaces:**
- Produces: `nora.instructions.frontmatter.parse_document(path, *, required, name_re)
  -> ParsedDocument` — `ParsedDocument(name, body, sha256, meta: dict, path)`. Raises
  `InstructionError` on every malformed shape, with the filename in the message.
- Produces: `nora.instructions.reload.DirectoryWatcher` — `fingerprint()`,
  `changed()`, and the `last_good` fallback, generic over what a scan produces
  (`Callable[[], T]`), counting failures on an injected `Metrics` under a **caller-named
  counter** so the doctrine keeps `nora_doctrine_reload_failures_total` unchanged.

- [ ] **Step 1: Write the failing unit tests for the two new modules**

  `tests/unit/test_instructions_frontmatter.py` — missing frontmatter, unparseable
  YAML, non-mapping frontmatter, a missing required key naming *which* key, a bad name
  charset, unknown keys preserved in `meta`, and `sha256` over the **body only** (not
  the frontmatter). `tests/unit/test_instructions_reload.py` — an unchanged directory
  reports no change; a touched file does; a scan raising keeps the last good value and
  increments the injected counter; the **first** scan raising re-raises because there is
  no last good.

  Run them: FAIL — `ModuleNotFoundError: nora.instructions`.

- [ ] **Step 2: Write the two modules**

  `frontmatter.py` holds the only `read_text` of a `.md` in the new package — keep it
  that way, so exactly one file needs the boundary-test allowlist.

- [ ] **Step 3: Register the package in all five places**

  - `pyproject.toml` `layers`: `nora.instructions` sits **below `nora.language`** so
    `nora.doctrine`, `nora.language` and `nora.agent` can all reach it. Put it on the
    `nora.skills` line as an independent sibling: `"nora.skills | nora.instructions"`.
  - `pyproject.toml` "only app and `__main__` read config": add `"nora.instructions"` to
    `source_modules`. The directory comes in as a `Path` from composition.
  - `pyproject.toml` "evals may only reach `nora.app` and `nora.testing`": add
    `"nora.instructions"` to `forbidden_modules`, per that contract's own comment that a
    new subpackage needs a line.
  - `ARCHITECTURE.md`: one package-table row, or
    `tests/test_architecture_doc.py::test_architecture_doc_covers_every_package` fails.
  - `tests/test_boundaries.py::test_doctrine_read_boundary`: add
    `PKG / "instructions" / "frontmatter.py"` to `allowed`, with a comment saying why —
    it is the *generic* reader the doctrine's own loader now delegates to, and the
    boundary this test protects (doctrine files have a single reader) is unchanged.

- [ ] **Step 4: Re-express `nora/doctrine/loader.py` on the core**

  `parse_section` keeps its signature and its return type and delegates the skeleton;
  `DoctrineStore` keeps its constructor, `get()`, and `reload_failures` **unchanged** —
  every construction site (`nora/app/composition.py:553`, `nora/app/offline.py:49`,
  `nora/web/api.py:125`, `nora/app/fire.py:351`, `evals/harness.py:265`,
  `evals/run.py:337`, `tests/integration/harness.py:355`, `tests/test_tools.py:69`,
  `tests/test_health.py`) must remain untouched. If one has to change, the carve was
  too wide (see the abort condition).

- [ ] **Step 5: Prove nothing moved**

  ```bash
  uv run pytest tests/test_doctrine_loader.py tests/test_doctrine_reload.py \
                tests/test_doctrine_assembly.py tests/test_doctrine_content.py \
                tests/test_doctrine_audiences.py tests/unit/test_instructions_*.py -q
  ```
  Expected: all green, **with no edits to any of the four doctrine test files.** Task
  1.1's golden is the load-bearing one.

- [ ] **Step 6: Full gates and commit**

  ```bash
  uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
    && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence \
    && uv run lint-imports
  git add nora/instructions/ nora/doctrine/loader.py pyproject.toml ARCHITECTURE.md \
          tests/test_boundaries.py tests/unit/test_instructions_frontmatter.py \
          tests/unit/test_instructions_reload.py
  git commit -m "refactor(instructions): extract the shared frontmatter/reload core"
  ```

---

### Task 1.3: `DreamerInstructions` — the Dreamer's own store

One doctrine, many objectives, one hash over the **assembled** result (doctrine body +
objective body, in the order the runtime assembles them). Substrate §4.3 is explicit
that this is `doctrine_sha256`'s shape one layer down and that `objective_sha256` was
considered and rejected: it names half the input, so a doctrine edit that changed every
adaptor's behaviour at once would leave no trace anywhere.

Code only in this task — no shipped files yet, so it is tested over `tmp_path` and
changes nothing at runtime.

**Files:**
- New: `nora/instructions/dreamer.py`
- Test: `tests/unit/test_dreamer_instructions.py`

**Interfaces:**
- Produces: `DreamerInstructions(dir: Path, *, reload: str = "mtime", strict: bool =
  True, metrics: Metrics)`, with
  `for_objective(name: str) -> InstructionSet`.
- Produces: `InstructionSet(text: str, sha256: str, versions: dict[str, int])` —
  `text` is what the runtime hands to `system_prompt=`; `sha256` is
  `instruction_sha256`; `versions` is `{"dreamer": 3, "room-language": 1}`, the shape
  `doctrine_versions={…}` already logs on the tag path.
- Frontmatter required keys (substrate §4.1): `name`, `kind`, `version`, `applies_to`,
  `description`, `change_policy`. **No `order`, no `authority_above/below`, no
  `not_here`** — those are doctrine-specific and the doctrine's gates do not transfer
  (substrate §5). `kind` here is descriptive: `dreamer-doctrine` or `objective`.

- [ ] **Step 1: Write the failing tests**

  Over `tmp_path`: the assembled text contains the doctrine body then the objective body
  in that order; the sha changes when **either** body changes (the rejected
  `objective_sha256` would not — assert it explicitly, this is the design's own
  argument); an unknown objective name raises rather than silently assembling the
  doctrine alone; a second doctrine file in `doctrine/` is a startup error (there is
  exactly one); `versions` carries both; an edit + a bumped mtime is picked up without
  reconstruction; a broken edit keeps the last good set and counts a failure.

- [ ] **Step 2: Implement over `nora.instructions.frontmatter` + `.reload`**

  Declare the failure counter in `nora/obs/metric_specs.py`
  (`nora_dreamer_instruction_reload_failures_total`) — `Metrics.inc` on an undeclared
  name raises `KeyError`.

- [ ] **Step 3: Gates and commit**

  ```bash
  uv run pytest tests/unit/test_dreamer_instructions.py -q
  git commit -m "feat(instructions): the Dreamer's doctrine + objective store"
  ```

---

### Task 1.4: Author the Dreamer's doctrine — `nora/dreamer/doctrine/dreamer.md`

**This is new text, not a re-partition, and it is the one task in this plan whose
deliverable is prose.** It is written now, before the runtime, so it is reviewed as
text on its own merits rather than alongside a loop (substrate §3.1's argument about
the first objective, applied to the doctrine).

Content, per substrate §2.1 — what is true of **every** Dreamer run:

- it is a **read-only research agent**: it may look, it may not act, and there is no
  verb here that speaks to anybody or writes anything;
- what to do with a source that **does not cover** the claim — R-27 one layer down, and
  the single check that would have prevented the whole `2026-08-20-reflection…` §1.1
  cascade: *a source that does not mention something has not told you it is false*;
- what counts as evidence, and that a source must be **on-topic for the claim** it is
  being used against;
- when to stop;
- that **«I could not check» is a result to report, not a gap to fill**.

Constraints on the writing:

- `change_policy: operator-only`. This text steers a component that will hold
  `web_fetch`; it is not an agent-editable file.
- **Portrait, not rulebook** — the standing house position (DEC-96, and the doctrine
  memory note: every concrete example becomes a CLOSED SET; positions generate, lists
  close). Do not write an enumeration of forbidden tools; write the position that makes
  the enumeration unnecessary, and let Task 2.1's derived frozenset be the *mechanism*.
- **The doctrine's gates do not run on this file** (substrate §5) — no RFC-2119
  register check, no kind boundaries, no authority graph. Say so in the file's own
  `description` so a reader does not assume a gate that is not there.

**Files:**
- New: `nora/dreamer/doctrine/dreamer.md`
- Test: `tests/unit/test_dreamer_instructions.py` (a shipped-file test, the shape
  `tests/test_doctrine_content.py` uses for the real doctrine)

- [ ] **Step 1: Write the file**, frontmatter first (`name: dreamer`,
  `kind: dreamer-doctrine`, `version: 1`, `applies_to: [dreamer]`, `description`,
  `change_policy: operator-only`).

- [ ] **Step 2: Add the shipped-corpus test** — the file parses in strict mode, its
  `change_policy` is `operator-only`, and the tree contains **exactly one** doctrine
  file. Nothing asserting its wording: this is prose, and a test that pins a sentence
  is a test that has to be edited every time the sentence improves.

- [ ] **Step 3: Commit**

  ```bash
  git add nora/dreamer/doctrine/dreamer.md tests/unit/test_dreamer_instructions.py
  git commit -m "feat(dreamer): the Dreamer's own doctrine — a read-only research agent"
  ```

---

### Task 1.5: §7.1 step (a) — lift the three constants verbatim

Substrate §7.1's two-step ship gate, first half: **lift each constant into its artifact
file verbatim, with a named test comparing the loader's output to the current constants
byte-for-byte.** No wording change of any kind. A change that both moves and improves
the text cannot be told apart from a substrate bug.

The three, from substrate §1.1:

| constant | file | becomes |
|---|---|---|
| `nora/language/observer.py:86` `_PROMPT_PREAMBLE` | `nora/dreamer/objectives/room-language.md` |
| `nora/language/observer.py:133` `_VERIFICATION_PROMPT_TEMPLATE` | `nora/dreamer/objectives/room-language-verification.md` |
| `nora/language/exchange_observer.py:45` `_PROMPT_PREAMBLE` | `nora/dreamer/objectives/room-exchange.md` |

**The verification prompt is not a third adaptor.** It is the known-entity check the
2026-08-20 known-entity design added inside the room-language extractor. It gets its
own file here purely so the lift stays verbatim and mechanical; Task 3.2's re-partition
is where it very likely stops existing at all, because "is this an established public
thing" is a *question a research loop asks with its own tools*, not a second one-shot
prompt. Do not pre-empt that here.

**Files:**
- New: three files under `nora/dreamer/objectives/`
- New: `tests/fixtures/dreamer_objectives_pre_move/{room-language,room-language-verification,room-exchange}.txt`
- Modify: `nora/language/observer.py`, `nora/language/exchange_observer.py`
- Modify: `nora/app/composition.py`
- Test: `tests/unit/test_language_observer.py`, `tests/unit/test_exchange_observer.py`,
  `tests/unit/test_dreamer_instructions.py`

**Interfaces:**
- Produces: `LexemeObserver(model, *, objective: _ObjectiveSource, …)` and the same on
  `ExchangePatternObserver`. `_ObjectiveSource` is a **documented structural type**
  (`def body(self, name: str) -> str`), not a `nora.domain.ports` Protocol — following
  `observer.py`'s own `_ModelClient` precedent and its stated reason: a shape with one
  consumer does not earn a shared-port contract suite. It is a *source*, not a string,
  because the text hot-reloads and the observer must re-read it per tick.

- [ ] **Step 1: Copy the constants out, byte for byte**

  Two copies of each: the objective file's **body** (after the frontmatter) and a
  fixture `.txt` under `tests/fixtures/dreamer_objectives_pre_move/`. The fixture is the
  permanent record of what the pre-move text was; it is deleted, deliberately and
  visibly, by the re-partition tasks (3.2, 3.4) and by nothing else.

  Watch for: the constants are Python implicit-concatenation with no trailing newline
  discipline, and `_VERIFICATION_PROMPT_TEMPLATE` contains `{surface_form}` / `{sense}`
  / `{results}` **and literal doubled braces `{{"known_entity": …}}`** for `str.format`.
  Decide the escaping once, in the loader-facing direction: the file stores the text as
  the model must see it, and whatever `_build_verification_prompt` does with braces
  stays in Python. State the choice in the file's frontmatter `description`.

- [ ] **Step 2: Write the byte-identical test**

  ```python
  @pytest.mark.parametrize("objective", [
      "room-language", "room-language-verification", "room-exchange",
  ])
  def test_the_objective_file_is_the_pre_move_constant_byte_for_byte(objective):
      """Substrate spec §7.1 step (a). The re-partition (step b) is Tasks 3.2/3.4
      and deletes the fixture it is measured against — until then, ANY edit to
      these bodies is a substrate bug, including an improving one.
      """
      shipped = DreamerInstructions(DREAMER_DIR, reload="startup", metrics=...)
      before = (PRE_MOVE / f"{objective}.txt").read_text(encoding="utf-8")
      assert shipped.objective_body(objective) == before
  ```

  Run it against the files: PASS. Change one character in an objective file: FAIL.
  Verify the failure, then revert.

- [ ] **Step 3: Inject the objective source into the two observers**

  Replace the module-level constant read in `_build_prompt` /
  `_build_verification_prompt` with a read through the injected source. Keep both
  functions' signatures otherwise; keep every parse/validation path untouched — this
  task moves *where the text comes from* and nothing else.

- [ ] **Step 4: Wire composition**

  `nora/app/composition.py` builds one `DreamerInstructions` from `cfg.dreamer.dir`
  (Task 1.6 adds the config) and passes it into both observers, beside the existing
  `_LanguageModelClient` and `search_client`. Same shape, same place.

- [ ] **Step 5: Prove the prompt did not move**

  The existing observer unit tests capture the prompt the fake model receives; add one
  per observer asserting the built prompt **starts with the pre-move fixture text**.
  Then:

  ```bash
  uv run pytest tests/unit/test_language_observer.py tests/unit/test_exchange_observer.py \
                tests/unit/test_dreamer_instructions.py -q
  ```

- [ ] **Step 6: Gates and commit**

  ```bash
  git add nora/dreamer/objectives/ tests/fixtures/dreamer_objectives_pre_move/ \
          nora/language/observer.py nora/language/exchange_observer.py \
          nora/app/composition.py tests/unit/
  git commit -m "refactor(dreamer): lift the three prompt constants into objective files, verbatim"
  ```

---

### Task 1.6: Config, bind mount, package data

Three things that make the tree real to the deployment, and the reason substrate §4.1
says bind-mounting matters more here than for the doctrine: the Dreamer has the slowest
loop in the system, so an edit-and-next-tick cycle instead of a rebuild is the
difference between tuning an objective and not tuning it.

**Files:**
- Modify: `nora/config.py` (`DreamerConfig`), `config.toml`, `config.example.toml`,
  `docker-compose.yml`, `pyproject.toml`, `nora/app/composition.py`
- Test: `tests/test_config.py`, `tests/test_deploy_assets.py`, `tests/test_env_template.py`

**Interfaces:**
- Produces: `[dreamer]` — `dir: Path` (default `nora/dreamer`),
  `reload: Literal["mtime","startup"]`, `strict: bool`. Modelled field-for-field on
  `DoctrineConfig` (`nora/config.py:1204`), including the `_resolve_paths` validator
  that resolves `dir` relative to the config file.
  **`enabled` is NOT in this task** — Phase 1 has nothing to enable. It arrives in
  Task 2.7 with the runtime, and it ships `false`.

- [ ] **Step 1: `DreamerConfig`**, whole-section-optional with every field defaulted
  (the `[route]`/`[presence]`/`[language]` shape), so a `config.toml` written before
  this section existed keeps loading under `extra="forbid"`.

- [ ] **Step 2: Bind mount**, `docker-compose.yml` beside the doctrine's, with the same
  comment shape:

  ```yaml
  # Read-only so edits on the host (Dreamer instruction hot-reload, mtime-based)
  # are visible inside the container without a rebuild or restart.
  - ./nora/dreamer:/app/nora/dreamer:ro
  ```

- [ ] **Step 3: Package data**, `pyproject.toml` `[tool.setuptools.package-data]` under
  the `nora` key, beside `"doctrine/*.md"`:
  `"dreamer/doctrine/*.md"`, `"dreamer/objectives/*.md"`. The tree has **no
  `__init__.py`** and must not get one — it is data, not a package. Add a test beside
  `tests/test_deploy_assets.py::test_migrations_are_declared_as_package_data`, which
  exists for exactly this failure mode.

- [ ] **Step 4: Template drift**, `config.example.toml` gets the section and
  `tests/test_env_template.py` covers it (A8's drift gate already exists — use it).

- [ ] **Step 5: Gates and commit**

  ```bash
  git commit -m "feat(dreamer): [dreamer] config, bind mount, package data"
  ```

---

### Task 1.7: Stamp `instruction_sha256` on every row an adaptor writes

**BLOCKED on Phase 0b.** Do not start until the four columns are on `master`; re-read
the live table definitions first rather than trusting this plan's description of them.

**DECISION GATE D5 — `model` alongside `instruction_sha256`?** Answer it before writing
the code. The argument for yes (substrate §6.2): the instructions are one input, the
model is another, `[language].model` has changed before, and **D2 will change it
again** — rows written either side of that change should be distinguishable. The
argument for waiting: it is a second column and a second migration on four live tables.
A backfill is impossible either way, which is the whole reason the column exists. If the
answer is yes, this task grows a hand-written migration (`0023_…`) and a
`scripts/gen_pg_baseline.py` regeneration; if no, record the refusal in the commit
message so the next reader knows it was asked.

**Files:**
- Modify: `nora/infra/sql/language.py`, `nora/infra/sql/exchange.py` (the append paths)
- Modify: `nora/language/consolidator.py`, `nora/language/exchange_consolidator.py`
  (thread the stamp from the observer's run to the append)
- Modify: `nora/app/runtime.py` (the tick log line)
- Test: `tests/contract/test_room_consolidator_contract.py`,
  `tests/contract/test_exchange_consolidator_contract.py`, `tests/unit/test_runtime.py`

**Interfaces:**
- Produces: `ObservationResult.instruction_sha256: str` — the hash of the assembled set
  that produced *these* candidates, read off the `InstructionSet` the observer used for
  this call. Threaded to `append_observations` and written on every row.
- `room_portrait` stays **out**, for substrate §4.3's narrower reason: its objective is
  executed in code, no instruction text governed the row, and stamping a hash of text
  that did not produce the output is provenance bound to the wrong property — the exact
  defect the column exists to remove. `lexeme_basis`/`exchange_basis` already answer its
  provenance question, and those rows now carry their own stamps, so the chain resolves.

- [ ] **Step 1: Named test per adaptor, first**

  In each consolidator's contract suite: after a `consolidate` that appends, **every**
  new row carries a non-empty `instruction_sha256`, and it equals the hash the
  instruction store reports for that objective. Then: edit the objective body, run
  again, and assert the new rows carry a **different** hash from the old ones — that is
  the property the column exists for, and a test that only checks non-emptiness would
  pass against a hardcoded constant.

- [ ] **Step 2: Thread the stamp** — observer → result → consolidator → store. No
  global, no module-level cache: the value belongs to the run.

- [ ] **Step 3: The tick log line** (substrate §4.4)

  `dream_loop` logs its instruction versions the way a tag start logs
  `doctrine_versions={…}` (`nora/engagement/pipeline.py:362`): the Dreamer doctrine's
  version plus each objective version that ran on that tick, one line, so a trace ties
  to a revision without opening the database.

- [ ] **Step 4: Schema parity + gates**

  ```bash
  uv run pytest tests/infra/test_schema_parity.py -q     # NOT alembic check
  uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
    && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence \
    && uv run lint-imports
  ```

- [ ] **Step 5: Commit**

  ```bash
  git commit -m "feat(dreamer): every adaptor row carries the instruction_sha256 that wrote it"
  ```

---

### Task 1.8: Phase 1 close — live hot-reload verification, fragment, decision entry

- [ ] **Step 1: Verify hot-reload on the running deployment**

  The same check `reload = "mtime"` gets on the doctrine, and the one substrate §7.5
  requires. Read the state back — never claim it:

  ```bash
  docker exec nora python -c "import pathlib;print(pathlib.Path('/app/nora/dreamer/objectives/room-language.md').stat().st_mtime)"
  # edit the objective file on the host, in the worktree, then:
  docker exec nora python -c "..."      # mtime moved inside the container
  docker exec nora tail -n 50 /app/data/nora.log | grep instruction_versions
  ```

  **`docker logs nora` is always empty** — read `/app/data/nora.log` through
  `docker exec`, or you will conclude a code path never ran.

  A bind-mount caveat that has bitten this repo: an atomic write changes the file's
  **inode**, and for a single-file mount that breaks the mapping. `nora/dreamer` is a
  **directory** mount, which is why it survives an editor's atomic save the way
  `nora/doctrine` does — but verify it rather than assuming it, on the real deployment,
  with the real editor.

- [ ] **Step 2: Release fragment** — `type: "feature"` with an honest `limitations`
  entry saying the objectives are still one-shot prompts and the runtime is Phase 2.

- [ ] **Step 3: Decision-index entry** (re-check the next free DEC; DEC-124 was the max
  at plan time): *the Dreamer's model-facing text is a versioned, hashed, hot-reloading
  artifact, and every row it writes records which instruction set produced it.*

- [ ] **Step 4: Full gate sweep, then commit**

  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  uv run python scripts/check_release_fragments.py --base origin/master
  uv run python scripts/render_release_notes.py
  ```

---

# PHASE 2 — the Dreamer runtime

**What Phase 2 ships:** an agent that can look and cannot act, with a bounded loop, a
per-adaptor budget, a screened write path, and a loop that schedules per adaptor so an
adaptor keyed on something other than a scope can join it. It ships **disabled**
(`[dreamer].enabled = false`) and changes nothing about the live Dreamer until Phase 3.

**Why this is the risky phase, stated plainly:** the existing agent machinery is welded
to the tag path in three places that are not obvious until you try to build a second
agent —

1. `create_agent(...)` **requires a `ReplyGuard`**, whose `terminal` parameter has no
   default because a guard that ends no turn is the ~25s runaway DEC-21 fixed;
2. `create_model_provider(...)` hardcodes `tool_choice=FIRST_CALL` (`"required"`),
   whose own docstring calls it load-bearing — and **it is load-bearing for a turn that
   must end in `send_reply`**. The Dreamer has no terminal verb at all: its loop ends
   when the model *stops calling tools* and returns its structured result, which under
   `"required"` can never happen;
3. the helper that reads the model's final authored text (`_final_text`) lives in
   `nora/app/agent_runner.py`, and `nora.agent` may not import `nora.app`.

None of the three is in any spec. Tasks 2.3 and 2.4 exist because of them.

---

### Task 2.1: The read/act split — one field, one derived subset

The keystone §4 says this is "one more field and one more derived subset, not a new
mechanism", and that is right — with one correction. The registry's derived subsets
(`terminal_tools()`, `cacheable_tools()`, `live_only_tools()`, `web_tier_tools()`) all
read **`NativeTool`** in `nora/agent/tools.py:123-197`, not `ToolSpec` in
`nora/agent/ports.py`. `ToolSpec` carries neither `cacheable` nor `source_tier`; the
keystone's §4 cites the wrong type. **The field goes on `NativeTool`.**

`terminal` is close but not sufficient, exactly as the keystone says: it marks
turn-*ending* verbs (`send_reply`, `send_gif`), and `remind`/`send_to`/`make_artifact`
act without being terminal.

**And the keystone's act-verb list is incomplete.** It names six —
`send_reply`, `send_to`, `send_gif`, `remind`, `cancel_reminder`, `make_artifact`. The
live table also carries `create_event`, `update_event`, `rsvp_event` and
`cancel_event`, which write to the Events store and are act verbs by the identical
criterion. **This is why the set must be derived and never hand-listed** — a plan that
copied the spec's six would have handed the Dreamer four write verbs. Say so in the
field's docstring; it is the best available argument for the mechanism.

**Files:**
- Modify: `nora/agent/tools.py` (`NativeTool`, and one flag per row of `NATIVE_TOOLS`)
- Modify: `nora/agent/registry.py` (`read_only_tools()`)
- Test: `tests/unit/test_tool_registry.py`

**Interfaces:**
- Produces: `NativeTool.read_only: bool = False` — **fail-closed default**, the same
  conservative direction `terminal` takes ("a tool wrongly marked terminal truncates the
  turn"; a tool wrongly marked read-only hands a background agent a write).
- Produces: `ToolRegistry.read_only_tools() -> frozenset[str]` — a `frozenset` because
  membership is the whole question and there is no per-tool value to carry, following
  `terminal_tools()`/`cacheable_tools()` rather than the tuple-returning pair whose
  consumers render a sentence.
- **`ToolSpec` does not grow the field.** `_spec()` (`tools.py:1044`) copies four
  declared fields into `ToolSpec`, and the Dreamer selects its tools at *build* time
  from `NATIVE_TOOLS`, so nothing downstream needs it. Adding it there would be a second
  copy of the same fact with nothing keeping the two in step.

- [ ] **Step 1: Write the failing tests**

  ```python
  def test_no_act_verb_is_ever_read_only():
      """The hard line (keystone §4): the Dreamer may look and may not act.

      Hand-listed HERE, in the test, and derived in the code — the opposite of the
      pre-DEC-29 catalogue. The spec's own list of act verbs was four short (the
      events family), which is the argument for deriving the set rather than
      writing it down twice.
      """
      acts = {"send_reply", "send_to", "send_gif", "remind", "cancel_reminder",
              "make_artifact", "create_event", "update_event", "rsvp_event",
              "cancel_event"}
      assert acts & ToolRegistry().read_only_tools() == frozenset()

  def test_every_registered_tool_declares_whether_it_only_reads():
      """A tool that neither reads nor acts does not exist; `skills` is the one
      exception (plugin=True, an activation, built by nobody here)."""

  def test_the_dreamer_tool_set_is_the_keystone_s_list():
      """recall_lookups, media_fetch, load_transcript, about, ask_memory,
      lookup_person, search_memory, group_members, web_search, web_fetch, now,
      calculate. Pinned so a tool that ships read_only by omission shows up as
      MISSING FROM THE DREAMER rather than as a leak."""
  ```

  The third test is what makes the fail-closed default safe: a new read tool that
  forgets the flag is excluded from the Dreamer, and the test names it.

- [ ] **Step 2: Add the field and flag the table**

  One `read_only=True` per read row. Work down the table in order; the classification is
  "does calling this change anything outside the turn". `search_gifs` reads; `send_gif`
  acts. `list_reminders`/`list_events` read; their siblings act. `version` reads.

  **`pulse_*` is in the keystone's tool set and is not in `NATIVE_TOOLS`** — the
  game-state family arrives through the MCP path (`registry.budgets(allowed_mcp=…)`,
  `nora/agent/mcp.py`), which is a different admission mechanism with no `NativeTool`
  row to flag. Stop and decide how an MCP tool declares read-only-ness; the honest
  interim is that the Dreamer's set is the **native** read-only subset and the MCP tier
  is excluded until it can declare the same field. Record whichever is chosen — do not
  let it default silently.

- [ ] **Step 3: `read_only_tools()`**, copying `cacheable_tools()` line for line.

- [ ] **Step 4: Gates and commit**

  ```bash
  uv run pytest tests/unit/test_tool_registry.py tests/test_guards_terminal.py -q
  git commit -m "feat(tools): tools declare whether they only read; the Dreamer's set is derived"
  ```

---

### Task 2.2: The adaptor and sink ports

Five fields, two verbs, and one thing the keystone does not mention.

**The two sink verbs are non-negotiable and the port's shape is the enforcement.**
`propose` and `supersede`; **no delete verb anywhere in the interface.** A single opaque
`write(result)` cannot express supersede, and a `delete` would make
«invalidate, never delete» a habit instead of a type. This comes from the fact-store
workstream and it constrains the port now even though the fact adaptor is Phase 4.

**Adaptors declare their own key.** Every existing adaptor's unit of work is a
`scope_id`; the fact adaptor's is a **workspace** (`Conversation.workspace`, DEC-105,
`nora/policy.py:83`). `_dream_once` hands every consolidator the same per-scope batch
off one shared watermark and cannot host a workspace-keyed adaptor unchanged.
`RoomPortraitSynthesizer` (`nora/app/runtime.py:352`) is the precedent for participating
with no watermark, lag or quiet condition at all.

**The thing the keystone does not mention: the unconditional rescore.** Today
`LanguageConsolidator.consolidate` rescores **even for an empty batch**, because decay
is time-driven and `dream_loop`'s 24h condition exists precisely to fade a dormant
room's lexicon — it arrives with `lag == 0`. None of the five adaptor fields has a place
for that. **Resolution, recorded here because the spec does not have one:** the runtime
calls `propose(key, ())` with an empty tuple when the adaptor ran and found nothing, and
the sink may act on it (that is where the rescore lives). An empty `propose` is
`nothing_found` — «I looked and there was nothing» — and is distinct from the runtime
**not calling propose at all**, which is `unavailable` — «I could not check». That is
R-27's three states expressed in the port, and it preserves today's decay behaviour
exactly.

**Files:**
- Modify: `nora/domain/ports.py`
- Test: `tests/contract/test_dream_adaptor_contract.py`,
  `tests/contract/test_dream_sink_contract.py`, `tests/test_ports.py`

**Interfaces:**

```
DreamAdaptor (Protocol, runtime_checkable, keyword-only)
  name:           str        # the watermark identity — see Task 2.8
  key_kind:       str        # "scope" | "workspace"
  uses_watermark: bool       # False for a fact-shaped adaptor (keystone §9.3)
  objective:      str        # the OBJECTIVE FILE NAME, not the text
  tools:          frozenset[str]   # tool NAMES; nora.language cannot import the
                                   # registry, so the runtime resolves them
  async inputs(*, key: str, batch: Sequence[TranscriptRow]) -> str
  async accept(*, key: str, payload: object) -> Sequence[Proposal]
  sink:           DreamSink

DreamSink (Protocol, runtime_checkable, keyword-only)
  async propose(*, key: str, proposals: Sequence[Proposal]) -> int   # rows written
  async supersede(*, key: str, target: str, because: str, source: str | None) -> int
  #   NO delete. Not on this port, not on any implementation of it.
```

- `objective` is a **name**, resolved by the runtime against `DreamerInstructions` —
  keeping `nora.language` free of file reading and of the instruction store entirely.
- `accept` is the adaptor's typed parse of an already-envelope-validated payload (Task
  2.5 draws the line between what the runtime validates and what the adaptor parses).
- `propose` returning a count preserves `_dream_once`'s existing `appended_total > 0`
  portrait trigger.

- [ ] **Step 1: Write both contract suites first**, each parametrised over a fake and
  (from Phase 3) a real implementation. The load-bearing tests:
  - `propose` is **idempotent** — the same proposals twice appends once, because the
    natural key says so. `tests/contract/test_room_consolidator_contract.py`'s own
    docstring makes this argument: *"a fake that simply appended would make this suite
    decorative."*
  - `propose(key, ())` returns 0 **and is a legal call**, not an error.
  - `supersede` on an absent target returns 0 and raises nothing.
  - `supersede` **retains** the row — a superseded row is still readable, with the time
    it stopped being true. This is the store constraint keystone §3.1 places on whatever
    the fact tier turns out to be; asserted here so it cannot be lost.
  - **No implementation of `DreamSink` has a delete method at all** — assert it
    structurally over `dir(sink)`. A rule stated in a docstring is not a rule.

- [ ] **Step 2: Declare the Protocols.** `tests/test_ports.py` enumerates every Protocol
  in `nora.domain.ports` automatically and fails on a positional parameter or a missing
  `@runtime_checkable`.

- [ ] **Step 3: Gates and commit**

  ```bash
  uv run pytest tests/contract/ tests/test_ports.py -q
  git commit -m "feat(domain): DreamAdaptor and DreamSink — propose and supersede, no delete"
  ```

---

### Task 2.3: `DreamGuard` — default-deny with no terminal verb

`create_agent` requires a `ReplyGuard`, and `ReplyGuard` is the tag path's guard: it
holds a `Deliverer`, it flips `tool_choice` to `auto` once a **terminal verb** has run,
and its `terminal` parameter has no default on purpose. The Dreamer has no terminal verb
and nothing to deliver.

**Do not reuse `ReplyGuard` and do not widen it.** Its own docstring records why
`terminal` may not default to `()` — a guard that ends no turn is the runaway DEC-21
fixed — and that reasoning is about a turn under `tool_choice="required"`. The Dreamer's
loop is a different shape and needs a different guard, not a weakened one.

What `DreamGuard` keeps from `ReplyGuard`: **R-7 default-deny** (a tool absent from
`budgets` is vetoed — this is what stops the model inventing one) and the per-tool call
ceiling. What it drops: the deliverer, terminality, the `tool_choice` relaxation, the
forced-first-tool pin, DEC-93's recovery.

**Files:**
- Modify: `nora/agent/guards.py`, or a new `nora/agent/dream_guard.py` if `guards.py`'s
  responsibilities are already at their limit — judge at implementation time against
  "one stage, one module, one public entry point", which is about responsibilities, not
  lines.
- Test: `tests/unit/test_dream_guard.py`

**Interfaces:**
- Produces: `DreamGuard(*, adaptor: str, budgets: Mapping[str, int], metrics: Metrics)`
  — a `HookProvider` whose `evaluate` is pure and SDK-free, the property `ReplyGuard`
  keeps and the reason its rules are testable without strands.

- [ ] **Step 1: Failing tests** — a tool outside `budgets` is vetoed (R-7); a tool at
  its ceiling is vetoed; an admitted call that never runs consumes no budget (DEC-33's
  in-flight reservation — if that shape is reused from `ReplyGuard` it must be reused as
  *code*, not re-derived); vetoes are counted; **no terminal verb exists and the guard
  never ends a turn.**

- [ ] **Step 2: Implement.** Keep `evaluate` pure; the hook adapter is a thin
  translation layer (RK-2).

- [ ] **Step 3: Commit**

  ```bash
  git commit -m "feat(agent): DreamGuard — R-7 default-deny for a loop with no terminal verb"
  ```

---

### Task 2.4: Make `nora/agent/factory.py` able to build a second agent

Three edits, each small, each with a live-path regression risk the existing tests cover.

**Files:**
- Modify: `nora/agent/factory.py`, `nora/app/agent_runner.py`
- Test: `tests/test_factory.py`, `tests/test_agent_runner_stop_reason.py`

**Interfaces:**
- `create_model_provider(..., tool_choice: str = FIRST_CALL)` — **the default is
  unchanged**, so every existing call site keeps `"required"` and the tag path is
  byte-identical. The Dreamer passes `"auto"`, and the docstring says why: the
  `"required"` argument is about a turn that must end in `send_reply`; a loop whose
  terminal condition is the model declining to call anything cannot use it.
- `create_dream_agent(*, model, system_prompt, tools, guard: DreamGuard,
  trace_attributes, retry, agent_name="dreamer") -> Agent` — a sibling of
  `create_agent`, in the same module, because this is the only module allowed to
  construct Strands objects. No `pre_fill`, no `skills_dir`, no `tool_call_log`; the
  same `session_manager=None` / `NullConversationManager()` posture.
- `final_text(result) -> str` promoted from `nora/app/agent_runner.py:_final_text` into
  `nora/agent/` and imported back by `agent_runner`. `nora.agent` may not import
  `nora.app`, and the Dreamer needs exactly this defensive extraction — including its
  documented posture that a renamed SDK field degrades to an empty result rather than
  raising into a caller that otherwise succeeded.

- [ ] **Step 1: Failing tests** — `create_model_provider` still emits `"required"` by
  default (pin it: this is the thing that must not move); it emits `"auto"` when asked;
  `create_dream_agent` produces an agent with no terminal verb in its tool list and no
  `AgentSkills` plugin; two dream agents share no state (the shape
  `test_two_agents_share_no_state` already uses).

- [ ] **Step 2: Implement, then re-run the whole factory and runner suite**

  ```bash
  uv run pytest tests/test_factory.py tests/test_agent_runner_stop_reason.py \
                tests/test_model_per_tag.py tests/test_guards_replyguard.py -q
  ```

- [ ] **Step 3: Commit**

  ```bash
  git commit -m "refactor(factory): a second agent constructor, and tool_choice as a parameter"
  ```

---

### Task 2.5: The runtime — `nora/agent/dreamer.py`

One module, one public entry point: **run one adaptor for one key, and return a
three-state outcome.**

```
adaptor declares an output schema
  -> agent researches with read-only tools
  -> returns a structured result
  -> runtime VALIDATES it against the schema
  -> runtime SCREENS it (Task 2.6)
  -> runtime WRITES it to the sink
```

**There is no write tool and there must not be one.** A `write_fact` tool is an act verb
pointed at the database instead of at the group, and it would defeat the screen: if the
model writes directly there is no sink left to screen at. The model proposes; code
disposes — the same asymmetry the existing consolidators already have, where
`LexemeObserver` extracts and `LanguageConsolidator` persists.

**Validation, and a judgement this plan makes rather than defers:** `jsonschema` is not
a dependency and this plan does not add one (`uv add` would be the alternative, and a
new runtime dependency for one call site is not proportionate). The line is drawn where
the existing code already draws it: **the runtime validates the envelope** — the
response parses as JSON, is an object, carries the declared top-level key, is under a
size cap, and every item is a mapping — and **the adaptor's `accept` does the typed
parse**, exactly as `_parse_observations`/`_parse_item` do today, including their
discipline that a malformed item is dropped silently rather than raising. Record this in
the module docstring; it is the sort of decision that gets re-litigated by whoever adds
the fourth adaptor.

**Files:**
- New: `nora/agent/dreamer.py`
- Test: `tests/unit/test_dreamer_runtime.py`

**Interfaces:**
- Produces: `DreamerRuntime(*, models, instructions: DreamerInstructions,
  registry: ToolRegistry, budgets: Mapping[str, AdaptorBudget], retry, metrics)` with
  `async run(*, adaptor: DreamAdaptor, key: str, batch: Sequence[TranscriptRow])
  -> DreamOutcome`.
- Produces: `DreamOutcome` — `status: "ok" | "nothing_found" | "unavailable"`,
  `written: int`, `instruction_sha256: str`, `stop_reason: str | None`. **R-27 at this
  layer, and the mapping is the point:** a model response that will not parse is
  `unavailable` («this batch was never actually read»), never `nothing_found`, because
  `_dream_once` must not advance the watermark past a batch nobody read. That is exactly
  what `ExtractionUnparseableError` protects today
  (`nora/language/consolidator.py`), and Phase 3 must not lose it — the outcome type is
  where it goes.
- Produces: `AdaptorBudget(turns, total_tokens, output_tokens, timeout_seconds)` — per
  adaptor, not one global ceiling: proving a fact is worth more calls than glossing a
  word, and a single number would starve the first or overpay for the second.

- [ ] **Step 1: Failing tests against a scripted model**

  - a well-formed return is validated, screened, written, and reported `ok` with the
    write count;
  - a return that parses but carries the wrong shape is `unavailable`, and **the sink is
    never called**;
  - a well-formed *empty* return is `nothing_found` and **`propose(key, ())` IS called**
    — the decay path, Task 2.2's resolution, asserted here;
  - each of the three limit stop reasons (`limit_turns`, `limit_total_tokens`,
    `limit_output_tokens`) is reported, not swallowed — Strands raises nothing and logs
    nothing when a cap binds, so `AgentResult.stop_reason` is the only channel;
  - a hung model call is bounded by `asyncio.wait_for` and reported `unavailable`.

- [ ] **Step 2: The bounded timeout is not optional**

  `nora/app/composition.py:145-164` records exactly why, from the last time this was
  gotten wrong: *"unlike a tag turn's timeout, a hung Dreamer call is QUIET"* —
  `dream_loop` marks the scope `running` and skips it every subsequent tick
  (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising, so the
  exponential backoff never engages and a hung provider is skipped forever rather than
  backed off. The wall clock wraps the **whole loop**, the way
  `nora/engagement/stages/invoke.py:121` and `nora/app/fire.py:845` each wrap theirs with
  their own population's number.

- [ ] **Step 3: Trace identity** — `agent_name="dreamer"` (or `dreamer:<adaptor>`), so a
  Dreamer run is findable in Langfuse as its own span rather than merged into Nora's.
  The provenance every claim about a run re-derives from is the trace; give it a name.

- [ ] **Step 4: Commit**

  ```bash
  git commit -m "feat(dreamer): the runtime — bounded read-only loop, validated return"
  ```

---

### Task 2.6: Screening before every sink write

**Adaptor output reaches the model.** `room_lexeme` renders into `<room_language>`,
`room_exchange` into `<room_exchange>`, `room_portrait` into `<room_portrait>`, and a
fact tier will render into its own block. Nora's identifier and coordinate screens
(R-18, R-28) sit on **her egress**; an adaptor writing to its sink is a second path into
the prompt that passes neither. With `web_fetch` in the tool set this matters more, not
less: the Dreamer reads attacker-influenceable pages and writes distillations of them
into a durable store that renders into every later turn. This is the audit's F14
(`room_language.sense` is an unbounded model-authored persistence channel, guarded today
only by a truncation and a plausibility reject) generalised — and it is why it is
settled once, here, rather than per adaptor later.

**Files:**
- New: `nora/agent/dream_screen.py`
- Test: `tests/unit/test_dream_screen.py`

**Interfaces:**
- Produces: `screen(proposals) -> tuple[kept, rejected]`, applied by the runtime
  immediately before `propose`. Reuses `nora.agent.egress.scan_identifiers` (E.164,
  ACI/PNI, key-shaped strings) and the coordinate predicate in `nora.agent.security`.
  **`scan_identifiers`, not `scan_reply`** — the model-name half of `scan_reply` matches
  nine ordinary English words and is only ever right for text addressed to the group;
  applied here it would silently drop legitimate lexemes.
- Behaviour: a screened-out proposal is **dropped, counted, and logged** — never
  silently discarded, and never fatal to the run. The run's other proposals still land.
  `nora_dream_screened_total{adaptor, reason}`.

- [ ] **Step 1: A named test per adaptor** — keystone §7 requires one per adaptor, and
  the reflection spec's §14.10 makes it non-optional for the fact adaptor: a proposal
  carrying a phone number never reaches the sink; one carrying a coordinate never
  reaches the sink; a benign proposal in the same batch does.

- [ ] **Step 2: Implement, declare the metric, wire it into the runtime.**

- [ ] **Step 3: Commit**

  ```bash
  git commit -m "feat(dreamer): screen every proposal before it reaches a sink"
  ```

---

### Task 2.7: Config and metrics for the runtime

**Files:**
- Modify: `nora/config.py`, `config.toml`, `config.example.toml`,
  `nora/obs/metric_specs.py`, `nora/app/composition.py`
- Test: `tests/test_config.py`, `tests/test_metrics_export.py`, `tests/test_composition.py`

**Interfaces:**
- `[dreamer]` grows: `enabled = false` (**ships OFF** — a new subsystem does not switch
  itself on, and every comparable subsystem here shipped inert), `model = ""` (empty
  means `[language].model`, which means today's tier and no silent change — **D2 is what
  eventually fills it**), and one budget block per adaptor:

  ```toml
  [dreamer.budgets.room_language]
  turns = 6
  total_tokens = 40000
  output_tokens = 4000
  timeout_seconds = 120
  ```

  The numbers are **unmeasured starting points and the comment must say so**, the way
  `[analytics]`'s `stay_radius_m` says `INHERITED, not measured`. Do not present a guess
  as a measurement.
- Metrics, appended to `metric_specs.py`'s tuples and never edited
  (`tests/test_metrics_export.py::DECLARED_NAMES`; `Metrics.inc` on an undeclared name
  raises `KeyError`): `nora_dream_run_total{adaptor, status}` (R-27's three states — a
  failed run is `unavailable`, never silently absent), `nora_dream_run_seconds{adaptor}`,
  `nora_dream_written_total{adaptor}`, `nora_dream_screened_total{adaptor, reason}`,
  `nora_dream_stop_reason_total{adaptor, reason}`.

- [ ] **Steps 1–3:** the config model; the metric declarations; composition wiring behind
  `cfg.dreamer.enabled`, with nothing constructed when off — the shape `language_store`
  and `portrait_synthesizer` already use. Gates. Commit.

---

### Task 2.8: DECISION GATE D3, then per-adaptor scheduling in `dream_loop`

**This is the task that touches live traffic. It is the one to review hardest.**

**D3 — per-adaptor failure isolation (keystone §9.4). Answer before writing code.**
The evidence pointing both ways is already in `nora/app/runtime.py`:

- *For isolation:* the portrait branch at the tail of `_dream_once` already has its
  **own** try/except, with a comment stating the reason — *"a portrait failure is
  independent of Plan 1/2's own health and must not trigger THEIR backoff"*. Under
  per-adaptor scheduling that argument applies to every adaptor pair.
- *Against, or at least not for free:* `_run_one`'s per-scope `except Exception`
  increments `failures[scope_id]` and drives the capped-exponential backoff whose whole
  purpose is to stop hammering an external model provider through an outage
  (non-negotiable 4). Isolating per adaptor without moving the backoff with it produces
  a loop that keeps calling a 429ing provider for the other three adaptors — worse than
  today, not better.

Whichever is chosen, **the backoff key must move with the isolation boundary.** Record
the decision in the commit message and in the decision-index entry.

**What happens to in-flight state — the migration hazard, and it is concrete.**
`_dream_once` reads and writes **one** watermark per scope under the hardcoded name
`"language"` and hands the same batch to every consolidator; `ExchangeConsolidator`
rides it and holds no cursor of its own (`nora/infra/sql/exchange.py`'s own module
docstring says so). Per-adaptor scheduling means per-adaptor watermarks. So:

- an adaptor that keeps the name `"language"` inherits the live cursor and nothing moves;
- an adaptor with a **new** name gets `watermark_for` → `0`
  (`nora/infra/sql/language.py:431` returns 0 for an unknown key) and re-reads the room
  from the beginning, `batch_limit=400` at a time, paying a model call per batch and
  re-deriving evidence that already exists. It is *safe* — the natural key makes the
  append idempotent — and it is expensive, slow, and will look like a bug.
- **Therefore: seed the new watermark rows from the shared `"language"` row before the
  new schedule ever fires.** One idempotent write per (adaptor, scope), verified by
  reading the rows back, as its own step with its own test.

**Files:**
- Modify: `nora/app/runtime.py`, `nora/app/composition.py`, `nora/__main__.py`
- Test: `tests/unit/test_runtime.py`

**Interfaces:**
- `dream_loop(..., adaptors: Sequence[DreamAdaptor] = (), runtime: DreamerRuntime |
  None = None)` — **additive**. The existing `consolidators` and `portrait` parameters
  stay and keep working; when `adaptors` is empty the loop is byte-for-byte today's.
  That is what makes Phase 3 a per-adaptor cutover instead of a big-bang switch.
- Key enumeration: the loop already holds `scopes()` (`nora/app/composition.py:670` —
  `conv.primary_id` for every allowlisted conversation). A workspace-keyed adaptor needs
  `workspaces()`, the same closure over `{conv.workspace for conv in
  allowlist.conversations}` (`nora/policy.py:166`). One callable per `key_kind`,
  resolved by the loop.
- `uses_watermark = False` adaptors get **no** watermark, lag, quiet or age condition —
  `RoomPortraitSynthesizer`'s existing posture — and therefore need their own fire
  condition, which is the adaptor's business and not `_dream_once`'s. Phase 3's two
  adaptors do not exercise this; it must still be *expressible*, or the fact adaptor
  cannot land without reopening this task.

- [ ] **Step 1: Write the failing loop tests** — an adaptor's failure does or does not
  touch the sibling's backoff (per D3); a scope still in flight is skipped, not
  re-entered (the existing guarantee, unchanged); the watermark advances **only after**
  the write commits and never after an `unavailable` outcome
  (`tests/unit/test_runtime.py::test_the_watermark_advances_only_after_observations_are_committed`
  already has the crash-shaped half — extend it with the `unavailable` shape); a
  workspace-keyed adaptor is ticked once per workspace, not once per scope.

- [ ] **Step 2: The watermark seeding step**, with a test that a newly-named adaptor
  starts at the shared cursor and **not** at 0.

- [ ] **Step 3: Implement, keeping `consolidators` working unchanged.**

- [ ] **Step 4: Full gates, then commit**

  ```bash
  uv run pytest tests/unit/test_runtime.py -q && uv run pytest -q
  git commit -m "feat(dreamer): dream_loop schedules per adaptor, on the adaptor's own key"
  ```

---

### Task 2.9: DECISION GATE D2 — which model does the Dreamer run

**Do not pick one. Measure.** The keystone (§9.1) names this open and notes the repo has
a measurement precedent for deciding rather than guessing; the tool-rename work
(DEC-58's method — only the identifier varies, n=16, and DECLINE on sibling leakage even
if self-routing holds) is the shape to copy.

Why it cannot be answered by reasoning: a research loop is a different workload from a
one-shot extraction. The one-shot tier was chosen for a single JSON completion nobody
waits on; the runtime's job is to read a page, notice what question it actually answers,
and search differently when it does not cover the claim — and the failure that motivated
this whole architecture happened **with search available**.

- [ ] **Step 1: State what a positive result looks like, before running anything.**
  *Never read an instrument's output as evidence without first checking it can measure
  the thing.* The candidate measure is the motivating case itself
  (`2026-08-20-reflection-reasoning-continuity-design.md` §1.1): given the KeenGamer
  result, then Polygon's *"Best tower order"* (silent on the Black Tower because it is
  out of that article's scope), does the loop distinguish **non-coverage from
  contradiction**? Reverting the Dreamer doctrine's non-coverage paragraph must turn it
  red.
- [ ] **Step 2: Run the same corpus across the candidate tiers**, at fixed instructions,
  varying only the model — the `instruction_sha256` from Phase 1 is what makes "fixed
  instructions" checkable rather than asserted.
- [ ] **Step 3: Record the verdict** in the decision index with its evidence, set
  `[dreamer].model`, and only then proceed to Task 3.5's cutover.

---

# PHASE 3 — migrate the two model-calling instances

**One adaptor at a time. Dual-pathed. The old path deleted last, after live evidence.**

Room-language goes first: it is the one with the live wrong row (`big walk` glossed as a
nighttime stroll, for a co-op puzzle video game), the one with a search port already
wired, and therefore the one where a research loop should show a visible difference.

---

### Task 3.1: The room-language adaptor

**Files:**
- New: `nora/language/adaptors.py`
- Modify: `nora/infra/sql/language.py` (satisfy `DreamSink`), `nora/app/composition.py`
- Test: `tests/contract/test_dream_sink_contract.py` (the real implementation joins the
  suite), `tests/unit/test_language_adaptor.py`

**Interfaces:**
- `RoomLanguageAdaptor` — `name="language"` (**the live watermark name; do not rename
  it**, see Task 2.8), `key_kind="scope"`, `uses_watermark=True`,
  `objective="room-language"`, `tools=frozenset({"web_search", "web_fetch", …})`,
  `inputs` = today's numbered-message rendering, `accept` = today's
  `_parse_observations`/`_parse_item`/`_extract` chain **moved, not rewritten**,
  `sink` = `SqlLanguageStore`.

- [ ] **Step 1: `SqlLanguageStore` satisfies `DreamSink`** — `propose` wraps
  `append_observations` and keeps the unconditional `rescore` (Task 2.2's resolution);
  `supersede` is where a retired lexeme goes. Join the contract suite: a port's second
  implementation without a shared suite is exactly what CLAUDE.md forbids.

- [ ] **Step 2: The adaptor**, moving the parse chain across unchanged. The evidence
  join in `_extract` — every returned candidate points at a row actually in the batch —
  is load-bearing and must move intact; fabricated provenance is worse than a missing
  entry.

- [ ] **Step 3: Wire it behind `[dreamer].enabled`**, beside the existing
  `LanguageConsolidator`, **not instead of it**. Off, the loop is today's.

- [ ] **Step 4: Gates and commit**

  ```bash
  uv run pytest -q && uv run lint-imports
  git commit -m "feat(dreamer): the room-language adaptor, beside the consolidator"
  ```

---

### Task 3.2: §7.1 step (b) — re-partition the room-language objective

**Its own commit, evaluated on its own.** Substrate §2.3: the ~9,500 characters split
three ways, and this is a **re-partition, not a relocation** —

- text true of **every** Dreamer run (be careful, what counts as evidence, do not assert
  what you could not check) → the **Dreamer doctrine**, once, where Task 1.4 already put
  it;
- text specifying the **return shape** → the adaptor's **output schema** field, which is
  structured and validated, not prose;
- what is left — what this adaptor is trying to find out → its **objective**.

This is where `room-language-verification.md` most likely stops existing: *is this an
established public thing* is a question a research loop asks with `web_search` and
`web_fetch`, not a second one-shot prompt with its own constant. Deleting it is a real
outcome of this task, not a failure of it — but it is a **behaviour change** and must be
argued in the commit message, not slipped in.

- [ ] **Step 1: Delete the pre-move fixture** for `room-language` (and, if it goes,
  `room-language-verification`) and the byte-identical test that guards it — **in the
  same commit as the re-partition.** A fixture that no longer describes the shipped text
  is worse than no fixture.
- [ ] **Step 2: Re-partition**, bumping the objective's frontmatter `version`.
- [ ] **Step 3: Show the change in output, not just in the file** — run the adaptor over
  a recorded batch before and after, and record both results in the commit message. A
  prompt edit whose effect nobody looked at is a prompt edit nobody can evaluate.
- [ ] **Step 4: Gates and commit.**

---

### Task 3.3: The room-exchange adaptor

Same shape as Task 3.1. `name="exchange"`; `SqlExchangeStore` satisfies `DreamSink`;
`accept` moves `ExchangePatternObserver`'s parse chain across including the closed-set
`act` validation against `EXCHANGE_ACTS`, which is what keeps this adaptor safe by
construction and must not be softened into a free-text field.

**Watermark note:** `exchange` has never had a watermark row of its own — it rides
`"language"`. Task 2.8's seeding step is what stops it re-reading every room from the
beginning. Verify the seeded rows exist **before** enabling this adaptor, by reading
`consolidation_watermark` back.

---

### Task 3.4: §7.1 step (b) for room-exchange

As Task 3.2. Delete its pre-move fixture in the same commit.

---

### Task 3.5: Live cutover, then delete the old path

**Gated on D2** (Task 2.9). Not before.

- [ ] **Step 1: Enable on one scope**, not four. The loop already ticks per scope; run
  the adaptor path on a single room and leave the rest on the consolidators.
- [ ] **Step 2: Watch, with the numbers named in advance** — `nora_dream_run_total` by
  status, `nora_dream_screened_total`, `nora_dream_run_seconds`, and the rows
  themselves. What to watch first is what `config.toml`'s own `[language]` comment
  already says about this subsystem: the block renders into **every** tag's prefill, so
  a bad lexeme is not one wrong reply, it is a standing instruction. A run that costs
  more than it did and produces the same glosses is a failed cutover, not a slow one.
- [ ] **Step 3: Widen to all four scopes.**
- [ ] **Step 4: Only then, delete the old path** — `LanguageConsolidator`,
  `ExchangeConsolidator`, `_LanguageModelClient`, and `dream_loop`'s `consolidators`
  parameter. If `RoomConsolidator` has no implementations left, it and its contract
  suite go with them; if `RoomPortraitSynthesizer` still uses the old shape, they stay
  and this step is partial — say which in the commit message.
- [ ] **Step 5: Read back what you claim.** `git log -1`, `docker exec` for the running
  code, the metric endpoint, `/app/data/nora.log` (**not** `docker logs nora`, which is
  always empty). Never claim a deploy without reading back the state.

---

### Task 3.6: Phase 3 close — decision index, acceptance rules, fragment, gate sweep

- [ ] **Step 1: Decision-index entries** (re-check the next free DEC): the
  runtime/adaptor split; the read/act line as a derived subset; the two-verb sink with
  no delete.
- [ ] **Step 2: Named acceptance-rule tests.** A new behavioural rule gets an entry in
  `design/09-decision-index.md` **and** a named test in
  `tests/test_acceptance_rules.py::RULE_TESTS`. Candidates: *the Dreamer holds no act
  verb and no write verb*; *nothing an adaptor writes reaches a sink unscreened*; *only
  the background pass invalidates — nothing deletes*.
- [ ] **Step 3: Release fragment** — `type: "feature"`, with the limitations stated
  honestly (the portrait adaptor is still the old shape; the fact adaptor is Phase 4;
  the budget numbers are unmeasured starting points unless Task 2.9 measured them).
- [ ] **Step 4: Final full gate sweep**

  ```bash
  uv run pytest -q
  uv run ruff check nora/ evals/ tests/
  uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
  uv run lint-imports
  uv run python scripts/check_release_fragments.py --base origin/master
  uv run python scripts/render_release_notes.py
  ```

---

## Risk and rollback

**The runtime is the risky part.** Phase 1 moves text and adds a column's value; Phase 3
is a per-adaptor cutover with the old path still standing beside it. Phase 2 is where a
running subsystem gets a new execution model.

| risk | why it is real | rollback |
|---|---|---|
| **A Dreamer loop that never terminates.** `tool_choice="required"` plus no terminal verb means the model must call *something* on every pass and nothing ends the turn. | `create_model_provider` hardcodes `"required"` today and its docstring calls it load-bearing. Under it the Dreamer's loop can only stop when a *limit* binds — a budget spent in full on every tick, four scopes, every 300s, with nobody waiting on it to notice. | Task 2.4's `tool_choice` parameter plus Task 2.5's turn cap and wall clock. Rollback is `[dreamer].enabled = false`; the consolidators are still wired until Task 3.5 Step 4. |
| **A quiet hang.** A hung provider call leaves the scope `running`, so every later tick is skipped and the backoff — which only triggers on an exception — never engages. | Measured and written up at `nora/app/composition.py:145-164`. It is the reason the one-shot client got a timeout at all. | The bounded `asyncio.wait_for` is non-negotiable in Task 2.5. |
| **A prompt-injected page becoming a standing instruction.** The Dreamer holds `web_fetch`; `room_language.sense` is unbounded model-authored text that persists 21+ days and renders into every subsequent turn. | Audit F14, and `render.py`'s own docstring: *"`escape()` … blocks tag forgery, not instruction text."* The live corpus is two rows, which is not evidence the channel is safe. | Task 2.6 screens every proposal before the write; a screen hit drops the item and counts it. Rollback for a bad corpus is a targeted `supersede`, never a delete. |
| **A re-read of the whole record.** A renamed adaptor starts at watermark 0. | `watermark_for` returns 0 for an unknown `(name, scope)` and `_dream_once` hardcodes `"language"` for the shared cursor. | Task 2.8's seeding step, verified by reading `consolidation_watermark` back before enabling. |
| **The loader extraction changes the live system prompt.** `nora/doctrine` assembles Nora's `system_prompt`, and its sha is the provenance every trace binds to. | 631 lines re-expressed under a bind mount that hot-reloads on mtime. | Task 1.1's golden assembly, captured **before** the refactor, plus Task 1.2's abort condition (>120 lines moved, or any doctrine test edited → revert and purpose-build). |
| **Two agents in one checkout.** | This repo has been bitten repeatedly; reviewers have watched files move under them mid-review. | A worktree per agent, and the Phase 0b coordination note at the top of this plan. |

**The one-line rollback per phase.** Phase 1 — revert the commit; the constants are
recoverable from `tests/fixtures/dreamer_objectives_pre_move/` until Tasks 3.2/3.4
delete them. Phase 2 — `[dreamer].enabled = false`; nothing is constructed and
`dream_loop` runs today's shape. Phase 3 — per adaptor, drop it from the `adaptors`
tuple; the consolidator beside it is still wired until 3.5 Step 4, and after that step
rollback is a revert. That is why the deletion is last, and why it is gated on live
evidence rather than on a green suite.

---

## Spec defects found while sequencing

Recorded rather than fixed silently, because each would have produced a wrong
implementation if a worker had followed the spec literally.

1. **The keystone's act-verb list is four verbs short.** §4 names `send_reply`,
   `send_to`, `send_gif`, `remind`, `cancel_reminder`, `make_artifact`. The live table
   also carries `create_event`, `update_event`, `rsvp_event`, `cancel_event`, which
   write to the Events store. A plan that hand-copied the six would have handed a
   background agent four write verbs — the strongest available argument for the spec's
   own rule that the set must be **derived, never hand-listed**.
2. **The keystone cites the wrong type for the new field.** §4 says *"`ToolSpec`
   (`nora/agent/ports.py:74-83`) already carries `budget`, `terminal`,
   `time_shiftable`, `cacheable`, `disclose_failure`, `timeout_seconds`"*. `ToolSpec`
   carries neither `cacheable` nor `source_tier`; those live on `NativeTool`
   (`nora/agent/tools.py:123`), and **all four derived subsets read `NATIVE_TOOLS`, not
   `ToolSpec`**. The field goes on `NativeTool`.
3. **`create_agent` cannot build the Dreamer, and no spec says so.** It requires a
   `ReplyGuard` (deliverer, terminal verbs, the `tool_choice` relaxation), and
   `create_model_provider` hardcodes `tool_choice="required"` — under which a loop with
   no terminal verb can never stop by itself. The keystone's §5 (*"the Dreamer runtime
   lives in `nora/agent/`, beside `factory.py`, which is the package already allowed to
   construct agents"*) reads as though the machinery is reusable as-is. It is not.
   Tasks 2.3 and 2.4 exist only because of this, and it is the largest piece of
   unplanned work the specs hide.
4. **`_final_text` is unreachable from `nora.agent`.** The defensive final-text
   extraction lives in `nora/app/agent_runner.py`, and the layer contract forbids
   `nora.agent` importing `nora.app`. Either it is promoted (this plan's choice) or the
   Dreamer duplicates it — and a second copy of a defensive parser is how the two drift.
5. **The five adaptor fields cannot express the unconditional rescore.** Today
   `consolidate` rescores even for an empty batch, because decay is time-driven and the
   24h fire condition exists exactly to fade a dormant room.
   objective/inputs/schema/tools/sink has no slot for it. Task 2.2 resolves it —
   `propose(key, ())` is a legal call meaning `nothing_found`, distinct from not calling
   `propose` at all, which is `unavailable` — but the specs do not, and a worker
   following them literally would have dropped decay.
6. **`ExtractionUnparseableError`'s guarantee has nowhere to go.** Today an unparseable
   model response *raises*, which is what stops `_dream_once` advancing the watermark
   past a batch nobody read. The adaptor contract returns a value, not an exception.
   Task 2.5's three-state `DreamOutcome` is where the guarantee lands; nothing in any
   spec says so, and losing it would consume messages permanently with nothing to retry.
7. **Substrate §2.3's re-partition cannot happen in Phase 1**, though §7.1 reads as
   though its two steps are adjacent. Two of its three targets — the output-schema field
   and the assembled doctrine+objective an agent loop consumes — are runtime artifacts.
   This plan does step (a) in Phase 1 and step (b) per adaptor in Phase 3, which is the
   only ordering that keeps §7.1's "one change cannot both move and re-partition" gate
   meaningful.
8. **`nora.language` cannot name a tool.** The adaptors live in `nora/language/`, the
   tool table lives in `nora/agent/`, and the layer contract forbids the import. The
   tool subset must therefore be **strings** resolved by the runtime — the same
   inversion `ToolRegistry.tool_names()` already performs for the doctrine loader. No
   spec mentions it.
9. **`pulse_*` is in the keystone's tool set and not in the tool table.** The game-state
   family arrives through the MCP path, which has no `NativeTool` row to carry a
   `read_only` flag. Task 2.1 stops and decides rather than assuming; the honest interim
   is that the Dreamer's set is the native read-only subset and the MCP tier is out.
10. **The audit's A9 names a function that does not exist.** `check_authority_chain` was
    renamed `check_authority_graph`, and acyclicity has already been added (the old name
    survives only in a docstring). Not this plan's work, but a reader arriving from the
    audit will grep for the wrong symbol.

---

## Self-review notes (carried in the plan, not stripped out)

- **Spec coverage.** Keystone §3 (runtime/adaptor split) → Tasks 2.2, 3.1, 3.3; §3.2
  (the return value is the write; no write tool) → Task 2.5; §4 (read/act) → Task 2.1;
  §5 (code location) → the File Structure block and the layer notes; §6 (per-adaptor
  budget + bounded timeout) → Tasks 2.5, 2.7; §7 (screening) → Task 2.6; §9.2/§9.3 →
  Task 2.8; §9.1/§9.4 → gates D2/D3. Substrate §4.1 → Tasks 1.4–1.6; §4.2 → Task 1.2
  (D1); §4.3 → Task 1.7; §4.4 → Task 1.7 Step 3; §7.1 → Tasks 1.5 (a) and 3.2/3.4 (b);
  §6.2/§6.5 → gates D5/D4. Reflection spec §5.3/§8.2/§14.10 shape Task 2.2's port and
  Task 2.6's screen and are otherwise Phase 4.
- **Tasks 2.8 and 3.5 are the two that are not copy-paste.** 2.8 rewrites the scheduling
  of a loop carrying live traffic and depends on D3; 3.5 is a production cutover whose
  success criterion is a judgement about output quality, not a green test. Budget real
  review time for both. Everything between 1.1 and 2.7 is ordinary, testable work.
- **Deliberate judgement calls no spec settles**, listed so a reviewer can overturn them
  cheaply: the shared core is a new `nora/instructions/` package rather than a module
  inside `nora/doctrine/` (the layer contract forces the question, and `nora.language`
  must be able to reach it); envelope validation in the runtime with the typed parse in
  the adaptor, and **no `jsonschema` dependency**; the objective source is a documented
  structural type rather than a `nora.domain.ports` Protocol, following `_ModelClient`'s
  stated precedent; `read_only` defaults `False` (fail-closed) with a pinned-set test
  rather than being a required field on 33 rows.
- **What this plan does not know**, each a read rather than a guess, and each named in
  the task that needs it: whether Phase 0b populated `instruction_sha256` from the
  inline constants or left the column unwritten; what the live `consolidation_watermark`
  rows look like today; and whether the MCP `pulse_*` tools can carry a declared field
  at all.
