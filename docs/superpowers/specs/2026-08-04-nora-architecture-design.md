---
kind: spec
title: "Nora — Architecture & Bootstrapping"
description: "Replace the POC structure with a pipeline+ports architecture: one process running N platform adapters, deterministic ambient ingest, stateless engagement, MCP for cold-path tools, and a standard-library infrastructure layer (SQLAlchemy Core/Alembic, pydantic, prometheus-client, structlog, OTLP→Langfuse), with mechanically enforced boundaries."
tags: [nora, spec, architecture, ports, pipeline, extensibility, mcp, langfuse]
timestamp: '2026-08-04'
revision: 2
supersedes: "design/08-stack-refactor-brief.md (library-swap-only scope)"
edges:
  - {dst: ../../../design/00-overview.md, rel: amends, provenance: asserted}
  - {dst: ../../../design/04-config-identity.md, rel: amends, provenance: asserted}
  - {dst: ../../../design/08-stack-refactor-brief.md, rel: supersedes, provenance: asserted}
---

# Nora — Architecture & Bootstrapping

> **Revision 2.** Revised after four independent audits (factual, extensibility,
> maintainability, migration-risk). Claims marked **[proven]** were verified by running
> code, not by reasoning. Findings that contradicted revision 1 are corrected in place;
> §12 records what changed and why, so the corrections are not silently absorbed.

## 1. Context

`design/08-stack-refactor-brief.md` scoped a library swap. That brief is correct but
insufficient: the libraries are the *infrastructure* layer, while every intended
extension axis is blocked by *structure*.

Nora is 2,535 lines in `nora/` (3,266 including `evals/`), with 48 test files and 453
collected tests. It is not bad code. It is **POC-shaped code**: the seams the design
documents promise exist as protocols but are not honoured by the wiring.

### What Nora is

Two paths, one boundary (`design/00-overview.md`):

- **Ambient path** — every message: parse → SQLite audit → Honcho feed. Deterministic.
  No agent loop, ever (D1).
- **Engagement path** — trigger fires → assemble context from the two stores → a fresh,
  stateless Strands agent → tools → reply → audit → send → feed back (D4).

SQLite is the audit record and transcript source. Honcho is the semantic store. Identity
is person-centric: one human is one Honcho peer across platforms (D6 + A2).

### Evidence of POC shape (verified 2026-08-04)

| Finding | Location | Consequence |
|---|---|---|
| `SignalAdapter` built directly in the composition root; `signal_cli` a top-level config key | `__main__.py:54,71` | a platform costs an edit to the composition root *and* the config schema |
| Handler reads a private attr for Nora's own account | `handler.py:85` (`adapter._account`) | not on the `PlatformAdapter` protocol; R-12 is Signal-specific |
| `Handler`: ~8 responsibilities, 229 lines | `handler.py` | no home for a relevance classifier but an inline `if` |
| **Six** test-only attrs compiled into production | `handler.py:41-46` — `_invoke_fn`, `_create_agent_fn`, `_last_prefill`, `_last_limits`, `_prefills`, `_guards` | `_prefills`/`_guards` are appended per tag and never cleared — **a live unbounded leak**, each retained `ReplyGuard` pinning a `Deliverer`→persona→adapter chain |
| `db_path` threaded as a de-facto connection handle | handler, guards, tools, health | open-and-close per call (`db.py:1`), no pooling |
| **Three** module-level mutable globals | `METRICS` (`obs/metrics.py:138`, 4 importers), `DECLARED`/`_BUCKETS` (`metrics.py:11`), **`_CLIENT`** (`agent/factory.py:22`, with a `close_client_sync()` docstringed *"Test helper"*) | global state; per-instance impossible |
| Fixed tool closure list | `agent/tools.py:90` → `[read_buffer, read_peer_card, send_reply]` | every capability is a code edit |
| **`ReplyGuard` hard-vetoes unknown tools** | `guards.py:169` `BUDGETS = {...}`; `evaluate()` returns R-7 *"The tool X does not exist"* | **any new tool is rejected before it runs** — this alone defeats the tool-registry seam |
| Production eval tooling imports the test package | `evals/harness.py:28` → `tests.integration.harness` | evals are a second composition root that had to borrow the test package's |
| `persons.resolve("signal", …)` hardcoded | `agent/tools.py:68` | a second platform silently returns no peer card |

## 2. Requirements

Confirmed with Miles, 2026-08-04.

**Real extension axes:**
1. More platforms — Telegram / Discord / WhatsApp / DM.
2. Smarter ambient — a relevance classifier replacing the boolean gate (A3).
3. More agent capability — tools, model tiers, escalation. Concretely: *"where should I
   have dinner in Yuen Long, I prefer Japanese"*, answered using Google Maps or similar.

**Explicitly not a requirement:** swappable memory backends. Honcho stays concrete; the
`MemoryStore` Protocol exists only so tests can fake it.

**Constraints:**
- One process, N adapters concurrently.
- Speed matters most on the hot path.
- Miles is an SRE; observability is a first-class consumer. Langfuse is already running.
- No backward compatibility required. Production DB has 0 rows.

**Operational constraints inherited from `design/08` — all five, preserved here because
08 is retired by this spec** (they were the most operationally valuable text in it):
1. Container builds require `docker build --network=host` — BuildKit's default bridge
   cannot resolve `deb.debian.org` on this host (systemd-resolved stub `127.0.0.53`).
2. Honcho health uses **`GET /health`**, not `GET /v3/workspaces` (POST-only → 405 →
   forever `health: starting`). See §9 F4 — this now gets a test.
3. **The signal-cli daemon is neutral and shared — never touched.** It is not owned by
   Nora. `derive_account` reads from it; nothing writes to it.
4. The legacy Hermes gateway is archived at
   `/home/norty/backups/nora-legacy-profile-20260804/`. Do not reference the old
   `~/.hermes/profiles/nora/` path.
5. Dependencies are added with **`uv add`**, never raw pip.

These move to `CLAUDE.md` in Phase 0 so they survive this spec too.

## 3. Decisions

### DEC-1 — Architecture: pipeline + ports, light layering

Composable stages behind Protocol ports, one composition root, registries from config.
**Rejected:** full hexagonal (ceremony unjustified at this scale); modular monolith with
an event bus (indirection that hurts tracing, which the SRE consumer would feel).

### DEC-2 — Hot path native, cold path MCP

| Capability | Frequency | Surface needed | Verdict |
|---|---|---|---|
| Honcho | every ambient message + every tag | 2 narrow ops | **native** |
| SQLite audit | every message | internal | **native** |
| Platform send | every reply | internal | **native** |
| Maps, web search | rare | broad, exploratory | **MCP** |

Honcho *has* an MCP server; deliberately unused. Not primarily for latency (the extra hop
is ~1–5 ms against a call costing tens to hundreds) but for **token budget and
precision** — its ~9 tool schemas would enter context on *every tag* when Nora needs two
operations — and for **failure control**.

Honcho is used **directly on the ambient path** (no model involvement, as today at
`handler.py:226`) and **as a native tool on the engagement path**.

**Corollary the audit forced (see §9 F1a):** because MCP servers *are* supervised
subprocesses, supervision is specified, not assumed — `ToolProvider` carries lifecycle
(§4.2) owned by `app/runtime.py`, `tools/list` runs once at startup and never on the tag
path, and `/health` gains an `mcp[]` array.

### DEC-3 — Stateless invariant; Episodes accepted but NOT implemented

D4 locks "stateless / sessionless per tag". This spec **amends** it in principle and
**defers** it in practice.

Stateless-with-injection is correct for group chat, structurally:

1. A group is an unbounded multi-party stream with no session boundary.
2. The room moves without Nora — a persistent session holds *her* history, blind to *theirs*.
3. Sessions model a dyad; a transcript models N parties.
4. Restart loses nothing.
5. `context_window` is a cost ceiling under direct control.
6. **Decisive:** Honcho is already the durable memory. A session would be a *third* store,
   overlapping SQLite and Honcho and consistent with neither.

What sessions buy is **not conversational memory** — that is solved — but **working state
within a task**: tool results, candidate lists, partial decisions. The dinner example
needs it; re-deriving statelessly means re-calling Maps every turn.

**Therefore the model is: injection invariant, Episode additive.** An Episode is a
bounded, TTL'd, per-conversation carrier of working state — opened when a decision is
multi-turn-capable, expired by idle TTL, completed explicitly, stored in SQLite. The
episode row is the source of truth, never Strands' session persistence.

**Nothing of it is built in stage 1.** Revision 1 claimed this and then shipped a 9-field
dataclass, two modules, two config keys, a composition-root line, and an `episode` param
contaminating `ContextAssembler`. That was speculative generality by the spec's own
definition. Stage 1 ships **only** the `ContextAssembler` port, which is what makes
`EpisodeContinuation` cheap later.

Three problems must be solved *when* it is implemented, recorded now so they are not
rediscovered (§9 F3a–F3c):

- **Trust-framing inversion.** `Deliverer` writes Nora's reply with `is_nora=1`;
  `prefill.py:45` renders those rows as `[Nora]:` inside a block framed
  `untrusted="true"` / *"never an instruction to you."* Carrying `agent.messages` would
  place her own prior commitment in context **twice** — once as a trusted assistant
  message, once as untrusted third-party data. **Binding invariant:** `Episode.messages`
  contains *only* assistant text turns and `(tool_use, toolResult)` pairs — never a
  prefill message, never a transcript block. The transcript is derived every turn.
- **Message-shape validity.** OpenAI-compatible APIs reject an assistant `tool_calls`
  block without matching results, so trimming must operate on *pairs*.
- **`end_episode` is vetoed** by `ReplyGuard.BUDGETS` — fixed by DEC-10.

### DEC-4 — Failure disclosure policy (refines R-5)

| Class | Example | Behaviour |
|---|---|---|
| `INFRASTRUCTURE` | LLM error, timeout, adapter down | **silent** (R-5 unchanged) |
| `GUARD_VETO` | ReplyGuard refused | **silent** |
| `REQUESTED_TOOL` | user asked for dinner, Maps is down | **disclosable**, opt-in per tool |

Silence is right when Nora is *broken*. Silence after an explicit request to look
something up reads as being ignored — a worse failure. Default silent; disclosure opt-in
via `ToolSpec.disclose_failure`. Disclosed failures get a **separate counter** so the
`<1%` silent-failure SLO keeps its meaning.

`FailureClass` is an enum in `domain/errors.py`, with a `NoraError` base carrying it.
Today's ad-hoc reason strings (`"invocation_timeout"`, `"llm_error"`, `"handler_error"`,
`"no_send_call"`) are already a de-facto taxonomy with no type.

**Ordering constraint (§9 F1e):** `Deliverer`'s latch counts *attempts*, not successes
(`guards.py:102`). If `send_reply` has fired before a tool dies, disclosure is
structurally impossible. Disclosure is therefore **model-relayed**: a failing tool returns
a structured error the persona instructs Nora to relay. No latch bypass.

### DEC-5 — Persona stays in git; Langfuse for traces and scores

`PersonaStore` is not "a prompt": multi-section composition, audience filtering by
`applies_to`, a **security property** (`sha256` over `persona_text` is what LeakGuard
matches; `canary` for leak detection), and **fail-safe reload** (last-good on a broken
edit). Langfuse versions one prompt per name; fitting Nora means flattening the
composition or reimplementing assembly over N prompts.

Also: the persona is the product and deserves review/blame/revert and atomic deploy; and
hosting it trades a fail-safe for a hard startup dependency on the identity layer.

**Correction to revision 1:** trace↔prompt linkage is *already implemented* —
`obs/tracing.py:41-47` emits `tag_id`, `conversation_id`, `persona_sha256`, wired at
`handler.py:135`. Only `persona.versions` is missing. Revision 1 claimed this as a
benefit still to be obtained.

### DEC-6 — Strands Evals is the runner; Langfuse is the store

Not competitors. `strands-agents-evals` produces results; Langfuse stores and compares
them (scores, datasets, dataset runs). Note a live-model path already exists at
`evals/run.py:147-201` and does *not* use the test package — the `tests/` coupling is
specific to the `trigger`/`no_trigger`/`all` suites.

### DEC-7 — SQLAlchemy Core + Alembic

Nora has **one table, no relations, no joins**. Tortoise's value is querysets and
related-model traversal — nothing Nora gains — so it adds ORM ceremony to a layer whose
docstring says "No semantics live here" (`db.py:3`). SQLAlchemy **Core** is schema
metadata plus a SQL expression builder: exactly the wanted capability. Async is moot —
SQLAlchemy's async SQLite driver *is* aiosqlite. Alembic wins the migration tie on
maturity.

**But `alembic check` is a far weaker gate than revision 1 assumed. See DEC-11.**

### DEC-8 — `config.toml` + `identity.toml`

TOML for both: `tomllib` is stdlib, parsing is unambiguous, and it matches
`pyproject.toml`. **JSON rejected for identity** because it is hand-edited by design (D6)
and has no comments. (Revision 1 cited today's `persons.yaml` as using comments for
future-platform entries; it does not — the canonical example in `design/04` does. The
argument stands on JSON's lack of comments.)

PyYAML stays: persona frontmatter remains YAML so a section can be promoted to a Strands
Skill (S3).

Two files, different change cadences: identity changes when a human joins; config on deploy.

**No `${VAR}` interpolation.** pydantic does not interpolate inside TOML values,
so revision 1's `${GOOGLE_MAPS_API_KEY}` would have required building an interpolation
engine for a disabled tool. Secrets are referenced by **`*_env` naming** throughout — the
pattern already proven at `llm.api_key_env`.

### DEC-9 — Adapters own platform identity

Identity discovery moves onto the port: `async def resolve_identity() -> PlatformIdentity`.
Signal implements it as `derive_account()` does today. The registry calls it at startup
and fails fast if an enabled adapter cannot establish who it is, so R-12 works
per-platform and `adapter._account` disappears.

**Nora's own account is never written to a config or identity file.** The identity file
describes *other people*.

`PlatformIdentity` carries **`aliases: frozenset[str]`**, not one id: signal-cli emits
`source` *or* `sourceNumber` (`parser.py:35`) and sometimes a UUID; Telegram has numeric
`id` + `username`. R-12 tests membership, not equality.

### DEC-10 — `ReplyGuard` takes budgets from the tool registry (**stage 1**)

`guards.py:169` hardcodes `BUDGETS = {"read_buffer": 2, "read_peer_card": 1,
"send_reply": 1}` and `evaluate()` vetoes anything absent under R-7. Ported as-is, this
**rejects every MCP tool and `end_episode` before they run** — silently defeating both
the tool axis and the episode model.

`ReplyGuard(budgets: Mapping[str, int])`, supplied by the `ToolRegistry`; each registered
tool declares its own per-invocation budget; unknown-tool default-deny is *preserved*.
This is stage-1 work because it blocks two of three extension axes.

### DEC-11 — `alembic check` is not the schema gate; a parity test is **[proven]**

Empirically verified against a byte-identical copy of the production schema:

1. **Naive metadata fails on production but passes in CI.** `id INTEGER PRIMARY KEY
   AUTOINCREMENT` has `notnull=0` in SQLite; SQLAlchemy's `primary_key=True` implies
   `nullable=False`. Result: a `modify_nullable` diff against the prod-shaped DB, none
   against a `create_all` DB. The natural "fix" — `--autogenerate` — emits a
   `batch_alter_column` **table rebuild on production**, contradicting "nothing is
   dropped or rebuilt". **Fix: declare `Column("id", Integer, primary_key=True,
   autoincrement=True, nullable=True)`** — verified to produce no diff.
2. **The `ts DESC` index never round-trips.** Declaring `sa.Index(..., sa.text("ts DESC"))`
   produces a permanent `remove_index`/`add_index` pair on *every* check, because
   reflection returns a plain column and autogenerate compares them as different.
   **Fix: exclude `idx_messages_conv_ts` from autogenerate via an `include_object` hook,
   with a comment explaining why.**
3. **The gate is blind to two of the three things §6 promises.** Removing the
   CHECK constraint or dropping `DESC` each produced **no diff at all** under
   `compare_type=True, compare_server_default=True` **[proven]**. **Corrected
   2026-08-05:** revision 2 also claimed server defaults were invisible. They
   are not — changing or removing `created_at`'s or `is_nora`'s server default
   each produces a `modify_default` diff, verified against a copy of the
   production database. The conclusion is unchanged (the parity test is still
   the real gate, for the CHECK constraint and the index ordering), but the
   claim was wrong and is not silently absorbed.

Therefore: `alembic check` guards *column presence, column type, and server
defaults* — nothing more; it remains blind to the CHECK constraint and the
index's `DESC` ordering. A dedicated **schema-parity test** is the real
guarantee — build a fresh DB via `alembic upgrade head`, then assert against
`sqlite_master.sql` / `PRAGMA index_xinfo`: the CHECK constraint present,
`AUTOINCREMENT` present, `idx_messages_conv_ts` has `desc=1` on `ts`,
`created_at` default is `datetime('now')`.

Also set **`sqlite_autoincrement=True`**: without it SQLAlchemy emits no `AUTOINCREMENT`,
so production (stamped) and every fresh test DB would diverge — and since `db.append`
deletes rows on every insert and `db.recent` tie-breaks on `id`, rowid reuse would make
the suite stop testing production's schema.

**`alembic check` runs against a copy of `data/messages.db`, never only a fresh DB.**

### DEC-12 — Mechanically enforced boundaries

The architecture is Protocol ports and **the repo has no type checker**; `ruff` has no
`select` (so its 23 `# noqa: BLE001` markers are inert — in a codebase whose defining rule
is about swallowing failures); **`ruff` is not even installed** (`uv run ruff check` →
`Failed to spawn`, absent from `uv.lock`); there is **no CI**. Protocols without
enforcement are comments.

Adopted (all four, confirmed by Miles):
- **mypy** `--strict` on `nora/domain/` and `nora/config/` — small, framework-free, and
  where correctness is architectural. Plus explicit `x: PlatformAdapter = SignalAdapter(...)`
  assignments in tests to force Protocol conformance at type-check time.
- **import-linter**, layers `app` > `ingest|engagement` > `agent|platforms|infra` > `domain`;
  forbidden: `domain` → any framework; **everything except `app` → `config`** (this is what
  turns "a stage never sees Settings" from prose into a gate); `platforms.*` mutually independent.
- **ruff `select`** = `["E","F","W","I","N","UP","B","BLE","ASYNC","S","C4","SIM","TID","RUF"]`.
- **Contract tests** — a port may not gain a second implementation, *including a test
  fake*, without a shared contract suite both pass. This is what stops `FakeHoncho`
  drifting from `HonchoClient` and integration tests going green against behaviour
  production lacks.

## 4. Architecture

### 4.1 Packages

```
nora/
  domain/     models.py, ports.py, errors.py, identity.py, leak.py   ← zero framework imports
  config/     settings.py, identity.py            ← importable ONLY by app/
  ingest/     pipeline.py, policy.py, stages/{parse,audit,identity,memory_feed,gate}.py
  engagement/ pipeline.py, stages/{admission,context,persona,tools,invoke,deliver,feedback}.py
  agent/      ports.py, factory.py, hooks.py (ReplyGuard), tools/{registry,adapt,native/}
  platforms/  registry.py, signal/
  infra/
    sql/      engine.py, schema.py, audit.py, migrations/
    honcho/   client.py
  persona/    loader.py
  obs/        logging.py, metrics.py, tracing.py, health.py
  app/        composition.py, runtime.py, cli.py
  testing/    fakes.py, builders.py               ← shipped; tests/ AND evals/ import from here
```

Four corrections to revision 1's layout, each from a concrete defect:

- **`stores/` was a grab-bag.** Audit and Honcho have *opposite* contracts — `db.py:3`
  "No semantics live here" (a failed append drops the message) versus `honcho.py:3`
  "Honcho is never load-bearing: every method logs and returns a benign value on any
  failure" — plus different dependency sets and change cadences. Split into `infra/sql/`
  and `infra/honcho/`.
- **`guards` cannot move wholesale into `engagement/`.** `agent/guards.py` is three
  things: `LeakGuard` (pure text/sha256, no deps → `domain/leak.py`), `Deliverer` (I/O →
  `engagement/stages/deliver.py`), and `ReplyGuard` (a **`strands.HookProvider`** →
  `agent/hooks.py`). Moving the file intact would import strands into `engagement/` and
  widen the boundary allowlist.
- **`EngagePolicy` belongs to `ingest/`**, not `engagement/` — stage 5 (`EngageGate`) is
  on the ingest pipeline, so revision 1's placement inverted the dependency.
- **`nora/testing/`** ships in the package so `tests/` *and* `evals/` import fakes from
  one place. Revision 1 said evals stop importing `tests/` without saying what they import
  instead.

`stages/` subpackages exist so `pipeline.py` stays a readable table of contents and each
stage is one module with one public `async def run(...)`. §4.5 dissolves `Handler`;
`stages/` is what stops it re-accreting.

`evals/` may import `nora.app` and `nora.testing` — nothing else.

### 4.2 Ports

```python
# domain/ports.py — framework-free
class PlatformAdapter(Protocol):
    name: str
    async def resolve_identity(self) -> PlatformIdentity: ...
    async def listen(self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]) -> None: ...
    async def send(self, *, target: ConversationTarget, text: str) -> SendResult: ...
    async def health(self) -> AdapterHealth: ...

class AuditStore(Protocol):      # append / recent / set_delivery_status / prune / stats
class MemoryStore(Protocol):     # ensure_workspace / ensure_peer / feed / peer_card / health
class EngagePolicy(Protocol):
    async def should_engage(self, *, msg: NormalizedMessage) -> EngageDecision: ...
class ContextAssembler(Protocol):
    async def assemble(self, *, request: EngagementRequest) -> EngagementContext: ...
```

```python
# agent/ports.py — may reference Strands at the adapt boundary only
@dataclass(frozen=True)
class ToolScope:  tier: ModelTier; conversation_type: str

@dataclass(frozen=True)
class ToolSpec:
    name: str; description: str; schema: dict
    call: Callable[..., Awaitable[Any]]
    budget: int = 1                      # feeds ReplyGuard (DEC-10)
    disclose_failure: bool = False       # feeds DEC-4
    timeout_seconds: float | None = None

class ToolProvider(Protocol):
    name: str
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> dict: ...
    async def tools(self, *, scope: ToolScope) -> Sequence[ToolSpec]: ...
```

Port corrections from the audits:

- **`ToolProvider` moved out of `domain/`.** It returned a Strands `Tool`, contradicting
  §4.1's own rule *and* `tests/test_boundaries.py:26` (*"Only agent/factory.py and
  agent/guards.py may import strands"*). `agent/tools/adapt.py` converts
  `ToolSpec → strands tool` at the boundary.
- **`ToolProvider` is async and has lifecycle.** MCP discovery is a `tools/list` RPC over
  a subprocess; a sync bare function has nowhere to hang start/stop/health. Strands'
  `MCPClient` offers `start`/`stop` and **no restart, no health, no reconnect**, and its
  `list_tools_sync()` blocks the event loop — i.e. every listener in the process.
- **`ToolSpec` carries `disclose_failure` and `budget`.** Revision 1 declared
  `disclose_failure` in config and required it in DEC-4 with nowhere on the port to carry it.
- **`EngagePolicy` is async.** `ClassifierPolicy` is I/O. Sync→async later is a breaking
  change through the ingest pipeline; free now.
- **`ContextAssembler` takes an `EngagementRequest`**, not `(msg, episode | None)`. The
  old signature forced the *caller* to fetch an episode before knowing whether the
  assembler wanted one — moving the change rather than removing it. Assemblers own their
  dependencies (`EpisodeContinuation(episodes, audit)`), so swapping really is a config
  change. `EngagementRequest` carries `(msg, decision, tag_id)` — which is also the only
  route by which a classifier's rationale can reach the prefill, required by
  `design/02 §True Ambient Hooks`.
- **`send() -> SendResult`**, not `bool`. Telegram returns 429 with `retry_after`, and
  because `Deliverer`'s latch counts *attempts* (`guards.py:102`), a bool would consume
  the single shot on the most common transient failure. `SendResult(ok, retryable, retry_after)`.
- **`parse()` dropped from the port.** It forces every adapter to pretend its transport is
  dict-shaped (Discord's gateway and Telegram's update batches are not) and drags an
  untyped dict into the pipeline. `platforms/signal/parser.py` stays a directly-tested module.
- **`health() -> AdapterHealth`**, a dataclass, not a stringly-typed dict.
- **Convention: all port methods take keyword-only arguments.** Free extensibility forever.

### 4.3 Ingest pipeline (every message, deterministic, no agent — D1)

| # | Stage | Failure behaviour |
|---|---|---|
| 1 | `Parse` | `None` → drop silently |
| 2 | `Audit` | metric + drop the message; **never kill the listener** (`handler.py:74`) |
| 3 | `Identity` | unmapped → `None`, continue (D6) |
| 4 | `MemoryFeed` | mapped senders only; off-path; never blocks ingest |
| 5 | `EngageGate` | policy error → `EngageDecision(engage=False, reason="policy_error")` + `nora_errors_total{stage="engage_gate"}` |

```python
@dataclass(frozen=True)
class EngageDecision:
    engage: bool
    reason: str                    # category
    rationale: str | None = None   # free text — reaches the prefill (design/02)
    tier: ModelTier | None = None
    confidence: float | None = None
    threshold: float | None = None # a confidence is uninterpretable without it
    allow_episode: bool = False
```

`TriggerTextPolicy` ships now: deterministic, `tier = flash`, **no LLM on the ambient
path**. Stage 5 had no failure-behaviour row in revision 1 because `TriggerTextPolicy`
cannot fail; `ClassifierPolicy` can.

**When the classifier lands it is `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`,
first-match-wins, deterministic trigger first and short-circuiting** — not a replacement.
Revision 1 framed the classifier as *replacing* the gate, which under R-5 means an LLM
outage silently stops explicit `nora.000` tags from working. That is strictly worse than
today. It also becomes an off-path stage behind a bounded queue: `on_event` is awaited by
the SSE reader (`listener.py:79`), so an inline 200–800 ms call stalls the socket read for
every adapter in the process.

### 4.4 Engagement pipeline (only when `engage=True`)

`Admission` (debounce, R-10, R-12) → `ContextAssembly` → `Persona` → **`Routing` (tier)**
→ **`ToolBinding`** → `Invoke` (timeout by tier) → `Deliver` (ReplyGuard + Deliverer,
audit-first) → `Feedback`.

**`Routing` precedes `ToolBinding`** — reversed from revision 1, which bound tools *by
tier* before choosing the tier. Combined with stage 1's `tier = flash`, that made
`scope = ["deep"]` unreachable and the dinner question unanswerable.

**R-1, R-5, R-7, R-10, R-12 are preserved.** R-7's unknown-tool default-deny is kept; only
its hardcoded table becomes injected (DEC-10).

### 4.5 What this deletes

- `Handler` — dissolved into stages.
- All six test-only attrs, taking the unbounded leak with them.
- **Three** module globals: `METRICS`, `metrics.DECLARED`/`_BUCKETS`, and
  **`agent/factory.py:22 _CLIENT`** with its `close_client_sync()` test helper — the same
  defect class, missed by revision 1.
- `db_path` threading.
- `store/migrate.py` — retired. Note `db.SCHEMA_VERSION` currently flows into
  `db.stats()` and is asserted by `tests/test_main.py:104`; Alembic subsumes it and the
  `stats()` contract changes.
- `db.prune` is **dead code** — nothing calls it; the real prune is inline in `append`.
  It is kept on `AuditStore` only if a caller appears.

### 4.6 Composition root

Components receive **dependencies, not config**. A stage never sees `Settings`; it
receives an `AuditStore`. Enforced by import-linter (DEC-12), not by convention.

```python
# app/composition.py — the ONLY place that reads Settings
async def build_app(settings: Settings) -> App:
    engine   = create_async_engine(settings.store.url)   # a local, not a global
    audit    = SqlAuditStore(engine, settings.store)
    memory   = HonchoStore(settings.memory)
    tools    = ToolRegistry.from_config(settings.tools, audit=audit, memory=memory)
    adapters = AdapterRegistry.from_config(settings.platforms)
    tasks    = BackgroundTasks()                          # owns every spawned coroutine
    ...
```

Production, tests, and evals all call this. `nora/testing/build_test_app(**overrides)`
wraps it.

**`app/runtime.py` owns what `Handler` used to:** per-adapter supervisor tasks (restart
with backoff, `nora_adapter_restarts_total{platform}`; a wedged adapter drives `/health`
503 — naive `asyncio.gather` either kills the process on one adapter's bug or hides a dead
listener behind a 200), the `BackgroundTasks` set (`handler.py:40` documents that asyncio
holds only weak refs — that requirement outlives `Handler`), MCP provider lifecycle, and
the `Debounce._last` TTL sweep (`tag.py:63` is an unbounded dict, now growing with
platforms × conversations).

## 5. Configuration

### 5.1 `config.toml`

Phase 1 ships a **value-for-value translation of the live `config.yaml`** — see §7.

```toml
[platforms.signal]
enabled        = true
base_url       = "http://127.0.0.1:18080"
account        = "+85290000100"       # live value preserved; "" would derive from the daemon
nora_sender_id = "nora"

[llm]
base_url    = "http://localhost:20128/v1"
api_key_env = "OMNIROUTE_API_KEY"

[llm.tiers.flash]
model = "deepseek-v4-flash"
max_tokens = 400
temperature = 0.7
timeout_seconds = 30                  # live invocation_timeout_seconds preserved
[llm.tiers.flash.limits]
turns = 6
total_tokens = 8000

[memory]
base_url = "http://127.0.0.1:8000"
workspace_id = "nora"

[store]
url = "sqlite+aiosqlite:///data/messages.db"
max_per_conversation = 500
context_window = 40

[engagement]
policy = "trigger_text"
[engagement.trigger]
text = "nora.000"
min_interval_seconds = 5

[obs.logging]
level = "INFO"
path = "data/nora.log"
format = "json"
[obs.metrics]
port = 9100
[obs.tracing]
enabled = true
exporter = "file"                     # live value preserved; otlp is a later commit
endpoint = ""
```

Preserved behaviours that are easy to lose in a pydantic port:

- **Base-relative path resolution** — relative paths resolve against the config file's
  directory, with `~` expansion, as `config._resolve_path` does today.
- **Strictness** — `extra = "forbid"` on every model. A typo in `trigger.text` silently
  disables Nora; that is why this matters.
- **`identity_file`** — revision 1 introduced `identity.toml` and left **no pointer to
  it**, silently dropping today's `persons_file` (`config.py:112`, consumed at
  `__main__.py:47`). It is a key: `identity_file = "identity.toml"`.

`[llm.tiers.deep]` and `[tools.mcp.*]` live in `config.example.toml` only, until routing
and MCP exist. Revision 1 shipped a fully-specified `deep` tier that nothing routed to and
a fully-specified Maps block for a provider §7 said wasn't built.

**Deleted claim:** revision 1 said a SQLAlchemy URL makes "Postgres later a config edit."
False — this spec commits to `render_as_batch=True`, `sqlite+aiosqlite`, and a WAL /
`busy_timeout` / `foreign_keys` connect hook three lines later. The URL form is free; the
portability claim is not, and a false seam is worse than none.

### 5.2 `identity.toml`

```toml
[persons.miles]
name = "Miles"
[persons.miles.platforms]
signal = "+85212345678"
# telegram = "123456789"              # future
```

`honcho_peer_id = person_id` by construction (A2); reverse index by `(platform, sender_id)`
at startup. Validation moves to pydantic; `resolve` / `display_name` / duplicate detection
stay custom. `persons.py:65` already does `str(sender_id)`, so TOML integer ids stringify
correctly.

## 6. Infrastructure

**Persistence.** SQLAlchemy Core `MetaData` in `infra/sql/schema.py` is the source of
truth, with `nullable=True` on `id` and `sqlite_autoincrement=True` (DEC-11). Alembic with
`render_as_batch=True`; the initial migration reproduces the current schema; `alembic stamp
head` on the 0-row production DB so the file is the baseline. WAL / `busy_timeout=5000` /
`foreign_keys=ON` move to an engine connect hook.

**Two concurrency defects fixed here, not deferred** — both are pre-existing and both get
worse with N adapters:

- **`db.append` runs INSERT *plus* a correlated-subquery prune in one transaction on every
  message** (`db.py:86-91`), O(rows-per-conversation) per write. WAL permits one writer;
  under an N-listener burst this yields `SQLITE_BUSY`, and §4.3 stage 2's behaviour is
  "drop the message" — **silent audit loss, the one thing SQLite exists to prevent.**
  Append becomes a bare INSERT; pruning moves to a periodic task. Add `nora_store_busy_total`.
- **`HonchoClient.ensure_session` / `ensure_peer` are check-then-act** (`honcho.py:81`).
  Two concurrent misses create two sessions for one peer, splitting the peer card — and
  person-centric identity means Miles's Signal and Telegram messages hit the *same* peer id
  simultaneously. Per-peer `asyncio.Lock`; stated as a `MemoryStore` invariant:
  **`ensure_*` is idempotent under concurrency.**

**Observability.**

- **structlog** + stdlib bridge. Redaction generalises to the N declared secrets —
  explicitly including MCP provider env values. `tag_id` as a contextvar.
- **prometheus-client**, rendered via `generate_latest()` into the **existing aiohttp app
  on port 9100** — *not* `start_http_server()`, which would stand up a second server and
  break `docker-compose.yml`'s healthcheck and `deploy/watchdog.sh`.
  **`platform` is added as a label** to `nora_tags_total`, `nora_replies_total`,
  `nora_silent_failures_total`, `nora_errors_total` — today only `nora_messages_total` has
  one, so with N adapters you cannot tell which platform is failing. Adding a label is
  backward-compatible; revision 1's "metric names and HELP unchanged" constraint was
  protecting nothing here. **Metric names remain append-only** — this is an SRE's repo and
  renames break dashboards.
- **OTel → Langfuse.** Not free, contrary to revision 1: `opentelemetry-exporter-otlp` is
  **not installed**, `obs/tracing.py:35` imports the **gRPC** exporter, and Langfuse's
  `/api/public/otel` is **HTTP/protobuf** — incompatible. Requires a new dependency, an
  HTTP exporter branch, and a `headers_env` field that `TracingConfig` does not have.
  Langfuse verified live: web on `:3100`, `/api/public/health` → 200,
  `/api/public/otel/v1/traces` → 401 without credentials.

**Health.** Keeps `GET /health` for the Honcho probe. Gains `adapters[]` and `mcp[]`.
**`sse` stays a top-level key** — `deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`
and `test_health_reports_all_four_dependencies` asserts `set(body) >= {"sse", …}`.

## 7. Staging

**Stage 1 changes structure, not behaviour** — and, per §9 F9, **preserves every current
config *value*, not merely every key**. Revision 1 smuggled three behaviour changes into
the phase labelled structure-only: invocation timeout 30 s → 20 s (a 33 % cut to the
production reply budget), tracing exporter `file` → `otlp` (with a `LANGFUSE_OTEL_HEADERS`
secret in neither `.env.example` nor compose — a container that refuses to boot), and
account pinned → derived (a new startup dependency on signal-cli). Each is now its own
later commit with its own verification.

| Seam | Stage 1 ships | Later |
|---|---|---|
| `EngagePolicy` | `TriggerTextPolicy` — deterministic, no LLM | `CompositePolicy([TriggerText, Classifier])` |
| `ContextAssembler` | `TranscriptInjection` | `EpisodeContinuation` + `EpisodeStore` |
| `ToolRegistry` | the native **three**, budgets injected (DEC-10) | MCP providers with lifecycle |
| Model tier | `flash` only; `Routing` is a lookup inside `Invoke` until a second tier exists | `deep` + escalation |
| `AdapterRegistry` | Signal only | Telegram / Discord / WhatsApp |
| Failure policy | enum + counters | `REQUESTED_TOOL` disclosure |

**Not built in stage 1:** `Episode`/`EpisodeStore` in any form (DEC-3), MCP providers,
the `deep` tier, `AuditStore.upsert`, `platform_message_id`/`edited_at`. On the last:
a schema column is not a port, and the whole point of adopting Alembic is that adding one
later is a cheap versioned migration.

## 8. Phases

**Per-phase gate:** `pytest` green · `ruff check` clean · `mypy nora/domain nora/config`
clean · `lint-imports` clean · `alembic check` **against a copy of the production DB** ·
**`docker build --network=host` + `docker compose up -d --no-build` + `curl :9100/health`
→ 200 with `sqlite.reachable: true` and `sse` present** · tag `nora:p<n>`.

The container gate moved from *final only* to *every phase*: it is the only gate that
catches a regressed Honcho probe, a config that won't boot, or missing migration assets.

| P | Concern |
|---|---|
| **0** | **Prep, landed against current code, tests green.** Dev deps into `pyproject.toml` `[dependency-groups]` (ruff, mypy, import-linter, alembic, pytest) + `uv.lock`; delete `requirements-dev.txt`; add CI. **Honcho `GET /health` assertion test** (F4). `nora/domain` import-direction test. Config-construction seams: replace `harness._build_config`'s `dataclasses.replace` chain with a format-agnostic `_config_overrides(**kw)`; move direct `*Config` construction in `test_factory`/`test_logging`/`test_tracing` behind fixtures; make `evals/run.py`'s config load injectable. Back up `config.yaml`, `persons.yaml`, `data/messages.db`. Tag `nora:p0`. |
| 1 | Foundation: `domain/`, `config/` pydantic, value-for-value `config.toml` + `identity.toml`, composition-root skeleton. Rewrites `test_config.py`, `test_env_template.py`, config half of `test_main.py` (**format tests, not behaviour tests — they cannot be the oracle**). Rewrites `test_boundaries.py` to allowlist by package prefix. Includes `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, README, **runbook rewritten for Docker** (`runbook.md:64` says `systemctl`; Nora runs in a container, and `test_docs.py` passes it because it only greps the word "rollback"). `ARCHITECTURE.md` + `CLAUDE.md` created. |
| 2 | Persistence. `nullable=True` on `id`; `sqlite_autoincrement=True`; `include_object` excluding `idx_messages_conv_ts`; **schema-parity test**; `alembic.ini` into `COPY` and `infra/sql/migrations/**` into `package-data` (a `versions/` dir has no `__init__.py`, so `packages.find` will not install it — the image would ship without its migrations). Rewrites `test_migration`, `test_db`, `test_health`, `test_tools`, `test_guards_deliverer`, and the harness fault injection (`monkeypatch.setattr(db, "append")` becomes a no-op once `AuditStore` is injected — 10 parametrized R-5 cases). **Risk: high.** |
| 3 | Pipelines; `Handler` dissolved; **evals composition-root reuse moved here from P7** — otherwise `evals/` and two test files stay broken for four consecutive phases. Update `test_acceptance_rules.RULE_TESTS` deliberately (9 of its 21 named tests live in `tests/integration/`). **Risk: highest.** |
| 4 | Platforms: registry, `resolve_identity()`, `PlatformIdentity.aliases`, `SendResult`. Optionally adopt `account = ""`. |
| 5 | Agent: tool registry, `ToolSpec`, DEC-10 budgets. Update the boundary allowlist for the `tools/` package. **Risk: medium.** |
| 6 | Obs: structlog, prometheus-client, OTLP→Langfuse. **`tests/conftest.py`'s autouse `METRICS.reset()` must change in the same commit — otherwise all 453 tests fail to *collect*.** Rewrites `test_metrics_stub` (8), `test_metrics_export` (9), `test_logging` (11). **Risk: medium.** |
| 7 | Langfuse datasets + scores (DEC-6). **Risk: low.** |

### 8.1 Rollback

| Phase | Rollback |
|---|---|
| 1 | `git revert`, which restores `config.yaml` too — **it is tracked in git** (corrected 2026-08-04: revision 1 claimed `.gitignore` excluded it; only `.dockerignore` does, and only `persons.yaml` is genuinely untracked). It holds no secret values — `api_key_env` names the variable, never the key. Phase 0 still backs both files up, which costs nothing and covers `persons.yaml`, the one that really is untracked. |
| 2 | `git revert` + `DROP TABLE alembic_version`. **Do not clear `PRAGMA user_version`** — it is still `1`, so the retired `migrate()` short-circuits and the v1→v2 rebuild cannot re-run. That is precisely what makes phase 2 reversible. Phase 0 copies `data/messages.db`. |
| 3–7 | `git revert`; phase 4 additionally restores the pinned account if `""` was adopted. |
| **any** | **Container rollback requires the tagged image.** Only `nora:latest` existed before Phase 0, so there was nothing to roll back to. Every phase now tags `nora:p<n>`, making rollback `docker compose up -d --no-build`. |

**Operational blocker on every rollback, found during Phase 0 execution:** there is no
`.env` file, and `docker-compose.yml:18` requires `OMNIROUTE_API_KEY` to be exported in
the invoking shell (`${OMNIROUTE_API_KEY:?…}` — it fails fast by design). So
`docker compose up -d` from a fresh shell **fails**, including during a rollback, which
is exactly when nobody wants to go hunting for a key. The value currently exists only in
the running container's environment. **CLOSED 2026-08-04:** a gitignored `.env` now exists and its key was verified against
OmniRoute (`GET /v1/models` → 200, both `deepseek-v4-flash` and `deepseek-v4-pro`
visible). Verifying rather than merely setting it matters here: `config.py:190` only
checks the variable is non-empty, so an invalid key would let Nora boot, report healthy,
and then stay silent on every reply under R-5.

**Another live trap, same root cause:** `docker-compose.yml:22` bind-mounts `./config.yaml`
into the container, and that file is **tracked in git** (§8.1 above). Any `git checkout`,
`git revert`, or branch switch performed on the host during Phase 1 rewrites *live
production config in place*, with no container restart to signal it happened. Separately,
a Phase 1 commit that `git rm`s `config.yaml` (e.g. once the TOML rename lands) will leave
nothing at that path on the host — and Docker's bind mount then recreates the mount point
as an empty *directory* on the next `up`, not a file, which the container fails to read as
config. `config.yaml.pre-toml` (§8's Phase 0 backup) covers recovering the data; it does
not cover this failure mode, since the directory-vs-file mismatch has to be cleaned up by
hand before the backup can be restored.

## 9. Audit findings folded in

Four independent audits, 2026-08-04. Items verified by running code are **[proven]**.

**Blocking defects in revision 1:** ReplyGuard's hardcoded allowlist rejecting every new
tool (DEC-10); `ToolBinding` before `Routing` making the dinner question unanswerable
(§4.4); `ToolProvider` in `domain/` violating both §4.1's rule and
`tests/test_boundaries.py:26`; `alembic check` failing on production while passing in CI
**[proven]** and being blind to the CHECK constraint and `DESC` ordering **[proven]**
(DEC-11 — its own revision 2 additionally claimed blindness to server defaults; that claim
was itself wrong and was corrected 2026-08-05, see DEC-11); `persons_file` dropped with no
replacement (§5.1); three behaviour
changes smuggled into stage 1 (§7); phase 1 breaking ~110 of 453 tests; phase 6 breaking
all 453 at collection; phase 3 leaving evals broken for four phases.

**Corrected over-claims:** DEC-5's span attributes were already implemented; the Postgres
portability claim; `${VAR}` interpolation that pydantic does not provide; "DMs
differ by policy config, not code path" (`parser.py:32` drops non-group messages,
`send.py:34` refuses non-group sends — DMs are adapter code); the Episode implementation
surface violating the spec's own no-speculation rule; counts (48 test files / 453 tests,
2,535 lines in `nora/`, **three** native tools).

**Pre-existing defects surfaced:** `db.append`'s per-message prune under WAL contention;
`HonchoClient.ensure_*` check-then-act; `agent/factory.py:22 _CLIENT`; the episode
trust-framing inversion; missing `platform` labels; `ruff` not installed and no CI; no
rollback image; `runbook.md` documenting systemd for a Docker deployment; the Honcho
`GET /health` fix having **zero** test coverage because `test_honcho_card.py` mounts a
wildcard route returning 200 for any method on any path.

## 10. Conventions

Normative. These are what keep the shape as the project grows.

- **Module size:** soft cap 200 lines, hard cap 300 — calibrated on this repo's own
  evidence (`handler.py` went wrong at 229 with 8 responsibilities; healthy modules are
  50–120). One stage = one module = one public `run()`. Enforced by
  `tests/architecture/test_module_size.py`.
- **Package surface:** every `__init__.py` re-exports its public API and carries a
  docstring naming the boundary and its allowed dependencies. All seven package
  `__init__.py` files are currently **0 bytes** — which is exactly how `adapter._account`
  happened. Cross-package imports target the package root, never a submodule.
- **Where new code goes:**

  | I want to… | Touch only |
  |---|---|
  | add a platform | `platforms/<name>/` + one `[platforms.<name>]` block |
  | add a native tool | `agent/tools/native/<name>.py` + a registry entry |
  | add an MCP server | `[tools.mcp.<name>]` — no Python |
  | change engagement decisions | `ingest/policy.py` |
  | add a persisted column | `infra/sql/schema.py` + `alembic revision --autogenerate` |
  | add a behavioural rule | the decision index + a named test in `RULE_TESTS` |

- **Naming:** ports are role nouns (`…Store` / `Adapter` / `Policy` / `Provider` /
  `Assembler`); implementations are technology- or strategy-prefixed (`SqlAuditStore`,
  `TriggerTextPolicy`); all durations end `_seconds`; secrets are referenced by `*_env`.
- **Test-seam policy:** production classes take collaborators through `__init__` only —
  no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons. Faking the
  LLM means a fake model provider injected by `build_app`, not `_invoke_fn`. Assert on
  observable output, not on retained constructed objects (the reason `_prefills`/`_guards`
  existed).
- **Contract tests:** no port gains a second implementation — *including a fake* —
  without a shared contract suite both pass.
- **Test layout:** `tests/{unit,contract,integration,architecture,acceptance}/` mirroring
  the package tree. `unit/` does no I/O and never builds the app.
- **Deprecation:** *code* deprecation is a non-issue (single deployment, one operator —
  delete it, git has it), so no compat shims. Four things do need policy: **schema**
  (forward-only Alembic revisions), **config keys** (with `extra="forbid"` a removed key
  is a hard startup failure — removals go in a runbook "operator actions" section),
  **metric names** (append-only), **`/health` JSON** (additive only — the watchdog parses it).
- **Decision log:** `design/` frontmatter is already richer than standard ADRs, but
  decisions live inside long documents under **eight identifier namespaces with no index**
  (`D*`, `A*`, `R-*`, `S*`, `C.*`, `E.*`, `M*`, `DEC-*`), and **18 production modules cite
  section numbers of the spec this one supersedes** (`handler.py:1` "spec C.8",
  `models.py:1` "spec C.2", `config.py:1` "spec D.1" …), which in six months resolve to
  nothing. Phase 1 adds `design/09-decision-index.md` listing every decision with its
  statement, defining document, and enforcing test; collapses future identifiers to
  `DEC-n` (architecture) and `R-n` (enforced behaviour); and rewrites in-code docstring
  references from spec sections to rule IDs, because rules are versionless and section
  numbers are not.
- **Documentation obligation:** a root `ARCHITECTURE.md` (two-path diagram, package table
  with allowed imports, port list with implementations, a file-by-file walk of one ambient
  message and one tag, the code-placement table), **pinned by a test** that walks `nora/*/`
  and asserts every package appears in it — the same trick `test_docs.py` already uses,
  and what makes the map un-rottable. Plus `CLAUDE.md` carrying the gate commands, the
  never-do list, and §2's five operational constraints.
- **Reading order** (state in README): README → `ARCHITECTURE.md` → decision index →
  `design/*.md` → `docs/superpowers/specs/` (history). Mark superseded specs *inside the file*.

## 11. Deferred, with reasons

- `PlatformAdapter.list_contacts()` + a `nora persons suggest` CLI emitting `identity.toml`
  stubs for operator confirmation. Does not violate D6. Not built unless asked.
- Episodes (DEC-3) — accepted, not implemented.
- MCP providers, the `deep` tier, `AuditStore.upsert`, `platform_message_id`/`edited_at`.
- Group-episode continuation semantics (`continue_when`) — undefined until episodes exist,
  and an open product question: continuing an episode on untagged messages would have Nora
  replying untagged, which presses on D1.

## 12. Changes from revision 1

Structural: `ToolProvider` moved to `agent/ports.py` with lifecycle and `ToolSpec`;
`EngagePolicy` async and relocated to `ingest/`; `ContextAssembler` takes
`EngagementRequest`; `send() -> SendResult`; `parse()` off the port;
`PlatformIdentity.aliases`; `Routing` before `ToolBinding`; `stores/` split into
`infra/sql` + `infra/honcho`; guards split three ways; `nora/testing/` added; DEC-10,
DEC-11, DEC-12 added.

Scope: Episode implementation surface removed; `deep` tier and MCP config moved to the
example file; `${VAR}` interpolation dropped; Postgres claim deleted.

Process: Phase 0 added; evals moved to phase 3; container gate every phase; image tagging;
§8.1 rollback; §10 conventions; four tooling gates; `design/08`'s five operational
constraints preserved.
