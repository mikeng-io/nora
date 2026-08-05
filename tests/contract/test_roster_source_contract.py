"""`RosterSource` has two implementations, so it gets one suite both pass.

`nora.platform.signal.roster.SignalRoster` (over a real aiohttp daemon serving
the shapes signal-cli actually returns) and `nora.testing.FakeRosterSource`.
CLAUDE.md's rule — "a port never gains a second implementation, INCLUDING a
test fake, without a shared contract suite" — exists because of the three
no-ops this codebase shipped green: each had a fake that answered every request
the same way, so the test asserted the implementation back to itself.

The property that actually needs both implementations held to it is the
degradation: `available=False` and an EMPTY roster must never be the same
value, and both implementations must reach `available=False` by both routes an
operator can hit — the daemon is unreadable, and the daemon answers but does
not list this conversation (stale group state on a linked device, which this
deployment has hit and which `sendSyncRequest` is the fix for).
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.platform.signal.roster import SignalRoster
from nora.testing import FakeRosterSource

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
OTHER = "someOtherGroupId="

MEMBERS = (
    frozenset({"+85290000101", "a1000001-0000-4000-8000-000000000001"}),
    frozenset({"+85290000103", "a1000002-0000-4000-8000-000000000002"}),
)


def _daemon(*, groups, status=200):
    async def rpc(request):
        payload = await request.json()
        if status != 200:
            return web.json_response({}, status=status)
        return web.json_response(
            {"jsonrpc": "2.0", "result": groups, "id": payload["id"]}
        )

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    return app


async def _signal(aiohttp_server, *, groups, status=200):
    server = await aiohttp_server(_daemon(groups=groups, status=status))
    return SignalRoster(base_url=str(server.make_url("")), account="+85290000100")


def _live_group(group_id=GROUP):
    """One `listGroups` row in the shape the live daemon returned on 2026-08-06."""
    return {
        "id": group_id, "name": "Ridgeline", "isMember": True, "isBlocked": False,
        "members": [
            {"number": "+85290000101", "uuid": "a1000001-0000-4000-8000-000000000001",
             "isAdmin": True},
            {"number": "+85290000103", "uuid": "a1000002-0000-4000-8000-000000000002",
             "isAdmin": False},
        ],
    }


@pytest.fixture(params=["signal", "fake"])
async def sources(request, aiohttp_server):
    """One (healthy, unreadable) pair per implementation.

    Both members of the pair come from the same implementation so that every
    assertion below is about ONE class, run twice — not about two classes
    compared to each other.
    """
    if request.param == "signal":
        return {
            "healthy": await _signal(aiohttp_server, groups=[_live_group()]),
            "unreadable": await _signal(aiohttp_server, groups=[], status=500),
            "wrong_group": await _signal(aiohttp_server, groups=[_live_group(OTHER)]),
        }
    return {
        "healthy": FakeRosterSource(groups={GROUP: MEMBERS}),
        "unreadable": FakeRosterSource(fail=True),
        "wrong_group": FakeRosterSource(groups={OTHER: MEMBERS}),
    }


async def test_a_readable_group_reports_available_with_its_members(sources):
    roster = await sources["healthy"].roster(conversation_id=GROUP)
    assert roster.available is True
    assert roster.members == MEMBERS


async def test_members_keep_the_id_forms_paired_per_account(sources):
    """A flat set would destroy the pairing, and the pairing is the point:
    under sealed sender the same account reaches Nora as a number on one
    message and as an ACI on another, and only this says they are one account.
    """
    roster = await sources["healthy"].roster(conversation_id=GROUP)
    for account in roster.members:
        assert len(account) == 2, account


async def test_an_unreadable_daemon_is_unavailable_and_never_raises(sources):
    roster = await sources["unreadable"].roster(conversation_id=GROUP)
    assert roster.available is False
    assert roster.members == ()


async def test_a_conversation_the_daemon_does_not_list_is_unavailable_not_empty(sources):
    """signal-cli returning nothing for a group Nora is demonstrably in is a
    real observed state — group state goes stale on a linked device. Reporting
    it as "this group has no members" would be a confident lie told by the
    exact failure mode this deployment has already had."""
    roster = await sources["wrong_group"].roster(conversation_id=GROUP)
    assert roster.available is False
    assert roster.members == ()


async def test_every_implementation_declares_the_platform_it_is_bound_to(sources):
    """`roster()` takes no `platform` — an implementation is bound to one,
    exactly as `PlatformAdapter` is."""
    for source in sources.values():
        assert source.platform == "signal"
