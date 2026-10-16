"""One POST to a Google Maps Platform endpoint — or None. NEVER raises.

The routing analogue of `nora.infra.transit.catalogue.get_text`: exactly one
place decides what a failure looks like, so both the Routes client and the
Places resolver degrade the same way and neither can raise inside an agent
turn (where an exception costs the whole reply, R-5).

**The key travels in a header, never in the body or the URL** (`X-Goog-Api-Key`,
the shape Google documents), and it is the ONE secret this subsystem holds. It
is passed in from the composition root already resolved from the environment
(DEC-8) — this module reads no config and no environment of its own.

**The field mask is required, not optional, and it is a bill.** Google Maps
Platform charges per requested field: `X-Goog-FieldMask: *` on a Routes call
would request — and be billed for — every field including the polyline and the
per-step navigation text. Every caller here passes a field mask naming ONLY the
fields it renders, and this helper refuses to send a request without one so that
"forgot the mask" fails loud at the call site rather than quietly on the
invoice.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

log = logging.getLogger("nora.routes")


async def post_json(
    url: str,
    *,
    api_key: str,
    field_mask: str,
    body: dict[str, Any],
    timeout_seconds: float,
) -> Any | None:
    """POST `body` to `url` with the Google auth and field-mask headers. NEVER raises.

    Returns the decoded JSON, or None for any failure — a non-200 (Google sends
    a JSON error envelope with a 400 for a malformed request, which is logged
    but still collapses to None because the caller has nothing useful to say
    about it to the group), a body that is not JSON, or any transport
    exception. A session per call, like `nora.infra.honcho.client` and the
    transit catalogue: these are a handful of requests an hour to one host, and
    a pooled session would be state to own for no gain.
    """
    if not field_mask:
        # A programming error, not a runtime one: a Google call with no field
        # mask is billed for everything. Refused here rather than sent.
        log.error("routes call to %s attempted with no field mask", url)
        return None
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }
    try:
        timeout_config = aiohttp.ClientTimeout(total=timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout_config) as session,
            session.post(url, json=body, headers=headers) as response,
        ):
            text = await response.text()
            if response.status != 200:
                # Google's error body names the reason; log a bounded slice of
                # it (never the key — it is a header, not in this text) so a 400
                # from a bad request is diagnosable without a second probe.
                log.warning("routes %s answered %s: %s", url, response.status, text[:200])
                return None
            try:
                return json.loads(text)
            except ValueError:
                log.warning("routes %s did not return JSON", url)
                return None
    except Exception as exc:                                        # noqa: BLE001
        # Broad and never re-raised, for the reason the transit helper gives:
        # this runs inside an agent turn. aiohttp alone raises ClientError,
        # asyncio.TimeoutError, UnicodeDecodeError and ssl.SSLError from here,
        # and a fifth in a later release must degrade to None, not to silence.
        log.warning("routes %s unreachable: %s", url, exc)
        return None
