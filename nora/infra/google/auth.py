"""`GoogleCredentials`: the ONE Google OAuth client and refresh token every
Google service of Nora's shares (operator, 2026-10-10;
design/external-files/00-design.md §4).

It owns the refresh-token grant against `oauth2.googleapis.com/token` and the
access token it returns, held in memory until its own expiry and never
written anywhere. A consumer (the Drive adapter today) is handed this object
and calls `access_token(scope=...)` with the scope it declares
(`nora.infra.google.scopes`); it never sees the client secret or the refresh
token. One refresh serves every consumer.

**Fail closed per service.** The token endpoint's `scope` field is the set the
refresh token was granted. A consumer whose scope is not in it gets
`GoogleAuthError("scope_not_granted")`, and the operator log names the
missing scope and the fix (re-run `scripts/google_auth.py`); a consumer whose
scope IS granted keeps working. An answer with no `scope` field grants
nothing.

`invalid_grant` (revoked, expired, or a Testing-mode consent screen's 7-day
expiry) is `GoogleAuthError("invalid_grant")`, an error log naming the fix,
and `nora_google_invalid_grant_total`, so a dead sign-in is one alert rather
than a week of `unavailable`. Neither the secret, the refresh token nor an
access token is ever logged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

import httpx

log = logging.getLogger("nora.google")

__all__ = ["TOKEN_URL", "GoogleAuthError", "GoogleCredentials"]

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
#: Seconds before an access token's stated expiry that it is refreshed.
_EXPIRY_MARGIN = 60.0
#: The most of a token answer read; a real one is under 2 KB.
_MAX_TOKEN_BODY = 64 * 1024


class Counter(Protocol):
    """`nora.obs.metrics.Metrics` as this module uses it; injected, since
    `nora.infra` sits below `nora.obs` in the layers contract."""

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...


class GoogleAuthError(Exception):
    """No access token for this consumer. `reason` is `invalid_grant`,
    `scope_not_granted` or `refresh_failed`; a consumer reports it as
    `unavailable` («I couldn't check»), never as «there is nothing»."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GoogleCredentials:
    """One per process, built by the composition root from `[google]`."""

    def __init__(
        self, *, client_id: str, client_secret: str, refresh_token: str,
        metrics: Counter, timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.__client_id = client_id
        self.__client_secret = client_secret
        self.__refresh_token = refresh_token
        self._metrics = metrics
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport
        self._clock = clock
        self._access: str | None = None
        self._granted: frozenset[str] = frozenset()
        self._expires_at = 0.0
        self._warned: set[str] = set()
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "GoogleCredentials(<secrets withheld>)"

    def invalidate(self) -> None:
        """Drop the access token (a consumer was answered 401 with it)."""
        self._access = None

    async def access_token(self, *, scope: str) -> str:
        """A live access token for a consumer that needs `scope`, refreshed
        when missing or about to expire. Raises `GoogleAuthError`."""
        async with self._lock:
            if self._access is None or self._clock() >= self._expires_at:
                await self._refresh()
            if scope not in self._granted:
                if scope not in self._warned:
                    self._warned.add(scope)
                    log.error(
                        "Google sign-in lacks the scope %s; this service stays "
                        "unavailable. Re-run scripts/google_auth.py with the same client "
                        "and replace the refresh token in .env (other Google services "
                        "keep working)", scope,
                    )
                raise GoogleAuthError("scope_not_granted")
            assert self._access is not None  # noqa: S101 - set by _refresh or raised
            return self._access

    async def _refresh(self) -> None:
        form = {
            "grant_type": "refresh_token", "refresh_token": self.__refresh_token,
            "client_id": self.__client_id, "client_secret": self.__client_secret,
        }
        try:
            async with (
                httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client,
                client.stream("POST", TOKEN_URL, data=form) as resp,
            ):
                body = bytearray()
                async for chunk in resp.aiter_raw():
                    if len(body) + len(chunk) > _MAX_TOKEN_BODY:
                        raise GoogleAuthError("refresh_failed")
                    body += chunk
                status = resp.status_code
            payload = httpx.Response(status, content=bytes(body)).json()
        except GoogleAuthError:
            raise
        except Exception as exc:
            log.warning("Google token refresh failed: %s", type(exc).__name__)
            raise GoogleAuthError("refresh_failed") from exc
        if not isinstance(payload, dict):
            payload = {}
        if status == 200 and payload.get("access_token"):
            expires_in = payload.get("expires_in", 3600)
            seconds = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
            self._access = str(payload["access_token"])
            self._expires_at = self._clock() + max(0.0, seconds - _EXPIRY_MARGIN)
            self._granted = frozenset(str(payload.get("scope", "")).split())
            self._warned.clear()
            return
        self._access = None
        error = str(payload.get("error", ""))
        if error == "invalid_grant":
            self._metrics.inc("nora_google_invalid_grant_total")
            log.error(
                "Google refused the refresh token (invalid_grant: %s). The sign-in is "
                "revoked or expired; run scripts/google_auth.py and replace the "
                "refresh token [google] refresh_token_env names in .env",
                str(payload.get("error_description", ""))[:200],
            )
            raise GoogleAuthError("invalid_grant")
        log.warning("Google token refresh answered %s %s", status, error[:80])
        raise GoogleAuthError("refresh_failed")
