"""The rotating-refresh-token chain, against BOTH OAuth lanes (DEC-308).

`xai` and `openai_codex` share `oauth_lane.RotatingOAuthLane`; this suite runs
the same properties against each, through the keepalive where the property is
the keeper's. The token server ROTATES on every refresh and REFUSES a spent
token (both issuers do), so a double spend is a visible 401, never a quiet
success. No network: a local aiohttp server, a fake clock, a fake sleep.
"""
# ruff: noqa: S105 — every token here is a fake for a local server.

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.app.oauth_keepalive import KeepaliveTiming, OAuthKeepalive
from nora.infra.image_gen.oauth_lane import RotatingOAuthLane
from nora.infra.image_gen.openai_codex_oauth import OpenAICodexOAuth
from nora.infra.image_gen.xai_oauth import XaiOAuth
from nora.obs.metrics import Metrics

LANES = ("xai", "openai_codex")
DAY = 86400.0


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _jwt(n: int, exp: float) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    claims = {"exp": exp, "n": n, "https://api.openai.com/auth": {"chatgpt_account_id": "a"}}
    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


@pytest.fixture
async def issuer():
    """Both issuers' token endpoints on one server, rotating and refusing a
    spent token. `ttl` is the access token's life on the shared fake clock."""
    clock = Clock()
    state: dict = {"live": {"rt-boot"}, "sent": [], "n": 0, "ttl": 10 * DAY,
                   "delay": 0.0, "status": 0, "clock": clock}

    async def token(request: web.Request) -> web.Response:
        data = await request.post()
        rt = str(data.get("refresh_token"))
        state["sent"].append(rt)
        if state["status"]:
            error = state.get("error", "unavailable")
            return web.json_response({"error": error}, status=state["status"])
        if rt not in state["live"]:
            return web.json_response(
                {"error": "invalid_grant", "error_description": "Invalid or unknown refresh token"},
                status=400,
            )
        state["live"].discard(rt)
        state["n"] += 1
        new_rt = f"rt-{state['n']}"
        state["live"].add(new_rt)
        await asyncio.sleep(state["delay"])
        return web.json_response({
            "access_token": _jwt(state["n"], clock.now + state["ttl"]),
            "refresh_token": new_rt,
            "expires_in": state["ttl"],
        })

    async def discovery(_request: web.Request) -> web.Response:
        return web.json_response({"token_endpoint": str(server.make_url("/oauth2/token"))})

    app = web.Application()
    app.router.add_post("/oauth/token", token)  # OpenAI
    app.router.add_post("/oauth2/token", token)  # xAI
    app.router.add_get("/.well-known/openid-configuration", discovery)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/")).rstrip("/"), state
    await server.close()


def _lane(kind: str, tmp_path: Path, base: str, clock: Clock, bootstrap: str = "rt-boot",
          state_dir: Path | None = None) -> RotatingOAuthLane:
    folder = state_dir or (tmp_path / "state")
    if kind == "xai":
        return XaiOAuth(refresh_token=bootstrap, state_file=folder / "xai-oauth.json",
                        issuer=base, timeout_seconds=5.0, clock=clock)
    return OpenAICodexOAuth(bootstrap_refresh_token=bootstrap,
                            state_file=folder / "openai-codex-oauth.json",
                            issuer=base, timeout_seconds=5.0, clock=clock)


def _call(lane: RotatingOAuthLane) -> Callable[[], Awaitable[object]]:
    """What `generate_image` does first: ask its lane for an access token."""
    return lane.access_token  # type: ignore[attr-defined]


class Stop(asyncio.CancelledError):
    """Ends a keeper run after N sleeps (CancelledError: the supervisor
    re-raises it, as it does a shutdown)."""


def _sleeper(clock: Clock, slept: list[float], stop_after: int):
    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) > stop_after:
            raise Stop
        clock.now += seconds
        await asyncio.sleep(0)

    return sleep


async def _run_keeper(lane: RotatingOAuthLane, clock: Clock, *, stop_after: int,
                      interval: float = DAY, metrics: Metrics | None = None) -> list[float]:
    slept: list[float] = []
    keeper = OAuthKeepalive(metrics or Metrics(), clock=clock,
                            sleep=_sleeper(clock, slept, stop_after), rand=lambda: 0.0)
    keeper.register(lane, KeepaliveTiming(interval_seconds=interval))
    with pytest.raises(asyncio.CancelledError):
        await keeper.run(lane, KeepaliveTiming(interval_seconds=interval))
    return slept


# -- the keeper ---------------------------------------------------------------


@pytest.mark.parametrize("kind", LANES)
async def test_the_keeper_refreshes_on_the_interval_with_no_calls(kind, tmp_path, issuer):
    """No image call for days must not mean a dead login: the keeper alone
    keeps it alive. With a 10-day access token the interval alone drives the
    keeper: a refresh at boot (no state yet), then one per day, no call made."""
    base, srv = issuer
    lane = _lane(kind, tmp_path, base, srv["clock"])
    slept = await _run_keeper(lane, srv["clock"], stop_after=3)
    assert slept == [0.0, DAY, DAY, DAY]
    assert srv["sent"] == ["rt-boot", "rt-1", "rt-2"]


@pytest.mark.parametrize("kind", LANES)
async def test_the_keeper_refreshes_before_the_access_token_expires(kind, tmp_path, issuer):
    """A 2-hour access token and a day's interval: the expiry wins, and the
    refresh lands an hour (the lane's skew) before it, while still valid."""
    base, srv = issuer
    srv["ttl"] = 7200.0
    lane = _lane(kind, tmp_path, base, srv["clock"])
    slept = await _run_keeper(lane, srv["clock"], stop_after=2)
    assert slept == [0.0, 3600.0, 3600.0]
    assert srv["sent"] == ["rt-boot", "rt-1"]


@pytest.mark.parametrize("kind", LANES)
async def test_a_restarted_keeper_plans_from_the_state_file(kind, tmp_path, issuer):
    """A process restart must not refresh again at once (that is a spend per
    deploy) nor forget the last refresh: it plans from the link the state file
    holds."""
    base, srv = issuer
    clock = srv["clock"]
    assert await _call(_lane(kind, tmp_path, base, clock))()
    clock.now += 6 * 3600
    later = _lane(kind, tmp_path, base, clock)
    slept = await _run_keeper(later, clock, stop_after=0)
    assert slept == [DAY - 6 * 3600]
    assert srv["sent"] == ["rt-boot"]


@pytest.mark.parametrize("kind", LANES)
async def test_the_keeper_and_live_calls_spend_the_token_once(kind, tmp_path, issuer):
    """The keeper's refresh and two `generate_image` calls at the same moment,
    on the lane object they share: ONE refresh reaches the issuer. A second
    spend of the rotated token would be a 400 here."""
    base, srv = issuer
    srv["delay"] = 0.2
    lane = _lane(kind, tmp_path, base, srv["clock"])
    call = _call(lane)
    first, kept, second = await asyncio.gather(
        call(), lane.refresh(unless_refreshed_since=0.0), call(),
    )
    assert srv["sent"] == ["rt-boot"]
    assert first.status == second.status == "ok"  # type: ignore[attr-defined]
    assert not isinstance(kept, str)


@pytest.mark.parametrize("kind", LANES)
async def test_a_keeper_from_another_process_adopts_a_refresh_it_did_not_make(
    kind, tmp_path, issuer,
):
    """The keeper planned from link 1; a call in ANOTHER process (its own
    lane object, the same state file) refreshed meanwhile. The keeper adopts
    link 2 from the file instead of spending the dead token it planned with."""
    base, srv = issuer
    clock = srv["clock"]
    keeper_lane = _lane(kind, tmp_path, base, clock)
    planned = await keeper_lane.refresh(unless_refreshed_since=0.0)
    assert not isinstance(planned, str)
    clock.now += 60
    other = _lane(kind, tmp_path, base, clock)
    stale = await _call(other)()
    await other._acquire(rejected=stale.access_token)  # type: ignore[attr-defined]
    adopted = await keeper_lane.refresh(unless_refreshed_since=planned.refreshed_at)
    assert srv["sent"] == ["rt-boot", "rt-1"]
    assert not isinstance(adopted, str) and adopted.refresh_token == "rt-2"


# -- the login ----------------------------------------------------------------


@pytest.mark.parametrize("kind", LANES)
async def test_a_fresh_login_supersedes_a_dead_chain_in_the_state_file(kind, tmp_path, issuer):
    """After `xai-login` writes a new token to .env, the old state file's dead
    refresh token must not win over it: the fingerprint retires the old
    chain."""
    base, srv = issuer
    clock = srv["clock"]
    assert (await _call(_lane(kind, tmp_path, base, clock))()).status == "ok"  # type: ignore[attr-defined]
    srv["live"].clear()  # the old chain dies (idle expiry or revocation)
    clock.now += 30 * DAY  # and its access token has long expired
    srv["live"].add("rt-new-login")
    fresh = _lane(kind, tmp_path, base, clock, bootstrap="rt-new-login")
    assert (await _call(fresh)()).status == "ok"  # type: ignore[attr-defined]
    assert srv["sent"] == ["rt-boot", "rt-new-login"]


def _legacy_file(kind: str, tmp_path: Path, refresh_token: str) -> Path:
    """A legacy state file: no fingerprint."""
    folder = tmp_path / "state"
    folder.mkdir()
    name = "xai-oauth.json" if kind == "xai" else "openai-codex-oauth.json"
    (folder / name).write_text(json.dumps({
        "access_token": "at-legacy", "expires_at": 1, "refresh_token": refresh_token,
    }))
    return folder / name


@pytest.mark.parametrize("kind", LANES)
async def test_a_pre_fingerprint_state_file_holding_the_live_chain_is_kept(
    kind, tmp_path, issuer, caplog,
):
    """A new login, then a refresh that wrote the rotated token to an
    unfingerprinted file. The .env token is spent; the file's is the live one.
    Ignoring the file would log the lane out."""
    base, srv = issuer
    srv["live"] = {"rt-legacy-live"}  # rt-boot was spent by the old code
    path = _legacy_file(kind, tmp_path, "rt-legacy-live")
    with caplog.at_level(logging.ERROR):
        assert (await _call(_lane(kind, tmp_path, base, srv["clock"]))()).status == "ok"  # type: ignore[attr-defined]
    assert srv["sent"] == ["rt-legacy-live"]
    saved = json.loads(path.read_text())
    assert saved["bootstrap_sha256"] == hashlib.sha256(b"rt-boot").hexdigest()
    assert saved["refresh_token"] == "rt-1"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("kind", LANES)
async def test_a_pre_fingerprint_state_file_holding_a_dead_chain_yields_to_the_login(
    kind, tmp_path, issuer, caplog,
):
    """The stale-file shape: the old file's token is dead and a new
    login is in .env. One refused try, then the login, with no ERROR (the
    lane is fine) and the file now fingerprinted to the login."""
    base, srv = issuer
    path = _legacy_file(kind, tmp_path, "rt-dead")
    with caplog.at_level(logging.ERROR):
        assert (await _call(_lane(kind, tmp_path, base, srv["clock"]))()).status == "ok"  # type: ignore[attr-defined]
    assert srv["sent"] == ["rt-dead", "rt-boot"]
    saved = json.loads(path.read_text())
    assert saved["bootstrap_sha256"] == hashlib.sha256(b"rt-boot").hexdigest()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("kind", LANES)
async def test_a_pre_fingerprint_chain_refused_for_another_reason_keeps_the_login_unspent(
    kind, tmp_path, issuer,
):
    """Only an issuer saying THIS token is dead retires the old file's chain.
    A refusal of the client (`invalid_client`) says nothing about the token,
    and replaying the possibly-spent .env token then could revoke the family."""
    base, srv = issuer
    _legacy_file(kind, tmp_path, "rt-legacy-live")
    srv["status"], srv["error"] = 401, "invalid_client"
    result = await _call(_lane(kind, tmp_path, base, srv["clock"]))()
    assert (result.status, result.reason) == ("unavailable", "auth")  # type: ignore[attr-defined]
    assert srv["sent"] == ["rt-legacy-live"]


@pytest.mark.parametrize("kind", LANES)
async def test_an_edge_403_is_retried_not_a_dead_login(kind, tmp_path, issuer):
    """A 403 without a dead-token code is a challenge in front of the issuer:
    transient. Mapped to `auth`, it would page "log in again" and park the keeper
    for a day."""
    base, srv = issuer
    srv["status"], srv["error"] = 403, "challenge"
    result = await _call(_lane(kind, tmp_path, base, srv["clock"]))()
    assert result.status == "unavailable"  # type: ignore[attr-defined]
    assert result.reason not in ("auth", "state_unwritable")  # type: ignore[attr-defined]


@pytest.mark.parametrize("kind", LANES)
async def test_a_transient_failure_of_a_live_call_is_logged(kind, tmp_path, issuer, caplog):
    base, srv = issuer
    srv["status"] = 503
    with caplog.at_level(logging.WARNING):
        result = await _call(_lane(kind, tmp_path, base, srv["clock"]))()
    assert (result.status, result.reason) == ("unavailable", "down")  # type: ignore[attr-defined]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(kind in w and "down" in w and "503" in w for w in warnings), warnings


@pytest.mark.parametrize("kind", LANES)
async def test_a_rotation_held_only_in_memory_raises_the_refused_gauge(
    kind, tmp_path, issuer, monkeypatch,
):
    """Refreshed, but the write failed: a restart now loses the chain. The
    alert's gauge must say so while there is still time, and clear once a
    later call's retry lands the write."""
    import os

    base, srv = issuer
    clock = srv["clock"]
    metrics = Metrics()
    lane = _lane(kind, tmp_path, base, clock)
    OAuthKeepalive(metrics, clock=clock).register(lane, KeepaliveTiming(interval_seconds=DAY))
    real_replace = os.replace

    def failing_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)
    assert (await _call(lane)()).status == "ok"  # type: ignore[attr-defined]
    assert metrics.get("nora_oauth_refresh_refused", lane=kind) == 1
    monkeypatch.setattr(os, "replace", real_replace)
    assert (await _call(lane)()).status == "ok"  # type: ignore[attr-defined]
    assert metrics.get("nora_oauth_refresh_refused", lane=kind) == 0
    assert srv["sent"] == ["rt-boot"]


@pytest.mark.parametrize("kind", LANES)
async def test_a_token_shorter_lived_than_the_skew_does_not_spin_the_keeper(
    kind, tmp_path, issuer,
):
    """A 30-minute token with a 1-hour skew: refresh at half its life, not
    every minute (the floor), which would be ~1,400 rotations a day."""
    base, srv = issuer
    srv["ttl"] = 1800.0
    lane = _lane(kind, tmp_path, base, srv["clock"])
    slept = await _run_keeper(lane, srv["clock"], stop_after=2)
    assert slept == [0.0, 900.0, 900.0]


@pytest.mark.parametrize("kind", LANES)
async def test_no_refresh_when_the_rotation_could_not_be_kept(kind, tmp_path, issuer):
    base, srv = issuer
    locked = tmp_path / "ro"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = await _call(_lane(kind, tmp_path, base, srv["clock"], state_dir=locked))()
    finally:
        locked.chmod(0o700)
    assert (result.status, result.reason) == ("unavailable", "state_unwritable")  # type: ignore[attr-defined]
    assert srv["sent"] == []


# -- loud failure, and its metrics -------------------------------------------


@pytest.mark.parametrize("kind", LANES)
async def test_a_refused_refresh_is_logged_with_its_lane_and_never_its_token(
    kind, tmp_path, issuer, caplog,
):
    """A refresh refused with `invalid_grant` must be logged, not swallowed."""
    base, srv = issuer
    lane = _lane(kind, tmp_path, base, srv["clock"], bootstrap="rt-spent-secret")
    with caplog.at_level(logging.WARNING):
        result = await _call(lane)()
    assert (result.status, result.reason) == ("unavailable", "auth")  # type: ignore[attr-defined]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a refused refresh token must log at ERROR"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert kind in text and "auth" in text and "invalid_grant" in text
    assert "rt-spent-secret" not in text


@pytest.mark.parametrize("kind", LANES)
async def test_the_gauges_follow_the_lane_from_boot_through_a_refusal(kind, tmp_path, issuer):
    base, srv = issuer
    clock = srv["clock"]
    metrics = Metrics()
    lane = _lane(kind, tmp_path, base, clock)
    keeper = OAuthKeepalive(metrics, clock=clock)
    keeper.register(lane, KeepaliveTiming(interval_seconds=DAY))
    assert metrics.get("nora_oauth_last_refresh_timestamp_seconds", lane=kind) == 0
    assert metrics.get("nora_oauth_refresh_interval_seconds", lane=kind) == DAY

    refreshed = await lane.refresh(unless_refreshed_since=0.0)  # the keeper's path
    assert not isinstance(refreshed, str)
    assert metrics.get("nora_oauth_last_refresh_timestamp_seconds", lane=kind) == clock.now
    assert metrics.get("nora_oauth_access_expiry_timestamp_seconds", lane=kind) == pytest.approx(
        clock.now + 10 * DAY, abs=1,
    )

    # A restart publishes the state file's link, not 0.
    rebooted = Metrics()
    OAuthKeepalive(rebooted, clock=clock).register(
        _lane(kind, tmp_path, base, clock), KeepaliveTiming(interval_seconds=DAY),
    )
    assert rebooted.get("nora_oauth_last_refresh_timestamp_seconds", lane=kind) == clock.now

    srv["live"].clear()
    clock.now += DAY
    assert isinstance(await lane.refresh(unless_refreshed_since=clock.now), str)
    assert metrics.get("nora_oauth_refresh_refused", lane=kind) == 1
    assert metrics.get("nora_oauth_refresh_failures_total", lane=kind, reason="auth") == 1
    # A live call's failure counts too: the observer sits on the lane.
    clock.now += 10 * DAY  # the access token has expired, so the call refreshes
    await _call(lane)()
    assert metrics.get("nora_oauth_refresh_failures_total", lane=kind, reason="auth") == 2


@pytest.mark.parametrize("kind", LANES)
async def test_a_usable_check_never_serves_an_empty_cache(kind, tmp_path, issuer, monkeypatch):
    """`_usable` is what says a cached link may answer. Told yes with nothing
    cached, the lane must refresh, not return None: an assert on that
    narrowing is stripped by `python -O`, and the caller would get None."""
    base, srv = issuer
    lane = _lane(kind, tmp_path, base, srv["clock"])
    monkeypatch.setattr(lane, "_usable", lambda *_args: True)
    assert (await _call(lane)()).status == "ok"  # type: ignore[attr-defined]
    assert srv["sent"] == ["rt-boot"]
