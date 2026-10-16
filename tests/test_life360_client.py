"""`Life360Client` — auth, token cache, and the 403/challenge discipline
(DEC-55).

Every scenario runs offline against a local `aiohttp` server that behaves like
Life360 — INCLUDING the shapes that make this hard (CLAUDE.md: never a fake that
answers every request the same way):

* the token endpoint (`POST /token.json`) and the members endpoint
  (`GET /circles/{id}/members`) are DIFFERENT paths with different shapes; the
  token endpoint 405s on a GET and the members endpoint 405s on a POST, like the
  real host, and an unknown path 404s;
* the token endpoint REQUIRES the Basic client credential and the app
  User-Agent — omit either and it 403s, exactly the way the real endpoint
  refuses a client that does not look like the app;
* the members endpoint can return the 403 (ban signal) and the non-JSON
  challenge PAGE an app-update breakage produces;
* the server records every request, so a test can prove the client does NOT
  re-auth-loop — the ban risk.

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


def _member(mid, *, ts=None, share="1", disconnected="0", lat="22.44", lng="114.02",
            speed=None):
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
        if speed is not None:
            m["location"]["speed"] = speed
    return m


def _refused(fake, request):
    """The shared bearer gate for the data endpoints: None to proceed, or the
    refusal a real host gives a missing or dead token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.json_response({"errorMessage": "no token"}, status=403)
    bearer = auth.removeprefix("Bearer ")
    fake.bearers.append(bearer)
    if bearer in fake.rejected_bearers:
        return web.json_response({"errorMessage": "unauthorized"},
                                 status=fake.rejected_status)
    return None


class _Life360:
    """The two Life360 endpoints, each behaving like the real host — including
    the wrong verb, the ban 403 and the challenge page."""

    def __init__(self) -> None:
        # Per-endpoint behaviour a test flips.
        self.location_requests: list[tuple[str, str]] = []   # (member_id, type) pings
        self.members_mode = "ok"           # ok | 403 | challenge | empty | shape
        self.auth_mode = "ok"             # ok | 403 | notoken
        # Number of leading members calls that 403 before behaving as
        # `members_mode` — models a STALE token that recovers after a re-auth.
        self.members_403_countdown = 0
        self.places_mode = "ok"            # ok | 403 | challenge
        self.requests: list[str] = []      # path log, for the no-loop assertion
        # Token mode: every bearer a data endpoint saw, and the ones it refuses
        # (with `rejected_status` — Life360's refusal of a dead bearer may be a
        # 401 or a 403, and token mode must treat both as a rejected token).
        self.bearers: list[str] = []
        self.rejected_bearers: set[str] = set()
        self.rejected_status = 403
        self.members_body = {"members": [
            _member("m-live", ts=NOW_MS // 1000),               # fresh → live
            _member("m-stale", ts=NOW_MS // 1000 - 3600),       # 1h old → stale
            _member("m-off", share="0"),                        # sharing off
        ]}

    async def token(self, request: web.Request) -> web.Response:
        self.requests.append("token")
        auth = request.headers.get("Authorization", "")
        # The real endpoint refuses a client that does not look like the app: it
        # needs the Basic client credential AND the KOKO app User-Agent. Assert
        # the UA VALUE, not just its presence: aiohttp sends a default UA on
        # every request, so `"User-Agent" not in headers` is vacuous — dropping
        # the KOKO UA plumbing entirely would keep every test green while the
        # real host 403s.
        if not auth.startswith("Basic ") or request.headers.get("User-Agent") != UA:
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
        if (refused := _refused(self, request)) is not None:
            return refused
        return web.json_response({"circles": [{"id": "c1", "name": "Ridgeline"}]})

    async def places(self, request: web.Request) -> web.Response:
        """The places endpoint, shaped like the live probe: string
        coordinates, `circleId` echoed, nullable type fields."""
        self.requests.append("places")
        if (refused := _refused(self, request)) is not None:
            return refused
        if self.places_mode == "403":
            return web.json_response({"errorMessage": "forbidden"}, status=403)
        if self.places_mode == "challenge":
            return web.Response(
                text="<html><body>Are you human?</body></html>",
                status=200, content_type="text/html",
            )
        return web.json_response({"places": [
            {"id": "p-home", "ownerId": "o1", "circleId": "c-echoed-differently",
             "name": "Home", "latitude": "22.4471", "longitude": "114.0228",
             "radius": "80", "type": None, "typeLabel": None},
            {"id": "p-tower", "ownerId": "o1", "circleId": "c1",
             "name": "Acme Tower", "latitude": "22.2794", "longitude": "114.1720",
             "radius": "150", "type": None, "typeLabel": None},
            {"id": "", "name": "malformed — no id", "latitude": "x", "longitude": None},
        ]}, status=200)

    async def members(self, request: web.Request) -> web.Response:
        self.requests.append("members")
        if (refused := _refused(self, request)) is not None:
            return refused
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
        if self.members_mode == "429":
            return web.json_response({"errorMessage": "rate limited"}, status=429)
        return web.json_response(self.members_body, status=200)

    async def request_location(self, request: web.Request) -> web.Response:
        if (refused := _refused(self, request)) is not None:
            return refused
        data = await request.post()
        self.location_requests.append((request.match_info["mid"], data.get("type", "")))
        return web.json_response({"requestId": "r1", "isPollable": True}, status=200)


@pytest.fixture
async def life360():
    fake = _Life360()
    app = web.Application()
    app.router.add_post("/token.json", fake.token)
    app.router.add_get("/circles", fake.circles)
    app.router.add_get("/circles/{cid}/members", fake.members)
    app.router.add_get("/circles/{cid}/places", fake.places)
    app.router.add_post("/circles/{cid}/members/{mid}/request", fake.request_location)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/"))
    yield fake, base
    await server.close()


def _client(base, *, metrics=None, access_token=None) -> Life360Client:
    return Life360Client(
        access_token=access_token,
        client_credential=CREDENTIAL, username="u", password="p", user_agent=UA,  # noqa: S106
        token_url=f"{base}token.json",
        members_url=f"{base}circles/{{circle_id}}/members",
        circles_url=f"{base}circles",
        places_url=f"{base}circles/{{circle_id}}/places",
        request_url=f"{base}circles/{{circle_id}}/members/{{member_id}}/request",
        metrics=metrics,
    )


async def test_request_location_pings_the_phone_with_type_location(life360):
    """DEC-67: the on-demand ping — POST .../request with form `type=location`,
    authenticated. True on 200."""
    fake, base = life360
    client = _client(base)
    ok = await client.request_location(circle_id="c1", member_id="m-live")
    assert ok is True
    assert fake.location_requests == [("m-live", "location")]


async def test_request_location_is_false_when_life360_refuses(life360):
    """A ban-class 403 even after a re-auth → False (never raises); the caller
    falls back to the stored fix."""
    fake, base = life360
    fake.auth_mode = "notoken"                 # authentication yields no token
    client = _client(base)
    assert await client.request_location(circle_id="c1", member_id="m-live") is False


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
    Reverting `http.py` to parse `text[:_MAX_BODY]` turns this red."""
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
    # ONE token POST, TWO member GETs — the token is cached in memory.
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
    """The ban-risk guard. A members endpoint that always
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


async def test_a_429_is_ban_class_and_drives_the_backoff(life360):
    """An explicit rate-limit (429) is the strongest ban-class signal: 429 →
    BANNED, `auth_failures` climbs (the collector backs off), the alarm fires,
    and it does NOT re-auth (no credential POST mid-throttle). Revert the 429
    branch and this goes red."""
    fake, base = life360
    fake.members_mode = "429"
    metrics = Metrics()
    client = _client(base, metrics=metrics)
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.BANNED
    assert client.auth_failures == 1
    assert metrics.get("nora_presence_auth_alarms_total", reason="banned") == 1
    assert fake.requests.count("token") == 1           # authed once, did NOT re-auth on 429


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
    the client — decides an empty circle is suspicious."""
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


def test_classification_captures_speed_km_h_from_wire():
    """Life360 reports `location.speed` in metres/second; converted to km/h at
    the wire boundary so nothing downstream re-derives units."""
    body = {"members": [_member("s", ts=NOW_MS // 1000, speed="10")]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.speed_kmh == pytest.approx(36.0)


def test_classification_speed_missing_is_none():
    body = {"members": [_member("s", ts=NOW_MS // 1000)]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.speed_kmh is None


def test_classification_negative_speed_sentinel_is_none():
    """Life360 reports `-1` when it cannot derive a GPS speed — "unknown", not
    "stationary", so it must not collapse to 0."""
    body = {"members": [_member("s", ts=NOW_MS // 1000, speed="-1")]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.speed_kmh is None


def test_classification_garbage_speed_is_none():
    body = {"members": [_member("s", ts=NOW_MS // 1000, speed="not-a-number")]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.speed_kmh is None


@pytest.mark.parametrize("wire_speed", ["inf", "Infinity", "nan", "-inf"])
def test_classification_non_finite_speed_is_none(wire_speed):
    """`float("inf"/"nan")` parses without error but is not a speed — and
    `json.dumps(float("inf"))` emits invalid JSON, which would fault the
    tool-call-log/Langfuse write downstream if this ever reached a reply."""
    body = {"members": [_member("s", ts=NOW_MS // 1000, speed=wire_speed)]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.speed_kmh is None


def test_classification_implausibly_large_speed_is_none():
    """A GPS glitch, not a real reading — rejected outright rather than
    clamped, which would still be a manufactured number (R-5)."""
    body = {"members": [_member("s", ts=NOW_MS // 1000, speed="1e9")]}
    (m,) = parse_members(body, now_ms=NOW_MS)
    assert m.speed_kmh is None


# ── read_places ──────────────────────────────────────────────────────────────

async def test_a_places_read_types_the_wire_and_trusts_the_asked_circle(life360):
    """The live-probed shape parses: string coordinates become floats, the
    malformed row is skipped, and `circle_id` is the circle the collector ASKED
    about — the body's own `circleId` echo (deliberately different in the fake)
    cannot cross-file a place."""
    _fake, base = life360
    read = await _client(base).read_places(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.OK
    assert [(p.name, p.circle_id) for p in read.places] == [
        ("Home", "c1"), ("Acme Tower", "c1"),
    ]
    home = read.places[0]
    assert home.lat == pytest.approx(22.4471)
    assert home.radius_m == pytest.approx(80.0)


async def test_a_persistent_places_403_is_banned_and_never_loops(life360):
    fake, base = life360
    fake.places_mode = "403"
    metrics = Metrics()
    client = _client(base, metrics=metrics)
    read = await client.read_places(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.BANNED
    # One places GET, one re-auth, one retry — never a loop.
    assert fake.requests.count("places") == 2
    assert fake.requests.count("token") == 2
    # The ban MUST alarm and increment auth_failures, or a places-only 403 would
    # hammer the credential silently. Reverting `_places_banned` to an inline
    # `PlacesRead(BANNED)` reds this.
    assert metrics.get("nora_presence_auth_alarms_total", reason="banned") == 1.0
    assert client.auth_failures >= 1


async def test_a_places_challenge_page_is_its_own_status(life360):
    fake, base = life360
    fake.places_mode = "challenge"
    metrics = Metrics()
    read = await _client(base, metrics=metrics).read_places(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.CHALLENGE
    assert metrics.get("nora_presence_auth_alarms_total", reason="challenge") == 1.0


# ── token mode: Life360 refuses the password grant outright ──────────────────

STATIC = "tok-static-from-the-browser-cookie"


async def test_token_mode_reads_on_the_static_token_and_never_posts_the_credential(life360):
    """With `access_token`, every call — circle read, places read, the location
    ping, operator discovery — carries the operator's bearer, and the token
    endpoint is NEVER hit: the account's password grant is the thing Life360
    refuses, so POSTing it is pure ban risk. Reverting `_authenticate`'s
    token-mode early return turns this red."""
    fake, base = life360
    client = _client(base, access_token=STATIC)
    assert (await client.read_circle(circle_id="c1", now_ms=NOW_MS)).status is ReadStatus.OK
    assert (await client.read_places(circle_id="c1", now_ms=NOW_MS)).status is ReadStatus.OK
    assert await client.request_location(circle_id="c1", member_id="m-live") is True
    assert [c.circle_id for c in await client.discover()] == ["c1"]
    assert "token" not in fake.requests
    assert fake.bearers and set(fake.bearers) == {STATIC}


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_access_token_backs_off_alarms_and_never_posts_a_credential(
    life360, status,
):
    """A dead static token is `banned` with its OWN detail and alarm — the
    operator must mint a new one — and it drives the same `auth_failures`
    back-off. It never re-auths with the password (no token POST) and never
    retries the read (one members GET per read). Without `_token_rejected`, a 403
    falls into password mode's re-auth path and POSTs the credential; a 401 reads
    as plain `unavailable` with no back-off at all."""
    fake, base = life360
    fake.rejected_bearers = {STATIC}
    fake.rejected_status = status
    metrics = Metrics()
    client = _client(base, metrics=metrics, access_token=STATIC)
    reads = 3
    for _ in range(reads):
        read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
        assert read.status is ReadStatus.BANNED
        assert "LIFE360_AUTH_TOKEN" in read.detail
    assert client.auth_failures == reads
    assert "token" not in fake.requests
    assert fake.requests.count("members") == reads
    assert metrics.get("nora_presence_auth_alarms_total", reason="token_rejected") == reads
    # ONE alarm per dead-token read: a `banned` alongside would page the generic
    # PresenceAuthRefused rule for the same token as well.
    assert metrics.get("nora_presence_auth_alarms_total", reason="banned") == 0


async def test_a_rejected_access_token_on_places_and_the_ping_never_posts_a_credential(life360):
    """The places read and the on-demand ping hold the same rule: banned / False,
    alarmed, and no password grant behind them."""
    fake, base = life360
    fake.rejected_bearers = {STATIC}
    metrics = Metrics()
    client = _client(base, metrics=metrics, access_token=STATIC)
    places = await client.read_places(circle_id="c1", now_ms=NOW_MS)
    assert places.status is ReadStatus.BANNED
    assert "LIFE360_AUTH_TOKEN" in places.detail
    assert await client.request_location(circle_id="c1", member_id="m-live") is False
    assert "token" not in fake.requests
    assert metrics.get("nora_presence_auth_alarms_total", reason="token_rejected") == 2
    assert metrics.get("nora_presence_auth_alarms_total", reason="banned") == 0


async def test_password_mode_still_reauths_once_on_a_stale_token(life360):
    """Token mode is opt-in: with no `access_token`, a 403 still drops the token
    and re-POSTs the password grant exactly once (DEC-55), and no
    `token_rejected` alarm fires."""
    fake, base = life360
    metrics = Metrics()
    client = _client(base, metrics=metrics)
    await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    fake.members_403_countdown = 1
    read = await client.read_circle(circle_id="c1", now_ms=NOW_MS)
    assert read.status is ReadStatus.OK
    assert fake.requests.count("token") == 2
    assert metrics.get("nora_presence_auth_alarms_total", reason="token_rejected") == 0
