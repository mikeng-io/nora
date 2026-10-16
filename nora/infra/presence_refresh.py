"""The `where_is` LIVE-refresh client (DEC-67).

Calls the presence collector's `POST /refresh?person_id=…` to force an on-demand
Life360 reading before `where_is` reads the store. The COLLECTOR owns Life360 and
the DB write (the operator's chosen boundary), so this only triggers it —
Nora's process holds no Life360 credential and `where_is` stays a pure reader.

Never load-bearing: any failure returns False and `where_is` reads the last
stored fix, so the on-demand path can be down without `where_is` breaking.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("nora.presence_refresh")


class HttpLocationRefresher:
    """`refresh(person_id)` → the collector's `/refresh`. True only on a 200
    (a fresh fix was written); False on timeout / non-200 / transport error.

    Its own timeout is deliberately LARGER than the collector's refresh timeout,
    so Nora waits for the collector to finish pinging the phone rather than
    cutting it off — the collector bounds the actual wait, this is the ceiling on
    the whole round trip.
    """

    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._url = base_url.rstrip("/") + "/refresh"
        self._timeout_seconds = timeout_seconds

    async def refresh(self, *, person_id: str) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(self._url, params={"person_id": person_id}) as response,
            ):
                await response.read()
                return response.status == 200
        except Exception as exc:  # noqa: BLE001 — never load-bearing
            log.warning("live location refresh unreachable: %s", exc)
            return False
