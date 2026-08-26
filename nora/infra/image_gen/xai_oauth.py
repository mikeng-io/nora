"""xAI OAuth device flow + refresh, for Nora's OWN X Premium subscription lane.

DEC-137 / design/proposed/29: Nora holds her own xAI OAuth tokens so image
generation runs on the subscription entitlement (quota-bounded, not metered
per-call billing) without borrowing Hermes' auth store or a host credential
proxy.

Credential split (operator's `.env` rule, review F4):

- `.env` (host, immutable, DEC-8) holds ONLY the long-lived refresh token
  (`XAI_OAUTH_REFRESH_TOKEN`). One secret, one place.
- `./state/xai-oauth.json` (container-writable, same repo-tree state pattern
  as `./state/signal-cli`) holds the short-lived access token + its expiry.
  Boot precedence: read the state file; ONLY if absent/corrupt, derive an
  initial access token by refreshing with the `.env` refresh token. The
  access token never lives in `.env` — no two-source-of-truth restart race.

Endpoint + scope + skew constants mirror Hermes' `hermes_cli/auth.py`
(`XAI_OAUTH_CLIENT_ID`, `XAI_OAUTH_SCOPE`, `XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS`)
— verified live: Miles's `hermes auth add xai-oauth` login succeeded on this
host with this exact scope set, so `grok-cli:access` is a real, accepted
scope here regardless of what third-party docs say.

NEVER raises into the caller: every failure is an `OAuthState` with
`ok=False` and a `reason` the adapter can map (fail-closed, R-27).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiohttp

log = logging.getLogger("nora.image_gen.xai_oauth")

XAI_OAUTH_ISSUER = "https://auth.x.ai"
# Hermes' device-flow client (hermes_cli/auth.py) — verified live.
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_DEVICE_CODE_URL = f"{XAI_OAUTH_ISSUER}/oauth2/device/code"
XAI_OAUTH_DEVICE_TOKEN_URL = f"{XAI_OAUTH_ISSUER}/oauth2/device/token"
XAI_OAUTH_TOKEN_URL = f"{XAI_OAUTH_ISSUER}/oauth2/token"
# xAI access tokens are short-lived (~6h); refresh up to an hour early so an
# idle gateway (30 min between calls) never hits an expired token (Hermes'
# own constant).
XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 3600


class XaiOAuthFlowError(Exception):
    """An operator-diagnosable device-flow failure (refused, expired, or the
    token was never issued). Raised only by `device_flow_login` — the CLI
    catches it and prints a sentence, never a traceback."""


async def device_flow_login(
    *,
    client_id: str = XAI_OAUTH_CLIENT_ID,
    scope: str = XAI_OAUTH_SCOPE,
    timeout_seconds: float = 30.0,
) -> str:
    """Run the xAI OAuth device flow and return the long-lived REFRESH token.

    The one interactive command in the operator plane (`nora xai-login`):
    prints the verification URL + code, polls until the operator approves in
    the browser, and returns the refresh token to be written to the host
    `.env`. Fails with `XaiOAuthFlowError` — never a bare raise.
    """
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1. Request a device code.
        try:
            async with session.post(
                XAI_OAUTH_DEVICE_CODE_URL,
                data={"client_id": client_id, "scope": scope},
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise XaiOAuthFlowError(
                "could not reach xAI's device-code endpoint — is the host online?"
            ) from exc
        if resp.status != 200:
            raise XaiOAuthFlowError(
                f"xAI refused the device-code request (HTTP {resp.status})"
            )
        device_code = str(body.get("device_code") or "")
        user_code = str(body.get("user_code") or "")
        verification_uri = str(body.get("verification_uri") or XAI_OAUTH_ISSUER)
        if not device_code or not user_code:
            raise XaiOAuthFlowError("xAI returned no device code")

        interval = float(body.get("interval") or 5)
        deadline = time.time() + float(body.get("expires_in") or 900)

        # 2. Tell the operator where to go.
        print()
        print(f"1. Open  {verification_uri}")
        print(f"2. Enter code:  {user_code}")
        print("3. Approve access in the browser, then wait here.")
        print()

        # 3. Poll until the user approves (or the code expires).
        while time.time() < deadline:
            await asyncio.sleep(max(interval, 1.0))
            try:
                async with session.post(
                    XAI_OAUTH_DEVICE_TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": client_id,
                    },
                ) as resp:
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise XaiOAuthFlowError(
                    "lost contact with xAI while waiting for approval"
                ) from exc
            if resp.status == 200:
                refresh = body.get("refresh_token")
                if not isinstance(refresh, str) or not refresh:
                    raise XaiOAuthFlowError(
                        "xAI approved the login but issued no refresh token — "
                        "the scopes may lack offline_access"
                    )
                return refresh
            error = str(body.get("error") or "")
            if error in ("authorization_pending", "slow_down"):
                if error == "slow_down":
                    interval += 5.0
                continue
            if error in ("access_denied", "expired_token"):
                raise XaiOAuthFlowError(
                    "login cancelled or the device code expired — run again"
                )
            if resp.status == 429:
                raise XaiOAuthFlowError("xAI rate-limited the login — wait a bit")
        raise XaiOAuthFlowError(
            "the device code expired before approval — run again"
        )


@dataclass(frozen=True)
class OAuthState:
    """The adapter's view of the xAI OAuth lane. Fail-closed: a wiring gap is
    `status="unavailable"` with a `reason`, never a raise (R-27)."""

    status: Literal["ok", "unavailable"]
    access_token: str = ""
    reason: str = ""
    expires_in: float = 0.0


@dataclass
class _TokenState:
    access_token: str
    expires_at: float  # epoch seconds; 0 = unknown


class XaiOAuth:
    """Device flow + proactive refresh for the xAI subscription lane.

    Constructed by `nora.infra.image_gen.xai_adapter.XaiImageGenerator`,
    which owns the refresh token (resolved by NAME from config/env — DEC-8)
    and the state-file path.
    """

    def __init__(
        self,
        *,
        refresh_token: str,
        state_file: Path,
        client_id: str = XAI_OAUTH_CLIENT_ID,
        scope: str = XAI_OAUTH_SCOPE,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not refresh_token:
            raise ValueError("XAI_OAUTH_REFRESH_TOKEN must be set")
        self._refresh_token = refresh_token
        self._state_file = Path(state_file)
        self._client_id = client_id
        self._scope = scope
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._clock = clock or time.time
        self._lock = asyncio.Lock()
        self._cached: _TokenState | None = None

    # -- state file ---------------------------------------------------------

    def _load_state(self) -> _TokenState | None:
        try:
            raw = self._state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            token = str(data["access_token"])
            expires = float(data.get("expires_at") or 0)
            if not token:
                return None
            return _TokenState(access_token=token, expires_at=expires)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_state(self, token: str, expires_at: float) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(
                    {"access_token": token, "expires_at": expires_at},
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        except OSError:
            log.warning("xai_oauth: could not persist access token state file")

    # -- access -------------------------------------------------------------

    async def access_token(self) -> OAuthState:
        """Return a valid access token, refreshing proactively when needed.

        Single-flight: concurrent callers share one refresh. Fail-closed: a
        dead refresh token / Cloudflare challenge / network failure returns
        `OAuthState("unavailable", reason=...)` — never a raise and never a
        stale token.
        """
        async with self._lock:
            cached = self._cached
            now = self._clock()
            fresh = (
                cached is not None
                and cached.expires_at > now + XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS
            )
            if fresh and cached is not None:
                return OAuthState(status="ok", access_token=cached.access_token)

            # Boot precedence: state file first; only absent/corrupt → derive
            # from the .env refresh token.
            if cached is None:
                state = self._load_state()
                if (
                    state is not None
                    and state.expires_at > now + XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS
                ):
                    self._cached = state
                    return OAuthState(status="ok", access_token=state.access_token)

            result = await self._refresh()
            if result.status == "ok":
                self._cached = _TokenState(
                    access_token=result.access_token, expires_at=self._clock() + result.expires_in
                )
                self._save_state(result.access_token, self._clock() + result.expires_in)
            return result

    async def _refresh(self) -> OAuthState:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout.total)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    XAI_OAUTH_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self._client_id,
                    },
                ) as resp,
            ):
                body = await resp.json(content_type=None)
            if resp.status == 200:
                token = body.get("access_token")
                if not isinstance(token, str) or not token:
                    return OAuthState("unavailable", reason="auth")
                expires_in = body.get("expires_in") or 0
                return OAuthState("ok", access_token=token, reason="", expires_in=float(expires_in))
            # Cloudflare challenge: 403 while the token is still valid —
            # RETRYABLE, not terminal auth (design §4.2).
            if resp.status == 403:
                return OAuthState("unavailable", reason="cloudflare")
            if resp.status in (400, 401):
                return OAuthState("unavailable", reason="auth")
            if resp.status == 429:
                return OAuthState("unavailable", reason="rate_limited")
            return OAuthState("unavailable", reason="down")
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return OAuthState("unavailable", reason="down")
