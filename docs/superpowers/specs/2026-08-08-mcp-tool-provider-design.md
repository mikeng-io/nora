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
  `token_env` shape, used identically by `RouteConfig`, `ShortenerConfig`,
  `Life360Config`, `HkGovConfig`) — the value is never in `config.toml`. It
  lives in `.env` (gitignored, docker-compose injects it into the container),
  not in a file under `~/.config/`, which was the original ask and doesn't
  match this repo's secret-handling convention anywhere else.
- `budget` is the flat per-server tool-call budget (§4).

### 2.2 `nora/config.py`

A new frozen model:

```python
class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str
    token_env: str
    budget: int
```

and `ToolsConfig.mcp: dict[str, McpServerConfig] = {}` (keyed by server name,
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
`strands`. One function:

```python
def build_mcp_tools(cfg: dict[str, McpServerConfig]) -> dict[str, McpToolGroup]:
    ...
```

For each `enabled` entry: read the token from `os.environ[token_env]`
(missing/empty is a **startup warning + that server's tools absent**, never a
crash — see §5), open an `MCPClient` against `base_url`, call
`list_tools_sync()`, and return the discovered Strands tool objects paired
with that server's `budget`. Connection happens once at process start
(`nora/app/composition.py`), the same lifecycle as `factory.create_client` —
never per-tag.

**`nora/agent/registry.py`**. `ToolRegistry.build()` takes the MCP tool
groups as an additional constructor input and merges them with the native
`ToolSpec`s the same way it already merges the room-scoped tools; `.budgets()`
folds in each MCP tool's per-server budget. `ReplyGuard` (DEC-10) needs no
changes — it already consumes whatever `budgets()` returns.

**Conversation-scoped filtering.** Whatever resolves "this tag's
conversation" at engagement time (`nora/engagement/pipeline.py` /
`nora/ingest/policy.py`, where policy.toml gating already lives per the
reminders spec's round-1 finding) intersects the registry's available MCP
tool groups against that conversation's `tools` allowlist **before** the
tool list is handed to `ToolRegistry.build()` for that tag. An unauthorized
conversation's model call never sees a `pulse_*` schema — design/12 §3's
"scope enforced at assembly, never at egress" rule, applied to a new kind of
scope (server name) the same way it already applies to person/group scope.

## 4. Budget: flat per-server (Approach A)

Every tool a given MCP server advertises shares that server's one configured
`budget`. Considered and rejected: a per-tool override
(`[tools.mcp.pulse.overrides.<tool_name>] budget = N`) — more flexible, but
speculative for a server that doesn't exist yet, and none of Pulse's 7 tools
(all cheap read-only queries) need differentiated pricing. YAGNI; add
overrides when a real server actually needs them.

All MCP tools are **non-terminal** (they feed Nora's reply; only
`send_reply` sends it — DEC-32's `terminal_tools() == frozenset({"send_reply"})`
is unaffected) and **non-time-shiftable** (DEC-40) — their output has to be in
hand before Nora can answer, so they block the turn the same way `now` and
`calculate` do.

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
returns one canned value.

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
  server not in a conversation's `tools` allowlist must not appear in the
  tool list assembled for that conversation's tag — proven by asserting on
  the assembled tool set, not by asserting the model declined to use it.
- **`ToolRegistry`**: existing tests extended to confirm merged MCP budgets
  appear in `.budgets()` without colliding with native tool budget keys.

## 7. What ships with this spec vs. later

Ships: the generic `tools.mcp` mechanism, `nora/agent/mcp.py`, registry
merge, policy allowlist, Pulse configured and enabled, all six tests above.

Explicitly not this spec (§1): skill-layer packaging, per-tool budget
overrides, any second server's config, DM-path gating.
