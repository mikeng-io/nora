"""OpenAI (ChatGPT subscription) OAuth for the Codex lane: device login + refresh.

DEC-304: the second `ImageGenerator` provider runs on
Nora's OWN ChatGPT login, the way the `codex` CLI does, never on a shared
`~/.codex/auth.json`.

**Why the refresh token lives in the state file, not only in `.env`.**
OpenAI's refresh grant ROTATES the refresh token on every use, and a used one
is dead; xAI's does the same (DEC-308), so both lanes keep the chain
through `oauth_lane.RotatingOAuthLane`:

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
- The daemon refreshes it on a schedule (`nora.app.oauth_keepalive`),
  through the same locks as a call, so a use never meets an expired login.

Endpoints and the client id are the `codex` CLI's own (`/api/accounts/deviceauth/usercode`,
`/deviceauth/token`, `/codex/device`, `/deviceauth/callback`, `/oauth/token`, form-encoded
`grant_type=refresh_token`). Step 1 of the device flow answers
`{device_auth_id, user_code, interval: "5", expires_at}`; a pending poll
answers 403 `deviceauth_authorization_pending`.

NEVER raises into the caller: every failure is a `CodexAuth` with
`status="unavailable"` and a `reason` (R-27). Only `device_flow_login` raises,
and only `CodexLoginError`, which the CLI turns into a sentence.
"""

from __future__ import annotations

import asyncio
import base64
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

from .oauth_lane import (
    DEAD_TOKEN_CODES,
    Exchange,
    LaneEvent,
    LaneState,
    Refusal,
    RotatingOAuthLane,
)

log = logging.getLogger("nora.image_gen.openai_codex_oauth")

OPENAI_AUTH_ISSUER = "https://auth.openai.com"
# The `codex` CLI's public OAuth client (CODEX_APP_SERVER_LOGIN_CLIENT_ID).
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# ChatGPT access tokens live ~10 days (exp - iat = 240h);
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


class OpenAICodexOAuth(RotatingOAuthLane):
    """Refresh lane for Nora's own ChatGPT login (see the module docstring;
    the chain, the locks and the state file are `oauth_lane`'s)."""

    lane = "openai_codex"
    skew_seconds = ACCESS_TOKEN_REFRESH_SKEW_SECONDS

    def __init__(
        self,
        *,
        bootstrap_refresh_token: str,
        state_file: Path,
        client_id: str = OPENAI_CODEX_CLIENT_ID,
        issuer: str = OPENAI_AUTH_ISSUER,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
        observer: Callable[[LaneEvent], None] | None = None,
    ) -> None:
        super().__init__(
            bootstrap_refresh_token=bootstrap_refresh_token,
            state_file=state_file,
            timeout_seconds=timeout_seconds,
            clock=clock,
            observer=observer,
        )
        self._client_id = client_id
        self._token_url = f"{issuer.rstrip('/')}/oauth/token"

    def _expiry_of(self, access_token: str) -> float:
        return _expires_at(access_token)

    def _issued_of(self, access_token: str) -> float:
        iat = jwt_claims(access_token).get("iat")
        return float(iat) if isinstance(iat, (int, float)) else 0.0

    def _ok(self, state: LaneState) -> CodexAuth:
        account = account_id_of(state.access_token)
        if not account:
            return CodexAuth("unavailable", reason="auth")
        return CodexAuth("ok", access_token=state.access_token, account_id=account)

    # -- access -------------------------------------------------------------

    async def access_token(self, *, rejected: str = "") -> CodexAuth:
        """A usable access token, refreshing when it is near expiry or when
        the caller reports `rejected` (the token a 401 just refused).
        Single-flight in and across processes (`RotatingOAuthLane._acquire`)."""
        outcome = await self._acquire(rejected=rejected)
        if isinstance(outcome, str):
            return CodexAuth("unavailable", reason=outcome)
        return self._ok(outcome)

    async def _exchange(self, refresh_token: str) -> Exchange | Refusal:
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
        except (aiohttp.ClientError, TimeoutError) as exc:
            return Refusal("down", f"{type(exc).__name__} reaching the token endpoint")
        if status == 200 and isinstance(body, dict):
            access = body.get("access_token")
            if not isinstance(access, str) or not access:
                return Refusal("auth", "HTTP 200 without an access token")
            rotated = body.get("refresh_token")
            return Exchange(
                access_token=access,
                refresh_token=rotated if isinstance(rotated, str) and rotated else refresh_token,
                expires_at=_expires_at(access),
            )
        code = ""
        if isinstance(body, dict):
            err = body.get("error")
            code = str(err.get("code") if isinstance(err, dict) else err or "")
        detail = f"HTTP {status} {code}".strip()
        # A 403 is terminal only when the issuer says the token is dead; any
        # other 403 (an edge challenge) heals on retry, and calling it `auth`
        # would page the operator to log in again for nothing.
        if status in (400, 401) or (status == 403 and code in DEAD_TOKEN_CODES):
            return Refusal("auth", detail, code)
        if status == 403:
            return Refusal("down", detail, code)
        if status == 429:
            return Refusal("rate_limited", detail, code)
        return Refusal("down", detail, code)


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
