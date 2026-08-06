"""`Life360Client` — auth, token cache, and one circle read. NEVER raises.

The study (design/19 §1, §4) named both the ingredient that makes Life360's
private API answer and the failure that gets an account banned:

- **The missing ingredient is the Life360 app's User-Agent.** Auth is HTTP Basic
  with the app's client credential, `grant_type=password`, and that UA; without
  the UA the token endpoint refuses. Both the credential and the UA are passed
  in from the environment by the composition root (DEC-8) — this module inlines
  no secret.

- **The 403-loop is the ban risk, and this client will not hammer.** A `403` on
  a data call is Life360's stale-token / ban signal. The client re-auths at most
  ONCE per read, then retries the read exactly once. A `403` that a FRESH token
  also gets is treated as a BAN signal, not a stale-token signal: the read
  returns `banned`, an alarm fires, `auth_failures` increments, and NO further
  re-auth is attempted on that read. The actual exponential back-off — the
  WAITING — lives in the collector loop, which already sleeps a cadence between
  reads and lengthens it with `auth_failures` (design/19 §4.1). Keeping the wait
  there and the "one re-auth per read" here is what bounds the credential-POST
  rate without a clock or a sleep inside this class.

The token is cached IN MEMORY for the life of the process (one long-running
daemon holds it fine); it is dropped on a 403 and re-fetched on the next read.

Everything degrades to a typed `CircleRead`, never an exception into the loop
(R-5): transport failure, a non-200, an app-update challenge PAGE (a 200 whose
body is not JSON — the canary in §4), and a ban are four distinct statuses that
must never collapse into one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .http import request
from .members import Member, parse_members

log = logging.getLogger("nora.presence.life360")

TOKEN_URL = "https://api.life360.com/v3/oauth2/token.json"  # noqa: S105 — a URL, not a secret
MEMBERS_URL = "https://api.life360.com/v3/circles/{circle_id}/members"

# The Life360 Android app's User-Agent shape — the study's missing ingredient.
# A constructor default, overridable, because it is a property of the client
# Nora impersonates, not of this deployment; the real value is operator-set.
DEFAULT_USER_AGENT = "com.life360.android.safetymapd/KOKO/24.0.0 android/13"

TIMEOUT_SECONDS = 8.0


class ReadStatus(StrEnum):
    """How one circle read resolved.

    `banned` and `challenge` are held apart from the generic `unavailable`
    because the collector alarms differently on each: `challenge` means the
    wire shape changed under us (a body that is not JSON), `banned` means a
    fresh token was refused (the ban risk), and both are louder than a plain
    transport `unavailable`.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"
    CHALLENGE = "challenge"
    BANNED = "banned"


@dataclass(frozen=True)
class CircleRead:
    """The typed outcome of `read_circle`. `members` is populated only on `ok`
    and may be empty — an empty members list is itself the collector's
    unexpectedly-empty-circle canary (§4), decided there, not here."""

    status: ReadStatus
    members: tuple[Member, ...] = field(default_factory=tuple)
    detail: str = ""


class Life360Client:
    """One Life360 account's circle reads, behind the collector.

    Constructed once per process. `metrics` is injected and optional (the same
    port-layer rule `SqlRecordStore` states): `None` means no counters, correct
    for unit tests that assert on the returned status.
    """

    def __init__(
        self,
        *,
        client_credential: str,
        username: str,
        password: str,
        user_agent: str = DEFAULT_USER_AGENT,
        token_url: str = TOKEN_URL,
        members_url: str = MEMBERS_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
        metrics: Any = None,
    ) -> None:
        self._client_credential = client_credential
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self._token_url = token_url
        self._members_url = members_url
        self._timeout_seconds = timeout_seconds
        self._metrics = metrics
        self._token: str | None = None
        self._auth_failures = 0

    @property
    def auth_failures(self) -> int:
        """Consecutive reads that ended in a refused fresh token. The collector
        reads this to size its exponential back-off; a successful read resets
        it to 0."""
        return self._auth_failures

    async def read_circle(self, *, circle_id: str, now_ms: int) -> CircleRead:
        """Read one circle's members. NEVER raises; see module docstring for the
        four statuses and the one-re-auth-per-read discipline."""
        token = self._token
        if token is None:
            token = await self._authenticate()
            if token is None:
                # The auth endpoint refused with no usable token — treated as a
                # ban-class signal (bounded, alarmed), never a re-auth loop.
                return self._banned("initial authentication failed")

        result = await self._get_members(circle_id, token)
        # A stale-token 403: re-auth ONCE and retry the read ONCE. This is the
        # only place a second token POST can happen in a read, which is what
        # bounds the credential-POST rate the study warns about.
        if result.status == 403:
            self._token = None
            token = await self._authenticate()
            if token is None:
                return self._banned("re-auth after 403 failed")
            result = await self._get_members(circle_id, token)
            if result.status == 403:
                # A FRESH token also got 403 → a ban signal, not a stale token.
                # Stop here; the collector's back-off does the waiting.
                return self._banned("fresh token still 403 — ban risk")

        return self._classify(result, now_ms=now_ms)

    def _classify(self, result: Any, *, now_ms: int) -> CircleRead:
        if not result.ok:
            return CircleRead(ReadStatus.UNAVAILABLE, detail="transport failed")
        if result.status != 200:
            return CircleRead(
                ReadStatus.UNAVAILABLE, detail=f"http {result.status}"
            )
        if not result.is_json:
            # A 200 that is not JSON is the app-update challenge page — the
            # canary. Distinct from unavailable so the collector alarms on it.
            self._alarm("challenge")
            return CircleRead(ReadStatus.CHALLENGE, detail="non-JSON body")
        body = result.json
        if not isinstance(body, dict) or not isinstance(body.get("members"), list):
            # JSON, but not the members shape — an error envelope or a changed
            # schema. Unavailable rather than a spurious empty circle.
            return CircleRead(ReadStatus.UNAVAILABLE, detail="unexpected JSON shape")
        members = tuple(parse_members(body, now_ms=now_ms))
        self._auth_failures = 0
        return CircleRead(ReadStatus.OK, members=members)

    async def _get_members(self, circle_id: str, token: str) -> Any:
        return await request(
            "GET",
            self._members_url.format(circle_id=circle_id),
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
            timeout_seconds=self._timeout_seconds,
        )

    async def _authenticate(self) -> str | None:
        """POST the password grant with the Basic client credential and the app
        UA. Returns the access token, or None on any failure (transport, non-200
        including 403, or a body with no token). A 403 here is a refused
        credential — alarmed, never retried in a tight loop."""
        result = await request(
            "POST",
            self._token_url,
            headers={
                "Authorization": f"Basic {self._client_credential}",
                "User-Agent": self._user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
            },
            timeout_seconds=self._timeout_seconds,
        )
        if not result.ok or result.status != 200 or not result.is_json:
            if result.status == 403:
                self._alarm("auth_403")
            log.warning("life360 auth failed: status=%s json=%s", result.status, result.is_json)
            return None
        body = result.json
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            log.warning("life360 auth returned no access_token")
            return None
        self._token = token
        return token

    def _banned(self, detail: str) -> CircleRead:
        self._auth_failures += 1
        self._alarm("banned")
        log.warning("life360 read banned/refused: %s (failures=%d)", detail, self._auth_failures)
        return CircleRead(ReadStatus.BANNED, detail=detail)

    def _alarm(self, reason: str) -> None:
        if self._metrics is not None:
            self._metrics.inc("nora_presence_auth_alarms_total", reason=reason)
