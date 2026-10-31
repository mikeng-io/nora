"""The publisher as a real process: alive and updating with no app anywhere.

The design's whole point is independence — the projection keeps refreshing
while the app process is stopped (there IS no app in these tests; the
publisher reads the same files the app would, through the same loaders). All
fixtures are generated synthetics; nothing here touches a real deployment.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from tests.builders import copy_example_config

REPO_ROOT = Path(__file__).resolve().parents[2]

ROOM_A = "Q2hhdEdyb3VwSWRFeGFtcGxlQmFzZTY0PT0="
ROOM_B = "Q2hhdEdyb3VwSWRFeGFtcGxlQmFzZTY0Qj09"
ACCOUNT_KEY = "opaque-leg-subprocess"

_IDENTITY = """\
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "+85212345678"
"""

_POLICY = f"""\
default = "deny"

[[conversations]]
platform = "signal"
id = "{ROOM_A}"
label = "Family"
persons = ["miles"]
"""

_EXTRA_ROOM = f"""\
[[conversations]]
platform = "signal"
id = "{ROOM_B}"
label = "Back room"
persons = ["miles"]
"""

GUILD = "700000000000000001"
GCHAN_A = "700000000000000101"
GCHAN_B = "700000000000000102"
MILES_DISCORD = "100000000000000301"
BOT_PRIMARY = "a1000099-0000-4000-8000-000000000099"

_IDENTITY_DISCORD = f"""\
[persons.miles]
name = "Miles"

[[persons.miles.discord]]
user_id = "{MILES_DISCORD}"
"""

_POLICY_GUILD = f"""\
default = "deny"

[[conversations]]
platform = "discord"
guild = "{GUILD}"
label = "The Server"
persons = ["miles"]
"""


class _BridgeHandler(http.server.BaseHTTPRequestHandler):
    """The discord control worker's wire shape: POST /v1/reads -> {available,
    value:{channels, threads}}; GET /v1/self -> {primary, aliases}. Answers
    come from `server.state` per request so tests mutate upstream truth."""

    def _send(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        body = json.loads(
            self.rfile.read(int(self.headers["Content-Length"])),
        )
        if self.path != "/v1/reads" or body.get("op") != "channels":
            self._send({"error": "unknown"}, status=404)
            return
        gid = body["params"]["guild_id"]
        if not self.server.state.get("available", True):
            self._send({"available": False})
            return
        self._send({"available": True, "value": {
            "channels": dict(self.server.state["channels"].get(gid, {})),
            "threads": dict(self.server.state.get("threads", {})),
        }})

    def do_GET(self) -> None:
        if self.path != "/v1/self":
            self._send({"error": "unknown"}, status=404)
            return
        if not self.server.state.get("self_available", True):
            self._send({"error": "unavailable"}, status=503)
            return
        self._send({
            "primary": self.server.state.get("primary"),
            "aliases": list(self.server.state.get("aliases", [])),
        })

    def log_message(self, *_args) -> None:
        pass


def _bridge_http(state: dict) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BridgeHandler)
    server.state = state
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sources(root: Path, policy: str = _POLICY, identity: str = _IDENTITY) -> None:
    (root / "identity.toml").write_text(identity)
    (root / "policy.toml").write_text(policy)
    # Merged schema only: the env leg must equal platforms.signal.account_key.
    from nora import config as _config
    cfg = root / "config.toml"
    if "account_key" in _config.SignalPlatformConfig.model_fields:
        text = cfg.read_text()
        if 'account_key = "signal-acct-1"' in text:
            cfg.write_text(text.replace(
                'account_key = "signal-acct-1"',
                f'account_key = "{ACCOUNT_KEY}"', 1,
            ))
        else:
            assert 'account = ""' in text
            cfg.write_text(text.replace(
                'account = ""',
                f'account_key = "{ACCOUNT_KEY}"\naccount = ""', 1,
            ))


def _enable_account(root: Path) -> None:
    """Signal's configured `account` is the identity floor — without it (and
    with no /v1/self wired) the publisher correctly reports self-stale."""
    cfg = root / "config.toml"
    text = cfg.read_text()
    assert 'account = ""' in text
    cfg.write_text(text.replace(
        'account = ""', 'account = "+85287654321"', 1,
    ))


def _enable_discord(root: Path) -> None:
    cfg = root / "config.toml"
    text = cfg.read_text()
    assert "[platforms.discord]\nenabled = false" in text
    cfg.write_text(text.replace(
        "[platforms.discord]\nenabled = false",
        "[platforms.discord]\nenabled = true", 1,
    ))


def _launch(
    tmp_path: Path, *, platform: str = "signal", health_port: int = 0,
    extra: list[str] | None = None, account_key: str = ACCOUNT_KEY,
) -> tuple[subprocess.Popen, Path]:
    out_dir = tmp_path / "bridge-projection"
    out_dir.mkdir()
    env = {
        **os.environ,
        "NORA_BRIDGE_ACCOUNT_KEY": account_key,
        "PYTHONUNBUFFERED": "1",
    }
    argv = [
        sys.executable,
        str(REPO_ROOT / "deploy" / "bridge-projection" / "entry.py"),
        "--platform", platform,
        "--config", str(tmp_path / "config.toml"),
        "--projection-dir", str(out_dir),
        *(extra or []),
    ]
    if health_port:
        argv += ["--health-port", str(health_port)]
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, synthetic local files
        argv, cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc, out_dir / f"{platform}.json"


def _diagnosis(proc: subprocess.Popen) -> str:
    """stderr of the process — only read once it is dead, so a live wait never
    blocks on EOF that will not come."""
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    err = proc.stderr.read() if proc.stderr else ""
    return f"rc={proc.returncode} err={err[-1500:]}"


def _wait_for(
    predicate, *, timeout: float = 20.0, what: str = "condition",
    proc: subprocess.Popen | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise AssertionError(
                f"process died while waiting for {what}: {_diagnosis(proc)}")
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(
        f"timed out waiting for {what}"
        + (f": {_diagnosis(proc)}" if proc else "")
    )


def _wait_live(port: int, *, proc: subprocess.Popen) -> None:
    """Bounded real startup readiness: `/live` can only answer once the
    subprocess has imported, built its config and bound the health site — the
    readiness signal is the process itself, not a guessed deadline. Once it
    answers, the first projection lands inside one publisher tick, so the
    artifact wait that follows stays on the short bound."""
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"publisher exited before /live: {_diagnosis(proc)}")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/live")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            if resp.status == 200:
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for /live: {_diagnosis(proc)}")


def _projection(target: Path) -> dict | None:
    try:
        return json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)


def test_publisher_lives_and_updates_with_no_app(tmp_path: Path):
    copy_example_config(tmp_path)
    _sources(tmp_path)
    _enable_account(tmp_path)
    health_port = _free_port()
    proc, target = _launch(tmp_path, health_port=health_port)
    try:
        _wait_live(health_port, proc=proc)
        _wait_for(
            lambda: (p := _projection(target)) is not None and p["enabled"],
            what="first projection", proc=proc,
        )
        first = _projection(target)
        assert first["account_key"] == ACCOUNT_KEY
        assert first["rooms"] == [ROOM_A]
        rev = first["revision"]

        conn = http.client.HTTPConnection("127.0.0.1", health_port, timeout=5)
        conn.request("GET", "/health")
        body = conn.getresponse().read()
        conn.close()
        assert json.loads(body)["ok"] is True

        # A policy edit lands on the running process — the reload cadence,
        # no restart, no app anywhere.
        (tmp_path / "policy.toml").write_text(_POLICY + _EXTRA_ROOM)
        _wait_for(
            lambda: (p := _projection(target)) is not None
            and ROOM_B in p["rooms"],
            what="reloaded projection", proc=proc,
        )
        updated = _projection(target)
        assert ROOM_B in updated["rooms"]
        assert updated["revision"] != rev

        assert proc.poll() is None      # alive throughout: the app never ran
    finally:
        _stop(proc)


def test_entry_refuses_without_account_key(tmp_path: Path):
    """No opaque leg, no artifact, no process — the contract marks
    NORA_BRIDGE_ACCOUNT_KEY REQUIRED per bridge."""
    copy_example_config(tmp_path)
    _sources(tmp_path)
    out_dir = tmp_path / "bridge-projection"
    out_dir.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "NORA_BRIDGE_ACCOUNT_KEY"}
    result = subprocess.run(  # noqa: S603 — fixed argv, synthetic local files
        [
            sys.executable,
            str(REPO_ROOT / "deploy" / "bridge-projection" / "entry.py"),
            "--platform", "signal",
            "--config", str(tmp_path / "config.toml"),
            "--projection-dir", str(out_dir),
        ],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "ACCOUNT_KEY" in result.stderr
    assert not list(out_dir.iterdir())   # nothing published, nothing guessed


def test_boot_invalid_denies_then_recovers(tmp_path: Path):
    """First-invalid-state is an explicit deny artifact; a file fixed in place
    is picked up on a later tick without a restart."""
    copy_example_config(tmp_path)
    _sources(tmp_path, policy="this is [ not valid toml")
    proc, target = _launch(tmp_path)
    try:
        _wait_for(
            lambda: _projection(target) is not None,
            what="deny artifact", proc=proc,
        )
        deny = _projection(target)
        assert deny["enabled"] is False
        assert deny["rooms"] == [] and deny["dm_allowed_senders"] == []
        assert deny["account_key"] == ACCOUNT_KEY

        (tmp_path / "policy.toml").write_text(_POLICY)
        _wait_for(
            lambda: (p := _projection(target)) is not None and p["enabled"],
            what="recovered projection", proc=proc,
        )
        assert _projection(target)["rooms"] == [ROOM_A]
        assert proc.poll() is None
    finally:
        _stop(proc)


def test_fleet_publishes_all_platforms_with_per_platform_legs(tmp_path: Path):
    """`--all` is the deployment's shape: one process, every platform's file.
    Signal's leg comes from SIGNAL_BRIDGE_ACCOUNT_KEY (no shared env set —
    per-platform resolution only); the disabled platforms need no credential
    at all and still publish their enabled:false deny artifacts."""
    copy_example_config(tmp_path)
    _sources(tmp_path)
    _enable_account(tmp_path)
    out_dir = tmp_path / "bridge-projection"
    out_dir.mkdir()
    env = {
        **os.environ,
        "SIGNAL_BRIDGE_ACCOUNT_KEY": ACCOUNT_KEY,
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("NORA_BRIDGE_ACCOUNT_KEY", None)  # per-platform legs only
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, synthetic local files
        [
            sys.executable,
            str(REPO_ROOT / "deploy" / "bridge-projection" / "entry.py"),
            "--all",
            "--config", str(tmp_path / "config.toml"),
            "--projection-dir", str(out_dir),
        ],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        files = {p: out_dir / f"{p}.json"
                 for p in ("signal", "discord", "whatsapp", "instagram")}
        _wait_for(
            lambda: all(_projection(t) is not None for t in files.values()),
            what="all four platform artifacts", proc=proc,
        )
        signal_proj = _projection(files["signal"])
        assert signal_proj["enabled"] is True
        assert signal_proj["rooms"] == [ROOM_A]
        assert signal_proj["account_key"] == ACCOUNT_KEY
        for platform in ("discord", "whatsapp", "instagram"):
            deny = _projection(files[platform])
            assert deny["enabled"] is False, platform
            # The configured leg rides the artifact for provenance; no env
            # credential was needed (or present) for a disabled platform.
            assert deny["account_key"], platform
        assert proc.poll() is None      # the fleet stays up with no app
    finally:
        _stop(proc)


def test_discord_guild_scope_updates_while_app_absent(tmp_path: Path):
    """A `guild =` entry admits exactly what the live bridge enumerates: the
    channel appears on refresh, disappears when the directory reports
    unavailable — the whole flow on a real subprocess, no app anywhere."""
    copy_example_config(tmp_path)
    _enable_discord(tmp_path)
    _sources(tmp_path, policy=_POLICY_GUILD, identity=_IDENTITY_DISCORD)
    state = {
        "available": True,
        "channels": {GUILD: {GCHAN_A: "general"}},
        "threads": {},
        "primary": BOT_PRIMARY,
        "aliases": [],
    }
    server, base = _bridge_http(state)
    room_a = f"{GUILD}/{GCHAN_A}"
    try:
        # "default": the merged schema's platforms.discord.account_key
        # default — env must equal it whenever the field exists.
        proc, target = _launch(
            tmp_path, platform="discord", account_key="default", extra=[
                "--reads-url", f"{base}/v1/reads",
                "--self-url", f"{base}/v1/self",
            ],
        )
        try:
            _wait_for(
                lambda: (p := _projection(target)) is not None
                and room_a in p["rooms"],
                what="guild-expanded projection", proc=proc,
            )
            proj = _projection(target)
            assert proj["room_receipt_senders"][room_a] == [MILES_DISCORD]
            assert BOT_PRIMARY in proj["self_aliases"]

            # Upstream discovery: a new channel gains admission on the next
            # tick — nothing of the app was ever running.
            state["channels"][GUILD][GCHAN_B] = "dev"
            _wait_for(
                lambda: (p := _projection(target)) is not None
                and f"{GUILD}/{GCHAN_B}" in p["rooms"],
                what="discovered channel", proc=proc,
            )

            # Directory unavailable → the guild scope denies, honestly.
            state["available"] = False
            _wait_for(
                lambda: (p := _projection(target)) is not None
                and p["rooms"] == [],
                what="denied guild scope", proc=proc,
            )
            assert _projection(target)["room_receipt_senders"] == {}

            assert proc.poll() is None     # still alive through it all
        finally:
            _stop(proc)
    finally:
        server.shutdown()
        server.server_close()
