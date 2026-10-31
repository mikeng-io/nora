"""Entry smoke — the deployed entry modules against GENERATED config and
fake/absent transports, with env/mount/command taken from the RENDERED
compose service (`docker compose config`), never a handmade env that could
mask a missing DATABASE_URL or a dropped config.toml mount. Each assertion
pins an honest boundary: rendered env parses, the bridge collaborators
construct, and the process stops exactly at the one boundary a smoke cannot
fake (the signal-cli binary, Discord's real auth) — never earlier on a
config or import error, never later pretending the platform connected.
"""
from __future__ import annotations

import asyncio
import base64
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "deploy" / "signal-bridge" / "wrapper.py"
DISCORD_ENTRY = REPO_ROOT / "deploy" / "discord-bridge" / "entry.py"
CORRELATION_KEY = "ab" * 32
GUILD_ID = "999999999999999999"

# Every `:?` var docker-compose.yml interpolates — placeholder-but-valid
# shapes: the redis entrypoint refuses passwords outside [A-Za-z0-9_-],
# the correlation key is 64 hex chars.
_DOTENV = {
    "AUTH_SECRET": "test-auth-secret",
    "AUTH_URL": "http://127.0.0.1:8000",
    "DISCORD_BOT_TOKEN": "fake-token",
    "DISCORD_BRIDGE_ACCOUNT_KEY": "dc-1",
    "DISCORD_CLIENT_ID": "1234",
    "DISCORD_CLIENT_SECRET": "test-client-secret",
    "INSTAGRAM_BRIDGE_ACCOUNT_KEY": "ig-1",
    "NORA_BRIDGE_CORRELATION_KEY": CORRELATION_KEY,
    "NORA_BRIDGE_TOKEN": "test-bridge-token",
    "NORA_BRIDGE_API_TOKEN": "test-api-override",
    "NORA_REDIS_CONSUMER_PASSWORD": "consumer-pw",
    "NORA_REDIS_INSPECTOR_PASSWORD": "inspector-pw",
    "NORA_REDIS_PRODUCER_PASSWORD": "producer-pw",
    "POSTGRES_PASSWORD": "postgres-pw",
    "RUSTFS_ROOT_PASSWORD": "rustfs-pw",
    "RUSTFS_ROOT_USER": "rustfs",
    "SIGNAL_ACCOUNT": "+85290000102",
    "SIGNAL_BRIDGE_ACCOUNT_KEY": "sig-1",
    "WHATSAPP_BRIDGE_ACCOUNT_KEY": "wa-1",
}

# Keys whose ambient host value must NOT leak into the child — a host
# DATABASE_URL filling the gap is exactly the masking a rendered-env test
# exists to prevent.
_STRIP_PREFIXES = (
    "NORA_", "DATABASE_URL", "DISCORD_", "SIGNAL_", "XDG_DATA_HOME",
    "POSTGRES_", "PYTHONUNBUFFERED")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _redis_url(container) -> str:
    return (f"redis://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(6379)}/0")


def _rendered_service(name: str, env_file: Path) -> dict:
    """`docker compose config --format json` — the deployment's own rendered
    service: env after interpolation, mounts, command, depends_on."""
    result = subprocess.run(  # noqa: S603 — fixed argv, repo compose file
        ["docker", "compose", "--env-file", str(env_file),
         "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"compose config failed: {result.stderr[-500:]}"
    services = json.loads(result.stdout).get("services") or {}
    assert name in services, f"{name} absent from rendered compose"
    return services[name]


def _write_dotenv(tmp_path: Path) -> Path:
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in _DOTENV.items()) + "\n")
    return env_file


def _write_config(
    example_config_path: Path, path: Path, *,
    sidecar_endpoint: str | None = None, music_enabled: bool = False,
) -> Path:
    """The deployment config a test hands `--config` — the shipped template
    (via the `example_config_path` fixture, R-B) with the discord leg
    enabled in bridge mode, so the file is the contract `config.py` is
    written against rather than a bespoke fixture."""
    text = example_config_path.read_text()
    text = text.replace(
        "[platforms.discord]\nenabled = false",
        '[platforms.discord]\nenabled = true\nmode = "bridge"\n'
        'account_key = "dc-1"')
    if sidecar_endpoint is not None:
        text = text.replace(
            "sidecars = []",
            f'sidecars = [{{guild = "{GUILD_ID}", '
            f'endpoint = "{sidecar_endpoint}"}}]')
    if music_enabled:
        text = text.replace(
            "[music]\nenabled = false", "[music]\nenabled = true")
    path.write_text(text)
    return path


def _compose_run(
    pg_url: str, redis_url: str, tmp_path: Path, *,
    service: str, entry: Path, needs_config_mount: bool,
) -> tuple[dict, list[str]]:
    """`(env, argv)` for a host-side run of a deployed entry — the RENDERED
    compose environment verbatim except the two things a smoke is allowed
    to substitute (fixture endpoints, container paths → tmp_path). A var or
    mount the deployment forgot FAILS here, never silently defaults."""
    svc = _rendered_service(service, _write_dotenv(tmp_path))
    rendered = svc.get("environment") or {}
    for required in (
            "DATABASE_URL", "NORA_BRIDGE_ATTACH_DIR", "NORA_BRIDGE_JOURNAL",
            "NORA_REDIS_URL", "NORA_BRIDGE_API_TOKEN",
            "NORA_BRIDGE_ACCOUNT_KEY", "NORA_BRIDGE_CORRELATION_KEY"):
        assert required in rendered, (
            f"rendered {service} lost {required} — the entry reads it")
    mounts = {v.get("target") for v in svc.get("volumes") or []}
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(_STRIP_PREFIXES)}
    for key, raw in rendered.items():
        if raw is None:
            continue
        if key == "DATABASE_URL":
            env[key] = pg_url                       # fixture endpoint
        elif key == "NORA_REDIS_URL":
            env[key] = redis_url                    # fixture endpoint
        elif key == "DISCORD_BOT_TOKEN":
            env[key] = "fake-token"                 # never a platform connect
        elif raw.startswith("/"):
            env[key] = str(tmp_path / "container-root" / raw.lstrip("/"))
        else:
            env[key] = raw
    env["NORA_BRIDGE_PORT"] = str(_free_port())     # fixture endpoint
    # The projection volume target, mapped to a host path like the rest.
    env.setdefault(
        "NORA_BRIDGE_PROJECTION_DIR", str(tmp_path / "projection"))
    env["PYTHONUNBUFFERED"] = "1"
    argv = [sys.executable, str(entry)]
    if needs_config_mount:
        assert "/app/config.toml" in mounts, (
            f"{service} lost its read-only config.toml mount — the entry "
            "reads [platforms.discord]/[speech.tts]/[music] from it")
        command = svc.get("command") or []
        assert command, f"{service} lost its --config command"
        argv += [
            str(tmp_path / "config.toml") if a == "/app/config.toml" else a
            for a in command]
    return env, argv


def _run(argv: list[str], env: dict, *, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — fixed argv, generated env only
        argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        timeout=timeout)


def test_signal_wrapper_refuses_without_account_key(
        pg_url, _redis_container, tmp_path):
    env, argv = _compose_run(
        pg_url, _redis_url(_redis_container), tmp_path,
        service="signal-bridge", entry=WRAPPER, needs_config_mount=False)
    del env["NORA_BRIDGE_ACCOUNT_KEY"]
    result = _run(argv, env)
    assert result.returncode != 0
    assert "ACCOUNT_KEY" in result.stderr


def test_signal_wrapper_reaches_the_cli_boundary(
        pg_url, _redis_container, tmp_path):
    """Full rendered env: journal preflight + replay spawn + bridge
    construction all succeed; the process stops at `/usr/local/bin/signal-cli`
    — the image's one real prerequisite — with the honest spawn error, never a
    config/import failure first."""
    env, argv = _compose_run(
        pg_url, _redis_url(_redis_container), tmp_path,
        service="signal-bridge", entry=WRAPPER, needs_config_mount=False)
    result = _run(argv, env)
    assert result.returncode == 1
    assert "signal-cli" in result.stderr
    assert "ACCOUNT_KEY" not in result.stderr
    # The preflight probed the journal before ever touching the binary.
    journal = Path(env["NORA_BRIDGE_JOURNAL"])
    assert journal.parent.exists()


def test_discord_entry_refuses_without_account_key(
        pg_url, _redis_container, tmp_path, example_config_path):
    _write_config(example_config_path, tmp_path / "config.toml")
    env, argv = _compose_run(
        pg_url, _redis_url(_redis_container), tmp_path,
        service="discord-bridge", entry=DISCORD_ENTRY, needs_config_mount=True)
    del env["NORA_BRIDGE_ACCOUNT_KEY"]
    result = _run(argv, env)
    assert result.returncode != 0
    assert "ACCOUNT_KEY" in result.stderr


def test_discord_entry_serves_v1_before_connecting(
        pg_url, _redis_container, tmp_path, example_config_path):
    """Rendered env + generated config + fake token: the /v1 site binds and
    answers (401 to an unauthenticated probe — auth middleware is live)
    BEFORE `bridge.start()` finishes its Discord connect attempt. A real
    `discord.Client` login with a fake token fails on the platform's auth;
    the supervisor then retries with the site up — the poll wins because the
    site serves first."""
    _write_config(example_config_path, tmp_path / "config.toml")
    env, argv = _compose_run(
        pg_url, _redis_url(_redis_container), tmp_path,
        service="discord-bridge", entry=DISCORD_ENTRY, needs_config_mount=True)
    port = int(env["NORA_BRIDGE_PORT"])
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, rendered env only
        argv, cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        served = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=2)
                conn.request("GET", "/v1/health")
                status = conn.getresponse().status
                conn.close()
                assert status == 401  # serving AND authing — never open
                served = True
                break
            except OSError:
                time.sleep(0.1)
        assert served, (
            "entry never served /v1 — config or import failed first: "
            f"rc={proc.poll()}")
        assert env["NORA_BRIDGE_API_TOKEN"] != _DOTENV["NORA_BRIDGE_TOKEN"]
        for bearer, expected in ((env["NORA_BRIDGE_API_TOKEN"], 200),
                                 (_DOTENV["NORA_BRIDGE_TOKEN"], 401)):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/metrics", headers={
                "Authorization": f"Bearer {bearer}"})
            response = conn.getresponse()
            body = response.read()
            assert response.status == expected
            if expected == 200:
                assert b"# HELP" in body
            conn.close()
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
    # It either still lived (connect retry loop) or died on real auth — both
    # are past the config boundary this smoke owns.
    err = proc.stderr.read() if proc.stderr else ""
    assert "ACCOUNT_KEY" not in err
    assert "ModuleNotFoundError" not in err


def _controller_app(record: list) -> web.Application:
    """A fake audio-controller — the real `/voice/*` control contract: join
    and stream answer the `state` snapshot, status answers the flat
    snapshot. Behaves like the real server, wrong-call shapes included."""
    app = web.Application()

    def _state() -> dict:
        return {
            "voice": "connected", "generation": "gen-1",
            "channel_id": "cortez-1", "playback_id": "pb-1",
            "player": {"status": "idle", "position_ms": 0, "current": None},
            "speech_queue": 0, "crossfade_seconds": 0,
        }

    async def join(request: web.Request) -> web.Response:
        record.append(("join", await request.json()))
        return web.json_response(
            {"ok": True, "request_id": "r", "state": _state()})

    async def status(request: web.Request) -> web.Response:
        record.append(("status", dict(request.query)))
        return web.json_response(_state())

    async def stream(request: web.Request) -> web.Response:
        record.append(("stream", await request.json()))
        return web.json_response(
            {"ok": True, "request_id": "r", "state": _state()})

    app.router.add_post("/voice/join", join)
    app.router.add_get("/voice/status", status)
    app.router.add_post("/stream", stream)
    return app


async def test_discord_entry_owns_the_voice_graph_from_the_deployed_config(
        pg_url, _redis_container, tmp_path, aiohttp_client,
        example_config_path):
    """Rendered compose env + generated config (discord bridge mode, music
    enabled, a `[speech.tts] sidecars` entry pointing at the fake
    controller): the BOOTED bridge owns the whole voice path the app
    relinquished — `/v1/voice/{g}/join` reaches the controller's
    `/voice/join`, `/v1/voice/{g}/speech` reaches `/stream` on the same
    adopted generation, `/v1/voice/event` dedupes and its sidecar allow-list
    IS the config's — all with no nora process running."""
    record: list = []
    controller = await aiohttp_client(_controller_app(record))
    _write_config(
        example_config_path, tmp_path / "config.toml",
        sidecar_endpoint=str(controller.make_url("")).rstrip("/"),
        music_enabled=True)
    env, argv = _compose_run(
        pg_url, _redis_url(_redis_container), tmp_path,
        service="discord-bridge", entry=DISCORD_ENTRY, needs_config_mount=True)
    port = int(env["NORA_BRIDGE_PORT"])
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {env['NORA_BRIDGE_API_TOKEN']}"}
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=REPO_ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        async with aiohttp.ClientSession() as http:
            while True:
                assert proc.returncode is None, (
                    "entry died before serving /v1")
                try:
                    async with http.get(
                            f"{base}/v1/health", headers=headers) as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    pass
                assert time.monotonic() < deadline, (
                    "entry never served an authenticated /v1/health")
                await asyncio.sleep(0.2)

            # join → the controller's /voice/join, on the config's allow-list.
            async with http.post(
                    f"{base}/v1/voice/{GUILD_ID}/join",
                    json={"channel_id": "cortez-1"}, headers=headers) as r:
                assert r.status == 200, await r.text()
            joins = [p for kind, p in record if kind == "join"]
            assert joins and joins[0]["channel_id"] == "cortez-1"

            async with http.get(
                    f"{base}/v1/voice/{GUILD_ID}", headers=headers) as r:
                state = await r.json()
            assert state["connected"] and state["generation"] == "gen-1"

            # speech → the SAME session's generation on the controller.
            speech = b"\x00" * 64
            async with http.post(
                    f"{base}/v1/voice/{GUILD_ID}/speech",
                    data=speech, headers=headers) as r:
                assert r.status == 204, await r.text()
            streams = [p for kind, p in record if kind == "stream"]
            assert streams and streams[0]["generation"] == "gen-1"
            assert base64.b64decode(streams[0]["audio_b64"]) == speech

            # The consumer route — dedupe by event_id, sidecars as allow-list.
            event = {
                "type": "speech-finished", "event_id": "evt-1",
                "guild_id": GUILD_ID, "event_seq": 1}
            async with http.post(
                    f"{base}/v1/voice/event", json=event,
                    headers=headers) as r:
                assert r.status == 200
                assert (await r.json())["status"] == "ok"
            async with http.post(
                    f"{base}/v1/voice/event", json=event,
                    headers=headers) as r:
                assert (await r.json())["status"] == "duplicate"
            async with http.post(
                    f"{base}/v1/voice/event",
                    json={**event, "event_id": "evt-2",
                          "guild_id": "not-configured"},
                    headers=headers) as r:
                assert r.status == 400  # no controller configured → refused

            # Attachment custody — the compose env names the dir and the
            # entry created it: a PUT/GET roundtrip lands bytes durably on
            # the (host-mapped) volume path.
            blob = b"\x01\x02\x03" * 16
            async with http.put(
                    f"{base}/v1/attachments/att-1", data=blob,
                    headers=headers) as r:
                assert r.status == 200, await r.text()
            assert (
                Path(env["NORA_BRIDGE_ATTACH_DIR"]) / "att-1"
            ).read_bytes() == blob
            async with http.get(
                    f"{base}/v1/attachments/att-1", headers=headers) as r:
                assert r.status == 200
                assert await r.read() == blob
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()
