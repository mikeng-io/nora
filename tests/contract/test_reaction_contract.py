"""One suite, both implementations of `PlatformAdapter.react`.

CLAUDE.md: "A port never gains a second implementation — including a test fake
— without a shared contract suite both pass." `react()` is on
`nora.domain.ports.PlatformAdapter`, so `nora.testing.FakeAdapter.react` is a
genuine second implementation and this is that suite. `FakeAdapter`'s
documented exemption does not cover it: that exemption is about `.parse()` and
`.listen()`, neither of which is on the port, whereas `react()` is.

The property that actually matters here is the last one — a reaction NEVER
raises, whatever the platform does. Every caller of this port method
(`nora.engagement.stages.indicate`) runs detached under `spawn`, so an
exception has no one to report to and would surface only as a lost background
task. A fake that raised where production returns, or vice versa, would make
the pipeline tests prove the wrong thing.
"""

import inspect

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import PlatformAdapter, ReactionResult
from nora.platform.signal.adapter import SignalAdapter
from nora.testing import FakeAdapter

GROUP = ConversationTarget("signal", "Z3JvdXA=", "group")
ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"


async def _signal_adapter(aiohttp_server, *, status: int = 200):
    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if status != 200:
            return web.Response(status=status, text="boom")
        return web.json_response({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    # A placeholder account against a local aiohttp test server — nothing in
    # this suite can reach the real daemon or the real group.
    return SignalAdapter(str(server.make_url("")).rstrip("/"), "+15550000000")


@pytest.fixture(params=["signal", "fake"])
async def working(request, aiohttp_server):
    """An adapter whose platform accepts the reaction."""
    if request.param == "signal":
        return await _signal_adapter(aiohttp_server)
    return FakeAdapter(account="+15550000000")


@pytest.fixture(params=["signal", "fake"])
async def broken(request, aiohttp_server):
    """An adapter whose platform HAS reactions but rejects this one."""
    if request.param == "signal":
        return await _signal_adapter(aiohttp_server, status=500)
    return FakeAdapter(account="+15550000000", react_fails=True)


@pytest.mark.parametrize("impl", [SignalAdapter, FakeAdapter])
def test_react_matches_the_port_signature(impl):
    """Keyword-only after self, per `tests/test_ports.py`'s convention, and
    `ReactionResult`-returning. `isinstance` against a runtime_checkable
    Protocol only checks names, so the shape has to be asserted directly."""
    params = inspect.signature(impl.react).parameters
    for name in ("target", "emoji", "to_author", "to_timestamp", "remove"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert inspect.signature(impl.react).return_annotation == "ReactionResult"


@pytest.mark.parametrize("impl", [SignalAdapter, FakeAdapter])
def test_both_implementations_are_platform_adapters(impl):
    instance = impl("http://x", "+1") if impl is SignalAdapter else impl()
    assert isinstance(instance, PlatformAdapter)


async def test_a_successful_reaction_is_ok_and_supported(working):
    result = await working.react(
        target=GROUP, emoji="\N{EYES}", to_author=ACI, to_timestamp=1754300000000,
    )
    assert result == ReactionResult(ok=True, supported=True)


async def test_removing_a_reaction_is_accepted(working):
    result = await working.react(
        target=GROUP, emoji="\N{EYES}", to_author=ACI, to_timestamp=1754300000000, remove=True,
    )
    assert result.ok is True


async def test_a_rejected_reaction_reports_failure_not_unsupported(broken):
    """A platform that HAS reactions and refused one is `ok=False,
    supported=True`. Reporting it as unsupported would hide a wedged daemon
    behind a label that means "working as designed"."""
    result = await broken.react(
        target=GROUP, emoji="\N{EYES}", to_author=ACI, to_timestamp=1754300000000,
    )
    assert result == ReactionResult(ok=False, supported=True)


async def test_a_reaction_never_raises(broken):
    """The load-bearing property. PROBE: make either implementation raise on
    failure instead of returning, and this is the assertion that fails."""
    try:
        result = await broken.react(
            target=GROUP, emoji="\N{EYES}", to_author=ACI, to_timestamp=1754300000000,
        )
    except Exception as exc:  # noqa: BLE001 — catching broadly IS the assertion here
        pytest.fail(f"react() raised {type(exc).__name__}; it must always return")
    assert isinstance(result, ReactionResult)
