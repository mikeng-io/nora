"""The refresh-token chain both image OAuth lanes share (DEC-304, DEC-308).

xAI (`xai_oauth.py`) and OpenAI Codex (`openai_codex_oauth.py`) both ROTATE
the refresh token on use, and a spent one is dead. Everything that follows
from that is the same for both and lives here once; each lane supplies only
its token exchange (`_exchange`) and the shape its adapter wants back.

- `.env` (host, immutable, DEC-8) holds ONLY the bootstrap refresh token the
  operator's login wrote. It is spent by the first refresh and never sent
  again.
- The state file (container-writable `./state/`) holds the current access
  token, the CURRENT refresh token, when it was refreshed, when the access
  token expires, and the sha256 of the bootstrap token that chain grew from.
  A state file whose fingerprint does not match `.env` belongs to an older
  login and is ignored: a fresh login takes over without anyone deleting a
  file.
- A state file with no fingerprint names no login. It may hold the LIVE chain
  (the `.env` token was already rotated into it, so the bootstrap is spent) or
  a DEAD one (a new login since). So its token is tried first, and only if the issuer
  refuses it is the `.env` bootstrap spent; whichever succeeds is written
  back with the fingerprint, and the question never comes up again.
- A refresh is REFUSED when the state file cannot be written: the rotated
  token would be lost with the process, and the chain with it.
- Refreshes serialise in the process (an asyncio lock) and across processes
  on one host (an `flock` beside the state file), and a process re-reads the
  file under both before it refreshes: two holders of one rotating token is
  how one logs the other out.
- The daemon's keepalive (`nora.app.oauth_keepalive`) refreshes
  through `refresh()`, which takes the SAME locks as a `generate_image` call,
  so the keeper and a live call never both spend one token.

Every refresh failure is logged with the lane and the reason, never the
token, so a dead refresh token is visible. NEVER raises into the caller: a failure is a
reason string (R-27).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

log = logging.getLogger("nora.image_gen.oauth_lane")

#: Reasons no retry fixes: the issuer refused the refresh token (only a new
#: login does), or the state file cannot be written (only the operator can).
TERMINAL_REASONS: Final = frozenset({"auth", "state_unwritable"})

#: Issuer error codes that say THIS refresh token is dead (spent, expired,
#: revoked), as opposed to a refusal of the client or the request. Only these
#: retire a pre-fingerprint chain in favour of the `.env` login: spending the
#: bootstrap on any other refusal would replay a token that may be spent.
DEAD_TOKEN_CODES: Final = frozenset({
    "invalid_grant", "refresh_token_reused", "refresh_token_expired",
    "refresh_token_invalidated",
})


@dataclass(frozen=True)
class LaneState:
    """One link of the chain, as the state file holds it."""

    access_token: str
    refresh_token: str
    bootstrap_sha256: str
    expires_at: float  # epoch seconds; 0 = unknown
    refreshed_at: float  # epoch seconds of the refresh that minted it; 0 = unknown


@dataclass(frozen=True)
class Exchange:
    """What one refresh grant answered."""

    access_token: str
    refresh_token: str  # the rotated token, or the one spent if none came back
    expires_at: float


@dataclass(frozen=True)
class Refusal:
    """A refresh that did not happen: the reason (R-27's vocabulary for the
    adapter) and a detail for the log (an HTTP status and the issuer's error
    code, never a token)."""

    reason: str
    detail: str = ""
    code: str = ""  # the issuer's error code, when it sent one


@dataclass(frozen=True)
class LaneEvent:
    """A refresh outcome, for the metrics observer. Never carries a token."""

    lane: str
    ok: bool
    reason: str
    refreshed_at: float
    expires_at: float


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _release(fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


class RotatingOAuthLane:
    """The chain, the locks and the state file. Subclasses set `lane` and
    `skew_seconds` and implement `_exchange`."""

    lane: str = ""
    terminal_reasons: frozenset[str] = TERMINAL_REASONS
    #: Refresh this long before the access token expires, so a call never
    #: carries a token that expires mid-flight.
    skew_seconds: float = 3600.0

    def __init__(
        self,
        *,
        bootstrap_refresh_token: str,
        state_file: Path,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
        observer: Callable[[LaneEvent], None] | None = None,
    ) -> None:
        if not bootstrap_refresh_token:
            raise ValueError("a bootstrap refresh token is required")
        self._bootstrap = bootstrap_refresh_token
        self._bootstrap_sha = _sha256(bootstrap_refresh_token)
        self._state_file = Path(state_file)
        self._lock_file = self._state_file.with_suffix(self._state_file.suffix + ".lock")
        self._timeout = timeout_seconds
        self._clock = clock or time.time
        self._observer = observer
        self._lock = asyncio.Lock()
        self._cached: LaneState | None = None
        # True while `_cached` holds a rotation the state file does not: the
        # write failed. The cached token is then the only live one, so it is
        # what the next refresh spends, and every call retries the write.
        self._unsaved = False
        # Shielded refreshes still running after their caller was cancelled
        # (a strong reference, or the loop may collect them mid-flight).
        self._inflight: set[asyncio.Future[LaneState | str]] = set()

    def set_observer(self, observer: Callable[[LaneEvent], None] | None) -> None:
        self._observer = observer

    # -- what a subclass supplies -------------------------------------------

    async def _exchange(self, refresh_token: str) -> Exchange | Refusal:
        """Spend `refresh_token` at the issuer."""
        raise NotImplementedError

    def _expiry_of(self, access_token: str) -> float:
        """The access token's expiry when the state file does not say."""
        return 0.0

    def _issued_of(self, access_token: str) -> float:
        """When the access token was minted, when the state file does not say."""
        return 0.0

    # -- state file ---------------------------------------------------------

    def _load(self) -> LaneState | None:
        """The state file, if it belongs to the CURRENT `.env` login."""
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            access = str(data.get("access_token") or "")
            # Absent (not empty): a pre-fingerprint file, adopted as LEGACY.
            fingerprint = str(data["bootstrap_sha256"]) if "bootstrap_sha256" in data else ""
            state = LaneState(
                access_token=access,
                refresh_token=str(data["refresh_token"]),
                bootstrap_sha256=fingerprint,
                expires_at=float(data.get("expires_at") or self._expiry_of(access)),
                refreshed_at=float(data.get("refreshed_at") or self._issued_of(access)),
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        if not state.refresh_token or state.bootstrap_sha256 not in (self._bootstrap_sha, ""):
            return None
        return state

    def _save(self, state: LaneState) -> bool:
        """Atomic, durable, never world-readable: the tmp file is created
        0600 (not chmod-ed after the token is in it) and fsync-ed before the
        rename."""
        payload = json.dumps(
            {
                "access_token": state.access_token,
                "refresh_token": state.refresh_token,
                "bootstrap_sha256": state.bootstrap_sha256,
                "expires_at": state.expires_at,
                "refreshed_at": state.refreshed_at,
            },
            sort_keys=True,
        ).encode("utf-8")
        tmp = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)  # an older tmp keeps its mode through O_CREAT
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._state_file)
        except OSError:
            self._unsaved = True
            log.error(
                "%s: could not write the rotated refresh token to %s; it is held "
                "only in memory, every call retries the write, and a restart "
                "before one succeeds needs a new login",
                self.lane, self._state_file,
            )
            return False
        self._unsaved = False
        return True

    def _writable(self) -> bool:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return os.access(self._state_file.parent, os.W_OK)

    def _fresh(self, state: LaneState | None) -> bool:
        return (
            state is not None
            and bool(state.access_token)
            and state.expires_at > self._clock() + self.skew_seconds
        )

    def _usable(self, state: LaneState | None, rejected: str, since: float | None) -> bool:
        """`state` answers this request without a refresh: fresh, not the
        token the caller just had refused, and (for the keeper) minted after
        the refresh the keeper planned from."""
        return (
            state is not None
            and state.access_token != rejected
            and self._fresh(state)
            and (since is None or state.refreshed_at > since)
        )

    def snapshot(self) -> LaneState | None:
        """The current link without a refresh or a lock: the cache, else the
        state file. For the keeper's plan and the boot-time gauges."""
        return self._cached or self._load()

    # -- access -------------------------------------------------------------

    async def _acquire(
        self, *, rejected: str = "", since: float | None = None,
    ) -> LaneState | str:
        """A usable link, refreshing when the access token is near expiry,
        when the caller reports `rejected` (the token a 401 just refused), or
        when nothing has been minted after `since` (the keeper's interval).

        Single-flight in the process (asyncio lock) and across processes on
        the host (flock), re-reading the state file under both: if another
        process already refreshed, its link is adopted and no second refresh
        spends the (now dead) refresh token.
        """
        if not self._unsaved and self._usable(self._cached, rejected, since):
            assert self._cached is not None  # noqa: S101 — narrowed by _usable
            return self._cached
        # Shielded: a caller cancelled mid-refresh (a turn timing out) must
        # not abandon a refresh the issuer has already rotated, or the new
        # token is lost and the old one is dead.
        task = asyncio.ensure_future(self._locked(rejected, since))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return await asyncio.shield(task)

    async def refresh(self, *, unless_refreshed_since: float) -> LaneState | str:
        """The keeper's refresh: spend the chain now, unless a link minted
        after `unless_refreshed_since` already exists (a call or another
        process refreshed since the keeper planned), which is adopted."""
        return await self._acquire(since=unless_refreshed_since)

    async def _locked(self, rejected: str, since: float | None) -> LaneState | str:
        async with self._lock:
            cached = self._cached
            if self._usable(cached, rejected, since):
                assert cached is not None  # noqa: S101 — narrowed by _usable
                # Retry a write that failed. No flock needed: while our
                # rotation is unsaved no other process can hold a newer
                # chain (it would need the token only we have).
                if self._unsaved and self._save(cached):
                    self._observe(cached)  # clears the refused gauge
                return cached
            if not self._writable():
                return self._failed(
                    "state_unwritable", f"{self._state_file.parent} is not writable",
                )
            try:
                fd = os.open(self._lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                return self._failed("state_unwritable", f"could not open {self._lock_file}")
            waiter_owns_fd = False
            try:
                waiter = asyncio.ensure_future(
                    asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX)
                )
                try:
                    await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    # The thread may still be blocked in flock on this fd:
                    # closing it now would hand a reused descriptor to that
                    # thread. Release it when the thread returns instead.
                    waiter_owns_fd = True
                    waiter.add_done_callback(lambda _done: _release(fd))
                    raise
                except OSError:
                    return self._failed("state_unwritable", f"could not lock {self._lock_file}")
                if self._unsaved and cached is not None:
                    # Our rotation never reached the file, so the file's
                    # chain is older (dead): spend ours; the refresh's own
                    # save retries the write.
                    return await self._spend(cached.refresh_token)
                state = self._load()
                if self._usable(state, rejected, since):
                    assert state is not None  # noqa: S101 — narrowed by _usable
                    self._cached = state
                    self._observe(state)
                    return state
                if state is not None:
                    chain = state
                elif cached is not None:
                    chain = cached  # the file vanished; ours is current
                else:
                    return await self._spend(self._bootstrap)
                if chain.bootstrap_sha256 or chain.refresh_token == self._bootstrap:
                    return await self._spend(chain.refresh_token)
                # A pre-fingerprint chain: try it, and only if the issuer says
                # THIS token is dead is it an older login's, with the `.env`
                # login the live one.
                tried = await self._try(chain.refresh_token)
                if not isinstance(tried, Refusal):
                    return tried
                if tried.code not in DEAD_TOKEN_CODES:
                    return self._failed(tried.reason, tried.detail or "no detail")
                log.warning(
                    "%s: the unfingerprinted state file's token was refused (%s); "
                    "spending the .env login instead", self.lane, tried.detail,
                )
                return await self._spend(self._bootstrap)
            finally:
                if not waiter_owns_fd:
                    _release(fd)

    async def _spend(self, refresh_token: str) -> LaneState | str:
        """One refresh grant; a refusal is reported (logged, observed)."""
        outcome = await self._try(refresh_token)
        if isinstance(outcome, Refusal):
            return self._failed(outcome.reason, outcome.detail or "no detail")
        return outcome

    async def _try(self, refresh_token: str) -> LaneState | Refusal:
        """One refresh grant; a refusal is returned, not yet reported."""
        outcome = await self._exchange(refresh_token)
        if isinstance(outcome, Refusal):
            return outcome
        state = LaneState(
            access_token=outcome.access_token,
            refresh_token=outcome.refresh_token or refresh_token,
            bootstrap_sha256=self._bootstrap_sha,
            expires_at=outcome.expires_at,
            refreshed_at=self._clock(),
        )
        self._cached = state
        saved = self._save(state)  # a failure sets `_unsaved`; the next call retries
        self._observe(state)
        if not saved:
            # Refreshed, but the rotation lives only in memory: a restart now
            # loses the chain. The refused gauge says so before that happens.
            self._emit_failure("state_unwritable")
        return state

    # -- loud failure -------------------------------------------------------

    def _failed(self, reason: str, detail: str) -> str:
        """Log every failure with its lane and reason, never a token. A
        terminal one is an ERROR: it does not heal without the operator."""
        if reason in self.terminal_reasons:
            log.error(
                "%s: refresh failed (%s): %s; image generation on this lane is "
                "unavailable until the operator acts (a new login, or a writable "
                "state dir)",
                self.lane, reason, detail,
            )
        else:
            log.warning("%s: refresh failed (%s): %s; will retry", self.lane, reason, detail)
        self._emit_failure(reason)
        return reason

    def _emit_failure(self, reason: str) -> None:
        if self._observer is not None:
            with contextlib.suppress(Exception):
                self._observer(LaneEvent(self.lane, False, reason, 0.0, 0.0))

    def _observe(self, state: LaneState) -> None:
        if self._observer is not None:
            with contextlib.suppress(Exception):
                self._observer(
                    LaneEvent(self.lane, True, "", state.refreshed_at, state.expires_at)
                )
