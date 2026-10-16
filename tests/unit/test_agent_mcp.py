"""`nora.agent.mcp` (Task 2) — MCP server discovery against a real local
MCP server, not a mock, per CLAUDE.md's rule against a fake that answers
every request the same way."""

from __future__ import annotations

import logging
import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from nora.agent.mcp import McpMount, build_mcp_tools
from nora.agent.tools import NATIVE_TOOL_BUDGETS


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeMcpServer:
    """A real MCP server, run in-process on a background thread, with one
    tool that returns real data and one that always raises — so tests can
    exercise both the success and error shapes an actual server produces.

    ``broken_discovery=True`` replaces the server's ``tools/list`` handler
    itself (registered via the low-level ``mcp.server.lowlevel.Server``
    that FastMCP wraps at ``app._mcp_server``) with one that raises. That is
    the MCP protocol's tool-*listing* call, distinct from ``pulse_broken_tool``
    above which fails only when *invoked*. In the installed `mcp`
    (1.29.0), `Server._handle_request` catches a plain
    `Exception` raised from a request handler and turns it into a JSON-RPC
    error response (`mcp/server/lowlevel/server.py`), which the client-side
    `ClientSession.list_tools()` then raises as `mcp.shared.exceptions.McpError`
    — so `MCPClient.list_tools_sync()` raises after a successful `.start()`,
    exactly the post-connect discovery failure this fixture needs to produce.
    """

    def __init__(self, *, broken_discovery: bool = False) -> None:
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

        if broken_discovery:

            @app._mcp_server.list_tools()
            async def _broken_list_tools():
                raise RuntimeError("tool discovery exploded")

        self._server = uvicorn.Server(
            uvicorn.Config(
                app.streamable_http_app(), host="127.0.0.1", port=self.port, log_level="warning"
            )
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


@pytest.fixture
def fake_mcp_server_broken_discovery():
    server = _FakeMcpServer(broken_discovery=True)
    url = server.start()
    yield url
    server.stop()


def test_discovers_tools_from_a_real_server(fake_mcp_server, monkeypatch):
    monkeypatch.setenv("FAKE_TOKEN", "irrelevant-but-present")
    mounts = {
        "fake": McpMount(
            name="fake",
            base_url=fake_mcp_server,
            token_env="FAKE_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=3,
        ),
    }
    groups = build_mcp_tools(mounts)
    assert set(groups) == {"fake"}
    assert groups["fake"].budget == 3
    tool_names = {t.tool_name for t in groups["fake"].tools}
    assert tool_names == {"pulse_server_status", "pulse_broken_tool"}


def test_connection_failure_yields_absent_not_raised(monkeypatch):
    monkeypatch.setenv("DEAD_TOKEN", "x")
    mounts = {
        "dead": McpMount(
            name="dead",
            base_url="http://127.0.0.1:1/mcp",
            token_env="DEAD_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=1,
        ),
    }
    groups = build_mcp_tools(mounts)
    assert "dead" not in groups


def test_missing_token_env_yields_absent_not_raised(fake_mcp_server, monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    mounts = {
        "fake": McpMount(
            name="fake",
            base_url=fake_mcp_server,
            token_env="MISSING_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=1,
        ),
    }
    groups = build_mcp_tools(mounts)
    assert "fake" not in groups


# ---- tool-name collisions -------------------------------------------------

class _NamedToolsMcpServer:
    """A real MCP server (background thread, like `_FakeMcpServer` above)
    whose tools are named by the caller — the collision tests need to
    control what name a discovered tool arrives under, which the fixed
    `pulse_server_status`/`pulse_broken_tool` pair above cannot give them."""

    def __init__(self, tool_names: tuple[str, ...]) -> None:
        self.port = _free_port()
        app = FastMCP("fake-named", host="127.0.0.1", port=self.port)
        for tool_name in tool_names:
            def _handler(reported_name: str = tool_name) -> dict:
                return {"tool": reported_name}
            app.add_tool(_handler, name=tool_name)
        self._server = uvicorn.Server(
            uvicorn.Config(
                app.streamable_http_app(), host="127.0.0.1", port=self.port, log_level="warning"
            )
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
def named_mcp_server():
    """`named_mcp_server(("tool_a", "tool_b"))` starts a real server
    advertising exactly those tool names and returns its URL; every server
    started this way is stopped at teardown."""
    servers: list[_NamedToolsMcpServer] = []

    def _make(tool_names: tuple[str, ...]) -> str:
        server = _NamedToolsMcpServer(tool_names)
        servers.append(server)
        return server.start()

    yield _make
    for server in servers:
        server.stop()


def test_collision_with_a_native_tool_name_is_dropped_and_logged(
    named_mcp_server, monkeypatch, caplog,
):
    """`send_reply` is a real native tool (`NATIVE_TOOL_BUDGETS`). A remote
    MCP server that happens to also expose a tool by that name must not
    overwrite DEC-42's message-cap budget for it — the tool is dropped, with
    a warning naming both the server and the tool, and the server's OTHER
    tool is unaffected."""
    assert "send_reply" in NATIVE_TOOL_BUDGETS  # the premise this test needs
    monkeypatch.setenv("FAKE_TOKEN", "irrelevant-but-present")
    url = named_mcp_server(("send_reply", "pulse_ok"))
    mounts = {
        "fake": McpMount(
            name="fake", base_url=url,
            token_env="FAKE_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=3,
        ),
    }
    with caplog.at_level(logging.WARNING, logger="nora.agent.mcp"):
        groups = build_mcp_tools(mounts)
    names = {t.tool_name for t in groups["fake"].tools}
    assert names == {"pulse_ok"}
    assert any(
        "send_reply" in record.getMessage() and "fake" in record.getMessage()
        for record in caplog.records
    )


def test_collision_between_two_servers_first_processed_wins(named_mcp_server, monkeypatch, caplog):
    """Two configured servers advertising the SAME tool name: `mounts` is a
    plain dict, so iteration is insertion order and therefore deterministic
    — the first server processed keeps the name, the second server's
    colliding tool is dropped (and logged), its other tool unaffected."""
    monkeypatch.setenv("ALPHA_TOKEN", "x")
    monkeypatch.setenv("BETA_TOKEN", "y")
    url_alpha = named_mcp_server(("shared_tool", "alpha_only"))
    url_beta = named_mcp_server(("shared_tool", "beta_only"))
    mounts = {
        "alpha": McpMount(
            name="alpha", base_url=url_alpha,
            token_env="ALPHA_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=1,
        ),
        "beta": McpMount(
            name="beta", base_url=url_beta,
            token_env="BETA_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=1,
        ),
    }
    with caplog.at_level(logging.WARNING, logger="nora.agent.mcp"):
        groups = build_mcp_tools(mounts)
    assert {t.tool_name for t in groups["alpha"].tools} == {"shared_tool", "alpha_only"}
    assert {t.tool_name for t in groups["beta"].tools} == {"beta_only"}
    assert any(
        "shared_tool" in record.getMessage() and "beta" in record.getMessage()
        for record in caplog.records
    )


def test_discovery_failure_yields_absent_not_raised(fake_mcp_server_broken_discovery, monkeypatch):
    """`.start()` connects fine; the server's `tools/list` handler itself
    raises — the third `except` in `build_mcp_tools` (post-connect discovery
    failure), distinct from the connection-failure and config-resolution
    paths the other two tests cover."""
    monkeypatch.setenv("FAKE_TOKEN", "irrelevant-but-present")
    mounts = {
        "fake": McpMount(
            name="fake",
            base_url=fake_mcp_server_broken_discovery,
            token_env="FAKE_TOKEN",  # noqa: S106 — names an env var, not a secret
            budget=1,
        ),
    }
    groups = build_mcp_tools(mounts)
    assert "fake" not in groups
