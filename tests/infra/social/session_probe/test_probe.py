"""probe.py end to end with fakes: no Chrome DB, docker, opencli, network or sleep."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nora.infra.social.session_probe import probe as probe_mod
from nora.infra.social.session_probe.alerts import AlertBridgeSink, AlertMessage
from nora.infra.social.session_probe.cookies import ChromeCookie, CookieError, fingerprint
from nora.infra.social.session_probe.probe import Probe, ProbeConfig
from nora.infra.social.session_probe.runner import RunResult

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
FAR = NOW + timedelta(days=300)
CHROME_SID = "CHROME-SID-VALUE"
BRIDGE_SID = "OLD-BRIDGE-SID"


class FakeWorld:
    def __init__(self) -> None:
        self.health = '{"session":"authenticated"}'
        self.health_rc = 0
        self.token = "tok-test"  # noqa: S105 - a fake, never a real credential
        self.stop_rc = 0
        self.start_rc = 0
        self.bridge_dir: Path | None = None
        #: the bridge's in-memory jar; `docker stop` flushes it over cookies.json
        #: (the real SIGTERM handler: gw.Shutdown() -> flushCookies()).
        self.bridge_memory: dict[str, str] = {"sessionid": BRIDGE_SID, "mid": "keep-me"}
        self.running = True
        #: a `docker stop` that returns non-zero (e.g. timeout, rc 124) but is
        #: still completed by the daemon.
        self.stop_lands_despite_rc = False
        self.crash_after_stop = False
        self.crash_on_start = False
        self.whoami: dict[str, RunResult] = {}
        self.calls: list[list[str]] = []
        self.sleeps: list[float] = []
        self.sent: list[AlertMessage] = []
        self.now = NOW
        self.deliver = True

    def run(self, argv: Sequence[str], timeout: float, stdin: str | None) -> RunResult:
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "exec"] and len(argv) >= 6 and argv[3:5] == ["sh", "-c"]:
            script = argv[5]
            if (
                'printf "Authorization: Bearer %s\\n" "$NORA_BRIDGE_TOKEN"' not in script
                or "-H @-" not in script
            ):
                return RunResult(1, "", "curl: (22) 401 Unauthorized")
            if not self.running:  # exec into a stopped container fails
                return RunResult(1, "", "container is not running")
            return RunResult(self.health_rc, self.health)
        if argv[:2] == ["docker", "stop"]:
            if self.stop_rc == 0 or self.stop_lands_despite_rc:
                self.running = False
                if self.bridge_dir is not None:
                    (self.bridge_dir / "cookies.json").write_text(json.dumps(self.bridge_memory))
                if self.crash_after_stop:
                    raise RuntimeError("probe killed after docker stop")
            return RunResult(self.stop_rc)
        if argv[:2] == ["docker", "start"]:
            if self.crash_on_start:
                raise RuntimeError("probe killed at docker start")
            if self.start_rc == 0:
                self.running = True
            return RunResult(self.start_rc)
        if argv[1:3] == ["--profile", "nora"] and len(argv) >= 5 and argv[4] == "whoami":
            return self.whoami.get(argv[3], RunResult(0, "{}"))
        raise AssertionError(f"unexpected process: {argv}")

    def send(self, message: AlertMessage) -> bool:
        if not self.deliver:
            return False
        self.sent.append(message)
        return True

    def restarts(self) -> int:
        """Completed stop/start cycles (each sync is one)."""
        return sum(1 for c in self.calls if c[:2] == ["docker", "start"])

    def whoami_sites(self) -> list[str]:
        return [c[3] for c in self.calls
                if c[1:3] == ["--profile", "nora"] and len(c) > 4 and c[4] == "whoami"]


def jar(*, ig: str | None = CHROME_SID, x: bool = True, li: bool = True) -> list[ChromeCookie]:
    created = NOW - timedelta(days=5)
    out: list[ChromeCookie] = []
    if ig:
        out += [ChromeCookie(".instagram.com", "sessionid", ig, created, FAR),
                ChromeCookie(".instagram.com", "csrftoken", "csrf-new", created, FAR)]
    if x:
        out.append(ChromeCookie(".x.com", "auth_token", "xt", created, FAR))
    if li:
        out.append(ChromeCookie(".linkedin.com", "li_at", "lt", created, FAR))
    return out


@pytest.fixture
def world() -> FakeWorld:
    return FakeWorld()


@pytest.fixture
def bridge_dir(tmp_path: Path) -> Path:
    d = tmp_path / "instagram"
    d.mkdir()
    (d / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID, "mid": "keep-me"}))
    return d


def make(world: FakeWorld, bridge_dir: Path, tmp_path: Path, chrome=None, **cfg):  # type: ignore[no-untyped-def]
    config = ProbeConfig(
        chrome_db=tmp_path / "unused", bridge_dir=bridge_dir,
        state_file=tmp_path / "state" / "probe.jsonl", opencli_bin="opencli", **cfg,
    )

    def read() -> list[ChromeCookie]:
        if isinstance(chrome, Exception):
            raise chrome
        return chrome if chrome is not None else jar()

    world.bridge_dir = bridge_dir
    return Probe(config, read_chrome=read, run=world.run, sink=world, now=lambda: world.now,
                 sleep=world.sleeps.append, jitter=lambda hi: 1.5), config


def lines(config: ProbeConfig) -> list[dict]:
    return [json.loads(x) for x in config.state_file.read_text().splitlines()]


def test_healthy_run_is_pass_and_appends_one_line_without_secrets(world, bridge_dir, tmp_path):
    world.health = '{"session":"authenticated"}'
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, _ = p.run_once()
    assert verdict == "PASS"
    assert world.restarts() == 0 and world.sent == []
    recs = lines(cfg)
    assert len(recs) == 1
    raw = cfg.state_file.read_text()
    for secret in (CHROME_SID, BRIDGE_SID, "csrf-new"):
        assert secret not in raw  # values never recorded
    assert recs[0]["instagram_sessionid"] == {
        "chrome": fingerprint(CHROME_SID), "bridge": fingerprint(BRIDGE_SID)}
    assert stat.S_IMODE(cfg.state_file.stat().st_mode) == 0o600


def test_the_health_probe_authenticates_from_the_container_without_the_token_in_argv(
    world, bridge_dir, tmp_path,
):
    """/health is protected (a bare curl is a 401, which would read as
    `unreachable`). The token reaches curl on stdin, so it is neither passed to
    docker, recorded, nor visible in a process listing inside the container."""
    world.health = '{"session":"authenticated"}'
    world.token = "tok-never-in-argv"  # noqa: S105 - a fake, never a real credential
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, _ = p.run_once()
    assert verdict == "PASS"
    health_calls = [c for c in world.calls if c[:2] == ["docker", "exec"]]
    assert health_calls, world.calls
    for argv in world.calls:
        assert world.token not in " ".join(argv)
    script = health_calls[0][5]
    assert 'printf "Authorization: Bearer %s\\n" "$NORA_BRIDGE_TOKEN"' in script
    assert "-H @-" in script
    assert world.token not in cfg.state_file.read_text()


def test_the_bridge_health_script_keeps_the_token_out_of_curls_actual_argv(
    world, bridge_dir, tmp_path,
):
    """The outer docker argv is not the only process list that matters: curl
    runs inside the bridge container. Run the probe's real `sh -c` script with a
    synthetic token and a curl that records its argv, and assert the expansion
    never appears there while the header does arrive on stdin."""
    world.health = '{"session":"authenticated"}'
    p, _ = make(world, bridge_dir, tmp_path)
    p._bridge_health()  # type: ignore[attr-defined]
    script = next(
        c[5] for c in world.calls
        if c[:2] == ["docker", "exec"] and c[3:5] == ["sh", "-c"]
    )

    stub = tmp_path / "bin"
    stub.mkdir()
    curl = stub / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/curl-argv"\n'
        f'cat > "{tmp_path}/curl-stdin"\n'
        "printf '{\"session\":\"authenticated\"}'\n",
    )
    curl.chmod(0o755)
    token = "synthetic-bridge-token-never-argv"  # noqa: S105 - a fake
    env = dict(os.environ, NORA_BRIDGE_TOKEN=token, PATH=f"{stub}:{os.environ['PATH']}")
    done = subprocess.run(  # noqa: S603 - the script is probe.py's own sh -c text
        ["sh", "-c", script], env=env, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    argv = (tmp_path / "curl-argv").read_text().splitlines()
    assert "-H" in argv and "@-" in argv
    assert not any(token in arg for arg in argv)
    assert (tmp_path / "curl-stdin").read_text() == f"Authorization: Bearer {token}\n"


def test_logged_out_bridge_with_newer_chrome_cookie_is_synced(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "FAIL"
    assert world.restarts() == 1
    merged = json.loads((bridge_dir / "cookies.json").read_text())
    assert merged["sessionid"] == CHROME_SID          # Chrome wins
    assert merged["csrftoken"] == "csrf-new"          # Chrome's extra cookies arrive
    assert merged["mid"] == "keep-me"                 # bridge-only cookies survive
    assert stat.S_IMODE((bridge_dir / "cookies.json").stat().st_mode) == 0o600
    backups = list(bridge_dir.glob("cookies.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["sessionid"] == BRIDGE_SID
    assert not (bridge_dir / "cookies.json.tmp").exists()
    assert rec["sync"] == "ok" and rec["carry"]["attempts"] == 1
    assert rec["newer_sessionid"] in {"chrome", "bridge"}


def test_sync_is_capped_after_three_failed_attempts_and_alerts(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    # Chrome keeps a different cookie than the bridge: emulate the bridge
    # rejecting the synced cookie by restoring its old jar between runs.
    for i in range(3):
        (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
        world.now = NOW + timedelta(minutes=10 * (i + 1))
        p.run_once()
    assert world.restarts() == 3
    (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
    world.now = NOW + timedelta(minutes=50)
    verdict, rec = p.run_once()
    assert world.restarts() == 3                       # no fourth restart
    assert verdict == "FAIL"
    assert {"action": "capped", "attempts": 3} in rec["decisions"]
    assert any(m.reason == "sync_capped" for m in world.sent)


def test_bridge_recovery_resets_the_attempt_counter(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    p, cfg = make(world, bridge_dir, tmp_path)
    p.run_once()
    assert lines(cfg)[-1]["carry"]["attempts"] == 1
    world.health = '{"session":"authenticated"}'
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert lines(cfg)[-1]["carry"]["attempts"] == 0


def test_same_cookie_logged_out_alerts_and_does_not_restart(world, bridge_dir, tmp_path):
    (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": CHROME_SID}))
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, _ = p.run_once()
    assert verdict == "FAIL"
    assert world.restarts() == 0
    assert [m.reason for m in world.sent] == ["bridge_logged_out_same_cookie"]


def test_unreadable_chrome_db_is_error_never_pass_and_touches_nothing(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path, chrome=CookieError("cookie db not found"))
    verdict, rec = p.run_once()
    assert verdict == "ERROR"
    assert world.restarts() == 0
    assert json.loads((bridge_dir / "cookies.json").read_text())["sessionid"] == BRIDGE_SID
    assert rec["chrome_error"] == "cookie db not found"
    assert world.whoami_sites() == []


@pytest.mark.parametrize("stdout,rc", [("", 1), ("not json", 0), ('{"session":"weird"}', 0),
                                       ('{"nosession":1}', 0)])
def test_unreachable_or_garbled_bridge_health_is_error(world, bridge_dir, tmp_path, stdout, rc):
    world.health, world.health_rc = stdout, rc
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR" and rec["bridge_session"] == "unreachable"
    assert world.restarts() == 0


def test_failed_start_is_recorded_as_error(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    world.start_rc = 1
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR"
    assert rec["sync"] == "start_failed:rc=1"


def test_failed_stop_writes_nothing_and_is_error(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    world.stop_rc = 125
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR"
    assert rec["sync"] == "stop_failed:rc=125"
    assert json.loads((bridge_dir / "cookies.json").read_text())["sessionid"] == BRIDGE_SID
    assert list(bridge_dir.glob("cookies.json.bak-*")) == []
    assert not any(c[:2] == ["docker", "start"] for c in world.calls)


def test_sync_writes_after_the_bridge_stops_so_its_flush_cannot_undo_it(
    world, bridge_dir, tmp_path
):
    """The bridge flushes its old in-memory jar over cookies.json when it stops.
    Only stop -> write -> start ends with Chrome's jar on disk."""
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    p.run_once()
    verbs = [c[1] for c in world.calls if c[0] == "docker" and c[1] in {"stop", "start", "restart"}]
    assert verbs == ["stop", "start"]
    assert json.loads((bridge_dir / "cookies.json").read_text())["sessionid"] == CHROME_SID


def test_the_fake_bridge_flush_really_undoes_a_write_made_before_stop(world, bridge_dir):
    """Guards the model itself: the OLD order (write, then stop) ends with the old jar."""
    (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": CHROME_SID}))
    world.bridge_dir = bridge_dir
    world.run(["docker", "stop", "instagram-bridge"], 60.0, None)
    assert json.loads((bridge_dir / "cookies.json").read_text())["sessionid"] == BRIDGE_SID


def test_unreadable_run_between_syncs_does_not_reset_the_cap(world, bridge_dir, tmp_path):
    """sync, sync, sync, unreadable, sync: the 5th run must be CAPPED, not attempt 1."""
    world.health = '{"session":"logged_out"}'
    chrome: list = [jar()]
    cfg = ProbeConfig(chrome_db=tmp_path / "unused", bridge_dir=bridge_dir,
                      state_file=tmp_path / "state" / "probe.jsonl", opencli_bin="opencli")

    def read() -> list[ChromeCookie]:
        got = chrome[0]
        if isinstance(got, Exception):
            raise got
        return got

    p = Probe(cfg, read_chrome=read, run=world.run, sink=world, now=lambda: world.now,
              sleep=world.sleeps.append, jitter=lambda hi: 1.5)
    world.bridge_dir = bridge_dir
    for i in range(5):
        chrome[0] = CookieError("db locked") if i == 3 else jar()
        # the bridge keeps rejecting Chrome's cookie: restore its old jar each run
        (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
        world.now = NOW + timedelta(minutes=10 * (i + 1))
        p.run_once()
    recs = lines(cfg)
    assert [r["verdict"] for r in recs][3] == "ERROR"
    assert world.restarts() == 3                        # the 5th run did NOT sync
    assert {"action": "capped", "attempts": 3} in recs[4]["decisions"]
    assert recs[4]["carry"]["chrome_hash"] == fingerprint(CHROME_SID)
    assert recs[3]["carry"]["chrome_hash"] == fingerprint(CHROME_SID)  # not erased


def test_missing_bridge_jar_counts_as_different_and_is_recreated(world, bridge_dir, tmp_path):
    (bridge_dir / "cookies.json").unlink()
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    p.run_once()
    assert json.loads((bridge_dir / "cookies.json").read_text())["sessionid"] == CHROME_SID
    assert world.restarts() == 1


def test_backups_are_pruned_to_five(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    for i in range(7):
        (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
        world.now = NOW + timedelta(seconds=i + 1)
        p.run_once()
        # reset the counter's cap by pretending the cookie changed each time
        world.health = '{"session":"authenticated"}'
        p.run_once()
        world.health = '{"session":"logged_out"}'
    assert len(list(bridge_dir.glob("cookies.json.bak-*"))) == 5


# ---- the bridge must never stay stopped by the probe -----------------------

def marker(cfg: ProbeConfig) -> Path:
    return cfg.state_file.parent / "bridge-stopped-by-probe"


def starts(world: FakeWorld) -> int:
    return sum(1 for c in world.calls if c[:2] == ["docker", "start"])


def test_marker_is_written_before_the_stop_and_cleared_after_a_good_start(
    world, bridge_dir, tmp_path
):
    world.health = '{"session":"logged_out"}'
    p, cfg = make(world, bridge_dir, tmp_path)
    seen: list[bool] = []
    real = world.run

    def spy(argv, timeout, stdin):  # type: ignore[no-untyped-def]
        if list(argv[:2]) == ["docker", "stop"]:
            seen.append(marker(cfg).exists())
            assert stat.S_IMODE(marker(cfg).stat().st_mode) == 0o600
        return real(argv, timeout, stdin)

    p._run = spy  # type: ignore[assignment]
    p.run_once()
    assert seen == [True]
    assert not marker(cfg).exists()


def test_failed_start_is_recovered_by_the_next_run(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    world.start_rc = 1
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR" and rec["sync"] == "start_failed:rc=1"
    assert not world.running and marker(cfg).exists()
    world.start_rc = 0
    world.health = '{"session":"authenticated"}'
    world.now = NOW + timedelta(minutes=10)
    starts_before = starts(world)
    verdict, rec = p.run_once()
    assert starts(world) == starts_before + 1
    assert world.running and not marker(cfg).exists()
    assert rec["recovery_start"] == "ok" and rec["bridge_session"] == "authenticated"
    assert verdict == "PASS"


def test_a_start_that_keeps_failing_keeps_the_marker_and_alerts_loudly(
    world, bridge_dir, tmp_path
):
    world.health = '{"session":"logged_out"}'
    world.start_rc = 1
    p, cfg = make(world, bridge_dir, tmp_path)
    p.run_once()
    world.now = NOW + timedelta(minutes=10)
    verdict, rec = p.run_once()  # recovery start fails again
    assert verdict == "ERROR"
    assert marker(cfg).exists()
    assert rec["recovery_start"] == "start_failed:rc=1"
    assert {"action": "error", "reason": "bridge_stopped_by_probe_start_failed"} in rec["decisions"]
    assert any(m.reason == "bridge_stopped_by_probe_start_failed" for m in world.sent)


@pytest.mark.parametrize("crash", ["crash_after_stop", "crash_on_start"])
def test_a_probe_killed_between_stop_and_start_is_recovered(world, bridge_dir, tmp_path, crash):
    world.health = '{"session":"logged_out"}'
    setattr(world, crash, True)
    p, cfg = make(world, bridge_dir, tmp_path)
    with pytest.raises(RuntimeError):
        p.run_once()
    assert not world.running and marker(cfg).exists()
    setattr(world, crash, False)
    world.health = '{"session":"authenticated"}'
    world.now = NOW + timedelta(minutes=10)
    verdict, rec = p.run_once()
    assert world.running and not marker(cfg).exists()
    assert rec["recovery_start"] == "ok" and verdict == "PASS"


def test_a_stop_that_times_out_but_lands_is_recovered(world, bridge_dir, tmp_path):
    world.health = '{"session":"logged_out"}'
    world.stop_rc = 124
    world.stop_lands_despite_rc = True
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR" and rec["sync"] == "stop_failed:rc=124"
    assert not world.running and marker(cfg).exists()
    world.stop_rc, world.stop_lands_despite_rc = 0, False
    world.health = '{"session":"authenticated"}'
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert world.running and not marker(cfg).exists()


def _fail_cookie_write(monkeypatch):  # type: ignore[no-untyped-def]
    real = os.replace

    def replace(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if Path(dst).name == "cookies.json":
            raise OSError("disk full")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(probe_mod.os, "replace", replace)


def test_write_failure_restarts_the_bridge_and_clears_the_marker(
    world, bridge_dir, tmp_path, monkeypatch
):
    world.health = '{"session":"logged_out"}'
    _fail_cookie_write(monkeypatch)
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR" and rec["sync"] == "write_failed:OSError"
    assert starts(world) == 1 and world.running
    assert not marker(cfg).exists()


def test_write_failure_then_failed_start_keeps_the_marker_and_next_run_recovers(
    world, bridge_dir, tmp_path, monkeypatch
):
    world.health = '{"session":"logged_out"}'
    world.start_rc = 1
    _fail_cookie_write(monkeypatch)
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR" and rec["sync"] == "write_failed:OSError"
    assert starts(world) == 1 and not world.running      # the write-failed start was tried
    assert marker(cfg).exists()
    monkeypatch.undo()
    world.start_rc = 0
    world.health = '{"session":"authenticated"}'
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert world.running and not marker(cfg).exists()


def test_a_bridge_a_human_stopped_is_not_started_by_the_probe(world, bridge_dir, tmp_path):
    world.running = False           # stopped by hand: no marker exists
    p, cfg = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR" and rec["bridge_session"] == "unreachable"
    assert {"action": "error", "reason": "bridge_unreachable"} in rec["decisions"]
    assert starts(world) == 0 and not world.running
    assert "recovery_start" not in rec and not marker(cfg).exists()


def test_the_marker_is_not_written_when_no_sync_happens(world, bridge_dir, tmp_path):
    p, cfg = make(world, bridge_dir, tmp_path)
    p.run_once()
    assert not marker(cfg).exists()


# ---- live checks -----------------------------------------------------------

def test_live_check_runs_once_per_hour_across_runs_with_jitter(world, bridge_dir, tmp_path):
    p, _ = make(world, bridge_dir, tmp_path)
    p.run_once()
    assert sorted(world.whoami_sites()) == ["linkedin", "twitter"]
    assert world.sleeps == [1.5, 1.5]
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert len(world.whoami_sites()) == 2                  # not repeated
    world.now = NOW + timedelta(minutes=61)
    p.run_once()
    assert len(world.whoami_sites()) == 4                  # due again


def test_live_check_429_is_error_not_logged_out(world, bridge_dir, tmp_path):
    world.whoami["twitter"] = RunResult(77, "", "HTTP 429 Too Many Requests: not logged in")
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "ERROR"
    assert rec["live"]["x"] == "error"
    assert not any(m.reason == "live_check_logged_out" for m in world.sent)


def test_successful_whoami_containing_429_is_logged_in(world, bridge_dir, tmp_path):
    world.whoami["twitter"] = RunResult(0, '{"followers": 429}')
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert rec["live"]["x"] == "logged_in"
    assert verdict == "PASS"


def test_live_check_exit_77_is_logged_out_alert(world, bridge_dir, tmp_path):
    world.whoami["linkedin"] = RunResult(77, "", "not logged in")
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, _ = p.run_once()
    assert verdict == "FAIL"
    assert [(m.network, m.reason) for m in world.sent] == [("linkedin", "live_check_logged_out")]


def test_unknown_opencli_failure_is_error(world, bridge_dir, tmp_path):
    world.whoami["twitter"] = RunResult(1, "", "boom")
    p, _ = make(world, bridge_dir, tmp_path)
    assert p.run_once()[0] == "ERROR"


def test_missing_cookie_skips_that_networks_live_check(world, bridge_dir, tmp_path):
    p, _ = make(world, bridge_dir, tmp_path, chrome=jar(li=False))
    p.run_once()
    assert "linkedin" not in world.whoami_sites()


def test_no_instagram_whoami_is_ever_made(world, bridge_dir, tmp_path):
    world.whoami["instagram"] = RunResult(1, "", "HTTP 429 Too Many Requests")
    p, _ = make(world, bridge_dir, tmp_path)
    for i in range(3):
        world.now = NOW + timedelta(hours=2 * i)
        verdict, rec = p.run_once()
        assert verdict == "PASS"
        assert "instagram" not in rec["live"]
    assert "instagram" not in world.whoami_sites()


# ---- authenticated but disconnected ----------------------------------------

DISCONNECTED = '{"session":"authenticated","connected":false,"seq":0}'
CONNECTED = '{"session":"authenticated","connected":true,"seq":7}'


def test_one_disconnected_run_is_recorded_but_does_not_sync(world, bridge_dir, tmp_path):
    world.health = DISCONNECTED
    p, _ = make(world, bridge_dir, tmp_path)
    verdict, rec = p.run_once()
    assert verdict == "PASS"
    assert world.restarts() == 0 and world.sent == []
    assert rec["bridge_connected"] is False
    assert rec["carry"]["bridge_disconnected_runs"] == 1


def test_two_consecutive_disconnected_runs_with_a_differing_cookie_sync(
    world, bridge_dir, tmp_path
):
    world.health = DISCONNECTED
    p, _ = make(world, bridge_dir, tmp_path)
    p.run_once()
    world.now = NOW + timedelta(minutes=10)
    verdict, rec = p.run_once()
    assert verdict == "FAIL"
    assert rec["bridge_session"] == "disconnected"
    assert rec["sync"] == "ok" and world.restarts() == 1
    assert [c[:2] for c in world.calls if c[:2] in (["docker", "stop"], ["docker", "start"])] == [
        ["docker", "stop"], ["docker", "start"]]
    assert json.loads((bridge_dir / "cookies.json").read_text())["sessionid"] == CHROME_SID


def test_two_disconnected_runs_with_the_same_cookie_alert_and_do_not_sync(
    world, bridge_dir, tmp_path
):
    (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": CHROME_SID}))
    world.health = DISCONNECTED
    p, _ = make(world, bridge_dir, tmp_path)
    p.run_once()
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert world.restarts() == 0
    assert [m.reason for m in world.sent] == ["bridge_disconnected_same_cookie"]


def test_a_connected_run_between_disconnected_runs_resets_the_debounce(
    world, bridge_dir, tmp_path
):
    p, _ = make(world, bridge_dir, tmp_path)
    for i, health in enumerate([DISCONNECTED, CONNECTED, DISCONNECTED]):
        world.health = health
        world.now = NOW + timedelta(minutes=10 * i)
        _, rec = p.run_once()
    assert world.restarts() == 0
    assert rec["carry"]["bridge_disconnected_runs"] == 1


def test_authenticated_and_connected_with_a_differing_cookie_is_left_alone(
    world, bridge_dir, tmp_path
):
    world.health = CONNECTED
    p, _ = make(world, bridge_dir, tmp_path)
    for i in range(3):
        world.now = NOW + timedelta(minutes=10 * i)
        verdict, _ = p.run_once()
        assert verdict == "PASS"
    assert world.restarts() == 0 and world.sent == []


def test_disconnected_syncs_are_capped_and_the_first_run_does_not_reset_the_counter(
    world, bridge_dir, tmp_path
):
    world.health = DISCONNECTED
    p, _ = make(world, bridge_dir, tmp_path)
    recs = []
    for i in range(10):
        world.now = NOW + timedelta(minutes=10 * i)
        # the sync leaves the (fake) bridge holding Chrome's cookie: keep the
        # cookies different so only the cap can stop it
        (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
        recs.append(p.run_once()[1])
    assert world.restarts() == 3
    assert [r["carry"]["attempts"] for r in recs] == [0, 1, 1, 2, 2, 3, 3, 3, 3, 3]
    assert any(m.reason == "sync_capped" for m in world.sent)


def test_the_cap_survives_an_unreachable_run_between_disconnected_runs(world, bridge_dir, tmp_path):
    p, _ = make(world, bridge_dir, tmp_path)
    for i in range(14):
        world.now = NOW + timedelta(minutes=10 * i)
        if i % 3 == 2:
            world.health, world.health_rc = "", 1  # unreachable
        else:
            world.health, world.health_rc = DISCONNECTED, 0
        (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
        p.run_once()
    assert world.restarts() == 3


def test_the_debounce_restarts_after_the_probe_restarts_the_bridge(world, bridge_dir, tmp_path):
    world.health = DISCONNECTED
    p, _ = make(world, bridge_dir, tmp_path)
    recs = []
    for i in range(3):
        world.now = NOW + timedelta(minutes=10 * i)
        (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": BRIDGE_SID}))
        recs.append(p.run_once()[1])
    assert recs[1]["sync"] == "ok"
    assert recs[2]["carry"]["bridge_disconnected_runs"] == 1  # not 3: no sync, no alert yet
    assert recs[2]["sync"] is None
    assert not any(m.reason == "bridge_disconnected_same_cookie" for m in world.sent)


# ---- alert dedup -----------------------------------------------------------

def test_an_alert_is_not_repeated_within_the_window_but_is_after_it(world, bridge_dir, tmp_path):
    (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": CHROME_SID}))
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    p.run_once()
    world.now = NOW + timedelta(hours=1)
    p.run_once()
    assert len(world.sent) == 1
    world.now = NOW + timedelta(hours=13)
    p.run_once()
    assert len(world.sent) == 2


def test_a_corrupt_last_state_line_is_skipped_not_fatal(world, bridge_dir, tmp_path):
    p, cfg = make(world, bridge_dir, tmp_path)
    p.run_once()
    with cfg.state_file.open("a") as fh:
        fh.write("{torn line")
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert len(world.whoami_sites()) == 2  # carry still recovered from the good line


# ---- alert-bridge sink -----------------------------------------------------

def test_alert_bridge_sink_posts_an_alertmanager_shaped_payload_via_docker_exec():
    seen: dict = {}

    def run(argv, timeout, stdin):  # type: ignore[no-untyped-def]
        seen.update(argv=list(argv), stdin=stdin)
        return RunResult(0)

    assert AlertBridgeSink(run).send(AlertMessage("x", "chrome_session_missing", "log in"))
    assert seen["argv"][:4] == ["docker", "exec", "-i", "nora-alert-bridge"]
    body = json.loads(seen["stdin"])
    assert body["status"] == "firing"
    assert body["alerts"][0]["labels"]["job"] == "x"
    assert "chrome_session_missing" in body["alerts"][0]["annotations"]["summary"]


def test_alert_bridge_sink_reports_failure_without_raising():
    sink = AlertBridgeSink(lambda a, t, s: RunResult(1, "", "no such container"))
    assert sink.send(AlertMessage("probe", "bridge_unreachable", "x")) is False


def test_failed_delivery_is_not_marked_sent_so_it_retries(world, bridge_dir, tmp_path):
    (bridge_dir / "cookies.json").write_text(json.dumps({"sessionid": CHROME_SID}))
    world.health = '{"session":"logged_out"}'
    p, _ = make(world, bridge_dir, tmp_path)
    world.deliver = False
    p.run_once()
    assert world.sent == []
    world.deliver = True
    world.now = NOW + timedelta(minutes=10)
    p.run_once()
    assert len(world.sent) == 1


def test_a_second_run_under_the_lock_does_nothing_and_is_error(world, bridge_dir, tmp_path):
    import fcntl

    p, cfg = make(world, bridge_dir, tmp_path)
    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_file.parent / "probe.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        world.calls.clear()
        verdict, record = p.run_once()
    assert verdict == "ERROR"
    assert record["error"] == "probe_already_running"
    assert world.calls == []  # no docker, no opencli, no state line: nothing was touched
    assert not cfg.state_file.exists()


def test_config_reads_the_opencli_profile_and_refuses_a_blank_one():
    from nora.infra.social.session_probe.__main__ import config_from_env
    assert config_from_env({}).opencli_profile == "nora"
    assert config_from_env({"OPENCLI_PROFILE": "nora-alt"}).opencli_profile == "nora-alt"
    with pytest.raises(ValueError):
        config_from_env({"OPENCLI_PROFILE": "  "})
