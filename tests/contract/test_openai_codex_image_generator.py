"""`OpenAICodexImageGenerator` + `OpenAICodexOAuth` (DEC-304).

The OpenAI Codex lane against local aiohttp servers, no network: a fake Codex
backend that streams SSE the way the live one does, and a fake
`auth.openai.com` token endpoint that ROTATES the refresh token on every use
and refuses a spent one, the property this lane is built around.
"""
# ruff: noqa: S105, S106 — every token here is a fake for a local server.

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import ImageGenerator, ImageReference, ImageSize
from nora.infra.image_gen import build_image_generator
from nora.infra.image_gen.openai_codex_adapter import OpenAICodexImageGenerator
from nora.infra.image_gen.openai_codex_oauth import (
    CodexLoginError,
    OpenAICodexOAuth,
    device_flow_login,
    refresh_token_from_codex_auth,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _jwt(n: int, *, account: str = "acct-1", ttl: float = 10 * 86400) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    claims = {
        "exp": time.time() + ttl,
        "n": n,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
    }
    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


def _sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


def _image_events(status: str = "completed", result: bytes | None = PNG) -> bytes:
    item: dict = {"id": "ig_1", "type": "image_generation_call", "status": status}
    if result is not None:
        item["result"] = base64.b64encode(result).decode()
    return _sse(
        {"type": "response.created"},
        {"type": "response.output_item.done", "item": item},
        {"type": "response.output_item.done", "item": {"type": "message"}},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 1}}},
    )


TEXT_ONLY = _sse(
    {"type": "response.created"},
    {"type": "response.output_item.done", "item": {"type": "message"}},
    {"type": "response.completed", "response": {}},
)


@pytest.fixture
async def auth_server():
    """A token endpoint that rotates on every refresh and refuses a spent
    refresh token (OpenAI's behaviour, the reason this lane exists)."""
    state: dict = {"live": {"rt-boot"}, "sent": [], "n": 0, "behavior": ""}

    async def token(request: web.Request) -> web.Response:
        data = await request.post()
        state["sent"].append(data.get("refresh_token"))
        state["client_id"] = data.get("client_id")
        if state["behavior"] == "429":
            return web.json_response({"error": "slow"}, status=429)
        rt = data.get("refresh_token")
        if data.get("grant_type") != "refresh_token" or rt not in state["live"]:
            return web.json_response({"error": "refresh_token_reused"}, status=401)
        state["live"].discard(rt)
        state["n"] += 1
        new_rt = f"rt-{state['n']}"
        state["live"].add(new_rt)
        # The issuer has rotated; a slow answer is where a cancelled caller
        # would lose the new token.
        await asyncio.sleep(state.get("delay", 0))
        return web.json_response({"access_token": _jwt(state["n"]), "refresh_token": new_rt})

    app = web.Application()
    app.router.add_post("/oauth/token", token)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/")).rstrip("/"), state
    await server.close()


@pytest.fixture
async def codex_server():
    """A Codex backend: `state["reply"]` is a callable (request, body) ->
    response, and every request's headers + JSON body are recorded."""
    state: dict = {"requests": [], "reply": lambda req, body: _stream(_image_events())}

    async def responses(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        state["requests"].append((dict(request.headers), body))
        return state["reply"](request, body)

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/responses", responses)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/")).rstrip("/"), state
    await server.close()


def _stream(payload: bytes, status: int = 200) -> web.Response:
    return web.Response(body=payload, status=status, content_type="text/event-stream")


def _oauth(tmp_path: Path, issuer: str, bootstrap: str = "rt-boot") -> OpenAICodexOAuth:
    return OpenAICodexOAuth(
        bootstrap_refresh_token=bootstrap,
        state_file=tmp_path / "state" / "openai-codex-oauth.json",
        issuer=issuer,
        timeout_seconds=5.0,
    )


def _gen(oauth: OpenAICodexOAuth, base: str) -> OpenAICodexImageGenerator:
    return OpenAICodexImageGenerator(oauth=oauth, base_url=base, timeout_seconds=5.0)


# -- generation -------------------------------------------------------------


async def test_a_completed_image_call_is_ok_and_the_tool_is_forced(
    tmp_path, auth_server, codex_server,
):
    issuer, _ = auth_server
    base, cx = codex_server
    result = await _gen(_oauth(tmp_path, issuer), base).generate(
        prompt="a red apple", quality="quality",
    )
    assert result.status == "ok"
    assert result.raw == PNG
    assert result.media_type == "image/png"
    assert result.provider == "openai_codex"
    headers, body = cx["requests"][0]
    assert headers["chatgpt-account-id"] == "acct-1"
    assert headers["Authorization"].startswith("Bearer ")
    assert body["model"] == "gpt-5.5"
    assert body["tool_choice"] == {"type": "image_generation"}
    assert body["tools"][0]["model"] == "gpt-image-2"
    assert body["tools"][0]["quality"] == "high"
    assert body["stream"] is True and body["store"] is False
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "a red apple"}]


async def test_a_text_only_reply_is_unavailable_not_ok(tmp_path, auth_server, codex_server):
    """A 200 that carries no image is the failure this lane must not hide."""
    issuer, _ = auth_server
    base, cx = codex_server
    cx["reply"] = lambda req, body: _stream(TEXT_ONLY)
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat")
    assert (result.status, result.reason) == ("unavailable", "no_image")


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (_image_events(status="failed", result=None), ("unavailable", "no_image")),
        (_image_events(status="failed"), ("unavailable", "no_image")),
        (_image_events(status="in_progress"), ("unavailable", "no_image")),
        (_image_events(result=b"not a png"), ("unavailable", "bad_response")),
        (
            _sse({"type": "response.failed",
                  "response": {"error": {"code": "moderation_blocked"}}}),
            ("nothing_found", "refused"),
        ),
    ],
    ids=["call-failed", "failed-with-png", "in-progress-with-png", "not-a-png", "moderation"],
)
async def test_a_stream_without_a_usable_image_is_never_ok(
    tmp_path, auth_server, codex_server, events, expected,
):
    issuer, _ = auth_server
    base, cx = codex_server
    cx["reply"] = lambda req, body: _stream(events)
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat")
    assert (result.status, result.reason) == expected


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (429, {"error": {"code": "usage_limit_reached"}}, "quota"),
        (429, {"error": {"code": "too_many"}}, "rate_limited"),
        (403, {"error": {"code": "forbidden"}}, "unavailable"),
        (400, {"error": {"code": "unsupported_model"}}, "bad_request"),
        (502, {}, "down"),
    ],
)
async def test_http_failures_map_to_unavailable_reasons(
    tmp_path, auth_server, codex_server, status, body, reason,
):
    issuer, _ = auth_server
    base, cx = codex_server
    cx["reply"] = lambda req, b: web.json_response(body, status=status)
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat")
    assert (result.status, result.reason) == ("unavailable", reason)


async def test_a_401_refreshes_once_and_retries(tmp_path, auth_server, codex_server):
    issuer, auth = auth_server
    base, cx = codex_server
    first: dict = {}

    def reply(request, body):
        token = request.headers["Authorization"]
        first.setdefault("token", token)
        if token == first["token"]:
            return web.json_response({"error": {"code": "token_expired"}}, status=401)
        return _stream(_image_events())

    cx["reply"] = reply
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat")
    assert result.status == "ok"
    assert len(cx["requests"]) == 2
    assert auth["sent"] == ["rt-boot", "rt-1"]


async def test_a_401_after_the_retry_is_auth(tmp_path, auth_server, codex_server):
    issuer, _ = auth_server
    base, cx = codex_server
    cx["reply"] = lambda req, body: web.json_response({}, status=401)
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat")
    assert (result.status, result.reason) == ("unavailable", "auth")
    assert len(cx["requests"]) == 2


async def test_references_travel_as_data_uris_of_custody_bytes(
    tmp_path, auth_server, codex_server,
):
    issuer, _ = auth_server
    base, cx = codex_server
    held = {"a3f2c1": PNG, "b4e5d6": JPEG}

    async def resolver(code: str) -> bytes | None:
        return held.get(code)

    result = await _gen(_oauth(tmp_path, issuer), base).generate(
        prompt="make it green",
        reference_images=[ImageReference("[image a3f2c1]"), ImageReference("b4e5d6")],
        reference_resolver=resolver,
    )
    assert result.status == "ok"
    content = cx["requests"][0][1]["input"][0]["content"]
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64," + base64.b64encode(PNG).decode(),
    }
    assert content[2]["image_url"].startswith("data:image/jpeg;base64,")


async def test_a_reference_outside_custody_sends_nothing(tmp_path, auth_server, codex_server):
    issuer, auth = auth_server
    base, cx = codex_server

    async def resolver(code: str) -> bytes | None:
        return None

    gen = _gen(_oauth(tmp_path, issuer), base)
    result = await gen.generate(
        prompt="edit", reference_images=[ImageReference("a3f2c1")], reference_resolver=resolver,
    )
    assert (result.status, result.reason) == ("nothing_found", "unknown_reference")
    no_resolver = await gen.generate(prompt="edit", reference_images=[ImageReference("a3f2c1")])
    assert (no_resolver.status, no_resolver.reason) == ("unavailable", "no_reference_resolver")
    assert cx["requests"] == [] and auth["sent"] == []


@pytest.mark.parametrize(
    ("size", "wire", "named"),
    [
        (ImageSize(aspect_ratio="16:9"), "1536x1024", True),
        (ImageSize(aspect_ratio="2:3"), "1024x1536", True),
        (ImageSize(aspect_ratio="4:3"), "auto", False),  # type: ignore[arg-type]
        (None, "auto", False),
    ],
)
async def test_size_maps_to_a_gpt_image_size_or_auto(
    tmp_path, auth_server, codex_server, size, wire, named,
):
    issuer, _ = auth_server
    base, cx = codex_server
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat", size=size)
    assert result.status == "ok"
    body = cx["requests"][0][1]
    assert body["tools"][0]["size"] == wire
    assert ("aspect ratio" in body["instructions"]) is named


# -- credentials ------------------------------------------------------------


async def test_the_rotated_refresh_token_is_persisted_and_the_bootstrap_never_resent(
    tmp_path, auth_server,
):
    issuer, auth = auth_server
    oauth = _oauth(tmp_path, issuer)
    first = await oauth.access_token()
    assert first.status == "ok"
    saved = json.loads((tmp_path / "state" / "openai-codex-oauth.json").read_text())
    assert saved["refresh_token"] == "rt-1"
    assert saved["access_token"] == first.access_token
    assert oct(os.stat(tmp_path / "state" / "openai-codex-oauth.json").st_mode & 0o777) == "0o600"

    # A restarted process (fresh lane, same .env) reads the state file and
    # spends the ROTATED token; the bootstrap is dead and is never resent.
    again = _oauth(tmp_path, issuer)
    assert (await again.access_token()).access_token == first.access_token
    assert (await again.access_token(rejected=first.access_token)).status == "ok"
    assert auth["sent"] == ["rt-boot", "rt-1"]


async def test_a_second_process_adopts_a_refresh_it_did_not_make(tmp_path, auth_server):
    """Two lanes on one state file: when one refreshes, the other must adopt
    the result instead of spending its own (now dead) token."""
    issuer, auth = auth_server
    a, b = _oauth(tmp_path, issuer), _oauth(tmp_path, issuer)
    token_a = (await a.access_token()).access_token
    token_b = (await b.access_token()).access_token
    assert token_a == token_b
    rotated = await a.access_token(rejected=token_a)
    adopted = await b.access_token(rejected=token_b)
    assert adopted.access_token == rotated.access_token
    assert auth["sent"] == ["rt-boot", "rt-1"]


async def test_a_new_login_replaces_the_old_chain(tmp_path, auth_server):
    issuer, auth = auth_server
    await _oauth(tmp_path, issuer).access_token()
    auth["live"].add("rt-new-login")
    fresh = _oauth(tmp_path, issuer, bootstrap="rt-new-login")
    assert (await fresh.access_token()).status == "ok"
    assert auth["sent"] == ["rt-boot", "rt-new-login"]


async def test_no_refresh_when_the_rotation_could_not_be_kept(tmp_path, auth_server):
    issuer, auth = auth_server
    locked = tmp_path / "ro"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        oauth = OpenAICodexOAuth(
            bootstrap_refresh_token="rt-boot", state_file=locked / "s.json",
            issuer=issuer, timeout_seconds=5.0,
        )
        result = await oauth.access_token()
    finally:
        locked.chmod(0o700)
    assert (result.status, result.reason) == ("unavailable", "state_unwritable")
    assert auth["sent"] == []


async def test_a_refused_refresh_is_auth_and_a_429_is_rate_limited(tmp_path, auth_server):
    issuer, auth = auth_server
    dead = await _oauth(tmp_path, issuer, bootstrap="rt-spent").access_token()
    assert (dead.status, dead.reason) == ("unavailable", "auth")
    auth["behavior"] = "429"
    limited = await _oauth(tmp_path / "x", issuer).access_token()
    assert (limited.status, limited.reason) == ("unavailable", "rate_limited")


async def test_a_failed_write_keeps_spending_the_rotated_token_and_retries_the_write(
    tmp_path, auth_server,
):
    """A write failing AFTER the writability check must not leave the rotation
    only in memory, or the next refresh re-sends the dead bootstrap."""
    issuer, auth = auth_server
    state_file = tmp_path / "state" / "openai-codex-oauth.json"
    state_file.mkdir(parents=True)  # a directory: os.replace onto it fails
    oauth = OpenAICodexOAuth(
        bootstrap_refresh_token="rt-boot", state_file=state_file,
        issuer=issuer, timeout_seconds=5.0,
    )
    first = await oauth.access_token()
    assert first.status == "ok"
    second = await oauth.access_token(rejected=first.access_token)
    assert second.status == "ok"
    assert auth["sent"] == ["rt-boot", "rt-1"]
    state_file.rmdir()
    assert (await oauth.access_token()).access_token == second.access_token
    assert json.loads(state_file.read_text())["refresh_token"] == "rt-2"
    assert auth["sent"] == ["rt-boot", "rt-1"]


async def test_an_unsaved_rotation_beats_the_older_chain_in_the_file(
    tmp_path, auth_server, monkeypatch,
):
    """The file holds rt-1, the write of rt-2 failed: the next refresh spends
    rt-2 (never re-reads the dead rt-1), and the next call writes rt-3."""
    issuer, auth = auth_server
    oauth = _oauth(tmp_path, issuer)
    first = await oauth.access_token()
    real_replace = os.replace

    def failing_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)
    second = await oauth.access_token(rejected=first.access_token)
    third = await oauth.access_token(rejected=second.access_token)
    assert third.status == "ok"
    assert auth["sent"] == ["rt-boot", "rt-1", "rt-2"]
    monkeypatch.setattr(os, "replace", real_replace)
    assert (await oauth.access_token()).access_token == third.access_token
    saved = json.loads((tmp_path / "state" / "openai-codex-oauth.json").read_text())
    assert saved["refresh_token"] == "rt-3"


async def test_a_vanished_state_file_never_resends_the_bootstrap(tmp_path, auth_server):
    issuer, auth = auth_server
    oauth = _oauth(tmp_path, issuer)
    first = await oauth.access_token()
    (tmp_path / "state" / "openai-codex-oauth.json").unlink()
    assert (await oauth.access_token(rejected=first.access_token)).status == "ok"
    assert auth["sent"] == ["rt-boot", "rt-1"]


async def test_a_cancelled_caller_does_not_lose_a_rotation(tmp_path, auth_server):
    """The issuer rotates and the caller is cancelled before the answer lands;
    the refresh must still finish and be written."""
    issuer, auth = auth_server
    auth["delay"] = 0.3
    oauth = _oauth(tmp_path, issuer)
    call = asyncio.ensure_future(oauth.access_token())
    await asyncio.sleep(0.1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    await asyncio.sleep(1.0)  # the issuer answers at 0.3 s
    auth["delay"] = 0
    later = _oauth(tmp_path, issuer)
    assert (await later.access_token()).status == "ok"
    assert auth["sent"] == ["rt-boot"]


async def test_the_state_file_is_never_readable_by_others_and_is_synced(
    tmp_path, auth_server, monkeypatch,
):
    """The token is written into a file that is already
    0600, and synced before the rename."""
    issuer, _ = auth_server
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(os.fstat(fd).st_mode & 0o777)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    old_umask = os.umask(0o022)
    try:
        assert (await _oauth(tmp_path, issuer).access_token()).status == "ok"
    finally:
        os.umask(old_umask)
    assert synced == [0o600]


async def test_a_lock_failure_is_unavailable_never_a_raise(
    tmp_path, auth_server, codex_server, monkeypatch,
):
    """An flock error must not escape `generate()`."""
    import fcntl

    issuer, _ = auth_server
    base, _cx = codex_server

    def broken_flock(fd: int, op: int) -> None:
        raise OSError(37, "No locks available")

    monkeypatch.setattr(fcntl, "flock", broken_flock)
    result = await _gen(_oauth(tmp_path, issuer), base).generate(prompt="a cat")
    assert (result.status, result.reason) == ("unavailable", "state_unwritable")


# -- login ------------------------------------------------------------------


async def test_device_login_polls_until_approved_then_exchanges_with_pkce():
    seen: dict = {"polls": 0}

    async def usercode(request: web.Request) -> web.Response:
        seen["usercode"] = await request.json()
        return web.json_response(
            {"device_auth_id": "da-1", "user_code": "ABCD-1234", "interval": "0"}
        )

    async def poll(request: web.Request) -> web.Response:
        seen["polls"] += 1
        seen["poll"] = await request.json()
        if seen["polls"] < 3:
            return web.json_response(
                {"error": {"code": "deviceauth_authorization_pending"}}, status=403,
            )
        return web.json_response(
            {"authorization_code": "code-1", "code_challenge": "ch", "code_verifier": "ver-1"}
        )

    async def token(request: web.Request) -> web.Response:
        seen["exchange"] = dict(await request.post())
        return web.json_response(
            {"id_token": "x", "access_token": _jwt(1), "refresh_token": "rt-login"}
        )

    app = web.Application()
    app.router.add_post("/api/accounts/deviceauth/usercode", usercode)
    app.router.add_post("/api/accounts/deviceauth/token", poll)
    app.router.add_post("/oauth/token", token)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("/")).rstrip("/")
        tokens = await device_flow_login(issuer=base, poll_floor_seconds=0.01, timeout_seconds=5)
    finally:
        await server.close()
    assert tokens.refresh_token == "rt-login"
    assert seen["usercode"] == {"client_id": "app_EMoamEEZ73f0CkXaXp7hrann"}
    assert seen["poll"] == {"device_auth_id": "da-1", "user_code": "ABCD-1234"}
    assert seen["exchange"] == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "redirect_uri": f"{base}/deviceauth/callback",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "code_verifier": "ver-1",
    }


def test_import_refuses_codex_home_and_a_copy_of_the_operators_token(tmp_path, monkeypatch):
    """`$CODEX_HOME/auth.json` is the operator's too, and a
    copy of their file is the same login whatever its path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"tokens": {"refresh_token": "theirs"}}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    with pytest.raises(CodexLoginError, match="refusing") as by_path:
        refresh_token_from_codex_auth(home / "auth.json")
    assert "copy of" not in str(by_path.value)
    copy = tmp_path / "elsewhere" / "auth.json"
    copy.parent.mkdir()
    copy.write_text(json.dumps({"tokens": {"refresh_token": "theirs"}}))
    with pytest.raises(CodexLoginError, match="copy of") as refused:
        refresh_token_from_codex_auth(copy)
    assert "theirs" not in str(refused.value)


def test_import_refuses_the_operators_own_codex_login(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    own = tmp_path / ".codex" / "auth.json"
    own.parent.mkdir()
    own.write_text(json.dumps({"tokens": {"refresh_token": "theirs"}}))
    with pytest.raises(CodexLoginError, match="refusing"):
        refresh_token_from_codex_auth(own)
    dedicated = tmp_path / "nora-codex" / "auth.json"
    dedicated.parent.mkdir()
    dedicated.write_text(json.dumps({"tokens": {"refresh_token": "nora-own"}}))
    assert refresh_token_from_codex_auth(dedicated) == "nora-own"


# -- wiring -----------------------------------------------------------------


def test_the_factory_builds_the_codex_lane_only_with_a_token(tmp_path):
    built = build_image_generator(
        provider="openai_codex", refresh_token="rt", state_file=tmp_path / "s.json",
        model="gpt-image-2",
    )
    assert isinstance(built, OpenAICodexImageGenerator)
    assert isinstance(built, ImageGenerator)
    assert build_image_generator(
        provider="openai_codex", refresh_token="", state_file=tmp_path / "s.json",
    ) is None
    assert build_image_generator(
        provider="dall-e", refresh_token="rt", state_file=tmp_path / "s.json",
    ) is None


def test_the_codex_section_is_validated():
    from pydantic import ValidationError

    from nora.config import ImageGenConfig

    cfg = ImageGenConfig.model_validate({"provider": "openai_codex", "openai_codex": {}})
    assert cfg.openai_codex.model == "gpt-image-2"
    assert cfg.openai_codex.refresh_token_env == "OPENAI_CODEX_REFRESH_TOKEN"
    assert ImageGenConfig().provider == "xai"
    for bad in ({"quality": "high"}, {"standard_quality": "ultra"}, {"timeout_seconds": 0}):
        with pytest.raises(ValidationError):
            ImageGenConfig.model_validate({"openai_codex": bad})
