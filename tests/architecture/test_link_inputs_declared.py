"""Every built tool whose signature has a parameter named `url` is EITHER an
owner (`nora.agent.link_owners.LINK_OWNERS` names it as `owner.tool`) OR
declares that parameter in `NativeTool.link_inputs` (design/social-per-network/
30-link-owner-registry.md, "Tests and enforcement": "every tool whose
signature has a parameter named url or link is either an owner or declares it
in link_inputs").

**Scoped to `url`, not `link`, for PR 1.** `instagram_read` — today's only
owner — accepts both `link` (its own reading parameter) and `url` (design
13's alias, "same as link"); it is exempted here as an owner rather than by
declaring `link_inputs`, since its `url` is not a redirect TARGET, it is the
same argument under the name a model reaches for first. `x_read` and
`linkedin_read` will need the same exemption once PR 2/3 register them and
add their own `link` parameter to this check; widening this gate to cover
`link` for every tool, not just owners, is PR 3's job ("the owners use the
registry"), when those three tools start consulting the registry themselves
for a link belonging to a DIFFERENT network. `url` is the generic-fetcher
spelling (`web_fetch`, `media_fetch`) this PR's registry actually routes, so
this is the gate that catches a new one of THOSE that forgets to declare
itself — the failure design 30's "Where the lookup runs" §A calls out: "a new
fetcher that forgets the call is silently unrouted."
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


def test_every_url_parameter_is_declared_a_link_input():
    undeclared = []
    for spec in _built_specs():
        if spec.name in _OWNER_TOOLS:
            continue
        signature = inspect.signature(spec.call)
        if "url" not in signature.parameters:
            continue
        declared = {link_input.param for link_input in NATIVE_TOOLS[spec.name].link_inputs}
        if "url" not in declared:
            undeclared.append(spec.name)
    assert undeclared == [], (
        f"tool(s) with a url parameter and no link_inputs declaration: {undeclared}"
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
