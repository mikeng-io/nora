"""`nora.platform.instagram.self_id.derive_identity` — Nora's own id, from the
bridge's `GET /health`.

The bridge is a stand-in that answers the shapes the real one answers,
including its WRONG-call shapes: a checkpointed session still returns 200 with
a cached `self` block, and that is the case the whole module exists to refuse.
A fake that only ever answered the happy body would let a test prove nothing.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.identity import Identifier
from nora.platform.instagram.self_id import InstagramIdentityError, derive_identity

NORA_FBID = "18247361241432477"
NORA_IGID = "79597894900"


@pytest.fixture
async def bridge():
    """`(box, base_url)` — the bridge answers whatever the test puts in the
    box, so each test states the WIRE it is defending against."""
    box: dict = {
        "status": 200,
        "body": {
            "ok": True, "session": "authenticated", "connected": True, "seq": 22,
            "self": {"fbid": NORA_FBID, "igid": NORA_IGID, "username": "not_____nora"},
        },
    }

    async def health(request: web.Request) -> web.Response:
        return web.json_response(box["body"], status=box["status"])

    app = web.Application()
    app.router.add_get("/health", health)
    server = TestServer(app)
    await server.start_server()
    yield box, str(server.make_url("")).rstrip("/")
    await server.close()


async def test_an_authenticated_bridge_yields_the_fbid_as_primary(bridge):
    _box, base_url = bridge

    primary, identifiers = await derive_identity(base_url)

    assert primary == NORA_FBID
    assert Identifier("instagram", "fbid", NORA_FBID) in identifiers
    assert Identifier("instagram", "igid", NORA_IGID) in identifiers


async def test_the_username_is_NEVER_recorded_as_an_identifier(bridge):
    """A handle is renameable, so it is not a matchable kind on any platform
    and `Identifier` refuses to carry one. Asserted on the VALUES, so a kind
    renamed to something `Identifier` would accept still fails here."""
    _box, base_url = bridge

    _primary, identifiers = await derive_identity(base_url)

    assert all(i.value != "not_____nora" for i in identifiers)
    assert {i.kind for i in identifiers} == {"fbid", "igid"}


async def test_a_CHECKPOINTED_session_is_refused_even_though_it_answers_200(bridge):
    """The load-bearing one. The bridge deliberately answers 200 for a
    checkpoint — an Instagram challenge is a normal operating state, not a
    condition to crash on — and it still serves the `self` block it cached.
    An adapter that took the identity from that would register as fully
    healthy and 409 every single send."""
    box, base_url = bridge
    box["body"] = {**box["body"], "session": "checkpointed", "detail": "challenge required"}

    with pytest.raises(InstagramIdentityError, match="checkpointed"):
        await derive_identity(base_url)


async def test_a_LOGGED_OUT_session_is_refused(bridge):
    box, base_url = bridge
    box["body"] = {**box["body"], "session": "logged_out"}

    with pytest.raises(InstagramIdentityError, match="logged_out"):
        await derive_identity(base_url)


async def test_an_UNKNOWN_session_state_is_refused_rather_than_assumed_fine(bridge):
    """Fail-closed. Matching `authenticated` positively rather than excluding
    the two known-bad values is what makes a state this adapter has never
    heard of mean "cannot act"."""
    box, base_url = bridge
    box["body"] = {**box["body"], "session": "something_new"}

    with pytest.raises(InstagramIdentityError):
        await derive_identity(base_url)


async def test_a_non_200_is_refused(bridge):
    box, base_url = bridge
    box["status"] = 502

    with pytest.raises(InstagramIdentityError, match="502"):
        await derive_identity(base_url)


async def test_an_authenticated_bridge_with_no_self_fbid_is_refused(bridge):
    """A resumed boot that never ran `LoadIndex` and has no cached `self.json`
    yet. Without an fbid there is nothing to compare a mention against, and a
    guess would be wrong."""
    box, base_url = bridge
    box["body"] = {**box["body"], "self": {"fbid": "", "igid": NORA_IGID}}

    with pytest.raises(InstagramIdentityError, match=r"self\.fbid"):
        await derive_identity(base_url)


async def test_a_missing_igid_still_yields_the_messaging_identity(bridge):
    """The igid is the SOCIAL axis's id. Its absence must not cost the
    messaging one — that would be a story read taking mention-matching down
    with it."""
    box, base_url = bridge
    box["body"] = {**box["body"], "self": {"fbid": NORA_FBID}}

    primary, identifiers = await derive_identity(base_url)

    assert primary == NORA_FBID
    assert {i.kind for i in identifiers} == {"fbid"}


async def test_an_unreachable_bridge_raises_rather_than_returning_a_blank():
    """The caller (`AdapterRegistry`) degrades on this; a blank identity
    returned instead would put `""` in the self-alias set."""
    with pytest.raises(Exception):  # noqa: B017 — any failure, none of them silent
        await derive_identity("http://127.0.0.1:1", timeout=0.2)
