"""`GoogleCredentials`: the ONE Google OAuth client and refresh token every
Google service of Nora's shares.

It owns the refresh-token grant against `oauth2.googleapis.com/token` and the
access tokens it returns, held in memory until their own expiry and never
written anywhere. A consumer (the Drive adapter today) is handed this object
and calls `access_token(scope=...)` with the scope it declares
(`nora.infra.google.scopes`); it never sees the client secret or the refresh
token.

**Read-only holds in the credential, not only in the code.** Each consumer's
access token is minted for ITS scope set alone: the refresh request carries an
explicit `scope=` (a subset of the grant), so the Drive adapter's token
carries `drive.readonly` and nothing else, whatever else the one refresh
token was granted. Tokens are kept per scope set; two
consumers with the same set share one.

**Fail closed.**

- *The grant.* Once per process the full grant is read (a refresh with no
  `scope=`, whose access token is dropped unused). A grant holding a scope
  that could WRITE and that no consumer declared
  (`scopes.undeclared_write_scopes`) is refused for every consumer:
  `GoogleAuthError("undeclared_write_scope")` and an error log naming it.
- *The consumer.* A consumer whose scope is not in the grant gets
  `GoogleAuthError("scope_not_granted")`, logged once naming the scope and
  the fix (re-run `scripts/google_auth.py`); other consumers keep working. A
  minted token whose `scope` is not exactly what was asked is refused too.

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
import zlib
from collections.abc import Callable
from typing import NoReturn, Protocol

import httpx

from .scopes import undeclared_write_scopes

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


async def _capped_body(resp: httpx.Response, max_bytes: int) -> bytes:
    """`resp`'s body, read raw and capped at `max_bytes` of DECODED bytes. A
    body the server compressed anyway (gzip or deflate) is inflated here with
    each step's output capped; any other encoding is refused."""
    encoding = resp.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("identity", "", "gzip", "deflate"):
        raise GoogleAuthError("refresh_failed")
    inflate = zlib.decompressobj(47) if encoding in ("gzip", "deflate") else None
    body = bytearray()
    async for raw in resp.aiter_raw():
        pending = raw
        while pending:
            if inflate is None:
                chunk, pending = pending, b""
            else:
                chunk = inflate.decompress(pending, max_bytes - len(body) + 1)
                pending = inflate.unconsumed_tail
            if len(body) + len(chunk) > max_bytes:
                raise GoogleAuthError("refresh_failed")
            body += chunk
            if inflate is not None and not chunk:
                break
    if inflate is not None and not inflate.eof:
        raise GoogleAuthError("refresh_failed")  # a cut-off stream is not an answer
    return bytes(body)


class GoogleAuthError(Exception):
    """No access token for this consumer. `reason` is `invalid_grant`,
    `scope_not_granted`, `undeclared_write_scope` or `refresh_failed`; a consumer reports it as
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
        #: The full grant, read once; None until then.
        self._granted: frozenset[str] | None = None
        #: scope set -> (access token, expiry). Per consumer scope set.
        self._tokens: dict[frozenset[str], tuple[str, float]] = {}
        self._warned: set[str] = set()
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "GoogleCredentials(<secrets withheld>)"

    def invalidate(self, *, scope: str | None = None) -> None:
        """Drop an access token (a consumer was answered 401 with it); with no
        `scope`, every one."""
        if scope is None:
            self._tokens.clear()
        else:
            self._tokens.pop(frozenset({scope}), None)

    async def access_token(self, *, scope: str) -> str:
        """A live access token carrying `scope` ALONE, minted when missing or
        about to expire. Raises `GoogleAuthError`."""
        want = frozenset({scope})
        async with self._lock:
            if self._granted is None:
                answer = await self._refresh(None)
                self._granted = frozenset(str(answer.get("scope", "")).split())
            extra = undeclared_write_scopes(self._granted)
            if extra:
                if "*write" not in self._warned:
                    self._warned.add("*write")
                    log.error(
                        "Google sign-in grants scopes that can write and that no Nora "
                        "service declared (%s); every Google service stays unavailable. "
                        "Re-run scripts/google_auth.py and replace the refresh token in "
                        ".env", " ".join(extra),
                    )
                raise GoogleAuthError("undeclared_write_scope")
            if not want <= self._granted:
                self._scope_missing(scope)
            held = self._tokens.get(want)
            if held is not None and self._clock() < held[1]:
                return held[0]
            answer = await self._refresh(want)
            if frozenset(str(answer.get("scope", "")).split()) != want:
                # Google minted more or less than asked: never use it.
                self._scope_missing(scope)
            token = str(answer["access_token"])
            expires_in = answer.get("expires_in", 3600)
            seconds = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
            self._tokens[want] = (token, self._clock() + max(0.0, seconds - _EXPIRY_MARGIN))
            return token

    def _scope_missing(self, scope: str) -> NoReturn:
        if scope not in self._warned:
            self._warned.add(scope)
            log.error(
                "Google sign-in lacks the scope %s; this service stays "
                "unavailable. Re-run scripts/google_auth.py with the same client "
                "and replace the refresh token in .env (other Google services "
                "keep working)", scope,
            )
        raise GoogleAuthError("scope_not_granted")

    async def _refresh(self, scopes: frozenset[str] | None) -> dict[str, object]:
        """One refresh-token grant. `scopes`: the subset the access token is
        minted for; None reads the full grant (its token is never kept)."""
        form = {
            "grant_type": "refresh_token", "refresh_token": self.__refresh_token,
            "client_id": self.__client_id, "client_secret": self.__client_secret,
        }
        if scopes is not None:
            form["scope"] = " ".join(sorted(scopes))
        try:
            async with (
                httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client,
                # `identity`: httpx otherwise advertises gzip, Google then
                # compresses the answer, and the raw bytes below are not JSON
                # (the read fails with UnicodeDecodeError).
                client.stream("POST", TOKEN_URL, data=form,
                              headers={"Accept-Encoding": "identity"}) as resp,
            ):
                body = await _capped_body(resp, _MAX_TOKEN_BODY)
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
            return payload
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
        if error == "invalid_scope" and scopes is not None:
            self._scope_missing(next(iter(sorted(scopes))))
        log.warning("Google token refresh answered %s %s", status, error[:80])
        raise GoogleAuthError("refresh_failed")
