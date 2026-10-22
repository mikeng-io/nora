"""One probe run: measure Chrome and the bridge, decide, act, record.

All IO is injected (`Probe.__init__`), so the tests drive it with fakes: no
Chrome DB, no docker, no opencli, no network, no sleeping.

The record is the point. Every run appends ONE JSON line to the state file
saying what was measured (hash prefixes, times, never a value), what was
decided, what was done, and which side holds the newer sessionid. Session
lifetime becomes a number you read off that file instead of an anecdote.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .alerts import AlertMessage, AlertSink
from .cookies import (
    ChromeCookie,
    CookieError,
    fingerprint,
    instagram_jar,
    session_info,
    session_value,
)
from .decide import (
    NETWORKS,
    Action,
    Alert,
    BridgeSession,
    BridgeState,
    Capped,
    ChromeState,
    ErrorAction,
    History,
    LiveCheck,
    Network,
    Snapshot,
    SyncBridge,
    decide,
    next_attempts,
    verdict,
)
from .runner import Runner

log = logging.getLogger("nora.session_probe")

_BRIDGE_SESSIONS = ("authenticated", "checkpointed", "logged_out")
#: opencli site name per network (X is `twitter` in OpenCLI 1.8.7). Instagram
#: has none on purpose: `whoami` answers HTTP 429 every time, and the bridge's
#: own /health plus the cookie comparison is the Instagram liveness signal.
OPENCLI_SITE: dict[Network, str] = {"x": "twitter", "linkedin": "linkedin"}
#: Consecutive runs a bridge must read `authenticated` + `connected: false`
#: before it counts as `disconnected` (a restarting bridge is briefly so).
DISCONNECTED_RUNS = 2
_LOGGED_OUT_TEXT = re.compile(r"not logged in|log ?in required|logged.?out", re.I)
_RATE_LIMIT_TEXT = re.compile(r"\b429\b|too many requests|rate.?limit", re.I)
KEEP_BACKUPS = 5
STOP_MARKER = "bridge-stopped-by-probe"
START_FAILED_REASON = "bridge_stopped_by_probe_start_failed"


@dataclass(frozen=True)
class ProbeConfig:
    chrome_db: Path = Path("/home/norty/.local/share/nora-browser/Profile 1/Cookies")
    bridge_dir: Path = Path("/home/norty/workspace/nora/state/instagram")
    container: str = "instagram-bridge"
    bridge_port: int = 18082
    state_file: Path = field(
        default_factory=lambda: Path.home() / ".local/state/nora/session-probe.jsonl"
    )
    opencli_bin: str = "opencli"
    #: Nora's Browser Bridge profile alias; see `SocialBridge.__init__`.
    opencli_profile: str = "nora"
    live_interval: timedelta = timedelta(hours=1)
    live_jitter_max: float = 20.0
    alert_repeat: timedelta = timedelta(hours=12)

    @property
    def cookies_json(self) -> Path:
        return self.bridge_dir / "cookies.json"

    @property
    def marker(self) -> Path:
        """Present exactly while the probe may have left the bridge stopped."""
        return self.state_file.parent / STOP_MARKER


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class Probe:
    def __init__(
        self,
        config: ProbeConfig,
        *,
        read_chrome: Callable[[], list[ChromeCookie]],
        run: Runner,
        sink: AlertSink,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] = lambda hi: random.uniform(0.0, hi),  # noqa: S311
    ) -> None:
        self._cfg = config
        self._read_chrome = read_chrome
        self._run = run
        self._sink = sink
        self._now = now
        self._sleep = sleep
        self._jitter = jitter

    # ---- state file -------------------------------------------------------
    def _last_carry(self) -> dict[str, Any]:
        try:
            lines = self._cfg.state_file.read_text().splitlines()
        except OSError:
            return {}
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            carry = rec.get("carry") if isinstance(rec, dict) else None
            if isinstance(carry, dict):
                return carry
        return {}

    def _append(self, record: dict[str, Any]) -> None:
        path = self._cfg.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    # ---- measurements -----------------------------------------------------
    def _bridge_health(self) -> tuple[BridgeSession, bool | None]:
        """(raw session, `connected` when /health carries a boolean)."""
        # The bridge protects /health with the bearer token its own environment
        # already holds (compose sets NORA_BRIDGE_TOKEN). The token reaches curl
        # on STDIN (`-H @-`), never in argv: a process listing inside the bridge
        # container is readable by anything running there, and an expanded
        # Authorization header in curl's argv would hand it over. The `printf`
        # format is a constant, so the value is not an argument either. A
        # missing token is a 401, which is `unreachable` — the correct
        # fail-closed reading of a misconfigured bridge.
        res = self._run(
            ["docker", "exec", self._cfg.container, "sh", "-c",
             'printf "Authorization: Bearer %s\\n" "$NORA_BRIDGE_TOKEN" | '
             'curl -sS -m 5 -H @- '
             f"http://127.0.0.1:{self._cfg.bridge_port}/health"],
            15.0, None,
        )
        if res.rc != 0:
            return "unreachable", None
        try:
            body = json.loads(res.stdout)
            session = body["session"]
            connected = body.get("connected")
        except (ValueError, KeyError, TypeError, AttributeError):
            return "unreachable", None
        if session not in _BRIDGE_SESSIONS:
            return "unreachable", None
        return session, connected if isinstance(connected, bool) else None

    def _bridge_jar(self) -> dict[str, str]:
        """The bridge's cookies.json, or {} when missing/corrupt (which reads
        as «differs» — the sync then rewrites it whole)."""
        try:
            data = json.loads(self._cfg.cookies_json.read_text())
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _live_check(self, net: Network) -> LiveCheck:
        argv = [self._cfg.opencli_bin, "--profile", self._cfg.opencli_profile,
                OPENCLI_SITE[net], "whoami", "-f", "json"]
        res = self._run(argv, 60.0, None)
        text = f"{res.stdout}\n{res.stderr}"
        if res.rc == 0:
            return LiveCheck.LOGGED_IN  # a 429 inside a good JSON body is data
        if _RATE_LIMIT_TEXT.search(text):
            return LiveCheck.ERROR  # rate limited is not logged out
        if res.rc == 77 or _LOGGED_OUT_TEXT.search(text):
            return LiveCheck.LOGGED_OUT
        return LiveCheck.ERROR

    def _due_live(
        self, carry: dict[str, Any], cookies: dict[Network, Any], now: datetime
    ) -> dict[Network, LiveCheck]:
        results: dict[Network, LiveCheck] = {}
        last = carry.get("last_live_check") or {}
        for net in NETWORKS:
            if net not in OPENCLI_SITE or cookies.get(net) is None:
                continue  # nothing to verify; the missing cookie is its own alert
            try:
                prev = datetime.fromisoformat(last[net]) if net in last else None
            except ValueError:
                prev = None
            if prev is not None and now - prev < self._cfg.live_interval:
                continue
            self._sleep(self._jitter(self._cfg.live_jitter_max))
            results[net] = self._live_check(net)
            last[net] = now.isoformat()
        carry["last_live_check"] = last
        return results

    # ---- actions ----------------------------------------------------------
    def _sync(self, jar: dict[str, str]) -> str:
        """Stop the bridge, back up, merge Chrome's Instagram cookies over the
        bridge's, write atomically at 0600, start the bridge. Returns «ok» or an
        error tag.

        The order is load-bearing: the bridge's SIGTERM handler flushes its
        in-memory (old, rejected) jar back over cookies.json, so the write must
        land AFTER the process is gone and BEFORE it starts again."""
        target = self._cfg.cookies_json
        # The marker goes down BEFORE the stop: a crash, a timeout or a failed
        # start from here on leaves it behind, and the next run restarts what
        # this probe stopped (and only that).
        try:
            self._write_marker()
        except OSError as exc:
            return f"marker_failed:{type(exc).__name__}"  # nothing stopped yet
        stopped = self._run(["docker", "stop", self._cfg.container], 60.0, None)
        if stopped.rc != 0:
            # rc 124 = timeout, and the daemon may still finish the stop; the
            # marker stays so the next run's recovery start covers it.
            return f"stop_failed:rc={stopped.rc}"
        try:
            if target.exists():
                stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
                backup = target.with_name(f"cookies.json.bak-{stamp}")
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(target.read_bytes())
                for old in sorted(target.parent.glob("cookies.json.bak-*"))[:-KEEP_BACKUPS]:
                    old.unlink(missing_ok=True)
            merged = {**self._bridge_jar(), **jar}
            tmp = target.with_name("cookies.json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(merged, sort_keys=True))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except OSError as exc:
            # The bridge is down: bring it back before reporting the failure.
            # If THIS start fails too, the marker stays for the next run.
            revived = self._run(["docker", "start", self._cfg.container], 60.0, None)
            if revived.rc == 0:
                self._clear_marker()
            return f"write_failed:{type(exc).__name__}"
        started = self._run(["docker", "start", self._cfg.container], 60.0, None)
        if started.rc != 0:
            return f"start_failed:rc={started.rc}"
        self._clear_marker()
        return "ok"

    # ---- the stop marker --------------------------------------------------
    def _write_marker(self) -> None:
        path = self._cfg.marker
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(self._now().isoformat())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _clear_marker(self) -> None:
        self._cfg.marker.unlink(missing_ok=True)

    def _recover(self, record: dict[str, Any]) -> ErrorAction | None:
        """A marker means a previous run stopped the bridge and never confirmed
        the start. Start it (idempotent); clear the marker only on rc 0.
        Nothing here touches a bridge a human stopped: no marker, no start."""
        if not self._cfg.marker.exists():
            return None
        res = self._run(["docker", "start", self._cfg.container], 60.0, None)
        if res.rc == 0:
            self._clear_marker()
            record["recovery_start"] = "ok"
            return None
        record["recovery_start"] = f"start_failed:rc={res.rc}"
        return ErrorAction(START_FAILED_REASON)

    def _emit_alerts(
        self, actions: list[Action], carry: dict[str, Any], now: datetime
    ) -> list[str]:
        sent = carry.setdefault("last_alerts", {})
        delivered: list[str] = []
        for a in actions:
            if isinstance(a, Alert):
                msg = AlertMessage(a.network, a.reason, _HUMAN.get(a.reason, a.reason))
            elif isinstance(a, Capped):
                msg = AlertMessage("instagram", "sync_capped",
                                   f"{a.attempts} syncs in a row did not restore the bridge")
            elif isinstance(a, ErrorAction):
                msg = AlertMessage(
                    "probe", a.reason,
                    _HUMAN.get(a.reason, f"the probe could not measure: {a.reason}"),
                )
            else:
                continue
            key = f"{msg.network}:{msg.reason}"
            try:
                prev = datetime.fromisoformat(sent[key]) if key in sent else None
            except ValueError:
                prev = None
            if prev is not None and now - prev < self._cfg.alert_repeat:
                continue
            if self._sink.send(msg):
                sent[key] = now.isoformat()
                delivered.append(key)
        return delivered

    # ---- one run ----------------------------------------------------------
    def run_once(self) -> tuple[str, dict[str, Any]]:
        """One run, exclusive: a second run (a hand-started one beside the
        timer) would see the first's stop marker, «recover» a bridge that is
        about to be stopped, clear the marker, and strand it. So a run that
        cannot take the lock does nothing and reports ERROR."""
        lock_path = self._cfg.state_file.parent / "probe.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "ERROR", {"ts": self._now().isoformat(), "error": "probe_already_running"}
            return self._run_locked()

    def _run_locked(self) -> tuple[str, dict[str, Any]]:
        now = self._now()
        carry = self._last_carry()
        record: dict[str, Any] = {"ts": now.isoformat()}

        recovery_error = self._recover(record)  # before any health read or decision

        chrome_cookies: list[ChromeCookie] = []
        try:
            chrome_cookies = self._read_chrome()
            chrome = ChromeState(True, session_info(chrome_cookies))
        except CookieError as exc:
            chrome = ChromeState(False, {})
            record["chrome_error"] = str(exc)
        raw_session, bridge_connected = self._bridge_health()
        bridge_session: BridgeSession = raw_session
        # A bridge that says `authenticated` while its realtime socket is down
        # is only `disconnected` once that has held for DISCONNECTED_RUNS runs.
        if raw_session == "authenticated" and bridge_connected is False:
            disconnected_runs = int(carry.get("bridge_disconnected_runs", 0)) + 1
            if disconnected_runs >= DISCONNECTED_RUNS:
                bridge_session = "disconnected"
        else:
            disconnected_runs = 0
        carry["bridge_disconnected_runs"] = disconnected_runs

        chrome_sid = session_value(chrome_cookies, "instagram")
        chrome_fp = fingerprint(chrome_sid) if chrome_sid else None
        bridge_sid = self._bridge_jar().get("sessionid")
        bridge_fp = fingerprint(bridge_sid) if bridge_sid else None
        differs = chrome_fp != bridge_fp
        record["chrome"] = {
            n: (
                None if i is None
                else {"created": _iso(i.created_at), "expires": _iso(i.expires_at)}
            )
            for n, i in chrome.cookies.items()
        }
        record["instagram_sessionid"] = {"chrome": chrome_fp, "bridge": bridge_fp}
        record["newer_sessionid"] = self._newer(chrome, chrome_fp, bridge_fp)
        record["bridge_session"] = bridge_session
        record["bridge_connected"] = bridge_connected

        live = self._due_live(carry, dict(chrome.cookies), now) if chrome.readable else {}
        record["live"] = {n: r.value for n, r in live.items()}

        history = History(int(carry.get("attempts", 0)))
        actions = decide(Snapshot(now, chrome, BridgeState(bridge_session), differs, history, live))

        if recovery_error is not None:
            actions.append(recovery_error)
        sync_result: str | None = None
        synced = False
        for a in actions:
            if isinstance(a, SyncBridge):
                synced = True
                sync_result = self._sync(instagram_jar(chrome_cookies))
                if sync_result != "ok":
                    actions.append(ErrorAction(f"sync_{sync_result}"))
        record["sync"] = sync_result
        if sync_result == "ok":
            # The probe just restarted the bridge: it is disconnected again for
            # a moment, and that is the debounce's whole reason. Count afresh.
            carry["bridge_disconnected_runs"] = 0

        v = verdict(actions)
        record["decisions"] = [_describe(a) for a in actions]
        record["verdict"] = v
        record["alerts_sent"] = self._emit_alerts(actions, carry, now)
        carry["attempts"] = next_attempts(
            history.consecutive_sync_attempts, carry.get("chrome_hash"), chrome_fp,
            bridge_session, synced, bridge_connected,
        )
        if chrome_fp is not None:  # an unreadable run never erases the last known hash
            carry["chrome_hash"] = chrome_fp
        record["carry"] = carry
        self._append(record)
        return v, record

    def _newer(self, chrome: ChromeState, chrome_fp: str | None, bridge_fp: str | None) -> str:
        if chrome_fp is None or bridge_fp is None:
            return "unknown"
        if chrome_fp == bridge_fp:
            return "same"
        info = chrome.cookies.get("instagram")
        try:
            bridge_mtime = datetime.fromtimestamp(self._cfg.cookies_json.stat().st_mtime, UTC)
        except OSError:
            return "unknown"
        if info is None or info.created_at is None:
            return "unknown"
        return "chrome" if info.created_at > bridge_mtime else "bridge"


_HUMAN = {
    START_FAILED_REASON: "the probe stopped the instagram bridge and could not start it "
    "again; start it by hand (docker start instagram-bridge)",
    "chrome_session_missing": "no session cookie in Chrome; log in by hand over VNC",
    "chrome_session_expired": "the session cookie in Chrome has expired; log in by hand over VNC",
    "chrome_session_near_expiry": "Chrome's session expires within 72h; renew the login over VNC",
    "live_check_logged_out": "opencli whoami says logged out; log in by hand over VNC",
    "bridge_logged_out_same_cookie": "bridge is logged out and already holds Chrome's cookie; "
    "a sync cannot help, log in again in Chrome over VNC",
    "bridge_checkpointed_same_cookie": "bridge is checkpointed and holds Chrome's cookie; "
    "clear the challenge by hand",
    "bridge_disconnected_same_cookie": "bridge says authenticated but its realtime socket has "
    "stayed disconnected and it already holds Chrome's cookie; log in again in Chrome over VNC",
}


def _describe(a: Action) -> dict[str, Any]:
    if isinstance(a, SyncBridge):
        return {"action": "sync_bridge", "attempt": a.attempt}
    if isinstance(a, Alert):
        return {"action": "alert", "network": a.network, "reason": a.reason}
    if isinstance(a, Capped):
        return {"action": "capped", "attempts": a.attempts}
    return {"action": "error", "reason": a.reason}

