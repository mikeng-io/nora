"""Shared transport for the Ory clients — one `request` that answers in
typed results and never follows a redirect.

The discipline is `authorization.md`'s: a verdict whose status and body
disagree is `unavailable`, and a redirect is refused rather than followed —
a proxy must not be able to launder a denial into a success shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import aiohttp
from yarl import URL


class OryTransportError(Exception):
    """A call that never reached a usable answer: connect refused, timeout,
    redirect, malformed body, or a status the caller did not ask about."""


class HttpStatus(Enum):
    """The transport-level outcome a client maps into its own vocabulary.

    `DENIED` is a real verdict (Keto's 403, a 401/403 on a gated endpoint),
    not a failure; `UNAVAILABLE` covers everything that could not produce a
    trustworthy answer.
    """

    OK = "ok"
    ABSENT = "absent"
    DENIED = "denied"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class HttpResult:
    """One answered call: the mapped status, the parsed JSON body when one
    validated, and a machine-readable detail for the failure statuses."""

    status: HttpStatus
    body: Any = None
    detail: str = ""


async def request(
    session: aiohttp.ClientSession,
    method: str,
    url: str | URL,
    *,
    params: dict[str, str] | None = None,
    json_body: Any = None,
    api_key: str | None = None,
    timeout_seconds: float = 5.0,
    expected: frozenset[int] | dict[int, HttpStatus] | None = None,
) -> HttpResult:
    """One JSON call with fixed semantics.

    `expected` maps status codes to `HttpResult.status`; an int set is
    shorthand for `{code: OK}`. Any status outside the map, a redirect of
    any kind, a body that is not parseable JSON (when a body is present),
    a timeout or a transport error answers `UNAVAILABLE` — the caller never
    sees a shape it did not declare.

    When a body is expected to carry JSON, a response that cannot be parsed
    is `UNAVAILABLE`, not a `None` the caller could read as "no content".
    """
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if expected is None:
        expected_map: dict[int, HttpStatus] = {}
    elif isinstance(expected, frozenset):
        expected_map = dict.fromkeys(expected, HttpStatus.OK)
    else:
        expected_map = dict(expected)
    try:
        async with session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            allow_redirects=False,
        ) as response:
            status = response.status
            raw = await response.read()
    except (TimeoutError, aiohttp.ClientError, ConnectionError, OSError) as exc:
        return HttpResult(
            HttpStatus.UNAVAILABLE, detail=f"transport:{type(exc).__name__}"
        )

    if 300 <= status < 400:
        return HttpResult(HttpStatus.UNAVAILABLE, detail=f"redirect:{status}")

    body: Any = None
    if raw and raw.strip():
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return HttpResult(HttpStatus.UNAVAILABLE, detail="malformed-body")

    mapped = expected_map.get(status)
    if mapped is None:
        return HttpResult(HttpStatus.UNAVAILABLE, body=body, detail=f"status:{status}")
    return HttpResult(mapped, body=body, detail=f"status:{status}")
