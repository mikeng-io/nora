---
kind: spec
title: "MCP tool provider layer — DEC-2's cold path, built"
description: "A generic, config-only way to mount any MCP server as tools Nora can call in conversation. Pulse (a game-server monitor) is the first server configured; a second server later needs zero new Python. Implements the target ARCHITECTURE.md already names (`[tools.mcp.<name>]`) and closes out DEC-2's 'not built' status."
tags: [nora, spec, mcp, tools, pulse, policy]
timestamp: '2026-08-08'
status: settled — READY FOR PLAN
derives_from:
  - design/09-decision-index.md
  - design/12-prompt-tool-skill-boundaries.md
  - design/03-strands-reuse.md
  - design/10-identity-and-policy-schema.md
  - ARCHITECTURE.md
---

# MCP tool provider layer

## 0. What this document is

Nora's tool layer today is entirely native (`nora/agent/tools.py`): `now`,
`calculate`, `transit`, `read_buffer`, `send_reply`, plus the room-scoped
`group_members`/`lookup_person`/`about`. `DEC-2` drew the line for what comes
next — *"hot path (Honcho, SQLite audit, platform send) is native; cold path
(Maps, web search) is MCP"* — and marked it **not built**. `ARCHITECTURE.md`'s
"how do I…" table already names the target shape: *"add an MCP server →
`[tools.mcp.<name>]` — no Python."* This spec is that phase, motivated by the
first concrete need for it: **Pulse**, a read-only MCP server on another
Tailscale node (`orion`) that reports on a game server (player status,
leaderboards, world state, recent events).

**Why generic and not Pulse-specific:** a bespoke Pulse integration would work
for Pulse and nothing else; the architecture already committed (DEC-2,
`ARCHITECTURE.md`) to config-only MCP mounting as the intended shape, and a
second MCP server (Google Maps has been discussed previously) should cost
nothing but a config block once this exists. Building it bespoke now means
redoing this work at the second server instead of the first.

## 1. Scope

**In scope:**
- A `tools.mcp` config section (`nora/config.py`), one entry per server.
- `nora/agent/mcp.py` — connects to each enabled server, discovers its tools
  via `list_tools_sync()`, and hands them to `ToolRegistry` with a per-server
  budget.
- A `policy.toml` per-conversation tool allowlist, deny-by-default, enforced
  at tool-assembly time (never at egress — design/12 §3's rule).
- Pulse configured as the first (and, at ship time, only) entry.

**Out of scope (explicitly deferred, not this spec):**
- The `SkillStore` / on-demand skill layer (`nora/skills/`) is currently
  inert; this spec does not activate it. MCP tools are bound directly, the
  same way native tools are (design/12 §3: *"An MCP tool (Phase 10) is bound
  the same way, and its arguments are treated as model-supplied"*) —
  packaging MCP tools into skill bundles is future work design/12 §5 already
  names separately.
- Per-tool budget overrides (Approach B considered and rejected below).
- Any second MCP server's actual config — the mechanism is generic, but only
  Pulse ships with this spec.
- DM-path tool gating — policy.toml's `[dm]` block exists but is unreachable
  in v2 (parser drops DMs); the conversation allowlist below only needs to
  cover `[[conversations]]`.

## 2. Config schema

### 2.1 `config.toml`

```toml
[tools.mcp.pulse]
enabled = true
base_url = "https://orion.tail0a1b2c3d.ts.net/pulse/mcp"
token_env = "PULSE_MCP_TOKEN"
budget = 5
```

- `enabled` defaults `False` — same off-until-turned-on shape as `RouteConfig`
  and `ShortenerConfig`; a section existing in a committed config doesn't mean
  it's live.
- `base_url` and `token_env` are per-server on purpose: a second server needs
  its own endpoint and its own credential.
- `token_env` **names** an environment variable (DEC-8's `api_key_env`/
  `token_env` shape, used identically by `RouteConfig.api_key_env`,
  `ShortenerConfig.token_env`, and `PresenceConfig`'s `password_env`/
  `hkgov_token_env` fields) — the value is never in `config.toml`. It lives
  in `.env` (gitignored, docker-compose injects it into the container), not
  in a file under `~/.config/`, which was the original ask and doesn't match
  this repo's secret-handling convention anywhere else.
- `budget` is the flat per-server tool-call budget (§4).

### 2.2 `nora/config.py`

A new frozen model:

```python
class McpMountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str
    token_env: str
    budget: int
```

Named `McpMountConfig`, not `McpServerConfig` — `strands.tools.mcp.mcp_client`
already defines its own `MCPServerConfig` TypedDict, and `nora/agent/mcp.py`
will have both types in scope at once (one describing *our* config section,
one describing Strands' own connection parameters). Same words, different
casing, both live in the same file — a collision worth avoiding by naming
rather than by import aliasing.

`ToolsConfig.mcp: dict[str, McpMountConfig] = {}` (keyed by server name,
`"pulse"` etc.), consistent with every other optional/defaulted config
section in this file. A second server is a second dict entry — no schema
change.

### 2.3 `policy.toml`

`[[conversations]]` gains an optional field:

```toml
[[conversations]]
platform = "signal"
id       = "..."
label    = "..."
persons  = ["miles_novak"]
tools    = ["pulse"]
```

Absent or empty `tools` means **no MCP tools** for that conversation —
deny-by-default, matching R-5's existing posture for the whole file. This is
a policy-file field, not a config-file field, because CLAUDE.md's own
distinction applies directly: *"who may talk to her, and where"* is exactly
what "which MCP tools exist in this conversation" is — a trust decision, not
a deployment one.

## 3. Components

**`nora/agent/mcp.py`** (new). The only module besides `factory.py` that
touches `strands`'s MCP client machinery — consistent with the package
table's existing rule that `agent/` is the sole package allowed to import
`strands`. Built on `strands.tools.mcp.mcp_client.MCPClient.load_servers()`
— an existing Strands classmethod, not something this spec invents — which
takes an `mcpServers`-shaped mapping (`{name: {"url": ..., "headers": ...,
"continue_on_error": ...}}`), auto-detects transport, and supports
`${env:VAR}` interpolation in string values natively. `nora/agent/mcp.py`'s
one function translates `dict[str, McpMount]` (the plain domain type §3.1
below, never `nora.config`) into that shape — putting
`"Authorization": "Bearer ${env:<token_env>}"` in `headers` so the token
read happens inside Strands' own interpolation, not in Nora's code — sets
`continue_on_error: True` on every entry (a dead Pulse server must not
prevent Nora from starting), calls `load_servers()`, then `.start()` and
`.list_tools_sync()` on each returned client. `continue_on_error` only
covers connection failure (`start()`); a failure during `list_tools_sync()`
after a successful connect still raises and must be caught explicitly (§5).
Connection happens once at process start (`nora/app/composition.py`), the
same lifecycle as `factory.create_client` — never per-tag.

`build_mcp_tools(mounts: dict[str, McpMount]) -> dict[str, McpToolGroup]` is
the module's one function, where `McpToolGroup` is a plain frozen dataclass
(also in `nora/agent/mcp.py`): `budget: int` (copied from the `McpMount`)
and `tools: tuple[Any, ...]` (the `MCPAgentTool` objects `list_tools_sync()`
returned — typed `Any` rather than importing Strands' own type into a
signature `ToolRegistry` will also use, matching how `ports.py` already
keeps its `adapt()` return typed `Any` for the same reason). A server that
fails to connect or returns zero tools is simply absent from the returned
dict, never a `None` value or an empty-but-present entry — callers use
`dict.get(name)` and treat "not present" as the one absent state, matching
this codebase's existing avoidance of a second falsy-but-different state
(R-22's "could not ask" vs "nothing known" distinction, applied here to
"not configured" vs "configured, discovered, present").

### 3.1 The `nora.agent` / `nora.config` layer boundary

`nora/agent/mcp.py` may not import `nora.config` — import-linter's "only app
and __main__ read config" contract (`pyproject.toml`'s
`[[tool.importlinter.contracts]]`, `name = "only app and __main__ read
config"`) names `nora.agent` as a source module and `nora.config` as
forbidden, exactly the constraint `ToolRegistry.__init__`'s
`reminder_caps: ReminderCaps` already satisfies (`nora/agent/registry.py`,
composition.py's comment: *"`nora.agent` may not import `nora.config`... THIS
is the conversion point"*). This spec follows the identical shape: a plain
frozen `McpMount` dataclass in `nora/agent/mcp.py` (`name`, `base_url`,
`token_env`, `budget`), and `nora/app/composition.py` converts each
`cfg.tools.mcp[name]` (`McpMountConfig`, pydantic, `nora.config`) into one
`McpMount` before calling `build_mcp_tools()` — the same
`SomeDomainType(**cfg.something.model_dump())` pattern already used for
`ReminderCaps`.

**`nora/agent/registry.py` — new surface, not a reuse of an existing merge,
and narrower than the previous draft of this spec claimed.**
`ToolRegistry` is a **process-wide singleton**, built once by the
composition root (its own module docstring: *"a plain, constructed
collaborator... built once per process by the composition root"* —
`nora/app/composition.py:465`) — "per-tag" behavior comes from `.build()`/
`.specs()` being CALLED with fresh per-tag arguments (`Room`,
`conversation_id`, …), not from constructing a new `ToolRegistry` per tag.
Today `ToolRegistry.budgets()` is a straight `dict(NATIVE_TOOL_BUDGETS)` read
of a module-level constant, and `capability()`/`tool_names()`/
`terminal_tools()`/`live_only_tools()` all read that same static
`NATIVE_TOOLS` table and nothing else. There is no existing mechanism for
combining that static table with a dynamically-discovered, per-instance set
of MCP tool groups — this spec introduces the capability, it doesn't extend
one that already exists:

- `ToolRegistry.__init__` gains an `mcp_tools: Mapping[str, McpToolGroup] =
  {}` collaborator parameter, held the same way `transit`/`route`/etc. are.
- `.budgets()` folds in a flat entry per discovered MCP tool name
  (`{tool.tool_name: group.budget for group in mcp_tools.values() for tool
  in group.tools}`), **unconditionally, for every tag** — this dict is never
  rendered into a prompt or otherwise disclosed (it is `ReplyGuard`'s
  internal per-call-count ceiling, `nora/agent/guards.py`), so an entry for
  a tool a given conversation can never actually call is inert, not a leak.
  No conversation parameter needed here.
- `.specs()`/`.build()` gain a new parameter, `allowed_mcp: frozenset[str] =
  frozenset()` (server names, e.g. `{"pulse"}`), and append the Strands tool
  objects from only the MCP groups named in it to what they already return.
  **This is the actual enforcement point** — a tool not in the returned list
  cannot be called, because `Agent(tools=...)` only knows what it's handed.
- `capability()`, `tool_names()`, `terminal_tools()`, `live_only_tools()`
  stay **completely unchanged**. MCP tools are deliberately never added to
  `NATIVE_TOOLS` (that table is hand-authored, `when`-per-entry, no
  default — an auto-discovered tool has no such clause to give it) and so
  never enter the generated doctrine capability text at all. This is a
  correction of the previous draft, which claimed `capability()` would stay
  correctly scoped "for free" because `ToolRegistry` was (wrongly assumed to
  be) built per-tag — it is not, `capability()` is generated ONCE at startup
  by a throwaway `ToolRegistry()` (`nora/app/composition.py:241-242`, feeding
  `DoctrineStore`), fully disconnected from any conversation. Excluding MCP
  tools from that generation entirely sidesteps the question rather than
  needing to answer it, and avoids touching DEC-28/DEC-29's tested
  invariant ("no doctrine file may name a tool") at all. Nora relies on each
  MCP tool's own schema (already in the request, per `capability()`'s own
  documented reasoning — schemas are never repeated in doctrine text) for
  "what this does"; there is no authored "when to reach for it" for an
  auto-discovered tool, matching spec §1's decision not to build the skill
  layer's packaging for this phase either.

**Conversation-scoped filtering — `EngagementPipeline` needs a new
dependency it does not have today.** Policy.toml's existing gating happens
at INGEST (`nora/policy.py`'s loader; the room-membership check at
`nora/ingest/stages/scope.py`, DEC-18: *"the ROOM is checked BEFORE the
audit write"*) — **not** `nora/ingest/policy.py`, which is
`TriggerTextPolicy`, the unrelated trigger-word engagement gate. By the time
`nora/engagement/pipeline.py::EngagementPipeline._run_now` builds tools
(`nora/engagement/pipeline.py:281`, `self._tool_registry.build(...)`), the
tag has already passed that ingest-side room check — but `EngagementPipeline`
itself is never handed the `Policy`/allowlist object (`nora/app/composition.py`
threads `allowlist = load_policy(cfg.policy_file)` into the INGEST pipeline
only, at composition.py:307/579). This spec adds `policy: Policy` to
`EngagementPipeline.__init__`, threaded from the same `allowlist` instance
composition.py already builds. `nora/policy.py`'s `Conversation` dataclass
gains a `tools: frozenset[str] = frozenset()` field (populated from
`_ConversationEntry.tools: list[str]`, §2.3); `_run_now` resolves
`self._policy.conversation(msg.platform, msg.conversation_id)` (never `None`
here — ingest already proved it isn't) and passes its `.tools` as
`allowed_mcp` into `self._tool_registry.build(...)`. An unauthorized
conversation's `allowed_mcp` is empty, so no `pulse_*` schema is ever in the
list handed to `Agent(tools=...)` — design/12 §3's "scope enforced at
assembly, never at egress" rule, applied to a new kind of scope (server
name) the same way it already applies to person/group scope.

## 4. Budget: flat per-server (Approach A)

Every tool a given MCP server advertises shares that server's one configured
`budget`. Considered and rejected: a per-tool override
(`[tools.mcp.pulse.overrides.<tool_name>] budget = N`) — more flexible, but
speculative for a server that doesn't exist yet, and none of Pulse's 7 tools
(all cheap read-only queries) need differentiated pricing. YAGNI; add
overrides when a real server actually needs them.

All MCP tools are **non-terminal** (they feed Nora's reply; only
`send_reply` sends it — DEC-32's `terminal_tools() == frozenset({"send_reply"})`
is unaffected). Every tool call is synchronous inside the Strands loop
regardless of `time_shiftable`'s value — that field doesn't control blocking,
it controls whether `ToolRegistry.live_only_tools()` renders a "this
describes the current instant only, don't quote it later" warning into the
capability text (DEC-40's three values: `None` = no time dimension at all,
e.g. `calculate`; `False` = live-only and decays, e.g. `now`/`transit`;
`True` = takes a target time, e.g. `route`). Pulse's tools — player status,
world state, recent events — describe the current state of a live server,
so they get `time_shiftable=False`, the same value as `now`, not the `None`
value `calculate` gets; the plan should set this per discovered tool (or per
server, defaulting all of a server's tools to `False` unless there's reason
to differ) rather than leave it unset.

## 5. Error handling (R-22: "could not ask" ≠ "nothing known")

- **Startup, server unreachable or token missing/rejected:** that server's
  tools are absent from the registry entirely (logged, a metric bump —
  exact counter name TBD in the plan). Nora starts normally. If someone asks
  a Pulse question in an authorized conversation, the model simply has no
  `pulse_*` tool to call — indistinguishable from any other capability Nora
  doesn't have, never a crash of the whole process for one server's outage.
- **Mid-conversation, Pulse goes down after a successful startup connection:**
  the tool call raises/times out inside the loop; this surfaces as an
  ordinary tool-execution failure the same way a native tool failure does
  today (the plan confirms the exact existing mechanism — exception caught by
  the loop vs. a typed error return — and follows it, not a new pattern).
  Nora reports she couldn't check rather than fabricating an answer from
  stale or partial data.
- **Unauthorized conversation:** never sees the tools exist (§3) — enforced
  at assembly, so there's no "declined to call" state to handle; the tool
  schema was never in the prompt.

## 6. Testing

Per CLAUDE.md's rule against a fake that answers every request the same
way, `nora/agent/mcp.py` is tested against a small **local fake MCP server**
(the `mcp` package's own server-side, `streamablehttp` transport, run
in-process) exposing a couple of fake tools with real success / auth-failure
/ malformed-response shapes — not a mock of the Strands client that always
returns one canned value. `mcp` is currently only a transitive dependency
(pulled in by `strands-agents`) — test code importing `mcp.server` directly
needs it pinned explicitly via `uv add --dev mcp`, not left to keep working
by accident because something else happens to depend on it.

**Known gate gap, accepted as-is:** `nora/agent` is deliberately excluded
from the strict-mypy scope (`pyproject.toml`'s override list is
`nora.domain`, `nora.config`, `nora.app`, `nora.infra`, `nora.presence` —
`nora.agent` is named in that file's own comment as an intentional
exclusion, matching CLAUDE.md's gate command). `nora/agent/mcp.py` — reading
a bearer token from the environment, opening a network client at startup —
runs under ordinary, non-strict mypy like the rest of `nora/agent`. Not a
new gap this spec introduces, but worth naming rather than assuming away:
the plan should decide whether that's fine (consistent with the rest of the
package) or whether this module's env/network surface earns it stricter
typing as a one-off.

- **`nora/agent/mcp.py`**: tools get discovered and budgeted correctly; a
  rejected token or connection failure yields "server's tools absent," never
  a raised exception out of startup.
- **`nora/config.py`**: `tools.mcp` parses a dict of servers, `enabled`
  defaults `False`, `extra="forbid"` still rejects unknown keys, same
  `*_env`-shape tests the other integrations already have.
  Update `config.example.toml` with the `[tools.mcp.pulse]` block (disabled)
  matching every other optional section's documented-but-off convention.
- **`policy.toml` loader**: `tools` field parses, defaults to empty,
  `extra="forbid"` preserved.
- **Assembly-time gating** (new behavioral rule — needs a `design/09`
  decision-index entry, `DEC-71`, and a named test in
  `tests/test_acceptance_rules.py::RULE_TESTS`, `R-40`): a tool from a
  server not in a conversation's `allowed_mcp` must not appear in
  `ToolRegistry.build()`'s returned list for that conversation's tag —
  proven by asserting on the assembled tool set, not by asserting the model
  declined to use it. `EngagementPipeline` correctly resolves `allowed_mcp`
  from `Policy.conversation(...).tools` before calling `.build()`.
- **`ToolRegistry`**: existing tests extended to confirm `.budgets()`
  includes every discovered MCP tool's flat per-server budget unconditionally
  (§3 — this dict is never disclosed, so it does not need conversation
  scoping), and that `capability()`/`tool_names()`/`terminal_tools()`/
  `live_only_tools()` are byte-for-byte unaffected by any MCP server being
  configured — they read only `NATIVE_TOOLS`, which MCP tools never enter.

## 7. What ships with this spec vs. later

Ships: the generic `tools.mcp` mechanism, `nora/agent/mcp.py`, registry
merge, policy allowlist, Pulse configured and enabled, all six tests above.

Explicitly not this spec (§1): skill-layer packaging, per-tool budget
overrides, any second server's config, DM-path gating.
