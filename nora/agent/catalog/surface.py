from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import wraps
from typing import Any

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.catalog.groups import GRAPH_MEMORY_TOOLS, HONCHO_MEMORY_TOOLS
from nora.agent.link_owners import LinkInput
from nora.agent.link_owners import owner_of as _owner_of
from nora.agent.link_owners import redirect as _link_redirect
from nora.agent.ports import ToolSpec
from nora.agent.toolkit.results import _record, _record_media

_AUDIT_TASKS: set[asyncio.Future[None]] = set()


def on_memory_surface(name: str, *, graph: bool, everything: bool = False) -> bool:
    """Whether `name` is on the memory surface `graph` selects (DEC-316):
    a Honcho memory verb only off the graph, the graph's only on it, every
    other tool always."""
    if everything:
        return True
    if name in HONCHO_MEMORY_TOOLS:
        return not graph
    if name in GRAPH_MEMORY_TOOLS:
        return graph
    return True


def _apply_link_owners(
    specs: tuple[ToolSpec, ...],
    *,
    metrics,
    unwired: frozenset[str] = frozenset(),
) -> tuple[ToolSpec, ...]:
    """`build_tool_specs`'s one post-pass."""
    out = []
    for spec in specs:
        link_inputs = NATIVE_TOOLS[spec.name].link_inputs
        if not link_inputs:
            out.append(spec)
            continue
        out.append(
            replace(
                spec,
                call=_link_owner_wrapped(spec.name, spec.call, link_inputs, metrics, unwired),
            )
        )
    return tuple(out)


def _link_owner_wrapped(
    tool_name: str,
    call: Callable[..., Awaitable[Any]],
    link_inputs: tuple[LinkInput, ...],
    metrics,
    unwired: frozenset[str] = frozenset(),
) -> Callable[..., Awaitable[Any]]:
    """`call`, with each of `link_inputs` checked against the link-owner"""
    signature = inspect.signature(call)

    @wraps(call)
    async def routed(*args: object, **kwargs: object) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        for link_input in link_inputs:
            if link_input.unless and bound.arguments.get(link_input.unless):
                continue
            value = bound.arguments.get(link_input.param)
            if not value:
                continue
            found = _owner_of(str(value), unwired=unwired)
            if found is None or found[0].tool == tool_name or found[0].tool in link_input.accepts:
                # A link this tool owns is its own to read, never a redirect
                # to itself (`file_fetch`'s `link`); nor one it accepts from
                # another owner (`computer_import` copies `file_fetch`'s).
                continue
            owner, claim = found
            if tool_name == "media_fetch":
                _record_media(
                    metrics,
                    "nothing_found",
                    reason="link_owner",
                    owner=owner.network,
                    kind=claim.kind,
                )
            else:
                _record(
                    metrics,
                    tool_name,
                    "nothing_found",
                    reason="link_owner",
                    owner=owner.network,
                    kind=claim.kind,
                )
            return _link_redirect(owner, claim, link_input)
        return await call(*args, **kwargs)

    return routed
