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
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("nora.image_gen.xai_oauth")

XAI_OAUTH_ISSUER = "https://auth.x.ai"
# Hermes' device-flow client (hermes_cli/auth.py) — verified live.
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_DEVICE_CODE_URL = f"{XAI_OAUTH_ISSUER}/oauth2/device/code"
# The device-flow poll hits the OIDC *token* endpoint, not a bespoke
# /device/token path (RFC 8629 §3.5). Resolve it from discovery instead of
# hardcoding: xAI's token_endpoint is currently /oauth2/token, and the
# constants below mirror Hermes' discovery-driven flow so a provider
# endpoint move is absorbed once at boot, not across three call sites.
XAI_OAUTH_DISCOVERY_URL = f"{XAI_OAUTH_ISSUER}/.well-known/openid-configuration"
XAI_OAUTH_TOKEN_URL = f"{XAI_OAUTH_ISSUER}/oauth2/token"
# xAI access tokens are short-lived (~6h); refresh up to an hour early so an
# idle gateway (30 min between calls) never hits an expired token (Hermes'
# own constant).
XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 3600


class XaiOAuthFlowError(Exception):
    """An operator-diagnosable device-flow failure (refused, expired, or the
    token was never issued). Raised only by `device_flow_login` — the CLI
    catches it and prints a sentence, never a traceback."""


async def _discover_token_endpoint(
    session: aiohttp.ClientSession,
    discovery_url: str,
) -> str:
    """Resolve xAI's OIDC token endpoint (RFC 8629 §3.5: the device-flow poll
    targets the token endpoint, which is NOT a fixed /device/token path)."""
    async with session.get(discovery_url) as resp:
        if resp.status != 200:
            raise XaiOAuthFlowError(
                f"xAI OIDC discovery failed (HTTP {resp.status})"
            )
        try:
            payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as exc:
            raise XaiOAuthFlowError(
                "xAI OIDC discovery returned a non-JSON response"
            ) from exc
    endpoint = str((payload or {}).get("token_endpoint") or "")
    if not _looks_like_oauth_endpoint(endpoint):
        raise XaiOAuthFlowError(
            "xAI OIDC discovery returned no usable token_endpoint"
        )
    return endpoint


def _looks_like_oauth_endpoint(endpoint: str) -> bool:
    """HTTPS-only, except loopback (contract tests run a local TestServer)."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("https", "http"):
        return False
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1")
    return bool(parsed.hostname)


async def device_flow_login(
    *,
    client_id: str = XAI_OAUTH_CLIENT_ID,
    scope: str = XAI_OAUTH_SCOPE,
    timeout_seconds: float = 30.0,
    base_url: str | None = None,
) -> str:
    """Run the xAI OAuth device flow and return the long-lived REFRESH token.

    The one interactive command in the operator plane (`nora xai-login`):
    prints the verification URL + code, polls until the operator approves in
    the browser, and returns the refresh token to be written to the host
    `.env`. Fails with `XaiOAuthFlowError` — never a bare raise.
    """
    issuer = (base_url or XAI_OAUTH_ISSUER).rstrip("/")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    device_code_url = f"{issuer}/oauth2/device/code"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 0. Resolve the real token endpoint from OIDC discovery — the
        # device-flow poll must hit the *token* endpoint, which is not a
        # fixed /device/token path (RFC 8629 §3.5).
        token_endpoint = await _discover_token_endpoint(session, discovery_url)
        # 1. Request a device code.
        try:
            async with session.post(
                device_code_url,
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
                    token_endpoint,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": client_id,
                    },
                ) as resp:
                    # xAI's token endpoint can answer an empty body mid-poll
                    # (aiohttp returns None, not a raise). Treat it as
                    # "not approved yet" — same as authorization_pending —
                    # and keep waiting, but NEVER crash the flow on it.
                    try:
                        body = await resp.json(content_type=None)
                    except (aiohttp.ClientError, ValueError):
                        body = None
                    if body is None:
                        if resp.status == 200:
                            raise XaiOAuthFlowError(
                                "xAI returned an empty success body — "
                                "no refresh token; try again"
                            )
                        body = {}
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
    refresh_token: str | None = None  # rotated refresh token (M3), if any


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
        issuer: str = XAI_OAUTH_ISSUER,
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
        # M5: resolve the token endpoint from OIDC discovery ONCE per process
        # (cached lazily on first refresh; `XAI_OAUTH_TOKEN_URL` is the
        # fallback when discovery is unreachable). The module docstring's
        # "absorbed once at boot, not across three call sites" is only true
        # if the refresh path — the path that runs in production — shares
        # the discovery. Test seam: `issuer` lets contract tests point at a
        # local TestServer the same way `XaiImageGenerator.base_url` does.
        self._issuer = issuer.rstrip("/")
        self._token_endpoint: str | None = None

    def _discovery_url(self) -> str:
        return f"{self._issuer}/.well-known/openid-configuration"

    # -- state file ---------------------------------------------------------

    def _load_state(self) -> _TokenState | None:
        try:
            raw = self._state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            token = str(data["access_token"])
            expires = float(data.get("expires_at") or 0)
            if not token:
                return None
            refresh = data.get("refresh_token")
            return _TokenState(
                access_token=token,
                expires_at=expires,
                refresh_token=str(refresh) if isinstance(refresh, str) and refresh else None,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_state(
        self, token: str, expires_at: float, refresh_token: str | None = None,
    ) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            # M4: atomic write — tmp + os.replace, so a crash mid-write never
            # leaves a corrupt (or half-written) state file. 0o600: the file
            # holds a live access token; the default umask is not a policy.
            payload: dict[str, object] = {
                "access_token": token,
                "expires_at": expires_at,
            }
            if refresh_token:
                payload["refresh_token"] = refresh_token
            tmp = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
            tmp.write_text(
                json.dumps(payload, sort_keys=True),
                encoding="utf-8",
            )
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._state_file)
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
            # from the .env refresh token. M3: a rotated refresh token in the
            # state file (if present) is adopted here even when the access
            # token is EXPIRED (xAI rotates on use — the `.env` token can be
            # dead while a newer rotated token in the state file is live).
            # Adopting the rotated token before refreshing means a restarted
            # (or state-mutating) lane heals without a re-login, and the
            # refresh below then persists the next rotation back to state.
            if cached is None:
                state = self._load_state()
                if state is not None:
                    if state.refresh_token:
                        self._refresh_token = state.refresh_token
                    if state.expires_at > now + XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS:
                        self._cached = state
                        return OAuthState(status="ok", access_token=state.access_token)

            result = await self._refresh()
            if result.status == "ok":
                self._cached = _TokenState(
                    access_token=result.access_token, expires_at=self._clock() + result.expires_in
                )
                self._save_state(
                    result.access_token,
                    self._clock() + result.expires_in,
                    refresh_token=self._refresh_token,
                )
            return result

    async def _refresh(self) -> OAuthState:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout.total)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # M5: resolve the token endpoint from discovery once per
                # process (cached); hardcoded `XAI_OAUTH_TOKEN_URL` is the
                # fallback when discovery is unreachable, so an xAI endpoint
                # move is absorbed here exactly as it is in the device flow.
                if self._token_endpoint is None:
                    try:
                        self._token_endpoint = await _discover_token_endpoint(
                            session, self._discovery_url()
                        )
                    except XaiOAuthFlowError:
                        log.warning(
                            "xai_oauth: discovery failed; falling back to %s",
                            XAI_OAUTH_TOKEN_URL,
                        )
                        self._token_endpoint = XAI_OAUTH_TOKEN_URL
                async with session.post(
                    self._token_endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self._client_id,
                    },
                ) as resp:
                    body = await resp.json(content_type=None)
            if resp.status == 200:
                token = body.get("access_token")
                if not isinstance(token, str) or not token:
                    return OAuthState("unavailable", reason="auth")
                # M3: xAI may rotate the refresh token on use (offline_access).
                # Adopt it in memory AND persist it to the state file so the
                # lane survives rotation; `.env` stays the immutable bootstrap
                # source (DEC-8), the state file the preferred runtime value.
                rotated = body.get("refresh_token")
                if isinstance(rotated, str) and rotated and rotated != self._refresh_token:
                    self._refresh_token = rotated
                    self._save_state(
                        token,
                        self._clock() + float(body.get("expires_in") or 0),
                        refresh_token=rotated,
                    )
                    log.info("xai_oauth: refresh token rotated; state file updated")
                # M9: `expires_in` may be missing on a 200; a 0-expiry would
                # re-refresh on every call (endpoint hammering). Conservative
                # default: treat a missing/invalid value as short-lived but
                # finite (2h), still fail-safe — and MUST exceed the refresh
                # skew (3600s) or the fresh-check never passes and every
                # call re-refreshes.
                expires_in = body.get("expires_in")
                try:
                    expires = float(expires_in) if expires_in not in (None, 0) else 7200.0
                except (TypeError, ValueError):
                    expires = 7200.0
                return OAuthState(
                    "ok", access_token=token, reason="", expires_in=expires
                )
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
