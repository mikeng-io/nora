"""Every built tool whose signature has a parameter named `url` OR `link` is
EITHER an owner (`nora.agent.link_owners.LINK_OWNERS` names it as
`owner.tool`) OR declares that parameter in `NativeTool.link_inputs`
(the link-owner registry's rule).

The gate covers both `url` and `link`. `instagram_read`, `x_read` and
`linkedin_read` consult the registry themselves for a link belonging to a
DIFFERENT network (`owner_of`, called directly from `nora.agent.tools`, never
through `_apply_link_owners`'s fetcher post-pass — an owner's OWN readable link
must never be redirected away from itself, which that wrapper cannot tell apart
from a claim by someone else). All three are exempted here as owners: none
declares `link_inputs`, and none needs to, because none is a generic fetcher a
claimed link should bypass entirely before the tool runs. `url` is the
generic-fetcher spelling (`web_fetch`, `media_fetch`) the registry routes
through that post-pass, so this is the gate that catches a new one of THOSE that
forgets to declare itself — "a new fetcher that forgets the call is silently
unrouted." The `link` half is about the day a `link`-taking tool beyond the
three owners is added without registering as an owner or declaring
`link_inputs`.
"""

from __future__ import annotations

import inspect
import typing

from nora.agent.link_owners import LINK_OWNERS
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore
from tests.fakes import SinkSender

_OWNER_TOOLS = {owner.tool for owner in LINK_OWNERS}


class _NoStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


def _built_specs():
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    return ToolRegistry().specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_NoPersons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )


def test_every_url_or_link_parameter_is_declared_a_link_input():
    undeclared = []
    for spec in _built_specs():
        if spec.name in _OWNER_TOOLS:
            continue
        signature = inspect.signature(spec.call)
        present = {name for name in ("url", "link") if name in signature.parameters}
        if not present:
            continue
        declared = {link_input.param for link_input in NATIVE_TOOLS[spec.name].link_inputs}
        missing = sorted(present - declared)
        if missing:
            undeclared.append((spec.name, missing))
    assert undeclared == [], (
        f"tool(s) with a url/link parameter and no link_inputs declaration: {undeclared}"
    )


def test_at_least_one_tool_actually_exercises_this_gate():
    """A positive control (CLAUDE.md: pair every zero-assertion with one) —
    `web_fetch` and `media_fetch` both declare `link_inputs` today, so an
    empty `NATIVE_TOOLS` sweep above would pass for the wrong reason (nothing
    with a `url` parameter was ever built) as easily as for the right one."""
    declaring = {
        name for name, declared in NATIVE_TOOLS.items() if declared.link_inputs
    }
    assert {"web_fetch", "media_fetch"} <= declaring


def test_every_link_parameter_tool_is_a_registered_owner():
    """The `link` half's own positive control. Every built tool with a `link`
    parameter today (`instagram_read`, `x_read`, `linkedin_read`) is exempted
    from the sweep above only because `_OWNER_TOOLS` names it — an empty
    `link_tools` set would mean this module's own widening from `url` to
    `link` (this file's docstring) exercises nothing."""
    link_tools = {
        spec.name for spec in _built_specs()
        if "link" in inspect.signature(spec.call).parameters
    }
    assert link_tools, "no built tool has a link parameter — untested widening"
    # A non-owner may take a `link` only by declaring it a link input, so
    # the registry still routes another owner's link away from it
    # (`computer_import`, which copies a shared file and `accepts` only
    # `file_fetch`'s links).
    declared = {
        name for name, spec in NATIVE_TOOLS.items()
        if any(li.param == "link" for li in spec.link_inputs)
    }
    assert link_tools <= _OWNER_TOOLS | declared, (
        f"tool(s) with a link parameter that are neither registered owners nor "
        f"declare it a link input: {sorted(link_tools - _OWNER_TOOLS - declared)}"
    )


def test_accepts_is_an_explicit_allowlist_of_registered_owners():
    """`LinkInput.accepts` lets another owner's link reach a tool's body
    instead of being redirected. Pinned whole, so an over-broad `accepts` (a
    tool taking Instagram or X links it cannot handle) fails here by name;
    and each accepted name must be a registered owner, never a stray
    string that would silently accept nothing."""
    accepts = {
        name: {tool for li in spec.link_inputs for tool in li.accepts}
        for name, spec in NATIVE_TOOLS.items()
        if any(li.accepts for li in spec.link_inputs)
    }
    assert accepts == {"computer_import": {"file_fetch"}, "media_fetch": {"file_fetch"}}
    assert set().union(*accepts.values()) <= _OWNER_TOOLS
