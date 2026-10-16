"""xAI OAuth device flow + refresh, for Nora's OWN X Premium subscription lane.

Nora holds her own xAI OAuth tokens (DEC-137) so image
generation runs on the subscription entitlement (quota-bounded, not metered
per-call billing) without borrowing Hermes' auth store or a host credential
proxy.

Credential split (operator's `.env` rule; DEC-308):

- `.env` (host, immutable, DEC-8) holds ONLY the BOOTSTRAP refresh token
  `nora xai-login` wrote (`XAI_OAUTH_REFRESH_TOKEN`). xAI rotates the refresh
  token on use, so the first refresh spends it.
- `./state/xai-oauth.json` (container-writable) holds the chain from there:
  the access token, its expiry, the CURRENT refresh token, when it was
  refreshed, and the sha256 of the `.env` token it grew from. `oauth_lane`
  owns it, the same way it owns the Codex lane's: a state file from an older
  login is ignored, so a new `xai-login` takes over without anyone deleting a
  file. A state file with no fingerprint (written before DEC-308) has its
  token tried once, and the `.env` login is spent only if it is refused.
  Otherwise a dead state-file token would mask a fresh login.
- The daemon refreshes it on a schedule (`nora.app.oauth_keepalive`), so an
  idle token is not found refused (`invalid_grant`) on the first call.

Endpoint + scope + skew constants mirror Hermes' `hermes_cli/auth.py`
(`XAI_OAUTH_CLIENT_ID`, `XAI_OAUTH_SCOPE`, `XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS`).
`grok-cli:access` is a scope xAI accepts with this exact set.

NEVER raises into the caller: every failure is an `OAuthState` with
`ok=False` and a `reason` the adapter can map (fail-closed, R-27).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import aiohttp

from .oauth_lane import Exchange, LaneEvent, Refusal, RotatingOAuthLane

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


class XaiOAuth(RotatingOAuthLane):
    """Refresh lane for the xAI subscription (see the module docstring; the
    chain, the locks and the state file are `oauth_lane`'s).

    Constructed by the composition root, which resolves the bootstrap refresh
    token by NAME from config/env (DEC-8) and hands the SAME object to the
    adapter and to the keepalive.
    """

    lane = "xai"
    skew_seconds = XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS

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
        observer: Callable[[LaneEvent], None] | None = None,
    ) -> None:
        if not refresh_token:
            raise ValueError("XAI_OAUTH_REFRESH_TOKEN must be set")
        super().__init__(
            bootstrap_refresh_token=refresh_token,
            state_file=state_file,
            timeout_seconds=timeout_seconds,
            clock=clock,
            observer=observer,
        )
        self._client_id = client_id
        self._scope = scope
        # M5: resolve the token endpoint from OIDC discovery ONCE per process
        # (cached lazily on first refresh; `XAI_OAUTH_TOKEN_URL` is the
        # fallback when discovery is unreachable). Test seam: `issuer` lets
        # contract tests point at a local TestServer.
        self._issuer = issuer.rstrip("/")
        self._token_endpoint: str | None = None

    def _discovery_url(self) -> str:
        return f"{self._issuer}/.well-known/openid-configuration"

    # -- access -------------------------------------------------------------

    async def access_token(self) -> OAuthState:
        """Return a valid access token, refreshing proactively when needed.

        Single-flight in and across processes (`RotatingOAuthLane._acquire`).
        Fail-closed: a dead refresh token / Cloudflare challenge / network
        failure returns `OAuthState("unavailable", reason=...)`, never a
        raise and never a stale token.
        """
        outcome = await self._acquire()
        if isinstance(outcome, str):
            return OAuthState("unavailable", reason=outcome)
        return OAuthState(
            "ok",
            access_token=outcome.access_token,
            expires_in=max(0.0, outcome.expires_at - self._clock()),
        )

    async def _exchange(self, refresh_token: str) -> Exchange | Refusal:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
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
                        "refresh_token": refresh_token,
                        "client_id": self._client_id,
                    },
                ) as resp:
                    status = resp.status
                    try:
                        body = await resp.json(content_type=None)
                    except ValueError:
                        body = None
        except (aiohttp.ClientError, TimeoutError) as exc:
            return Refusal("down", f"{type(exc).__name__} reaching the token endpoint")
        if status == 200 and isinstance(body, dict):
            token = body.get("access_token")
            if not isinstance(token, str) or not token:
                return Refusal("auth", "HTTP 200 without an access token")
            # xAI rotates the refresh token on use (offline_access): the
            # rotated one is the chain from here on; the spent one is dead.
            rotated = body.get("refresh_token")
            # M9: `expires_in` may be missing on a 200; a 0-expiry would
            # re-refresh on every call (endpoint hammering). Conservative
            # default: 2h, which MUST exceed the refresh skew (3600s) or the
            # fresh-check never passes and every call re-refreshes.
            expires_in = body.get("expires_in")
            try:
                expires = float(expires_in) if expires_in not in (None, 0) else 7200.0
            except (TypeError, ValueError):
                expires = 7200.0
            return Exchange(
                access_token=token,
                refresh_token=rotated if isinstance(rotated, str) and rotated else refresh_token,
                expires_at=self._clock() + expires,
            )
        code = ""
        if isinstance(body, dict):
            err = body.get("error")
            code = str(err.get("code") if isinstance(err, dict) else err or "")
        detail = f"HTTP {status} {code}".strip()
        # Cloudflare challenge: 403 while the token is still valid —
        # RETRYABLE, not terminal auth.
        if status == 403:
            return Refusal("cloudflare", detail, code)
        if status in (400, 401):
            return Refusal("auth", detail, code)
        if status == 429:
            return Refusal("rate_limited", detail, code)
        return Refusal("down", detail, code)
