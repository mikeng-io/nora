"""`Life360Client` — auth, token cache, and one circle read. NEVER raises.

Two facts shape the client: what makes Life360's private API answer, and what
gets an account banned:

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
  reads and lengthens it with `auth_failures`. Keeping the wait
  there and the "one re-auth per read" here is what bounds the credential-POST
  rate without a clock or a sleep inside this class.

The token is cached IN MEMORY for the life of the process (one long-running
daemon holds it fine); it is dropped on a 403 and re-fetched on the next read.

**Token mode** (`access_token`): Life360 refuses the password grant outright for
some accounts — `403 "Invalid login and password"` with the right password,
attributed to a verified phone number on the account. The workaround is the
bearer a life360.com browser login leaves in the
`LIFE360_AUTH_TOKEN` cookie. Given one, the client uses it as THE token and never
POSTs the credential at all: there is no grant to fall back to, so a 401/403 on a
data call is a REJECTED TOKEN — `banned` with its own `token_rejected` alarm, the
same `auth_failures` back-off, and no re-auth. Only the operator can mint a new
one, so the loud, distinct signal is the whole recovery path.

Everything degrades to a typed `CircleRead`, never an exception into the loop
(R-5): transport failure, a non-200, an app-update challenge PAGE (a 200 whose
body is not JSON — the canary), and a ban are four distinct statuses that
must never collapse into one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nora.domain.presence import SavedPlace

from .http import HttpResult, request
from .members import Member, parse_members
from .places import parse_places

log = logging.getLogger("nora.presence.life360")

# The Life360 host is `api-cloudfront.life360.com` (behind Cloudflare). These
# exact paths + UA + the app client credential are the working recipe. Every part
# is load-bearing and Life360-specific: token is `/v3/oauth2/token.json` (WITH `.json`),
# circles is `/v3/circles` (NOT v4), and the UA is `.../23.49.0/Android/13`
# (slashes, capital Android). A wrong client token or UA does not 401 cleanly —
# Cloudflare serves a bot-challenge HTML 403 instead, which is why getting any of
# these wrong looks like "the whole API is blocked" rather than "bad credential".
TOKEN_URL = "https://api-cloudfront.life360.com/v3/oauth2/token.json"  # noqa: S105 — a URL, not a secret
MEMBERS_URL = "https://api-cloudfront.life360.com/v3/circles/{circle_id}/members"
PLACES_URL = "https://api-cloudfront.life360.com/v3/circles/{circle_id}/places"
CIRCLES_URL = "https://api-cloudfront.life360.com/v3/circles"
# The on-demand location request (DEC-67): POST pings the
# member's phone to report a FRESH position — the app's silent refresh button.
REQUEST_URL = "https://api-cloudfront.life360.com/v3/circles/{circle_id}/members/{member_id}/request"

# The Life360 Android app's User-Agent shape — without it the token endpoint
# refuses. A constructor default, overridable, because it is a property of the client
# Nora impersonates, not of this deployment; the real value is operator-set.
DEFAULT_USER_AGENT = "com.life360.android.safetymapd/KOKO/23.49.0/Android/13"

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
    unexpectedly-empty-circle canary, decided there, not here."""

    status: ReadStatus
    members: tuple[Member, ...] = field(default_factory=tuple)
    detail: str = ""


@dataclass(frozen=True)
class PlacesRead:
    """The typed outcome of `read_places`. `places` is populated only on `ok`
    and may be empty — a circle with no saved places is an ordinary answer, not
    a canary (unlike an empty members list): plenty of circles never name one."""

    status: ReadStatus
    places: tuple[SavedPlace, ...] = field(default_factory=tuple)
    detail: str = ""


@dataclass(frozen=True)
class DiscoveredMember:
    """A circle member as OPERATOR discovery needs them: the Life360 id to paste
    into a `[[persons.<id>.life360]]` block, and the NAME `read_circle` discards
    (the collector classifies location; it never needed the name)."""

    member_id: str
    name: str


@dataclass(frozen=True)
class DiscoveredCircle:
    circle_id: str
    name: str
    members: tuple[DiscoveredMember, ...]


class Life360DiscoveryError(Exception):
    """A discovery failure. UNLIKE `read_circle`, `discover` RAISES — it backs the
    one-shot `nora presence members` CLI, not the daemon loop, and an operator
    running it wants a clear message and a non-zero exit, not a typed-degraded
    value he has to inspect."""


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
        circles_url: str = CIRCLES_URL,
        places_url: str = PLACES_URL,
        request_url: str = REQUEST_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
        metrics: Any = None,
        access_token: str | None = None,
    ) -> None:
        self._client_credential = client_credential
        self._circles_url = circles_url
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self._token_url = token_url
        self._members_url = members_url
        self._places_url = places_url
        self._request_url = request_url
        self._timeout_seconds = timeout_seconds
        self._metrics = metrics
        # Token mode (module docstring): a static operator-supplied bearer.
        # `_authenticate` is the ONE place a credential can be POSTed, so it is
        # the one place token mode short-circuits — every read reaches it the
        # same way it reaches the password grant.
        self._access_token = access_token or None
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
        if self._token_rejected(result):
            return self._banned(self._TOKEN_REJECTED, alarm=None)
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

        if result.status == 429:
            # An explicit rate-limit — the strongest ban-class signal there is.
            # Do NOT re-auth (that POSTs the credential mid-throttle); hand it to
            # the collector's back-off, which the auth_failures increment drives.
            return self._banned("rate limited (429)")

        return self._classify(result, now_ms=now_ms)

    async def request_location(self, *, circle_id: str, member_id: str) -> bool:
        """Ask Life360 to ping this member's phone for a FRESH position — the
        app's silent refresh (DEC-67). `POST .../members/{id}/request` with form
        `type=location`. Best-effort: True only if Life360 accepted the request
        (200); False on no token, a non-200, or any transport failure. NEVER
        raises — the caller falls back to the last stored fix. Re-auths ONCE on a
        403, the same bound `read_circle` holds so an on-demand request cannot
        become a credential-POST loop."""
        token = self._token
        if token is None:
            token = await self._authenticate()
            if token is None:
                return False
        url = self._request_url.format(circle_id=circle_id, member_id=member_id)
        result = await self._post_request(url, token)
        if self._token_rejected(result):
            return False
        if result.status == 403:
            self._token = None
            token = await self._authenticate()
            if token is None:
                return False
            result = await self._post_request(url, token)
        return result.status == 200

    async def _post_request(self, url: str, token: str) -> HttpResult:
        return await request(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
            data={"type": "location"},
            timeout_seconds=self._timeout_seconds,
        )

    async def read_places(self, *, circle_id: str, now_ms: int) -> PlacesRead:
        """Read one circle's saved places. NEVER raises, and holds
        the identical one-re-auth-per-read discipline as `read_circle` — the two
        reads share the token, the credential-POST bound, and the ban-class
        classification, because they share the account whose ban we are bounding."""
        token = self._token
        if token is None:
            token = await self._authenticate()
            if token is None:
                return self._places_banned("initial authentication failed")
        result = await self._get(self._places_url.format(circle_id=circle_id), token)
        if self._token_rejected(result):
            return self._places_banned(self._TOKEN_REJECTED, alarm=None)
        if result.status == 403:
            self._token = None
            token = await self._authenticate()
            if token is None:
                return self._places_banned("re-auth after 403 failed")
            result = await self._get(
                self._places_url.format(circle_id=circle_id), token
            )
            if result.status == 403:
                return self._places_banned("fresh token still 403 — ban risk")
        if not result.ok:
            return PlacesRead(ReadStatus.UNAVAILABLE, detail="transport failed")
        if result.status != 200:
            return PlacesRead(
                ReadStatus.UNAVAILABLE, detail=f"http {result.status}"
            )
        if not result.is_json:
            self._alarm("challenge")
            return PlacesRead(ReadStatus.CHALLENGE, detail="non-JSON body")
        places = tuple(parse_places(result.json, circle_id=circle_id, now_ms=now_ms))
        return PlacesRead(ReadStatus.OK, places=places)

    async def discover(self) -> list[DiscoveredCircle]:
        """Every circle this account can see, each with its members' id + name —
        the mapping `nora presence members` prints for identity.toml. One-shot
        operator call: RAISES `Life360DiscoveryError` on any failure (see it)."""
        token = await self._authenticate()
        if token is None:
            raise Life360DiscoveryError(
                "authentication failed — check LIFE360_CLIENT_CREDENTIAL, "
                "LIFE360_USERNAME and LIFE360_PASSWORD (or set "
                "LIFE360_AUTH_TOKEN if Life360 refuses the password login)"
            )
        body = await self._get_json(self._circles_url, token)
        circles = body.get("circles") if isinstance(body, dict) else None
        if not isinstance(circles, list):
            raise Life360DiscoveryError("unexpected /circles response shape")
        out: list[DiscoveredCircle] = []
        for circle in circles:
            if not isinstance(circle, dict):
                continue
            circle_id = str(circle.get("id") or "").strip()
            if not circle_id:
                continue
            members_body = await self._get_json(
                self._members_url.format(circle_id=circle_id), token
            )
            out.append(
                DiscoveredCircle(
                    circle_id=circle_id,
                    name=str(circle.get("name") or "").strip(),
                    members=_discovered_members(members_body),
                )
            )
        return out

    async def _get_json(self, url: str, token: str) -> Any:
        """One authenticated GET for discovery. Raises on anything but a 200 JSON
        body — the CLI wants the failure surfaced, not degraded."""
        result = await request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
            timeout_seconds=self._timeout_seconds,
        )
        if not result.ok or result.status != 200 or not result.is_json:
            raise Life360DiscoveryError(
                f"GET {url} failed (status {result.status}, json={result.is_json})"
            )
        return result.json

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
        return await self._get(self._members_url.format(circle_id=circle_id), token)

    async def _get(self, url: str, token: str) -> Any:
        """One authenticated daemon GET — the non-raising twin of `_get_json`
        (which backs the operator CLI and raises). Both circle reads
        (`read_circle`, `read_places`) come through here."""
        return await request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
            timeout_seconds=self._timeout_seconds,
        )

    _TOKEN_REJECTED = "access token rejected — replace LIFE360_AUTH_TOKEN"  # noqa: S105 — a message, not a secret

    def _token_rejected(self, result: HttpResult) -> bool:
        """Token mode's refusal: a 401/403 on the operator's bearer. Alarmed as
        `token_rejected` HERE, once per refused call, so the caller only has to
        route it to its ban path. Always False in password mode, which keeps its
        own 403 → re-auth-once discipline unchanged."""
        if self._access_token is None or result.status not in (401, 403):
            return False
        self._alarm("token_rejected")
        return True

    async def _authenticate(self) -> str | None:
        """POST the password grant with the Basic client credential and the app
        UA. Returns the access token, or None on any failure (transport, non-200
        including 403, or a body with no token). A 403 here is a refused
        credential — alarmed, never retried in a tight loop. In token mode it
        returns the static token WITHOUT a request: the credential is never
        POSTed once the operator has handed over a bearer."""
        if self._access_token is not None:
            self._token = self._access_token
            return self._token
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

    def _places_banned(self, detail: str, *, alarm: str | None = "banned") -> PlacesRead:
        """The places-read twin of `_banned`. The three ban-class places outcomes
        MUST go through here, not construct `PlacesRead(BANNED)` inline: inline
        construction would skip the `_auth_failures` increment and the `banned`
        alarm, so a places-endpoint 403 would re-POST the credential every cycle
        with the collector's back-off unable to engage (DEC-55). The collector
        ALSO backs its places fetch off on non-OK (`_refresh_places`); this is the
        visibility half, that is the rate half."""
        self._auth_failures += 1
        if alarm is not None:
            self._alarm(alarm)
        log.warning("life360 places banned/refused: %s (failures=%d)",
                    detail, self._auth_failures)
        return PlacesRead(ReadStatus.BANNED, detail=detail)

    def _banned(self, detail: str, *, alarm: str | None = "banned") -> CircleRead:
        # `alarm=None` for a rejected token: `_token_rejected` already raised
        # the one alarm that carries its fix, and a second `banned` would page
        # the generic PresenceAuthRefused rule for the same dead token.
        self._auth_failures += 1
        if alarm is not None:
            self._alarm(alarm)
        log.warning("life360 read banned/refused: %s (failures=%d)", detail, self._auth_failures)
        return CircleRead(ReadStatus.BANNED, detail=detail)

    def _alarm(self, reason: str) -> None:
        if self._metrics is not None:
            self._metrics.inc("nora_presence_auth_alarms_total", reason=reason)


def _discovered_members(body: Any) -> tuple[DiscoveredMember, ...]:
    """Parse a /members body into (id, name) pairs for discovery. The members
    endpoint carries `firstName`/`lastName`, which `parse_members` (built for the
    collector's location classification) drops — this keeps them."""
    members = body.get("members") if isinstance(body, dict) else None
    if not isinstance(members, list):
        return ()
    out: list[DiscoveredMember] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        member_id = str(member.get("id") or "").strip()
        if not member_id:
            continue
        name = " ".join(
            str(member.get(key) or "").strip()
            for key in ("firstName", "lastName")
        ).strip()
        out.append(DiscoveredMember(member_id=member_id, name=name or "(unnamed)"))
    return tuple(out)
