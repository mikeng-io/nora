# MCP Tool Provider Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Nora mount any MCP server as conversation-callable tools purely by config, with Pulse (a game-server monitor reachable over Tailscale) as the first configured server.

**Architecture:** A new `nora/agent/mcp.py` builds Strands `MCPAgentTool` objects at process startup via `MCPClient.load_servers()`, keyed by server name. `ToolRegistry` grows a second, dynamically-discovered tool axis alongside its existing static `NATIVE_TOOLS` table — folded into `.budgets()` unconditionally and into `.specs()`/`.build()` behind a new `allowed_mcp` parameter. `EngagementPipeline` resolves that parameter per-tag from `policy.toml`'s new per-conversation `tools` allowlist, so an unauthorized conversation's model call never sees a `pulse_*` schema.

**Tech Stack:** Python 3.11+, Strands Agents SDK (`strands-agents==1.50.2`, already pinned), `mcp` (currently transitive, this plan pins it as an explicit dev dependency), pydantic (config), pytest/pytest-asyncio.

## Global Constraints

- `nora/agent/**` may import `strands`; no other package may (`tests/test_boundaries.py::test_strands_import_boundary`, enforced).
- `nora.agent` may **not** import `nora.config` (`pyproject.toml`'s `"only app and __main__ read config"` import-linter contract). Any config value `nora/agent/mcp.py` or `nora/agent/registry.py` needs arrives as a plain dataclass built by `nora/app/composition.py`.
- Every new/changed pydantic model keeps `model_config = ConfigDict(extra="forbid", frozen=True)`.
- Secrets are never inlined in `config.toml`: a `token_env` field **names** an environment variable; the value lives in `.env` (gitignored).
- `policy.toml` stays deny-by-default: an absent/empty allowlist field means **no** access, never "everything."
- Run before every commit in this plan: `uv run pytest -q`, `uv run ruff check nora/ evals/ tests/`, `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`, `uv run lint-imports`.
- Spec: `docs/superpowers/specs/2026-08-08-mcp-tool-provider-design.md` (read in full before starting; every task below cites it).

---

### Task 1: Config schema — `tools.mcp` in `nora/config.py`

**Files:**
- Modify: `nora/config.py` (add `McpMountConfig`, `ToolsConfig`, `Config.tools` field)
- Modify: `config.example.toml` (add a disabled `[tools.mcp.pulse]` example block)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `nora.config.McpMountConfig` (`enabled: bool`, `base_url: str`, `token_env: str`, `budget: int`), `nora.config.ToolsConfig` (`mcp: dict[str, McpMountConfig]`), `Config.tools: ToolsConfig`.

- [ ] **Step 1: Update `config.example.toml`**

Add near the `[route]`/`[shortener]` sections (`config.example.toml`, after the `[shortener]` block) — this is fixture data the tests below read via `toml_path`, not an implementation step, so it comes first without breaking TDD ordering (the tests still fail at Step 3 because `Config` has no `tools` field yet):

```toml
# MCP tool providers (spec: docs/superpowers/specs/2026-08-08-mcp-tool-provider-design.md).
# Off unless enabled=true; the token value goes in .env, never here.
[tools.mcp.pulse]
enabled = false
base_url = "https://orion.tail0a1b2c3d.ts.net/pulse/mcp"
token_env = "PULSE_MCP_TOKEN"
budget = 5
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_config.py`, using the file's real `toml_path` fixture (`tests/test_config.py:14-19` — copies `config.example.toml` to a tmp file and stubs `OMNIROUTE_API_KEY`) and the same string-surgery pattern `test_the_media_section_is_optional_and_defaults_to_custody_on` (`tests/test_config.py:165-183`) uses to prove a section is truly optional:

```python
def test_tools_mcp_is_optional_and_defaults_to_empty(toml_path):
    text = toml_path.read_text()
    head, _, tail = text.partition("[tools.mcp.pulse]\n")
    assert tail, "config.example.toml no longer declares [tools.mcp.pulse]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.tools.mcp == {}


def test_tools_mcp_parses_a_configured_server(toml_path, monkeypatch):
    monkeypatch.setenv("PULSE_MCP_TOKEN", "test-token")
    toml_path.write_text(toml_path.read_text().replace(
        "[tools.mcp.pulse]\nenabled = false", "[tools.mcp.pulse]\nenabled = true"))

    cfg = load(toml_path)

    assert cfg.tools.mcp["pulse"].enabled is True
    assert cfg.tools.mcp["pulse"].base_url == "https://orion.tail0a1b2c3d.ts.net/pulse/mcp"
    assert cfg.tools.mcp["pulse"].token_env == "PULSE_MCP_TOKEN"
    assert cfg.tools.mcp["pulse"].budget == 5


def test_tools_mcp_rejects_unknown_keys(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        "[tools.mcp.pulse]\nenabled = false",
        "[tools.mcp.pulse]\nenabled = false\ntypo_field = true"))
    with pytest.raises(ConfigError, match="typo_field"):
        load(toml_path)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k tools_mcp -v`
Expected: FAIL — `Config` has no `tools` attribute.

- [ ] **Step 4: Implement `McpMountConfig`/`ToolsConfig`**

In `nora/config.py`, add after `AgentConfig` (before `class Config(BaseModel):`):

```python
class McpMountConfig(BaseModel):
    """One MCP server Nora may mount as conversation tools (spec §2.1/§2.2).

    Optional and defaulted like `RouteConfig`/`ShortenerConfig`: `enabled`
    defaults False, so a section existing in a committed config doesn't mean
    it's live. `token_env` NAMES the environment variable holding the bearer
    token (DEC-8's `api_key_env`/`token_env` shape) — the value is never here,
    it lives in `.env`. `budget` is the flat per-server tool-call ceiling
    every tool this server advertises shares (spec §4) — not per-tool,
    because auto-discovered tools have no config-time identity to key on.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str
    token_env: str
    budget: int


class ToolsConfig(BaseModel):
    """The `[tools.mcp.<name>]` mount points (spec §2.2). A second server is
    a second `mcp` dict entry — no schema change, matching ARCHITECTURE.md's
    documented target ("add an MCP server -> no Python")."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mcp: dict[str, McpMountConfig] = Field(default_factory=dict)
```

In `class Config(BaseModel):`, add (after the `reminders` field, before `engagement`):

```python
    # Optional and defaulted — see `ToolsConfig`. Empty means no MCP server is
    # mounted; a config.toml without a `[tools]` section keeps loading.
    tools: ToolsConfig = ToolsConfig()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k tools_mcp -v`
Expected: PASS (all three)

- [ ] **Step 6: Full gate check**

Run: `uv run pytest -q && uv run ruff check nora/config.py config.example.toml tests/test_config.py && uv run mypy nora/config.py`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add nora/config.py config.example.toml tests/test_config.py
git commit -m "feat(config): add tools.mcp mount-point schema (spec section 2)"
```

---

### Task 2: `nora/agent/mcp.py` — MCP server discovery

**Files:**
- Create: `nora/agent/mcp.py`
- Test: `tests/unit/test_agent_mcp.py`
- Modify: `pyproject.toml` (`mcp` as explicit dev dependency)

**Interfaces:**
- Produces: `nora.agent.mcp.McpMount` (plain frozen dataclass: `name: str`, `base_url: str`, `token_env: str`, `budget: int`), `nora.agent.mcp.McpToolGroup` (plain frozen dataclass: `budget: int`, `tools: tuple[Any, ...]`), `nora.agent.mcp.build_mcp_tools(mounts: dict[str, McpMount]) -> dict[str, McpToolGroup]`.
- Consumes: nothing from earlier tasks (this module is independent of `nora.config` by design — Global Constraints).

- [ ] **Step 1: Pin `mcp` as an explicit dev dependency**

```bash
uv add --dev mcp
```

Verify: `grep '"mcp' pyproject.toml` shows `mcp` under `[dependency-groups] dev = [...]` (it was already present transitively via `strands-agents`, but must be pinned explicitly since the test in this task imports `mcp.server.fastmcp` directly — CLAUDE.md's "never pip install, dependencies via uv add" rule applies to transitive-only reliance too).

- [ ] **Step 2: Write the failing test — local fake MCP server fixture + discovery test**

Create `tests/unit/test_agent_mcp.py`:

```python
"""`nora.agent.mcp` (Task 2) — MCP server discovery against a real local
MCP server, not a mock, per CLAUDE.md's rule against a fake that answers
every request the same way."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from nora.agent.mcp import McpMount, build_mcp_tools


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeMcpServer:
    """A real MCP server, run in-process on a background thread, with one
    tool that returns real data and one that always raises — so tests can
    exercise both the success and error shapes an actual server produces."""

    def __init__(self) -> None:
        self.port = _free_port()
        app = FastMCP("fake-pulse", host="127.0.0.1", port=self.port)

        @app.tool()
        def pulse_server_status() -> dict:
            """Who is online."""
            return {"players_online": 2, "uptime_seconds": 3600}

        @app.tool()
        def pulse_broken_tool() -> dict:
            """Always fails, for the malformed/error-path test."""
            raise RuntimeError("upstream exploded")

        self._server = uvicorn.Server(
            uvicorn.Config(app.streamable_http_app(), host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        for _ in range(100):
            if getattr(self._server, "started", False):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("fake MCP server did not start in time")
        return f"http://127.0.0.1:{self.port}/mcp"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture
def fake_mcp_server():
    server = _FakeMcpServer()
    url = server.start()
    yield url
    server.stop()


def test_discovers_tools_from_a_real_server(fake_mcp_server, monkeypatch):
    monkeypatch.setenv("FAKE_TOKEN", "irrelevant-but-present")
    mounts = {
        "fake": McpMount(name="fake", base_url=fake_mcp_server, token_env="FAKE_TOKEN", budget=3),
    }
    groups = build_mcp_tools(mounts)
    assert set(groups) == {"fake"}
    assert groups["fake"].budget == 3
    tool_names = {t.tool_name for t in groups["fake"].tools}
    assert tool_names == {"pulse_server_status", "pulse_broken_tool"}


def test_connection_failure_yields_absent_not_raised(monkeypatch):
    monkeypatch.setenv("DEAD_TOKEN", "x")
    mounts = {
        "dead": McpMount(name="dead", base_url="http://127.0.0.1:1/mcp", token_env="DEAD_TOKEN", budget=1),
    }
    groups = build_mcp_tools(mounts)
    assert "dead" not in groups


def test_missing_token_env_yields_absent_not_raised(fake_mcp_server, monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    mounts = {
        "fake": McpMount(name="fake", base_url=fake_mcp_server, token_env="MISSING_TOKEN", budget=1),
    }
    groups = build_mcp_tools(mounts)
    assert "fake" not in groups
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/unit/test_agent_mcp.py -v`
Expected: FAIL — `nora.agent.mcp` does not exist.

- [ ] **Step 4: Implement `nora/agent/mcp.py`**

```python
"""`nora.agent.mcp` — MCP server discovery (spec: mcp-tool-provider-design
§3). Builds Strands tool objects for every configured, reachable MCP server
at process start. The only module besides `nora.agent.factory` that touches
`strands`'s MCP client machinery.

Deliberately takes plain dataclasses, never `nora.config` types: import-linter's
"only app and __main__ read config" contract forbids `nora.agent` from importing
`nora.config` (`pyproject.toml`). `nora.app.composition` is the conversion point,
the same shape `ReminderCaps` already uses (see `nora.agent.registry`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from strands.tools.mcp.mcp_client import MCPClient

log = logging.getLogger("nora.agent.mcp")


@dataclass(frozen=True)
class McpMount:
    """One MCP server to connect to, as a plain domain value (spec §3.1)."""

    name: str
    base_url: str
    token_env: str
    budget: int


@dataclass(frozen=True)
class McpToolGroup:
    """One server's discovered tools plus the flat budget they all share
    (spec §4 — Approach A). `tools` is typed `Any` rather than importing
    Strands' `MCPAgentTool` into a signature `ToolRegistry` also uses,
    matching how `nora.agent.ports.adapt` keeps its own return typed `Any`."""

    budget: int
    tools: tuple[Any, ...]


def build_mcp_tools(mounts: dict[str, McpMount]) -> dict[str, McpToolGroup]:
    """Connect to every mount, discover its tools, return only what succeeded.

    A server absent from the returned dict means "not configured, or
    unreachable, or its token was rejected" — the caller cannot and must not
    distinguish those (R-22's «could not ask» is not «nothing known», but
    among THOSE three sub-cases there is nothing more specific to say: all
    three mean the same thing to a conversation asking about it — the tools
    are not there). Never raises: one dead server must not stop Nora from
    starting. Uses `MCPClient.load_servers()` (an existing Strands
    classmethod, not hand-rolled per-server transport construction) with
    `${env:VAR}` interpolation for the bearer token, so the token read
    happens inside Strands' own interpolation, never in this function.
    """
    server_config = {
        mount.name: {
            "url": mount.base_url,
            "headers": {"Authorization": f"Bearer ${{env:{mount.token_env}}}"},
            "continue_on_error": True,
        }
        for mount in mounts.values()
    }
    clients = MCPClient.load_servers(server_config)
    groups: dict[str, McpToolGroup] = {}
    for mount, client in zip(mounts.values(), clients, strict=True):
        try:
            client.start()
        except Exception:
            log.warning("mcp server %r failed to connect, its tools are absent", mount.name, exc_info=True)
            continue
        try:
            tools = client.list_tools_sync()
        except Exception:
            log.warning("mcp server %r connected but tool discovery failed, its tools are absent", mount.name, exc_info=True)
            continue
        groups[mount.name] = McpToolGroup(budget=mount.budget, tools=tuple(tools))
    return groups
```

`MCPClient.load_servers()` preserves input order (it iterates the dict it's given), so `zip(mounts.values(), clients, strict=True)` pairs each `McpMount` with its client correctly — `strict=True` makes a length mismatch a loud `ValueError` rather than a silent misalignment, which matters here because misaligning name↔client would attribute one server's tools to another server's budget.

A rejected/missing token surfaces as a connection failure inside `client.start()` (the bearer token is sent during the MCP `initialize` handshake), which is why `test_missing_token_env_yields_absent_not_raised` and `test_connection_failure_yields_absent_not_raised` both land in the same `except Exception` branch — confirm this against the actual `mcp` 1.29.0 behavior when running Step 5; if an unresolvable `${env:...}` reference raises during `load_servers()` itself rather than during `.start()`, wrap that call in the same try/except instead.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_agent_mcp.py -v`
Expected: PASS (all four tests). If `test_missing_token_env_yields_absent_not_raised` fails because `load_servers()` itself raises on the unresolved `${env:MISSING_TOKEN}` (rather than deferring to `.start()`), move the `try/except` in `build_mcp_tools` to wrap the `MCPClient.load_servers(server_config)` call too, catching per-server config-resolution failures the same way — re-run until green.

- [ ] **Step 6: Full gate check**

Run: `uv run pytest -q && uv run ruff check nora/agent/mcp.py tests/unit/test_agent_mcp.py && uv run lint-imports`
Expected: all green — `lint-imports` in particular must show no `nora.agent` → `nora.config` violation.

- [ ] **Step 7: Commit**

```bash
git add nora/agent/mcp.py tests/unit/test_agent_mcp.py pyproject.toml uv.lock
git commit -m "feat(agent): MCP server discovery via strands MCPClient.load_servers (spec section 3)"
```

---

### Task 3: `policy.toml` per-conversation tool allowlist

**Files:**
- Modify: `nora/policy.py`
- Modify: `policy.example.toml`
- Test: `tests/test_policy.py`

**Interfaces:**
- Produces: `nora.policy.Conversation.tools: frozenset[str]` (new field, default `frozenset()`).
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_policy.py` (match its existing style — find the test that builds a minimal valid policy TOML and parses it):

```python
def test_conversation_tools_defaults_to_empty(tmp_path):
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("""
default = "deny"
[[conversations]]
platform = "signal"
id = "g1"
persons = ["miles"]
""")
    policy = load_policy(policy_file)
    convo = policy.conversation("signal", "g1")
    assert convo.tools == frozenset()


def test_conversation_tools_parses_allowlist(tmp_path):
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("""
default = "deny"
[[conversations]]
platform = "signal"
id = "g1"
persons = ["miles"]
tools = ["pulse"]
""")
    policy = load_policy(policy_file)
    convo = policy.conversation("signal", "g1")
    assert convo.tools == frozenset({"pulse"})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_policy.py -k conversation_tools -v`
Expected: FAIL — `Conversation` has no `tools` attribute.

- [ ] **Step 3: Implement**

In `nora/policy.py`, add `tools` to `Conversation` (`nora/policy.py:37-42`):

```python
@dataclass(frozen=True)
class Conversation:
    platform: str
    ids: frozenset[str]         # `id` plus `also_ids`, matched identically
    persons: frozenset[str] | None   # None == EVERYONE
    label: str = ""             # never matched on (§4.2)
    # The MCP servers this conversation may use (spec:
    # mcp-tool-provider-design §2.3). Empty means none — deny-by-default,
    # the same posture as the rest of this file (R-5).
    tools: frozenset[str] = frozenset()
```

Add `tools` to `_ConversationEntry` (`nora/policy.py:104-115`):

```python
class _ConversationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    id: str
    also_ids: list[str] = Field(default_factory=list)
    label: str = ""
    persons: list[str] | str = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
```

Thread it through in `load_policy` (`nora/policy.py:170-173`):

```python
        convo = Conversation(
            platform=entry.platform, ids=ids, persons=persons, label=entry.label,
            tools=frozenset(entry.tools),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_policy.py -k conversation_tools -v`
Expected: PASS

- [ ] **Step 5: Update `policy.example.toml`**

Add a `tools` line to the first `[[conversations]]` example block (after `persons  = ["miles", "ada"]`):

```toml
tools    = []                # MCP servers this room may use, e.g. ["pulse"].
                             # Empty means none, same deny-by-default as everything
                             # else here.
```

- [ ] **Step 6: Full gate check**

Run: `uv run pytest -q && uv run ruff check nora/policy.py policy.example.toml tests/test_policy.py`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add nora/policy.py policy.example.toml tests/test_policy.py
git commit -m "feat(policy): per-conversation MCP tool allowlist (spec section 2.3)"
```

---

### Task 4: `ToolRegistry` — fold in discovered MCP tools

**Files:**
- Modify: `nora/agent/registry.py`
- Test: `tests/unit/test_tool_registry.py`

**Interfaces:**
- Consumes: `nora.agent.mcp.McpToolGroup` (Task 2).
- Produces: `ToolRegistry(mcp_tools: Mapping[str, McpToolGroup] = {})` constructor param; `.budgets()` includes MCP entries; `.specs()`/`.build()` gain `allowed_mcp: frozenset[str] = frozenset()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_tool_registry.py` (reuse the file's existing `_built_tools`/`_room`/`_deliverer` helpers — see `tests/unit/test_tool_registry.py:26-56`):

```python
from nora.agent.mcp import McpToolGroup


class _FakeMcpTool:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name


def _mcp_groups() -> dict[str, McpToolGroup]:
    return {
        "pulse": McpToolGroup(
            budget=5,
            tools=(_FakeMcpTool("pulse_server_status"), _FakeMcpTool("pulse_weekly")),
        ),
    }


def test_budgets_include_every_discovered_mcp_tool_unconditionally():
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets()
    assert budgets["pulse_server_status"] == 5
    assert budgets["pulse_weekly"] == 5
    # native budgets are untouched
    assert budgets["now"] == 4


def test_capability_and_friends_are_unaffected_by_mcp_tools():
    plain = ToolRegistry()
    with_mcp = ToolRegistry(mcp_tools=_mcp_groups())
    assert with_mcp.capability() == plain.capability()
    assert with_mcp.tool_names() == plain.tool_names()
    assert with_mcp.terminal_tools() == plain.terminal_tools()
    assert with_mcp.live_only_tools() == plain.live_only_tools()


def test_build_omits_mcp_tools_when_allowed_mcp_is_empty():
    tools = _built_tools(ToolRegistry(mcp_tools=_mcp_groups()))
    names = {t.tool_name for t in tools}
    assert "pulse_server_status" not in names
    assert "pulse_weekly" not in names


def test_build_includes_only_the_allowed_mcp_servers_tools():
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    tools = registry.build(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeHoncho(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(), allowed_mcp=frozenset({"pulse"}),
    )
    names = {t.tool_name for t in tools}
    assert {"pulse_server_status", "pulse_weekly"} <= names
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_tool_registry.py -k mcp -v`
Expected: FAIL — `ToolRegistry.__init__` has no `mcp_tools` parameter.

- [ ] **Step 3: Implement**

In `nora/agent/registry.py`:

Add the import (near the top, with the other `.` imports):

```python
from .mcp import McpToolGroup
```

Extend `__init__` (`nora/agent/registry.py:67-82`) — add a new keyword parameter and store it:

```python
    def __init__(
        self, *,
        transit: TransitClient | None = None,
        route: RouteClient | None = None,
        vision: VisionClient | None = None,
        blobs: BlobStore | None = None,
        search: SearchClient | None = None,
        presence: PresenceReader | None = None,
        circles: tuple[Circle, ...] = (),
        refresher: Any = None,
        shortener: LinkShortener | None = None,
        ai_peer: str = "nora",
        reminders: ReminderStore | None = None,
        reminder_caps: ReminderCaps | None = None,
        clock: Callable[[], datetime] = utc_now,
        mcp_tools: Mapping[str, McpToolGroup] | None = None,
    ) -> None:
```

and, alongside the other collaborator assignments:

```python
        # Discovered MCP tool groups (spec: mcp-tool-provider-design §3),
        # keyed by server name. `.budgets()` folds every entry in
        # UNCONDITIONALLY — it is never disclosed to a model or user, so an
        # entry for a server a given conversation cannot call is inert, not
        # a leak. `.specs()`/`.build()`'s `allowed_mcp` parameter is the
        # actual enforcement point.
        self._mcp_tools = dict(mcp_tools) if mcp_tools is not None else {}
```

Extend `budgets()` (`nora/agent/registry.py:164-166`):

```python
    def budgets(self) -> Mapping[str, int]:
        """The per-tool call-count ceiling `ReplyGuard` enforces under R-7."""
        budgets = dict(NATIVE_TOOL_BUDGETS)
        for group in self._mcp_tools.values():
            for tool in group.tools:
                budgets[tool.tool_name] = group.budget
        return budgets
```

Extend `specs()`'s signature and body (`nora/agent/registry.py:272-316`) — add the parameter and append MCP tools after the native `ToolSpec`s are built. Note `specs()` returns `tuple[ToolSpec, ...]` today; MCP tools are already Strands tool objects, not `ToolSpec`s, so they cannot be returned from `specs()` (which promises plain, pre-adaptation specs) — they must be appended in `build()` instead, after `adapt()` runs. Change `specs()`'s signature only to accept and ignore-pass-through nothing new (native tool closures are unaffected), and do the real work in `build()`:

```python
    def build(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        honcho: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
        fire_turn: bool = False,
        allowed_mcp: frozenset[str] = frozenset(),
    ) -> list[Any]:
        """The registered tools, adapted to what `Agent(tools=...)` wants.

        `allowed_mcp` (spec §3) is the enforcement point for per-conversation
        MCP scope: only tool groups whose server name is in this set are
        appended. Native tools are unaffected — this codebase has no
        per-conversation gating for them today, and this change does not add
        one."""
        specs = self.specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
            fire_turn=fire_turn,
        )
        tools = [adapt(spec) for spec in specs]
        for name in allowed_mcp:
            group = self._mcp_tools.get(name)
            if group is not None:
                tools.extend(group.tools)
        return tools
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_tool_registry.py -v`
Expected: PASS, including the pre-existing tests (`test_the_registry_exposes_every_declared_tool`, `test_budgets_match_todays_table`, etc.) — those must be unaffected since `mcp_tools` defaults to empty everywhere they construct `ToolRegistry()`.

- [ ] **Step 5: Full gate check**

Run: `uv run pytest -q && uv run ruff check nora/agent/registry.py tests/unit/test_tool_registry.py && uv run lint-imports`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add nora/agent/registry.py tests/unit/test_tool_registry.py
git commit -m "feat(agent): fold discovered MCP tools into ToolRegistry (spec section 3)"
```

---

### Task 5: `EngagementPipeline` — resolve and enforce the conversation's MCP allowlist

**Files:**
- Modify: `nora/engagement/pipeline.py`
- Modify: `tests/unit/test_engagement_pipeline.py`
- Modify: `design/09-decision-index.md` (add `DEC-71`)
- Modify: `tests/test_acceptance_rules.py` (add `R-40`)

**Interfaces:**
- Consumes: `nora.policy.Policy`/`Conversation.tools` (Task 3), `ToolRegistry.build(..., allowed_mcp=...)` (Task 4).
- Produces: `EngagementPipeline(..., policy: Policy)` constructor param.

- [ ] **Step 1: Write the failing test — the assembly-time gating rule itself (this is R-40)**

Add to `tests/unit/test_engagement_pipeline.py`. First check the file's existing imports for `ToolRegistry`/`Metrics`/etc. (`tests/unit/test_engagement_pipeline.py:28-32`) and add `from nora.agent.mcp import McpToolGroup` and `from nora.policy import Conversation, Policy`. Then:

```python
def _policy_allowing(*, conversation_id: str = "g1", tools: frozenset[str] = frozenset()) -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({conversation_id}), persons=None, tools=tools,
    )
    return Policy(conversations=(convo,), _by_id={("signal", conversation_id): convo})


def _registry_with_pulse() -> ToolRegistry:
    groups = {
        "pulse": McpToolGroup(budget=5, tools=(_FakeMcpTool("pulse_server_status"),)),
    }
    return ToolRegistry(mcp_tools=groups)


class _FakeMcpTool:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name


async def test_unauthorized_conversation_never_receives_pulse_tools():
    pipeline = _build_pipeline(
        tool_registry=_registry_with_pulse(),
        policy=_policy_allowing(tools=frozenset()),  # not authorized for pulse
    )
    await pipeline.run(_request())
    delivered_tools = pipeline._runner.calls[-1]["tools"]
    assert not any(t.tool_name == "pulse_server_status" for t in delivered_tools)


async def test_authorized_conversation_receives_pulse_tools():
    pipeline = _build_pipeline(
        tool_registry=_registry_with_pulse(),
        policy=_policy_allowing(tools=frozenset({"pulse"})),
    )
    await pipeline.run(_request())
    delivered_tools = pipeline._runner.calls[-1]["tools"]
    assert any(t.tool_name == "pulse_server_status" for t in delivered_tools)
```

`ScriptedRunner` (`tests/unit/test_engagement_pipeline.py:172-192`) is a thin subclass of `nora.testing.scripted_agent.FakeAgentRunner`, whose `invoke(self, **kwargs)` (`nora/testing/scripted_agent.py:315-333`) calls `self._on_call(kwargs)` with every keyword `EngagementPipeline` passed — including `tools`, since `_run_now` calls `self._runner.invoke(..., tools=tools, ...)`. `ScriptedRunner.__init__` wires `on_call=self.calls.append`, so `pipeline._runner.calls[-1]["tools"]` is the exact list of Strands tool objects `Agent(tools=...)` would have received for this tag — no new test-double surface needed, this already exists.

Also add `policy=None` to `_build_pipeline`'s signature (`tests/unit/test_engagement_pipeline.py:223-238`) defaulting to `permissive_policy()` when `None`, so every pre-existing call to `_build_pipeline` (which doesn't pass `policy=`) keeps working unchanged:

```python
def _build_pipeline(
    *,
    # ... existing params unchanged ...
    policy=None,
):
    # ... existing body unchanged, then:
    pipeline = EngagementPipeline(
        # ... existing kwargs unchanged ...
        policy=policy if policy is not None else permissive_policy(),
    )
```

(`permissive_policy` from `nora.policy` — already used by `nora.app.offline` per `nora/policy.py:80-92`; import it at the top of the test file.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_engagement_pipeline.py -k pulse -v`
Expected: FAIL — `EngagementPipeline.__init__` has no `policy` parameter.

- [ ] **Step 3: Implement**

In `nora/engagement/pipeline.py`, add the import:

```python
from nora.policy import Policy
```

Add `policy: Policy` to `__init__` (`nora/engagement/pipeline.py:68-93`) and store it:

```python
        tool_registry: ToolRegistry,
        policy: Policy,
        debounce: Debounce,
```

```python
        self._tool_registry = tool_registry
        # policy.toml's per-conversation MCP allowlist (spec:
        # mcp-tool-provider-design §3). The ingest pipeline already checked
        # this tag's conversation is admitted at all (nora.ingest.stages.scope,
        # DEC-18) using the SAME Policy instance — this is the second read of
        # it, for a narrower question: which MCP servers, not whether to
        # engage at all.
        self._policy = policy
```

In `_run_now` (`nora/engagement/pipeline.py:281-287`), resolve the allowlist and pass it through:

```python
            convo = self._policy.conversation(msg.platform, msg.conversation_id)
            allowed_mcp = convo.tools if convo is not None else frozenset()
            tools = self._tool_registry.build(
                store=self._audit, conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer,
                room=room, question=msg.body,
                context_window=self._context_window, metrics=self._metrics,
                allowed_mcp=allowed_mcp,
            )
```

`convo is not None` is defensive, not load-bearing: by this point ingest has already proven the conversation is admitted (DEC-18), so `self._policy.conversation(...)` returning `None` here would indicate the ingest and engagement pipelines disagree about which `Policy` they were given — a wiring bug worth defaulting safely on (empty allowlist) rather than crashing the tag over.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_engagement_pipeline.py -v`
Expected: PASS, including every pre-existing test in the file (they all go through `_build_pipeline`'s new `policy=None` default).

- [ ] **Step 5: Register the new behavioral rule — `DEC-71` and `R-40`**

In `design/09-decision-index.md`, add a row (matching the table's existing format, after the `DEC-70` row):

```
| DEC-71 | MCP tool scope is enforced where `ToolRegistry.build()` is called (assembly), never at egress — `allowed_mcp` comes from `policy.toml`'s per-conversation `tools` field. | built (spec: docs/superpowers/specs/2026-08-08-mcp-tool-provider-design.md) |
```

`RULE_TESTS` (`tests/test_acceptance_rules.py:9-...`) is a plain `dict[str, str]` mapping a rule id to a test function NAME (a string, not an import) — `test_all_rules_have_a_named_test` (`tests/test_acceptance_rules.py:163-164`) checks that name appears somewhere in the whole suite's `collected` test names, not that this file imports it. So the rule's name just has to match a real test that exists anywhere. Add:

```python
    "R-40": "test_unauthorized_conversation_never_receives_pulse_tools",
```

right after the last existing entry in `RULE_TESTS`, matching the exact name already used for the test written in Step 1 above — no renaming or wrapper needed, that test already covers R-40 as written.

- [ ] **Step 6: Full gate check**

Run: `uv run pytest -q && uv run ruff check nora/engagement/pipeline.py tests/unit/test_engagement_pipeline.py tests/test_acceptance_rules.py && uv run mypy nora/app`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add nora/engagement/pipeline.py tests/unit/test_engagement_pipeline.py design/09-decision-index.md tests/test_acceptance_rules.py
git commit -m "feat(engagement): enforce per-conversation MCP tool allowlist at assembly (DEC-71, R-40)"
```

---

### Task 6: Composition root wiring

**Files:**
- Modify: `nora/app/composition.py`

**Interfaces:**
- Consumes: `McpMountConfig`/`ToolsConfig` (Task 1), `build_mcp_tools`/`McpMount` (Task 2), `ToolRegistry(mcp_tools=...)` (Task 4), `EngagementPipeline(policy=...)` (Task 5).

This task has no isolated unit test of its own — it is pure wiring, verified by the existing integration/startup tests plus a manual startup check. (Per the task-right-sizing rule in the writing-plans skill: fold verification into the task whose deliverable needs it, rather than inventing a test that would just re-assert what Tasks 1-5 already proved.)

- [ ] **Step 1: Build `McpMount`s from config and call `build_mcp_tools`**

In `nora/app/composition.py`, add the import:

```python
from nora.agent.mcp import McpMount, build_mcp_tools
```

Before the `tool_registry = ToolRegistry(...)` call (`nora/app/composition.py:465`), add:

```python
    # `nora.agent` may not import `nora.config` (import-linter's "only app and
    # __main__ read config") — this is the conversion point, the same shape
    # `ReminderCaps(**cfg.reminders.model_dump())` already uses just below.
    mcp_mounts = {
        name: McpMount(name=name, base_url=mount.base_url, token_env=mount.token_env, budget=mount.budget)
        for name, mount in cfg.tools.mcp.items()
        if mount.enabled
    }
    mcp_tool_groups = build_mcp_tools(mcp_mounts)
```

- [ ] **Step 2: Pass the discovered groups into `ToolRegistry`**

In the `ToolRegistry(...)` call (`nora/app/composition.py:465-...`), add:

```python
    tool_registry = ToolRegistry(
        route=route_client, vision=vision_client, blobs=blobs, search=search_client,
        presence=presence_store, circles=presence_circles,
        refresher=refresher,
        shortener=shortener_client,
        ai_peer=cfg.memory.ai_peer,
        reminders=reminders,
        reminder_caps=ReminderCaps(**cfg.reminders.model_dump()),
        mcp_tools=mcp_tool_groups,
        # ... clock and any other existing kwargs unchanged ...
    )
```

- [ ] **Step 3: Thread `allowlist` into `EngagementPipeline`**

The construction is at `nora/app/composition.py:518-530`. Add `policy=allowlist` to it:

```python
    engagement = EngagementPipeline(
        adapter=adapter, audit=audit, memory=memory, persons=persons,
        doctrine_store=doctrine_store, context_assembler=assembler, runner=runner,
        tool_registry=tool_registry,
        policy=allowlist,
        debounce=debounce, canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        nora_sender_id=cfg.platforms.signal.nora_sender_id,
        ai_peer=cfg.memory.ai_peer,
        context_window=cfg.store.context_window, timeout_seconds=tier.timeout_seconds,
        limits=limits, metrics=metrics, spawn=background.spawn, llm_state=llm_state,
        reactions=cfg.engagement.reactions.enabled,
        roster_source=roster_source,
        slots=slots,
    )
```

(`allowlist` is already in scope here — it's the same `Policy` instance `load_policy(cfg.policy_file)` built earlier in this function and already threaded into the ingest pipeline.)

- [ ] **Step 4: Run the full test suite and start-up check**

Run: `uv run pytest -q`
Expected: all green, including `tests/integration/` (which exercises `nora.app.composition.build_dependencies` end to end via `nora.app.offline`).

Run: `uv run python -c "from nora.app.composition import build_dependencies; import asyncio; from nora.config import load; from pathlib import Path"` — a smoke import, not a full startup (a full startup needs a real `config.toml`/`identity.toml`/`policy.toml` and a running signal-cli, out of scope for this check) — confirms no import-time error was introduced.

- [ ] **Step 5: Full gate check**

Run: `uv run pytest -q && uv run ruff check nora/ evals/ tests/ && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence && uv run lint-imports`
Expected: all green — this is the full CLAUDE.md gate, run once at the end of the feature.

- [ ] **Step 6: Commit**

```bash
git add nora/app/composition.py
git commit -m "feat(app): wire MCP tool discovery into the composition root (spec section 3)"
```

---

## After this plan: operator steps, not code

These are config/secret changes for whoever deploys this, not part of the PR:

1. Add `PULSE_MCP_TOKEN=<the real token>` to `.env` (gitignored) — read `~/.config/nora/pulse-token` for the value if that's where it was placed during the original reachability check, or mint a fresh one.
2. In the real (gitignored) `config.toml`, add and enable `[tools.mcp.pulse]` (copy the disabled example from `config.example.toml`, flip `enabled = true`).
3. In the real (gitignored) `policy.toml`, add `tools = ["pulse"]` to whichever `[[conversations]]` entry should be able to ask Nora about the game server.
4. Restart Nora (`identity.toml`/`policy.toml`/`config.toml` are loaded once at startup — CLAUDE.md).
5. Verify: message Nora from the authorized conversation asking about the game server; confirm no other conversation gets a response mentioning it.
