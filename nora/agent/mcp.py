"""`nora.agent.mcp` — MCP server discovery. Builds Strands tool objects for
every configured, reachable MCP server at process start. The only module that
constructs `strands`'s MCP client
objects (`MCPClient`) — `nora.agent.factory`'s own Strands construction
responsibilities are the model/agent wiring, not MCP.

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

from .tools import NATIVE_TOOL_BUDGETS

log = logging.getLogger("nora.agent.mcp")


@dataclass(frozen=True)
class McpMount:
    """One MCP server to connect to, as a plain domain value."""

    name: str
    base_url: str
    token_env: str
    budget: int


@dataclass(frozen=True)
class McpToolGroup:
    """One server's discovered tools plus the flat budget they all share.
    `tools` is typed `Any` rather than importing
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

    Calls `load_servers()` once per mount, rather than batching every mount
    into one config dict and pairing the results back up positionally.
    Against the installed `mcp`==1.29.0 / `strands-agents`==1.50.2, a
    per-server `continue_on_error: true` (as the batched form would need, to
    keep one bad mount from raising for all of them) makes `load_servers()`
    silently *drop* a server whose config fails to resolve — e.g. a missing
    `${env:VAR}` — from its return list, rather than raising. That leaves the
    returned `clients` list shorter than `mounts`, which would misalign a
    positional zip (`test_missing_token_env_yields_absent_not_raised`
    hits exactly this — `load_servers()` logs and skips the mount instead of
    raising or returning a matching-length list). Calling per-mount instead
    means a config-resolution failure raises `ValueError` straight out of
    that single-mount `load_servers()` call, caught below like any other
    per-mount failure, with no positional pairing to get wrong.
    """
    groups: dict[str, McpToolGroup] = {}
    # Every tool name claimed so far, across EVERY server processed — a
    # remote server names its own tools with no screening, so a name can
    # collide either with a native tool or with an earlier server's tool.
    # Seeded with the native names up front: those are never dropped in favour
    # of a discovered tool, only the reverse.
    claimed: set[str] = set(NATIVE_TOOL_BUDGETS)
    for mount in mounts.values():
        server_config = {
            mount.name: {
                "url": mount.base_url,
                "headers": {"Authorization": f"Bearer ${{env:{mount.token_env}}}"},
            }
        }
        try:
            (client,) = MCPClient.load_servers(server_config)
        except Exception:
            log.warning(
                "mcp server %r config could not be resolved, its tools are absent",
                mount.name,
                exc_info=True,
            )
            continue
        try:
            client.start()
        except Exception:
            log.warning(
                "mcp server %r failed to connect, its tools are absent", mount.name, exc_info=True
            )
            continue
        try:
            discovered = client.list_tools_sync()
        except Exception:
            log.warning(
                "mcp server %r connected but tool discovery failed, its tools are absent",
                mount.name,
                exc_info=True,
            )
            continue
        # Collision resolution: first server PROCESSED wins (`mounts` is a
        # plain dict, so this iterates in insertion order and is therefore
        # deterministic). A tool whose name collides — with a native tool, or
        # with a tool an earlier server in this same call already claimed —
        # is dropped: this whole server does not fail, only that one tool is
        # absent from it, the same "never raise, make the piece absent"
        # posture the three `except` blocks above already have.
        kept = []
        for tool in discovered:
            if tool.tool_name in claimed:
                log.warning(
                    "mcp server %r tool %r collides with an already-registered "
                    "tool name, dropping it (that server's other tools are "
                    "unaffected)",
                    mount.name, tool.tool_name,
                )
                continue
            claimed.add(tool.tool_name)
            kept.append(tool)
        groups[mount.name] = McpToolGroup(budget=mount.budget, tools=tuple(kept))
    return groups
