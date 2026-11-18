"""Authenticated reads of the collector's current provider circle observations."""

import time
from collections.abc import Callable

from aiohttp import ClientSession, ClientTimeout

from nora.domain.presence_circles import (
    CircleObservation,
    CircleUnavailableError,
    checked_observations,
)


class HttpCircleObservations:
    def __init__(
        self, *, base_url: str, key: str, circles: tuple[str, ...], ttl_ms: int,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._url = base_url.rstrip("/") + "/memberships"
        self._key, self._circles, self._ttl_ms, self._clock = key, circles, ttl_ms, clock

    async def read(self) -> tuple[CircleObservation, ...]:
        if not self._key:
            raise CircleUnavailableError("circle authentication is unavailable")
        try:
            async with (
                ClientSession(timeout=ClientTimeout(total=5)) as session,
                session.get(self._url, headers={
                    "Authorization": "Bearer " + self._key,
                }) as response,
            ):
                if response.status != 200:
                    raise CircleUnavailableError("circle source is unavailable")
                body = await response.json()
            if not isinstance(body, dict) or set(body) != {"circles"}:
                raise CircleUnavailableError("invalid circle response")
            if not isinstance(body["circles"], list):
                raise CircleUnavailableError("invalid circle list")
            rows = tuple(CircleObservation(row["circle_id"], row["observed_ms"],
                         tuple(row["member_ids"])) for row in body["circles"]
                         if isinstance(row, dict) and set(row) == {
                             "circle_id", "observed_ms", "member_ids"}
                         and isinstance(row["member_ids"], list))
            if len(rows) != len(body["circles"]):
                raise CircleUnavailableError("incomplete circle response")
            return checked_observations(rows, expected=self._circles,
                now_ms=self._clock(), ttl_ms=self._ttl_ms)
        except Exception:  # noqa: BLE001 — URLs, headers and provider identifiers stay private
            raise CircleUnavailableError("circle source is unavailable") from None
