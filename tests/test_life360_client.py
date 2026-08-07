"""`Life360Client` — auth, token cache, and the 403/challenge discipline
(design/19 §4, DEC-55).

Every scenario runs offline against a local `aiohttp` server that behaves like
Life360 — INCLUDING the shapes that make this hard (CLAUDE.md: never a fake that
answers every request the same way):

* the token endpoint (`POST /token.json`) and the members endpoint
  (`GET /circles/{id}/members`) are DIFFERENT paths with different shapes; the
  token endpoint 405s on a GET and the members endpoint 405s on a POST, like the
  real host, and an unknown path 404s;
* the token endpoint REQUIRES the Basic client credential and the app
  User-Agent (the study's missing ingredient) — omit either and it 403s, exactly
  the way the real endpoint refuses a client that does not look like the app;
* the members endpoint can return the 403 (ban signal) and the non-JSON
  challenge PAGE the study named as the app-update breakage;
* the server records every request, so a test can prove the client does NOT
  re-auth-loop — the ban risk (§4.1).

The load-bearing test is `test_a_persistent_403_backs_off_and_never_loops`:
reverting the one-re-auth-per-read discipline turns it red.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.presence import WireState
from nora.infra.life360 import Life360Client, ReadStatus
from nora.infra.life360.members import parse_members
from nora.obs.metrics import Metrics

CREDENTIAL = "dGVzdC1jbGllbnQtY3JlZGVudGlhbA=="
UA = "com.life360.android.safetymapd/KOKO/24.0.0 android/13"
NOW_MS = 1_000_000_000_000


def _member(mid, *, ts=None, share="1", disconnected="0", lat="22.44", lng="114.02"):
    m = {
        "id": mid,
        "features": {"shareLocation": share},
        "issues": {"disconnected": disconnected},
    }
    if ts is not None:
        m["location"] = {
            "timestamp": str(ts), "latitude": lat, "longitude": lng,
            "accuracy": "10", "battery": "80", "isDriving": "0",
            "address1": "Yoho Mall",
        }
    return m


class _Life360:
    """The two Life360 endpoints, each behaving like the real host — including
    the wrong verb, the ban 403 and the challenge page."""

    def __init__(self) -> None:
        # Per-endpoint behaviour a test flips.
        self.members_mode = "ok"           # ok | 403 | challenge | empty | shape
        self.auth_mode = "ok"             # ok | 403 | notoken
        # Number of leading members calls that 403 before behaving as
        # `members_mode` — models a STALE token that recovers after a re-auth.
        self.members_403_countdown = 0
        self.requests: list[str] = []      # path log, for the no-loop assertion
        self.members_body = {"members": [
            _member("m-live", ts=NOW_MS // 1000),               # fresh → live
            _member("m-stale", ts=NOW_MS // 1000 - 3600),       # 1h old → stale
            _member("m-off", share="0"),                        # sharing off
        ]}

    async def token(self, request: web.Request) -> web.Response:
        self.requests.append("token")
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic ") or "User-Agent" not in request.headers:
            # The real endpoint refuses a client that does not look like the app.
            return web.json_response({"errorMessage": "not authorized"}, status=403)
        form = await request.post()
        if form.get("grant_type") != "password":
            return web.json_response({"errorMessage": "bad grant"}, status=400)
        if self.auth_mode == "403":
            return web.json_response({"errorMessage": "login problem"}, status=403)
        if self.auth_mode == "notoken":
            return web.json_response({"token_type": "Bearer"}, status=200)
        return web.json_response(
            {"access_token": "tok-abc", "token_type": "Bearer"}, status=200
        )

    async def circles(self, request: web.Request) -> web.Response:
        self.requests.append("circles")
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return web.json_response({"errorMessage": "no token"}, status=403)
        return web.json_response({"circles": [{"id": "c1", "name": "Ridgeline"}]})

    async def members(self, request: web.Request) -> web.Response:
        self.requests.append("members")
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return web.json_response({"errorMessage": "no token"}, status=403)
        if self.members_403_countdown > 0:
            self.members_403_countdown -= 1
            return web.json_response({"errorMessage": "forbidden"}, status=403)
        if self.members_mode == "403":
            return web.json_response({"errorMessage": "forbidden"}, status=403)
        if self.members_mode == "challenge":
            # The app-update challenge PAGE: a 200 whose body is HTML, not JSON.
            return web.Response(
                text="<html><body>Are you human?</body></html>",
                status=200, content_type="text/html",
            )
        if self.members_mode == "empty":
            return web.json_response({"members": []}, status=200)
        if self.members_mode == "shape":
            return web.json_response({"errorMessage": "expired"}, status=200)
        return web.json_response(self.members_body, status=200)


@pytest.fixture
async def life360():
    fake = _Life360()
    app = web.Application()
    app.router.add_post("/token.json", fake.token)
    app.router.add_get("/circles", fake.circles)
    app.router.add_get("/circles/{cid}/members", fake.members)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/"))
    yield fake, base
    await server.close()


def _client(base, *, metrics=None) -> Life360Client:
    return Life360Client(
        client_credential=CREDENTIAL, username="u", password="p", user_agent=UA,  # noqa: S106
        token_url=f"{base}token.json",
        members_url=f"{base}circles/{{circle_id}}/members",
        circles_url=f"{base}circles",
        metrics=metrics,
    )


async def test_a_healthy_read_authenticates_then_returns_classified_members(life360):
    fake, base = life360
    client = _client(base)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.OK
    states = {m.life360_id: m.wire_state for m in read.members}
    assert states == {
        "m-live": WireState.LIVE,
        "m-stale": WireState.STALE,
        "m-off": WireState.SHARING_OFF,
    }
    assert fake.requests == ["token", "members"]


async def test_a_members_body_over_the_log_cap_still_parses(life360):
    """A real /members response is well over the http log cap (`_MAX_BODY`, 2 KB) —
    each member carries dozens of location/communications fields the client
    ignores. The FULL body must parse; only the LOGGED copy is bounded. Parse a
    truncated slice instead and every genuine response is shredded mid-document
    and misread as the challenge PAGE — green in tests, zero fixes in production
    (Kimi review 2026-08-07, the 61-day shape). Reverting `http.py` to parse
    `text[:_MAX_BODY]` turns this red."""
    fake, base = life360
    for m in fake.members_body["members"]:
        m["padding"] = "x" * 1500          # push the serialised body well past 2 KB
    read = await _client(base).read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.OK
    assert len(read.members) == 3          # parsed in full, not truncated to a canary


async def test_the_token_is_cached_across_reads(life360):
    fake, base = life360
    client = _client(base)
    await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    # ONE token POST, TWO member GETs — the token is cached in memory (§4).
    assert fake.requests.count("token") == 1
    assert fake.requests.count("members") == 2


async def test_a_stale_token_403_reauths_once_and_succeeds(life360):
    fake, base = life360
    client = _client(base)
    await client.read_circle(circle_id="c1", now_ms=NOW_MS)   # warms the token
    fake.requests.clear()
    # The cached token is now "stale": the FIRST members call 403s, then a
    # re-auth recovers it and the retry succeeds.
    fake.members_403_countdown = 1
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.OK
    # Exactly one re-auth happened inside that read, not a loop: GET 403 →
    # re-auth (one token POST) → GET 200.
    assert fake.requests.count("token") == 1
    assert fake.requests.count("members") == 2


async def test_a_persistent_403_backs_off_and_never_loops(life360):
    """R-31-adjacent / §4.1 — the ban-risk guard. A members endpoint that always
    403s must NOT trigger an unbounded re-auth loop: at most one re-auth per
    read, an alarm, and `auth_failures` climbing so the collector can back off.
    Revert the one-re-auth-per-read discipline and the token-POST count explodes."""
    fake, base = life360
    fake.members_mode = "403"
    metrics = Metrics()
    client = _client(base, metrics=metrics)

    reads = 3
    for _ in range(reads):
        read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
        assert read.status is ReadStatus.BANNED

    # Per read: token None → auth (POST) → GET 403 → re-auth (POST) → GET 403.
    # So at most 2 token POSTs PER READ — bounded, never a tight loop.
    assert fake.requests.count("token") <= 2 * reads
    assert client.auth_failures == reads               # climbs → collector backs off
    assert metrics.get("nora_presence_auth_alarms_total", reason="banned") == reads


async def test_a_challenge_page_is_its_own_status_and_alarms(life360):
    fake, base = life360
    fake.members_mode = "challenge"
    metrics = Metrics()
    client = _client(base, metrics=metrics)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.CHALLENGE        # NOT unavailable, NOT ok
    assert metrics.get("nora_presence_auth_alarms_total", reason="challenge") == 1


async def test_an_empty_circle_reads_ok_with_no_members(life360):
    """The client returns ok+[] for an empty circle; the collector's canary — not
    the client — decides an empty circle is suspicious (§4)."""
    fake, base = life360
    fake.members_mode = "empty"
    client = _client(base)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.OK
    assert read.members == ()


async def test_an_unexpected_json_shape_is_unavailable_not_empty(life360):
    fake, base = life360
    fake.members_mode = "shape"
    client = _client(base)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.UNAVAILABLE


async def test_a_refused_credential_is_banned_not_ok(life360):
    fake, base = life360
    fake.auth_mode = "403"
    metrics = Metrics()
    client = _client(base, metrics=metrics)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.BANNED
    assert metrics.get("nora_presence_auth_alarms_total", reason="auth_403") == 1


async def test_a_token_response_without_a_token_is_banned(life360):
    fake, base = life360
    fake.auth_mode = "notoken"
    client = _client(base)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.BANNED


# ── discover(): the operator capture path (nora presence members) ────────────

async def test_discover_lists_circles_and_members_with_names(life360):
    """`discover` keeps the member NAME (firstName/lastName) that `read_circle`'s
    classification path drops — the operator needs it to map a Life360 id to a
    person. Auth once, list circles, then members per circle."""
    fake, base = life360
    fake.members_body = {"members": [
        {"id": "m-miles", "firstName": "Miles", "lastName": "Novak"},
        {"id": "m-gab", "firstName": "Grant"},
    ]}
    circles = await _client(base).discover()
    assert [(c.circle_id, c.name) for c in circles] == [("c1", "Ridgeline")]
    assert {(m.member_id, m.name) for m in circles[0].members} == {
        ("m-miles", "Miles Novak"), ("m-gab", "Grant"),
    }


async def test_discover_raises_a_readable_error_when_auth_fails(life360):
    """UNLIKE read_circle, discover RAISES — it backs a one-shot CLI, and a bad
    credential must be a clear message + non-zero exit, not a degraded value."""
    from nora.infra.life360 import Life360DiscoveryError
    fake, base = life360
    fake.auth_mode = "403"
    with pytest.raises(Life360DiscoveryError, match="authentication failed"):
        await _client(base).discover()


# ── classification is pure; unit-test it directly (no server) ────────────────

def test_classification_orders_sharing_off_before_freshness():
    body = {"members": [_member("off", share="0", ts=NOW_MS // 1000 - 99999)]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.wire_state is WireState.SHARING_OFF
    assert m.has_fix is False


def test_classification_marks_disconnected():
    body = {"members": [_member("d", disconnected="1", ts=NOW_MS // 1000)]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.wire_state is WireState.DISCONNECTED


def test_classification_no_location_is_no_fix():
    body = {"members": [_member("n")]}          # sharing on, but no location block
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.wire_state is WireState.NO_FIX


def test_a_malformed_body_yields_no_members():
    assert parse_members("not a dict", now_ms=NOW_MS) == []
    assert parse_members({"error": "x"}, now_ms=NOW_MS) == []
