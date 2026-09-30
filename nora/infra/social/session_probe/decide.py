"""The pure decision: what the session probe does about what it measured.

No IO, no clock, no environment: `decide` takes a fixed, already-interpreted
snapshot and returns actions. Everything that touches Chrome, the bridge,
docker or the disk lives in `probe.py`, so every branch below is a unit test.

**PASS / FAIL / ERROR stay distinct** (the repo's fail-closed rule). An
unreadable Chrome DB or an unreachable bridge is an `ErrorAction`, and
`verdict()` maps any error to ERROR: «I could not check» is never «all
sessions are fine». An alert is a FAIL: the check ran and the answer was bad.

**Chrome is the owner, the bridge is a follower.** The probe never logs in
anywhere. A session that is missing, expired or near expiry in Chrome itself
needs a human in VNC, so it is an alert, not an action. The only thing the
probe repairs is the follower: copy Chrome's Instagram cookies over the
bridge's jar and restart the bridge (`SyncBridge`), at most `MAX_SYNC_ATTEMPTS`
times in a row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

Network = Literal["instagram", "x", "linkedin"]
NETWORKS: tuple[Network, ...] = ("instagram", "x", "linkedin")

BridgeSession = Literal["authenticated", "checkpointed", "logged_out", "unreachable"]

#: A Chrome session expiring inside this window is alerted while a human can
#: still renew it on their own schedule rather than after reads start failing.
NEAR_EXPIRY = timedelta(hours=72)

#: Consecutive syncs that did not yield `authenticated` before the probe stops
#: retrying. Every retry is a bridge restart, which is a fresh connection to
#: Instagram from the same account: the loop itself is a risk to the session.
MAX_SYNC_ATTEMPTS = 3


@dataclass(frozen=True)
class CookieInfo:
    """One network's session cookie as Chrome holds it (never its value)."""

    created_at: datetime | None
    expires_at: datetime | None  # None = a browser-session cookie, no expiry


@dataclass(frozen=True)
class ChromeState:
    """What Chrome's cookie jar says. `readable=False` means the DB could not
    be read or decrypted: every network is then unknown, not missing."""

    readable: bool
    cookies: Mapping[Network, CookieInfo | None] = field(default_factory=dict)


class LiveCheck(Enum):
    """Result of the hourly `opencli <net> whoami`, when one ran this cycle."""

    NOT_RUN = "not_run"
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    ERROR = "error"  # includes HTTP 429: rate limited is not logged out


@dataclass(frozen=True)
class BridgeState:
    session: BridgeSession


@dataclass(frozen=True)
class History:
    """Carried from the previous run."""

    consecutive_sync_attempts: int = 0


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    chrome: ChromeState
    bridge: BridgeState
    #: True when Chrome's Instagram sessionid hash differs from the bridge
    #: cookies.json's (a missing/unreadable jar counts as different).
    instagram_differs: bool
    history: History = field(default_factory=History)
    live: Mapping[Network, LiveCheck] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncBridge:
    """Backup cookies.json, merge Chrome's Instagram cookies over it, write
    atomically at mode 0600, `docker restart instagram-bridge`."""

    attempt: int


@dataclass(frozen=True)
class Alert:
    network: Network
    reason: str


@dataclass(frozen=True)
class Capped:
    """MAX_SYNC_ATTEMPTS syncs in a row did not reach `authenticated`: stop."""

    attempts: int


@dataclass(frozen=True)
class ErrorAction:
    """The probe could not measure something. Never a PASS."""

    reason: str


Action = SyncBridge | Alert | Capped | ErrorAction

Verdict = Literal["PASS", "FAIL", "ERROR"]


def _chrome_findings(snap: Snapshot) -> list[Action]:
    out: list[Action] = []
    for net in NETWORKS:
        info = snap.chrome.cookies.get(net)
        if info is None:
            out.append(Alert(net, "chrome_session_missing"))
        elif info.expires_at is not None and info.expires_at <= snap.now:
            out.append(Alert(net, "chrome_session_expired"))
        elif info.expires_at is not None and info.expires_at - snap.now < NEAR_EXPIRY:
            out.append(Alert(net, "chrome_session_near_expiry"))
    return out


def _live_findings(snap: Snapshot) -> list[Action]:
    out: list[Action] = []
    for net in NETWORKS:
        result = snap.live.get(net, LiveCheck.NOT_RUN)
        if result is LiveCheck.LOGGED_OUT:
            out.append(Alert(net, "live_check_logged_out"))
        elif result is LiveCheck.ERROR:
            out.append(ErrorAction(f"live_check_error:{net}"))
    return out


def _bridge_findings(snap: Snapshot, chrome_ig_usable: bool) -> list[Action]:
    session = snap.bridge.session
    if session == "unreachable":
        return [ErrorAction("bridge_unreachable")]
    if session == "authenticated":
        return []
    # checkpointed or logged_out: the follower is down.
    if not chrome_ig_usable:
        # Nothing good to copy; the Chrome-side alert already says why.
        return []
    if not snap.instagram_differs:
        # The bridge already holds Chrome's exact cookie and is still refused:
        # a sync would rewrite the same bytes. Only a human login helps.
        reason = "bridge_logged_out_same_cookie" if session == "logged_out" else (
            "bridge_checkpointed_same_cookie"
        )
        return [Alert("instagram", reason)]
    attempts = snap.history.consecutive_sync_attempts
    if attempts >= MAX_SYNC_ATTEMPTS:
        return [Capped(attempts), Alert("instagram", "sync_capped")]
    return [SyncBridge(attempts + 1)]


def decide(snap: Snapshot) -> list[Action]:
    """Snapshot -> actions. Empty list means PASS."""
    if not snap.chrome.readable:
        actions: list[Action] = [ErrorAction("chrome_cookies_unreadable")]
        if snap.bridge.session == "unreachable":
            actions.append(ErrorAction("bridge_unreachable"))
        return actions
    actions = _chrome_findings(snap)
    ig = snap.chrome.cookies.get("instagram")
    ig_usable = (
        ig is not None
        and (ig.expires_at is None or ig.expires_at > snap.now)
        and snap.live.get("instagram", LiveCheck.NOT_RUN) is not LiveCheck.LOGGED_OUT
    )
    actions += _live_findings(snap)
    actions += _bridge_findings(snap, ig_usable)
    return actions


def verdict(actions: list[Action]) -> Verdict:
    """ERROR outranks FAIL outranks PASS: an unmeasured thing is never green."""
    if any(isinstance(a, ErrorAction) for a in actions):
        return "ERROR"
    return "FAIL" if actions else "PASS"


def next_attempts(
    prev_attempts: int,
    prev_chrome_hash: str | None,
    chrome_hash: str | None,
    bridge_session: BridgeSession,
    synced_now: bool,
) -> int:
    """Consecutive-sync counter carried to the next run.

    Reset when the bridge is authenticated, or when Chrome's cookie changed
    (a human re-logged in: the earlier failures were about a different
    cookie). Otherwise it counts syncs that did not yield `authenticated`.
    An unreachable bridge tells us nothing, so the counter holds.
    """
    if bridge_session == "authenticated":
        return 0
    if chrome_hash != prev_chrome_hash:
        return 1 if synced_now else 0
    return prev_attempts + 1 if synced_now else prev_attempts


def utcnow() -> datetime:
    return datetime.now(UTC)
