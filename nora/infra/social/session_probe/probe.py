"""One probe run: measure Chrome and the bridge, decide, act, record.

All IO is injected (`Probe.__init__`), so the tests drive it with fakes: no
Chrome DB, no docker, no opencli, no network, no sleeping.

The record is the point. Every run appends ONE JSON line to the state file
saying what was measured (hash prefixes, times, never a value), what was
decided, what was done, and which side holds the newer sessionid. Session
lifetime becomes a number you read off that file instead of an anecdote.
"""

from __future__ import annotations

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
#: opencli site name per network (X is `twitter` in OpenCLI 1.8.7).
OPENCLI_SITE: dict[Network, str] = {
    "instagram": "instagram", "x": "twitter", "linkedin": "linkedin",
}
_LOGGED_OUT_TEXT = re.compile(r"not logged in|log ?in required|logged.?out", re.I)
_RATE_LIMIT_TEXT = re.compile(r"\b429\b|too many requests|rate.?limit", re.I)
KEEP_BACKUPS = 5


@dataclass(frozen=True)
class ProbeConfig:
    chrome_db: Path = Path("/home/norty/.config/google-chrome/Default/Cookies")
    bridge_dir: Path = Path("/home/norty/workspace/nora/state/instagram")
    container: str = "instagram-bridge"
    bridge_port: int = 18082
    state_file: Path = field(
        default_factory=lambda: Path.home() / ".local/state/nora/session-probe.jsonl"
    )
    opencli_bin: str = "opencli"
    live_interval: timedelta = timedelta(hours=1)
    live_jitter_max: float = 20.0
    alert_repeat: timedelta = timedelta(hours=12)

    @property
    def cookies_json(self) -> Path:
        return self.bridge_dir / "cookies.json"


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
    def _bridge_health(self) -> BridgeSession:
        res = self._run(
            ["docker", "exec", self._cfg.container, "curl", "-sS", "-m", "5",
             f"http://127.0.0.1:{self._cfg.bridge_port}/health"],
            15.0, None,
        )
        if res.rc != 0:
            return "unreachable"
        try:
            body = json.loads(res.stdout)
            session = body["session"]
        except (ValueError, KeyError, TypeError):
            return "unreachable"
        return session if session in _BRIDGE_SESSIONS else "unreachable"

    def _bridge_jar(self) -> dict[str, str]:
        """The bridge's cookies.json, or {} when missing/corrupt (which reads
        as «differs» — the sync then rewrites it whole)."""
        try:
            data = json.loads(self._cfg.cookies_json.read_text())
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _live_check(self, net: Network) -> LiveCheck:
        argv = [self._cfg.opencli_bin, OPENCLI_SITE[net], "whoami", "-f", "json"]
        res = self._run(argv, 60.0, None)
        text = f"{res.stdout}\n{res.stderr}"
        if _RATE_LIMIT_TEXT.search(text):
            return LiveCheck.ERROR  # rate limited is not logged out
        if res.rc == 0:
            return LiveCheck.LOGGED_IN
        if res.rc == 77 or _LOGGED_OUT_TEXT.search(text):
            return LiveCheck.LOGGED_OUT
        return LiveCheck.ERROR

    def _due_live(
        self, carry: dict[str, Any], cookies: dict[Network, Any], now: datetime
    ) -> dict[Network, LiveCheck]:
        results: dict[Network, LiveCheck] = {}
        last = carry.get("last_live_check") or {}
        for net in NETWORKS:
            if cookies.get(net) is None:
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
        """Backup, merge Chrome's Instagram cookies over the bridge's, write
        atomically at 0600, restart the container. Returns «ok» or an error tag."""
        target = self._cfg.cookies_json
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
            return f"write_failed:{type(exc).__name__}"
        res = self._run(["docker", "restart", self._cfg.container], 60.0, None)
        return "ok" if res.rc == 0 else f"restart_failed:rc={res.rc}"

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
                msg = AlertMessage("probe", a.reason, f"the probe could not measure: {a.reason}")
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
        now = self._now()
        carry = self._last_carry()
        record: dict[str, Any] = {"ts": now.isoformat()}

        chrome_cookies: list[ChromeCookie] = []
        try:
            chrome_cookies = self._read_chrome()
            chrome = ChromeState(True, session_info(chrome_cookies))
        except CookieError as exc:
            chrome = ChromeState(False, {})
            record["chrome_error"] = str(exc)
        bridge_session = self._bridge_health()

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

        live = self._due_live(carry, dict(chrome.cookies), now) if chrome.readable else {}
        record["live"] = {n: r.value for n, r in live.items()}

        history = History(int(carry.get("attempts", 0)))
        actions = decide(Snapshot(now, chrome, BridgeState(bridge_session), differs, history, live))

        sync_result: str | None = None
        synced = False
        for a in actions:
            if isinstance(a, SyncBridge):
                synced = True
                sync_result = self._sync(instagram_jar(chrome_cookies))
                if sync_result != "ok":
                    actions.append(ErrorAction(f"sync_{sync_result}"))
        record["sync"] = sync_result

        v = verdict(actions)
        record["decisions"] = [_describe(a) for a in actions]
        record["verdict"] = v
        record["alerts_sent"] = self._emit_alerts(actions, carry, now)
        carry["attempts"] = next_attempts(
            history.consecutive_sync_attempts, carry.get("chrome_hash"), chrome_fp,
            bridge_session, synced,
        )
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
    "chrome_session_missing": "no session cookie in Chrome; log in by hand over VNC",
    "chrome_session_expired": "the session cookie in Chrome has expired; log in by hand over VNC",
    "chrome_session_near_expiry": "Chrome's session expires within 72h; renew the login over VNC",
    "live_check_logged_out": "opencli whoami says logged out; log in by hand over VNC",
    "bridge_logged_out_same_cookie": "bridge is logged out and already holds Chrome's cookie; "
    "a sync cannot help, log in again in Chrome over VNC",
    "bridge_checkpointed_same_cookie": "bridge is checkpointed and holds Chrome's cookie; "
    "clear the challenge by hand",
}


def _describe(a: Action) -> dict[str, Any]:
    if isinstance(a, SyncBridge):
        return {"action": "sync_bridge", "attempt": a.attempt}
    if isinstance(a, Alert):
        return {"action": "alert", "network": a.network, "reason": a.reason}
    if isinstance(a, Capped):
        return {"action": "capped", "attempts": a.attempts}
    return {"action": "error", "reason": a.reason}

