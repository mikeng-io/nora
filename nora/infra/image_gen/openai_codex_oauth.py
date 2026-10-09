"""OpenAI (ChatGPT subscription) OAuth for the Codex lane: device login + refresh.

DEC-304 / design/proposed/29 §11: the second `ImageGenerator` provider runs on
Nora's OWN ChatGPT login, the way the `codex` CLI does, never on a shared
`~/.codex/auth.json`.

**Why the refresh token lives in the state file, not only in `.env`.**
OpenAI's refresh grant ROTATES the refresh token on every use, and a used one
is dead. xAI's lane (`xai_oauth.py`) can treat `.env` as the long-lived
source; this one cannot. So:

- `.env` (host, immutable, DEC-8) holds ONLY the bootstrap refresh token the
  login wrote (`OPENAI_CODEX_REFRESH_TOKEN`, named in config). It is spent by
  the first refresh and never sent again.
- `./state/openai-codex-oauth.json` (container-writable) holds the current
  access token, the CURRENT refresh token, and the sha256 of the bootstrap
  token that chain grew from. A state file whose fingerprint does not match
  `.env` belongs to an older login and is ignored: a fresh `nora
  openai-codex-login` takes over without anyone deleting a file.
- A refresh is REFUSED when the state file cannot be written: the rotated
  token would be lost with the process, and the chain with it.
- Refreshes serialise across processes on one host (an `flock` beside the
  state file), and a process re-reads the file under the lock before it
  refreshes: two processes holding one rotating token is how one logs the
  other out.

Endpoints and the client id are the `codex` CLI's own (read from the 0.161.0
binary 2026-10-09: `/api/accounts/deviceauth/usercode`, `/deviceauth/token`,
`/codex/device`, `/deviceauth/callback`, `/oauth/token`, form-encoded
`grant_type=refresh_token`). Step 1 of the device flow was probed live and
answered `{device_auth_id, user_code, interval: "5", expires_at}`; a pending
poll answers 403 `deviceauth_authorization_pending`. The approval, the code
exchange and the refresh have NOT been run against the live issuer.

NEVER raises into the caller: every failure is a `CodexAuth` with
`status="unavailable"` and a `reason` (R-27). Only `device_flow_login` raises,
and only `CodexLoginError`, which the CLI turns into a sentence.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiohttp

log = logging.getLogger("nora.image_gen.openai_codex_oauth")

OPENAI_AUTH_ISSUER = "https://auth.openai.com"
# The `codex` CLI's public OAuth client (CODEX_APP_SERVER_LOGIN_CLIENT_ID).
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# ChatGPT access tokens live ~10 days (measured 2026-10-09: exp - iat = 240h);
# refresh an hour early so a call never carries a token that expires mid-flight.
ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 3600
# The claim the access token carries the ChatGPT account id under; the
# backend wants it back as the `chatgpt-account-id` header.
_AUTH_CLAIM = "https://api.openai.com/auth"


class CodexLoginError(Exception):
    """An operator-diagnosable login failure. Raised only by
    `device_flow_login` / `refresh_token_from_codex_auth`."""


@dataclass(frozen=True)
class CodexAuth:
    """The adapter's view of the lane. `account_id` rides with the token
    because the backend wants both on every request."""

    status: Literal["ok", "unavailable"]
    access_token: str = ""
    account_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class _State:
    access_token: str
    refresh_token: str
    bootstrap_sha256: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def jwt_claims(token: str) -> dict[str, object]:
    """The payload of a JWT, unverified: the issuer verifies it, this only
    reads its own token's expiry and account id. `{}` for anything else."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _expires_at(access_token: str) -> float:
    exp = jwt_claims(access_token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else 0.0


def account_id_of(access_token: str) -> str:
    auth = jwt_claims(access_token).get(_AUTH_CLAIM)
    if isinstance(auth, dict):
        value = auth.get("chatgpt_account_id")
        if isinstance(value, str):
            return value
    return ""


def _release(fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


class OpenAICodexOAuth:
    """Refresh lane for Nora's own ChatGPT login (see the module docstring)."""

    def __init__(
        self,
        *,
        bootstrap_refresh_token: str,
        state_file: Path,
        client_id: str = OPENAI_CODEX_CLIENT_ID,
        issuer: str = OPENAI_AUTH_ISSUER,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not bootstrap_refresh_token:
            raise ValueError("a bootstrap refresh token is required")
        self._bootstrap = bootstrap_refresh_token
        self._bootstrap_sha = _sha256(bootstrap_refresh_token)
        self._state_file = Path(state_file)
        self._lock_file = self._state_file.with_suffix(self._state_file.suffix + ".lock")
        self._client_id = client_id
        self._token_url = f"{issuer.rstrip('/')}/oauth/token"
        self._timeout = timeout_seconds
        self._clock = clock or time.time
        self._lock = asyncio.Lock()
        self._cached: _State | None = None
        # True while `_cached` holds a rotation the state file does not: the
        # write failed. The cached token is then the only live one, so it is
        # what the next refresh spends, and every call retries the write.
        self._unsaved = False
        # Shielded refreshes still running after their caller was cancelled
        # (a strong reference, or the loop may collect them mid-flight).
        self._inflight: set[asyncio.Future[CodexAuth]] = set()

    # -- state file ---------------------------------------------------------

    def _load(self) -> _State | None:
        """The state file, if it belongs to the CURRENT `.env` login."""
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            state = _State(
                access_token=str(data.get("access_token") or ""),
                refresh_token=str(data["refresh_token"]),
                bootstrap_sha256=str(data["bootstrap_sha256"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not state.refresh_token or state.bootstrap_sha256 != self._bootstrap_sha:
            return None
        return state

    def _save(self, state: _State) -> bool:
        """Atomic, durable, never world-readable: the tmp file is created
        0600 (not chmod-ed after the token is in it) and fsync-ed before the
        rename."""
        payload = json.dumps(
            {
                "access_token": state.access_token,
                "refresh_token": state.refresh_token,
                "bootstrap_sha256": state.bootstrap_sha256,
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
                "openai_codex_oauth: could not write the rotated refresh token to %s; "
                "it is held only in memory, every call retries the write, and a "
                "restart before one succeeds needs a new login",
                self._state_file,
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

    def _fresh(self, state: _State | None) -> bool:
        return (
            state is not None
            and bool(state.access_token)
            and _expires_at(state.access_token)
            > self._clock() + ACCESS_TOKEN_REFRESH_SKEW_SECONDS
        )

    def _ok(self, state: _State) -> CodexAuth:
        account = account_id_of(state.access_token)
        if not account:
            return CodexAuth("unavailable", reason="auth")
        return CodexAuth("ok", access_token=state.access_token, account_id=account)

    # -- access -------------------------------------------------------------

    async def access_token(self, *, rejected: str = "") -> CodexAuth:
        """A usable access token, refreshing when it is near expiry or when
        the caller reports `rejected` (the token a 401 just refused).

        Single-flight in the process (asyncio lock) and across processes on
        the host (flock), re-reading the state file under both: if another
        process already refreshed, its token is adopted and no second
        refresh spends the (now dead) refresh token.
        """
        cached = self._cached
        if (
            not self._unsaved
            and cached is not None
            and cached.access_token != rejected
            and self._fresh(cached)
        ):
            return self._ok(cached)
        # Shielded: a caller cancelled mid-refresh (a turn timing out) must
        # not abandon a refresh the issuer has already rotated, or the new
        # token is lost and the old one is dead.
        task = asyncio.ensure_future(self._locked_access(rejected))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return await asyncio.shield(task)

    async def _locked_access(self, rejected: str) -> CodexAuth:
        async with self._lock:
            cached = self._cached
            if cached is not None and cached.access_token != rejected and self._fresh(cached):
                if self._unsaved:
                    # Retry the write that failed. No flock needed: while our
                    # rotation is unsaved no other process can hold a newer
                    # chain (it would need the token only we have).
                    self._save(cached)
                return self._ok(cached)
            if not self._writable():
                log.error(
                    "openai_codex_oauth: %s is not writable; refusing to refresh "
                    "(the rotated token would be lost)",
                    self._state_file.parent,
                )
                return CodexAuth("unavailable", reason="state_unwritable")
            try:
                fd = os.open(self._lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                return CodexAuth("unavailable", reason="state_unwritable")
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
                    log.error("openai_codex_oauth: could not lock %s", self._lock_file)
                    return CodexAuth("unavailable", reason="state_unwritable")
                if self._unsaved and cached is not None:
                    # Our rotation never reached the file, so the file's
                    # chain is older (dead): spend ours; the refresh's own
                    # save retries the write.
                    return await self._refresh(cached.refresh_token)
                state = self._load()
                if state is not None and state.access_token != rejected and self._fresh(state):
                    self._cached = state
                    return self._ok(state)
                if state is not None:
                    spend = state.refresh_token
                elif cached is not None:
                    spend = cached.refresh_token  # the file vanished; ours is current
                else:
                    spend = self._bootstrap
                return await self._refresh(spend)
            finally:
                if not waiter_owns_fd:
                    _release(fd)

    async def _refresh(self, refresh_token: str) -> CodexAuth:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self._client_id,
                    },
                ) as resp,
            ):
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
        except (aiohttp.ClientError, TimeoutError):
            return CodexAuth("unavailable", reason="down")
        if status == 200 and isinstance(body, dict):
            access = body.get("access_token")
            if not isinstance(access, str) or not access:
                return CodexAuth("unavailable", reason="auth")
            rotated = body.get("refresh_token")
            state = _State(
                access_token=access,
                refresh_token=rotated if isinstance(rotated, str) and rotated else refresh_token,
                bootstrap_sha256=self._bootstrap_sha,
            )
            self._cached = state
            self._save(state)  # a failure sets `_unsaved`; the next call retries
            return self._ok(state)
        if status in (400, 401, 403):
            code = ""
            if isinstance(body, dict):
                err = body.get("error")
                code = str(err.get("code") if isinstance(err, dict) else err or "")
            log.warning("openai_codex_oauth: refresh refused (HTTP %s %s)", status, code)
            return CodexAuth("unavailable", reason="auth")
        if status == 429:
            return CodexAuth("unavailable", reason="rate_limited")
        return CodexAuth("unavailable", reason="down")


# -- operator login ---------------------------------------------------------


@dataclass(frozen=True)
class CodexTokens:
    access_token: str
    refresh_token: str


async def device_flow_login(
    *,
    issuer: str = OPENAI_AUTH_ISSUER,
    client_id: str = OPENAI_CODEX_CLIENT_ID,
    timeout_seconds: float = 30.0,
    max_wait_seconds: float = 900.0,
    poll_floor_seconds: float = 1.0,
) -> CodexTokens:
    """The `codex login --device-auth` flow, for Nora's own login.

    Prints a URL and a one-time code, polls until the operator approves in a
    browser signed in to Nora's ChatGPT account, then exchanges the
    authorization code (PKCE, the verifier comes back from the poll) for
    tokens. Raises `CodexLoginError` only.
    """
    base = issuer.rstrip("/")
    accounts = f"{base}/api/accounts"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{accounts}/deviceauth/usercode", json={"client_id": client_id},
            ) as resp:
                status = resp.status
                body = await resp.json(content_type=None)
            if status != 200 or not isinstance(body, dict):
                raise CodexLoginError(f"OpenAI refused the device-code request (HTTP {status})")
            device_auth_id = str(body.get("device_auth_id") or "")
            user_code = str(body.get("user_code") or body.get("usercode") or "")
            if not device_auth_id or not user_code:
                raise CodexLoginError("OpenAI returned no device code")
            try:
                interval = float(body.get("interval") or 5)
            except (TypeError, ValueError):
                interval = 5.0

            print()
            print(f"1. Open  {base}/codex/device  in a browser signed in to NORA's ChatGPT account")
            print(f"2. Enter code:  {user_code}   (expires in 15 minutes)")
            print("3. Approve, then wait here.")
            print()

            deadline = time.monotonic() + max_wait_seconds
            grant: dict[str, object] | None = None
            while time.monotonic() < deadline:
                await asyncio.sleep(max(interval, poll_floor_seconds))
                async with session.post(
                    f"{accounts}/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                ) as resp:
                    status = resp.status
                    try:
                        polled = await resp.json(content_type=None)
                    except ValueError:
                        polled = None
                if status == 200 and isinstance(polled, dict):
                    grant = polled
                    break
                if status in (403, 404):
                    continue  # deviceauth_authorization_pending
                raise CodexLoginError(f"device login failed (HTTP {status})")
            if grant is None:
                raise CodexLoginError("the device code expired before approval; run again")

            code = str(grant.get("authorization_code") or "")
            verifier = str(grant.get("code_verifier") or "")
            if not code or not verifier:
                raise CodexLoginError("OpenAI approved the login but sent no authorization code")
            async with session.post(
                f"{base}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{base}/deviceauth/callback",
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            ) as resp:
                status = resp.status
                tokens = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise CodexLoginError("lost contact with auth.openai.com during the login") from exc
    if status != 200 or not isinstance(tokens, dict):
        raise CodexLoginError(f"the code exchange failed (HTTP {status})")
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise CodexLoginError("the code exchange issued no refresh token")
    return CodexTokens(access_token=access, refresh_token=refresh)


def _codex_refresh_token(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        refresh = data["tokens"]["refresh_token"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return refresh if isinstance(refresh, str) else ""


def refresh_token_from_codex_auth(path: Path) -> str:
    """The refresh token in a `codex` `auth.json` made by a DEDICATED login
    (`CODEX_HOME=<new dir> codex login --device-auth`, the prefix form, not
    an exported `CODEX_HOME`).

    Refuses the operator's own codex login, `~/.codex/auth.json` and
    `$CODEX_HOME/auth.json`, by path AND by value (a copy of the file is the
    same login): sharing one rotating refresh token means whichever side
    refreshes first logs the other out. Values are compared, never logged.
    """
    resolved = path.expanduser().resolve()
    own = [Path.home() / ".codex" / "auth.json"]
    if os.environ.get("CODEX_HOME"):
        own.append(Path(os.environ["CODEX_HOME"]).expanduser() / "auth.json")
    refusal = (
        "that login is your codex CLI's, and a shared rotating refresh token logs "
        "one side out. Make a dedicated one with "
        "`CODEX_HOME=$(mktemp -d) codex login --device-auth` (prefix, not export)."
    )
    if any(resolved == candidate.resolve() for candidate in own):
        raise CodexLoginError(f"refusing {resolved}: {refusal}")
    refresh = _codex_refresh_token(resolved)
    if not refresh:
        raise CodexLoginError(f"{resolved} holds no codex refresh token")
    for candidate in own:
        theirs = _codex_refresh_token(candidate)
        if theirs and hmac.compare_digest(theirs.encode(), refresh.encode()):
            raise CodexLoginError(f"refusing {resolved}: it is a copy of {candidate}; {refusal}")
    return refresh
