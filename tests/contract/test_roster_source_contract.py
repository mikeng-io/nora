"""`RosterSource` has FOUR implementations, so it gets one suite all four pass.

`nora.platform.signal.roster.SignalRoster` (over a real aiohttp daemon serving
the shapes signal-cli actually returns), `DiscordRoster`, `InstagramRoster`
(both over a stand-in for their real HTTP endpoint) and
`nora.testing.FakeRosterSource`.
CLAUDE.md's rule — "a port never gains a second implementation, INCLUDING a
test fake, without a shared contract suite" — exists because a fake that
answers every request the same way makes a test assert the implementation back
to itself.

The property that actually needs both implementations held to it is the
degradation: `available=False` and an EMPTY roster must never be the same
value, and both implementations must reach `available=False` by both routes an
operator can hit — the daemon is unreadable, and the daemon answers but does
not list this conversation (stale group state on a linked device, which
`sendSyncRequest` is the fix for).
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.platform.discord.roster import DiscordRoster
from nora.platform.instagram.roster import InstagramRoster
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
    """One `listGroups` row in the shape the live daemon returns."""
    return {
        "id": group_id, "name": "Ridgeline", "isMember": True, "isBlocked": False,
        "members": [
            {"number": "+85290000101", "uuid": "a1000001-0000-4000-8000-000000000001",
             "isAdmin": True},
            {"number": "+85290000103", "uuid": "a1000002-0000-4000-8000-000000000002",
             "isAdmin": False},
        ],
    }


DISCORD_ROOM = "100000000000000203/100000000000000204"
DISCORD_OTHER = "9999999999999999999/100000000000000204"


async def _discord(aiohttp_server, *, members: list[dict], status: int = 200):
    """A `DiscordRoster` pointed at a stand-in for `GET /guilds/{id}/members`.

    Routed on the GUILD in the path, so asking about a guild the bot is not in
    genuinely 404s — the `wrong_group` case has to be reachable the way it
    really is, not simulated by handing back an empty list.
    """
    async def handler(request: web.Request) -> web.Response:
        if request.match_info["guild"] != DISCORD_ROOM.split("/")[0]:
            return web.json_response({"message": "Unknown Guild"}, status=404)
        if status != 200:
            return web.json_response({"message": "boom"}, status=status)
        return web.json_response(members)

    app = web.Application()
    app.router.add_get("/guilds/{guild}/members", handler)
    server = await aiohttp_server(app)
    return DiscordRoster("tok", base_url=str(server.make_url("")).rstrip("/"))


INSTAGRAM_ROOM = "1183780976338188"
INSTAGRAM_OTHER = "9999999999999999"
# A three-person room: TWO rows, because `Users[]` excludes the
# viewer and Nora is not a member of her own room. Both id spaces are on the
# wire; only the messaging fbid becomes a membership token (see the roster
# module's docstring).
INSTAGRAM_MEMBERS = [
    {"fbid": "186217604552756", "igid": "61652861337", "username": "nka_bpumvg"},
    {"fbid": "18457536310970624", "igid": "72038255145", "username": "third"},
]


async def _instagram(aiohttp_server, *, members: list[dict], status: int = 200):
    """An `InstagramRoster` pointed at a stand-in for the bridge's
    `GET /threads/{thread_fbid}/members`.

    Routed on the room id, so asking about a thread the bridge cannot resolve
    genuinely fails the way the real one does (the error envelope at the
    `thread_lookup` stage) — the `wrong_group` case has to be reachable the
    way it really is, not simulated by handing back an empty list.
    """
    async def handler(request: web.Request) -> web.Response:
        if request.match_info.get("thread_fbid") != INSTAGRAM_ROOM:
            return web.json_response(
                {"error": {"code": "internal", "message": "route lookup failed",
                           "stage": "thread_lookup"}}, status=500,
            )
        if status != 200:
            return web.json_response(
                {"error": {"code": "internal", "message": "boom"}}, status=status,
            )
        return web.json_response({"room_id": INSTAGRAM_ROOM, "title": "g",
                                  "is_group": True, "members": members})

    app = web.Application()
    app.router.add_get("/threads/{thread_fbid}/members", handler)
    server = await aiohttp_server(app)
    return InstagramRoster(str(server.make_url("")).rstrip("/"))


@pytest.fixture(params=["signal", "discord", "instagram", "fake"])
async def sources(request, aiohttp_server):
    """One (healthy, unreadable, wrong_group) set per implementation, plus the
    facts that legitimately DIFFER between platforms.

    Every member of a set comes from the same implementation so that each
    assertion below is about ONE class, run three times — not about three
    classes compared to each other.

    `ids_per_account` is declared rather than asserted as a constant because it
    is a genuine platform difference: Signal pairs a phone number with an ACI
    for one account (sealed sender delivers either), while a Discord member has
    exactly one immutable snowflake. What must hold on BOTH is the shape — one
    frozenset PER ACCOUNT, never a flat set of ids — which is what the pairing
    test actually checks.
    """
    if request.param == "signal":
        return {
            "platform": "signal",
            "conversation": GROUP,
            "members": MEMBERS,
            "ids_per_account": 2,
            "healthy": await _signal(aiohttp_server, groups=[_live_group()]),
            "unreadable": await _signal(aiohttp_server, groups=[], status=500),
            "wrong_group": await _signal(aiohttp_server, groups=[_live_group(OTHER)]),
        }
    if request.param == "discord":
        member_rows = [
            {"user": {"id": "100000000000000001"}},
            {"user": {"id": "100000000000000002"}},
        ]
        return {
            "platform": "discord",
            "conversation": DISCORD_ROOM,
            "members": (
                frozenset({"100000000000000001"}),
                frozenset({"100000000000000002"}),
            ),
            "ids_per_account": 1,
            "healthy": await _discord(aiohttp_server, members=member_rows),
            "unreadable": await _discord(aiohttp_server, members=[], status=500),
            "wrong_group": await _discord(aiohttp_server, members=member_rows),
        }
    if request.param == "instagram":
        return {
            "platform": "instagram",
            "conversation": INSTAGRAM_ROOM,
            "members": (
                frozenset({"186217604552756"}),
                frozenset({"18457536310970624"}),
            ),
            # ONE, and not because Instagram has one id kind — it has two, and
            # both are on the wire. Only the messaging fbid is a membership
            # token: an @-mention can carry nothing else, and the id forms are
            # indistinguishable by shape, so a public igid in the same frozenset
            # would be a coin toss on addressing a tag with a number no mention
            # accepts.
            "ids_per_account": 1,
            "healthy": await _instagram(aiohttp_server, members=INSTAGRAM_MEMBERS),
            "unreadable": await _instagram(
                aiohttp_server, members=INSTAGRAM_MEMBERS, status=503,
            ),
            "wrong_group": await _instagram(aiohttp_server, members=INSTAGRAM_MEMBERS),
        }
    return {
        "platform": "signal",
        "conversation": GROUP,
        "members": MEMBERS,
        "ids_per_account": 2,
        "healthy": FakeRosterSource(groups={GROUP: MEMBERS}),
        "unreadable": FakeRosterSource(fail=True),
        "wrong_group": FakeRosterSource(groups={OTHER: MEMBERS}),
    }


#: A conversation each implementation will NOT resolve. Per platform because a
#: Signal group id is not a thing Discord or Instagram could even be asked
#: about — handing one over would make `wrong_group` pass on a malformed-id
#: branch instead of on the one it is written for.
_UNRESOLVABLE = {"discord": DISCORD_OTHER, "instagram": INSTAGRAM_OTHER}


def _other(sources) -> str:
    """A conversation this source will NOT resolve."""
    return _UNRESOLVABLE.get(sources["platform"], GROUP)


async def test_a_readable_group_reports_available_with_its_members(sources):
    roster = await sources["healthy"].roster(conversation_id=sources["conversation"])
    assert roster.available is True
    assert set(roster.members) == set(sources["members"])


async def test_members_keep_the_id_forms_paired_per_account(sources):
    """A flat set would destroy the pairing, and the pairing is the point:
    under sealed sender the same account reaches Nora as a number on one
    message and as an ACI on another, and only this says they are one account.
    """
    roster = await sources["healthy"].roster(conversation_id=sources["conversation"])
    assert roster.members, "a healthy roster must not be empty"
    for account in roster.members:
        assert len(account) == sources["ids_per_account"], account


async def test_an_unreadable_daemon_is_unavailable_and_never_raises(sources):
    roster = await sources["unreadable"].roster(conversation_id=sources["conversation"])
    assert roster.available is False
    assert roster.members == ()


async def test_a_conversation_the_daemon_does_not_list_is_unavailable_not_empty(sources):
    """signal-cli returning nothing for a group Nora is demonstrably in is a
    real state — group state goes stale on a linked device. Reporting it as
    "this group has no members" would be a confident lie."""
    roster = await sources["wrong_group"].roster(conversation_id=_other(sources))
    assert roster.available is False
    assert roster.members == ()


async def test_every_implementation_declares_the_platform_it_is_bound_to(sources):
    """`roster()` takes no `platform` — an implementation is bound to one,
    exactly as `PlatformAdapter` is."""
    for key in ("healthy", "unreadable", "wrong_group"):
        assert sources[key].platform == sources["platform"]
