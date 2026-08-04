---
kind: spec
title: "Nora — Architecture & Bootstrapping"
description: "Replace the POC structure with a pipeline+ports architecture: one process running N platform adapters, deterministic ambient ingest, stateless engagement with bounded episodes, MCP for cold-path tools, and a standard-library infrastructure layer (SQLAlchemy Core/Alembic, pydantic-settings, prometheus-client, structlog, OTLP→Langfuse)."
tags: [nora, spec, architecture, ports, pipeline, extensibility, mcp, langfuse]
timestamp: '2026-08-04'
supersedes: "design/08-stack-refactor-brief.md (library-swap-only scope)"
edges:
  - {dst: ../../../design/00-overview.md, rel: amends, provenance: asserted}
  - {dst: ../../../design/04-config-identity.md, rel: amends, provenance: asserted}
  - {dst: ../../../design/08-stack-refactor-brief.md, rel: supersedes, provenance: asserted}
---

# Nora — Architecture & Bootstrapping

## 1. Context

`design/08-stack-refactor-brief.md` scoped a library swap: hand-rolled persistence,
config, metrics, and logging → standard libraries. That brief is correct but
insufficient. The libraries are the *infrastructure* layer; the problem is the
*architecture*, and the architecture decides where the libraries plug in.

Nora today is ~3,300 lines with a real test suite (45 files) and genuinely hard-won
behavioural rules. It is not bad code. It is **POC-shaped code**: the seams the design
documents promise exist as protocols but are not honoured by the wiring, so every one
of the intended extension axes costs a cross-cutting edit.

This spec replaces the brief's scope. The brief's library choices survive inside it.

### What Nora is

Two paths, one boundary (`design/00-overview.md`):

- **Ambient path** — every message: parse → SQLite audit → Honcho feed. Deterministic
  write path. No agent loop, ever (D1).
- **Engagement path** — trigger fires → assemble context from the two stores → a
  *fresh, stateless* Strands agent → tools → reply → audit → send → feed back (D4).

SQLite is the audit record and transcript source. Honcho is the semantic store. Identity
is person-centric so one human is one Honcho peer across platforms (D6 + A2 amendment).

### Evidence of POC shape (verified 2026-08-04)

| Finding | Location | Consequence |
|---|---|---|
| `SignalAdapter` constructed directly in the composition root; `signal_cli` is a top-level config key | `__main__.py:54,71` | adding a platform edits the composition root *and* the config schema |
| Handler reaches into a private attribute for Nora's own account | `handler.py:85` (`adapter._account`) | not on the `PlatformAdapter` protocol; R-12 is Signal-specific |
| `Handler` holds ~8 responsibilities in 229 lines | `handler.py` | no home for a relevance classifier except an inline `if` |
| Test seams compiled into production | `handler.py:41-46` (`_invoke_fn`, `_create_agent_fn`, `_prefills`, `_guards`) | `_prefills`/`_guards` grow unbounded — a live memory leak |
| `db_path` threaded as a de-facto connection handle | handler, guards, tools, health | open-and-close per call; no pooling |
| `METRICS` module-level singleton | `obs/metrics.py`, imported by 4 modules | global mutable state; per-instance metrics impossible |
| Fixed tool closure list | `agent/tools.py` `build_tools()` | every new capability is a code edit |
| **Production eval tooling imports the test package** | `evals/harness.py:28` → `tests.integration.harness` | evals are a second composition root with no legitimate seam, so they borrowed the test package's; the live-model path is explicitly deferred in that file's own docstring |

## 2. Requirements

Confirmed with Miles, 2026-08-04.

**Extension axes that are real** (drive the design):
1. More platforms — Telegram / Discord / WhatsApp / DM.
2. Smarter ambient — a relevance classifier replacing the boolean trigger gate (A3).
3. More agent capability — tools, model tiers, escalation. Concretely: *"where should I
   have dinner in Yuen Long, I prefer Japanese"* answered using Google Maps or similar.

**Explicitly not a requirement** (do not build): swappable memory backends. Honcho is
the backbone and stays concrete. A `MemoryStore` Protocol exists only so tests can fake
it — there will be one implementation.

**Constraints:**
- One process running N adapters concurrently. Not one process per platform.
- Speed matters most on the hot path.
- Miles is an SRE; observability is a first-class consumer, with Langfuse already running.
- No backward compatibility required. Production DB has 0 rows.
- `uv` manages dependencies. Container builds need `docker build --network=host`.
- The signal-cli daemon is neutral and shared — never touched.

## 3. Decisions

### DEC-1 — Architecture: pipeline + ports, light layering

Composable pipeline stages behind Protocol ports, one composition root, registries
resolved from config. **Rejected:** full hexagonal/clean architecture (ceremony
unjustified for a single-process app with one table — the brief itself said so);
modular monolith with an internal event bus (indirection that hurts tracing, which the
SRE consumer would feel).

### DEC-2 — Hot path native, cold path MCP

| Capability | Frequency | Surface needed | Verdict |
|---|---|---|---|
| Honcho | every ambient message + every tag | 2 narrow ops | **native** |
| SQLite audit | every message | internal | **native** |
| Platform send | every reply | internal | **native** |
| Google Maps, web search | rare | broad, exploratory | **MCP** |

Honcho *has* an MCP server; it is deliberately not used. The reason is not primarily
latency — the extra hop is ~1–5 ms against a call costing tens to hundreds. The reasons
are **token budget and precision**: the Honcho MCP server exposes ~9 tools whose schemas
would enter the model's context on *every tag*, when Nora needs exactly two operations
(read peer card, feed exchange); and **failure control** — native means Nora owns the
timeout and the R-5 policy directly, rather than supervising a subprocess that can die
mid-tag.

Honcho is accessed **directly on the ambient path** (no model involvement, as today at
`handler.py:226`) and **as a native tool on the engagement path**. Same client, two
access modes, neither via MCP.

### DEC-3 — Stateless invariant plus bounded Episodes (amends D4)

`design/00-overview.md` D4 locks "stateless / sessionless per tag". This spec **amends**
D4 rather than silently drifting from it.

Stateless-with-injection stays correct for group chat, for reasons that are structural,
not stylistic:

1. A group is an unbounded multi-party stream with no session boundary.
2. The room moves without Nora — between tags, hundreds of human messages may pass. A
   persistent session holds *her* history but is blind to *theirs*.
3. Sessions model a dyad; a transcript models N parties.
4. Restart loses nothing.
5. `context_window` is a cost ceiling under direct control.
6. **Decisive:** Honcho is already the durable memory. A session would be a *third*
   store, overlapping SQLite and Honcho and consistent with neither.

What sessions actually buy is **not conversational memory** — that is solved. It is
**working state within a task**: tool results, candidate lists, partial decisions. The
dinner example needs this; re-deriving it statelessly means re-calling Google Maps every
turn (slow, costly, and non-deterministic, so "the second one" becomes ambiguous).

Therefore: **injection is invariant; an Episode is additive.**

```python
@dataclass(frozen=True)
class Episode:
    episode_id: str; conversation_id: str; platform: str
    opened_at: float; last_active_at: float; turns: int
    messages: tuple[dict, ...]        # carried agent working state
    tool_results: tuple[dict, ...]    # cached cold-path tool output
    status: str                       # open | completed | expired
```

- **Opened** when the decision is multi-turn-capable and policy permits episodes for the
  conversation type.
- **Expired** by idle TTL (default 600 s), lazily on read plus a sweep.
- **Completed** explicitly — an `end_episode` tool, or a delivered reply closing the thread.
- **Stored** in the same SQLite database. Not Honcho (not semantic), not a third service.

The safety invariant, which is what makes this composable rather than a fork:

```python
EpisodeContinuation.assemble(msg, episode):
    transcript = await audit.recent(conv_id, window)   # ALWAYS — the room moved
    return EngagementContext(
        transcript=transcript,                          # fresh view of the room
        peer_card=...,
        carried_messages=episode.messages,              # working state, additive
        carried_tool_results=episode.tool_results,
    )
```

Strands' session manager may back the message list, but **the episode row is the source
of truth**, not Strands' persistence. Group DMs and 1:1 DMs then differ by *policy
config*, not by code path.

### DEC-4 — Failure disclosure policy (refines R-5)

R-5 ("never speak on failure") is blanket today. Cold-path external tools break that.

| Class | Example | Behaviour |
|---|---|---|
| `INFRASTRUCTURE` | LLM error, timeout, adapter down | **silent** (R-5 unchanged) |
| `GUARD_VETO` | ReplyGuard refused | **silent** |
| `REQUESTED_TOOL` | user asked for dinner, Maps is down | **disclosable**, opt-in per tool |

Silence is right when Nora is *broken* — invisible-when-broken is doctrine. But silence
after an explicit request to look something up reads as being ignored, which is a worse
failure. Default remains silent; disclosure is opt-in per tool
(`disclose_failure = true`, set only on cold-path MCP tools). Disclosed failures get a
**separate counter** so the `<1%` silent-failure SLO retains its meaning.

### DEC-5 — Persona stays in git; Langfuse for traces and scores

`PersonaStore` is not "a prompt". It is a multi-section composition (frontmatter with
`order`/`applies_to`/`version`), with audience filtering, a **security property**
(`sha256` over `persona_text` is what LeakGuard matches; `canary` for leak detection),
and **fail-safe reload** (last-good fallback on a broken edit). Langfuse prompt
management versions one prompt per name; fitting Nora would mean flattening the
composition or reimplementing assembly over N Langfuse prompts.

Two further reasons: the persona is the product and deserves review/blame/revert and
atomic deploy with the code that expects it; and hosting it would trade a fail-safe for
a hard startup dependency on the identity layer.

The one real benefit — trace↔prompt-version linkage — is obtained for ~5 lines by
putting `persona.sha256` and `persona.versions()` (already computed and logged at
`handler.py:155`) onto the OTel span as attributes.

Revisit only for runtime persona A/B with traffic splitting.

### DEC-6 — Strands Evals is the runner; Langfuse is the store

Not competitors. `strands-agents-evals` produces results (`OutputEvaluator`,
`TrajectoryEvaluator`, deterministic `ToolCalled`, red-team strategies). Langfuse stores
and compares them (scores, datasets, dataset runs). Eval results are pushed as Langfuse
scores; the adversarial corpus becomes a versioned Langfuse dataset instead of a YAML
file diffed by eye.

### DEC-7 — SQLAlchemy Core + Alembic

Confirmed from the brief's recommendation, on Nora-specific grounds: Nora has **one
table, no relations, no joins**. Tortoise's value is querysets and related-model
traversal — none of which Nora has to gain — so it would add ORM ceremony (model
classes, `Tortoise.init()` lifecycle, connection ownership) to a layer whose docstring
says "No semantics live here" (`db.py:3`). SQLAlchemy **Core** is schema metadata plus a
SQL expression builder: exactly the one capability wanted (declarative schema →
autogenerated migrations) and nothing else. Async is a non-issue — SQLAlchemy's async
SQLite driver *is* aiosqlite, already in the tree. Alembic then wins the migration-tool
tie on maturity (10+ years, documented SQLite batch mode, `alembic check` for drift)
against Tortoise's native migrations, which shipped February 2026.

### DEC-8 — `config.toml` + `identity.toml`

TOML for both: `tomllib` is stdlib on 3.11+, parsing is unambiguous (no YAML boolean
coercion surprises), and it matches `pyproject.toml`. **JSON was rejected for the
identity file** because that file is hand-edited by an operator by design (D6) and JSON
has no comments — today's `persons.yaml` uses comments meaningfully for future-platform
entries.

PyYAML does **not** leave the dependency tree: persona frontmatter stays YAML so a
section can still be promoted to a real Strands Skill without rewrite (S3).

Two files, not one, because they have different change cadences: identity changes when a
human joins the group; config changes on deploy.

### DEC-9 — Adapters own platform identity (amends design/04 mechanism, not principle)

`design/04-config-identity.md` requires that Nora derive her own account from the
signal-cli daemon rather than duplicating it in code. That principle is preserved and
generalised: identity discovery moves **onto the port**.

```python
class PlatformAdapter(Protocol):
    name: str
    async def resolve_identity(self) -> PlatformIdentity   # called once at startup
```

Signal implements it exactly as `derive_account()` does today (`account = ""` derives
from the daemon; non-empty overrides for multi-account). Telegram would use `getMe`;
Discord the gateway `READY` event. The registry calls it during startup and fails fast if
an enabled adapter cannot establish who it is — so R-12 works per-platform with no
special-casing, and `adapter._account` disappears.

**Nora's own account is never written to a config or identity file.** The identity file
describes *other people*. Nora's labels (`nora_sender_id`, `display_name`) remain config
values — labels, not the authoritative account.

Two identity questions, two sources:

| | Source | Why |
|---|---|---|
| Nora's own identity | the platform daemon, at startup | one source of truth (design 04) |
| Everyone else's identity | `identity.toml`, operator-maintained | D6 — the Deriver cannot know a number is Miles |

Platform-supplied `sender_name` remains a sanitized fallback for unmapped senders, never
fed to Honcho (A2 amendment, unchanged).

## 4. Architecture

### 4.1 Packages

```
nora/
  domain/       models.py, ports.py, errors.py, identity.py   ← zero framework imports
  ingest/       ambient pipeline + stages
  engagement/   pipeline, context assembly, episodes, policy, guards, prefill
  agent/        strands factory, routing, tools/{registry,native/,mcp}
  platforms/    registry + signal/
  stores/       audit (SQLAlchemy), episodes, memory (Honcho), migrations/
  persona/      loader                                        ← ported unchanged
  obs/          logging, metrics, tracing, health
  config/       pydantic-settings models
  app/          composition.py (the ONE composition root), runtime.py
```

`domain/` importing no framework is the rule that keeps the rest honest: if a port needs
aiohttp or Strands to express itself, the port is wrong.

### 4.2 Ports

```python
class PlatformAdapter(Protocol):
    name: str
    async def resolve_identity(self) -> PlatformIdentity
    async def listen(self, on_event) -> None
    async def parse(self, raw) -> NormalizedMessage | None
    async def send(self, target, text) -> bool
    async def health(self) -> dict

class AuditStore(Protocol):       # append / recent / set_delivery_status / prune / stats
class MemoryStore(Protocol):      # ensure_peer / feed / peer_card  — one impl, Protocol for fakes
class EngagePolicy(Protocol):     # should_engage(msg) -> EngageDecision
class ContextAssembler(Protocol): # assemble(msg, episode | None) -> EngagementContext
class ToolProvider(Protocol):     # tools(scope) -> list[Tool]
class EpisodeStore(Protocol):     # get / put / expire, TTL'd
```

### 4.3 Ingest pipeline (every message, deterministic, no agent — D1)

| # | Stage | Failure behaviour |
|---|---|---|
| 1 | `Parse` | `None` → drop silently (non-message envelopes) |
| 2 | `Audit` | metric + drop the message; **never kill the listener** (preserves `handler.py:74`) |
| 3 | `Identity` | unmapped → `None`, continue (D6) |
| 4 | `MemoryFeed` | mapped senders only; spawned off-path; never blocks ingest |
| 5 | `EngageGate` | `EngagePolicy` → `EngageDecision` |

```python
@dataclass(frozen=True)
class EngageDecision:
    engage: bool
    reason: str                    # "trigger_text" | "classifier" | "no_match"
    tier: ModelTier | None         # flash | deep
    confidence: float | None       # None for the deterministic trigger
```

`TriggerTextPolicy` ships now: `engage = is_tagged(body, trigger)`, `tier = flash`,
deterministic, **no LLM on the ambient path**. `ClassifierPolicy` later returns a score
against a threshold and may select `deep` — same interface, config swap. That is the A3
amendment landing without touching the pipeline.

### 4.4 Engagement pipeline (only when `engage=True`)

`Admission` (debounce, R-10 in-flight drop, R-12 self-trigger) → `ContextAssembly` →
`Persona` → `ToolBinding` → `Routing` (tier) → `Invoke` (timeout by tier) → `Deliver`
(ReplyGuard + Deliverer, audit-first) → `Feedback` (Honcho exchange feed; episode update
or close).

**R-1, R-5, R-10, R-12 are preserved verbatim. They move; they do not change.**

### 4.5 What this deletes

- `Handler` — dissolved into stages; ~8 responsibilities become ~8 small units.
- `invoke_fn` / `create_agent_fn` / `_prefills` / `_guards` — test back-doors leave
  production, taking the unbounded-list leak with them.
- `db_path` threading and the `METRICS` module singleton — both become injected
  dependencies from the composition root.
- `store/migrate.py` — retired; its v1→v2 job is done and history lives in git.

### 4.6 Composition root

Components receive **dependencies, not config**. A stage never sees `Settings`; it
receives an `AuditStore`. This is what makes stages testable in isolation and prevents
the god object reforming.

```python
# app/composition.py — the ONLY place that reads Settings
async def build_app(settings: Settings) -> App:
    engine   = create_async_engine(settings.store.url)     # ONE engine, a local, not a global
    audit    = SqlAuditStore(engine, settings.store)
    episodes = SqlEpisodeStore(engine, settings.store.episode_ttl_seconds)
    memory   = HonchoStore(settings.memory)
    tools    = ToolRegistry.from_config(settings.tools, audit=audit, memory=memory, ...)
    adapters = AdapterRegistry.from_config(settings.platforms)
    ...
```

Production, tests, **and evals** all call this function. Tests and evals substitute fakes
and in-memory SQLite. This is what removes `evals/harness.py`'s import of
`tests.integration.harness` and unblocks the live-model path deferred in its docstring.

## 5. Configuration

### 5.1 `config.toml`

```toml
[platforms.signal]
enabled        = true
base_url       = "http://127.0.0.1:18080"
account        = ""                       # empty = derive from the signal-cli daemon
nora_sender_id = "nora"

[llm]                                     # gateway-level, platform-independent
base_url    = "http://localhost:20128/v1"
api_key_env = "OMNIROUTE_API_KEY"

[llm.tiers.flash]
model = "deepseek-v4-flash"
max_tokens = 400
temperature = 0.7
timeout_seconds = 20
[llm.tiers.flash.limits]
turns = 6
total_tokens = 8000

[llm.tiers.deep]
model = "deepseek-v4-pro"
max_tokens = 1200
temperature = 0.5
timeout_seconds = 90                      # tool-using research needs room
[llm.tiers.deep.limits]
turns = 20
total_tokens = 40000

[memory]                                  # Honcho
base_url     = "http://127.0.0.1:8000"
workspace_id = "nora"

[store]
url                  = "sqlite+aiosqlite:///data/messages.db"   # replaces buffer.db_path
max_per_conversation = 500
context_window       = 40
episode_ttl_seconds  = 600

[engagement]
policy = "trigger_text"                   # → "classifier" later, no code change
[engagement.trigger]
text = "nora.000"
min_interval_seconds = 5
[engagement.episodes]
enabled_for = []                          # group stays single-shot; DMs enable later

[tools.mcp.google_maps]
enabled = false
command = "npx"
args    = ["-y", "@modelcontextprotocol/server-google-maps"]
scope   = ["deep"]                        # bound only on the deep tier
disclose_failure = true
timeout_seconds  = 15
[tools.mcp.google_maps.env]
GOOGLE_MAPS_API_KEY = "${GOOGLE_MAPS_API_KEY}"

[persona]
dir = "nora/persona"
reload = "mtime"
strict = true
chinese_script = "traditional"
canary = ""
display_name = "Nora"

[obs.logging]
level = "INFO"
path = "data/nora.log"
format = "json"

[obs.metrics]
port = 9100

[obs.tracing]
enabled  = true
exporter = "otlp"
endpoint = "http://127.0.0.1:3100/api/public/otel"   # verified 2026-08-04
headers_env = "LANGFUSE_OTEL_HEADERS"
```

Notes on the changes from today's `config.yaml`:

- `store.url` is a SQLAlchemy URL rather than a path, so Postgres later is a config edit.
- **`trigger.invocation_timeout_seconds` is removed.** It is replaced by per-tier
  `timeout_seconds`, which is authoritative — a tool-using `deep` invocation legitimately
  needs a longer budget than a `flash` reply, and a single global timeout cannot express
  that. Exactly one timeout governs an invocation: the one on its tier.
- The single hardcoded `OMNIROUTE_API_KEY` check (`config.py:188`) generalises:
  pydantic-settings validates **all** declared required secrets at startup, so a missing
  Maps key fails fast rather than at the first dinner question.
- **Base-relative path resolution is preserved.** Relative paths (`store.url`'s file
  component, `obs.logging.path`, `persona.dir`) resolve against the config file's
  directory, with `~` expansion, exactly as `config._resolve_path` does today. This is a
  behaviour to carry over, not to redesign.
- Strictness is preserved: `extra = "forbid"` on every settings model, so an unknown or
  misspelled key remains a startup failure rather than a silent default. A typo in
  `trigger.text` would silently disable Nora, which is why this matters.

### 5.2 `identity.toml`

```toml
[persons.miles]
name = "Miles"
[persons.miles.platforms]
signal = "+85212345678"
# discord  = "123456789012345678"     # future
# telegram = "123456789"              # future
```

`honcho_peer_id = person_id` by construction (A2). A reverse index keyed by
`(platform, sender_id)` is built at startup. Validation moves to pydantic; the domain
logic (`resolve`, `display_name`, duplicate detection) stays custom.

## 6. Infrastructure

**Persistence.** SQLAlchemy Core `MetaData` in `stores/schema.py` is the single source of
truth. Alembic with `render_as_batch=True` for SQLite's `ALTER` limitations. The initial
migration reproduces the current v2 schema exactly — same columns, same
`CHECK (conversation_type IN ('group','dm'))`, same two indexes — then `alembic stamp
head` on the existing 0-row database so the file becomes the baseline and nothing is
dropped or rebuilt. `alembic check` becomes a CI drift gate. `PRAGMA journal_mode=WAL` /
`busy_timeout=5000` / `foreign_keys=ON` move into an engine-level connect hook instead of
being re-issued per call.

The `db.py` facade semantics (`append`, `recent`, `stats`, `set_delivery_status`,
`prune`) are preserved on `AuditStore`; the per-call `path` parameter is dropped in
favour of the composition-root-owned engine, with all call sites updated in the same
commit.

**Observability** — three signals, three consumers:

- **structlog** with the stdlib bridge. Secret redaction generalises from one key to the
  N declared secrets. Heartbeat extra fields preserved. `tag_id` bound as a contextvar so
  every log line in an engagement correlates without manual threading.
- **prometheus-client**. Metric names and HELP text unchanged; `/metrics` output stays
  compatible. Registry injected, not a module singleton. Adds
  `nora_stage_duration_seconds{stage=...}` so latency can be attributed to Honcho, the
  LLM, or the send.
- **OTel → Langfuse** via the existing `exporter = "otlp"`. Strands' GenAI semantic
  conventions supply spans, token counts, and tool calls at no cost. Additions:
  `persona.sha256` and `persona.versions` as span attributes (DEC-5), and `tag_id` as the
  trace correlation ID.

Langfuse is already running on this host: web UI on `:3100`, `/api/public/health` → 200,
`/api/public/otel/v1/traces` → 401 without credentials (Basic auth from the public/secret
key pair, supplied via `LANGFUSE_OTEL_HEADERS`).

Health keeps **`GET /health`** for the Honcho probe — the 2026-08-04 fix stands, and must
not regress — and gains an `adapters[]` array so a wedged listener is visible per platform.

## 7. Staging

**Stage 1 changes structure, not behaviour.** Every extension point ships as a port with
exactly one implementation: the current one. If stage 1 changes any observable behaviour,
that is a bug, and the existing 45 test files are the oracle.

| Seam | Stage 1 ships | Later |
|---|---|---|
| `EngagePolicy` | `TriggerTextPolicy` — deterministic, no LLM | `ClassifierPolicy` (A3) |
| `ContextAssembler` | `TranscriptInjection` — stateless, as today | `EpisodeContinuation` + `EpisodeStore` |
| `ToolRegistry` | the native four only | MCP servers mounted from config |
| Model tier | `flash` wired | `deep` routing + escalation |
| `AdapterRegistry` | Signal only | Telegram / Discord / WhatsApp |
| Failure policy | `INFRASTRUCTURE` / `GUARD_VETO` → silent | `REQUESTED_TOOL` disclosure |

Deliberately **not built** in stage 1: the `EpisodeStore` implementation, the TTL sweeper,
and any MCP provider instance. The *ports* cost nothing and make these cheap later; the
implementations would be dead code until DMs and cold-path tools exist. Nothing
speculative is built; nothing needs restructuring to add.

## 8. Phases

One concern per commit. Every phase gated on `pytest` green + `ruff check` clean +
`alembic check` clean.

| P | Concern | Risk |
|---|---|---|
| 1 | Foundation: `domain/` models + ports, `config/` pydantic-settings, `config.toml` + `identity.toml`, composition-root skeleton. **Includes README + runbook updates** (`test_docs.py` pins README content, and the config format changes) | breaking config change |
| 2 | Persistence: SQLAlchemy + Alembic behind `AuditStore`, baseline stamp, retire `migrate.py`, rewrite `test_migration.py` | medium — own verification cycle |
| 3 | Pipelines: `ingest/` + `engagement/`, **Handler dissolved** | **highest** — behaviour tests are the oracle |
| 4 | Platforms: registry + `resolve_identity()`, drop `adapter._account` | low |
| 5 | Agent: tool registry (native only), tier routing wired | low |
| 6 | Obs: structlog, prometheus-client, OTLP → Langfuse | low |
| 7 | Evals: composition-root reuse, drop the `tests/` import | low |

**Final gate:** `docker build --network=host` (BuildKit cannot resolve deb.debian.org via
the default bridge on this host), `docker compose up -d --no-build`,
`curl http://127.0.0.1:9100/health` → HTTP 200 with `sqlite.reachable: true`. The
signal-cli daemon is not touched at any point.

## 9. Testing

- Store tests use in-memory SQLite.
- `app/composition.py` is the single graph builder for production, tests, and evals.
- `evals/harness.py` stops importing `tests.integration.harness`; `invoke_fn` /
  `create_agent_fn` leave production code entirely.
- `test_migration.py` is rewritten against Alembic with behaviour assertions identical.
- Behaviour assertions across all existing suites are preserved unchanged — they are the
  contract that stage 1 must not violate.

## 10. Optional, pending decision

`PlatformAdapter.list_contacts()` plus a `nora persons suggest` CLI that queries adapters
for known contacts and emits `identity.toml` stubs for operator confirmation. This does
not violate D6 — the operator still approves every mapping — it only removes transcription
work. **Not included unless Miles asks for it.**
