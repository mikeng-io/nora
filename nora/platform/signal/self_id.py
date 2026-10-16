"""Nora's own Signal id forms, derived from the daemon — and counted when it fails.

WHAT ACTUALLY WORKS ON THIS DAEMON
----------------------------------
Verified against signal-cli 0.14.7 at 127.0.0.1:18080, the container in this
repo's `docker-compose.yml`. Read this before changing the endpoint:

* `GET /api/v1/accounts` — **404 Not Found.** It is not a broken route, it is
  not a route at all. `signal-cli-jsonrpc(5)`, shipped in
  the image at `/opt/signal-cli-0.14.7/man/man5/`, states that `--http` exposes
  exactly three endpoints: `POST /api/v1/rpc`, `GET /api/v1/events`,
  `GET /api/v1/check`.
* JSON-RPC `listAccounts` — `-32601 Method not implemented`. It exists only in
  MULTI-account mode (daemon started with no `-a`); this daemon is pinned to one
  account, so there is no RPC that will tell you which account that is. That is
  why `nora.platform.signal.account.derive_account` cannot be rescued the same
  way and `platforms.signal.account` in config.toml is load-bearing.
* JSON-RPC `listContacts` with `{"recipient": "<primary>"}` — **works**, and is
  a purely LOCAL store read. Returns Nora's own contact row:
  `number`, `uuid` (her **ACI**) and `username`. This is the primary source.
* JSON-RPC `getUserStatus` with `{"recipient": ["<primary>"]}` — **works**, but
  the `uuid` it returns is **NOT the ACI**. It is the **PNI** (phone-number
  identity), because it answers a phone-number lookup. Confirmed against the
  account store: `aciAccountData.serviceId` matches what `listContacts` gives,
  `pniAccountData.serviceId` matches what `getUserStatus` gives, and the two are
  different UUIDs. Wiring `getUserStatus` alone would therefore add the PNI and
  *miss the ACI*, which is the id form that appears in a real envelope's
  `sourceUuid`. Both are collected;
  neither is trusted to stand in for the other.

Both calls go out as ONE batched JSON-RPC POST (the man page's "single or batch
JSON-RPC request"), so this costs one HTTP round trip at startup. Every call is
read-only: nothing here registers, links, unlinks or writes to the account.

WHY IT IS COUNTED
-----------------
R-17. Enrichment is best-effort by design — `primary` is already known when this
runs, so failing here must not stop Nora starting. A best-effort path that only logs a
`log.warning` can stay dead unnoticed, so every degraded path here
increments `nora_identity_enrichment_degraded_total{platform,reason}`, and the
`nora_identity_aliases{platform}` gauge is published on EVERY path including
total failure, so a silent regression is visible as `== 1` rather than as an
absent series. See `nora/obs/metric_specs.py` for why both are needed.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import aiohttp

from ...domain.identity import Identifier

log = logging.getLogger("nora.signal.self_id")

DEGRADED_METRIC = "nora_identity_enrichment_degraded_total"
ALIASES_METRIC = "nora_identity_aliases"

_CONTACTS_ID = "nora-self-contacts"
_STATUS_ID = "nora-self-status"

# signal-cli contact-row field -> the `id_kinds("signal")` vocabulary. Kept as a
# table so a new id form is one row, and so `Identifier` — not this module —
# stays the single place that decides whether a kind is matchable at all.
_CONTACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("number", "phone"),
    ("uuid", "uuid"),
    # `username` is deliberately ABSENT: it is not a matchable kind on any
    # platform (see `nora.domain.identity._ID_KINDS`), and building an
    # `Identifier` for it would raise `UnmatchableIdentifierError`.
)


class MetricsSink(Protocol):
    """The two-method slice of `nora.obs.metrics.Metrics` this module needs.

    Structural, like `nora.platform.registry._PlatformsConfig`, so `nora.platform`
    states its dependency without importing `nora.obs`. It is deliberately NOT
    optional and has no null default: a caller who forgets to pass metrics gets
    a `TypeError`, not a silently uncounted degradation — which is the whole
    point of R-17.
    """

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...
    def gauge(self, name: str, value: float, **labels: object) -> None: ...


async def _rpc_batch(
    base_url: str,
    primary: str,
    timeout: float,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> list[Any]:
    """One POST, both methods. See the module docstring for why both."""
    payload = [
        {
            "jsonrpc": "2.0",
            "id": _CONTACTS_ID,
            "method": "listContacts",
            "params": {"recipient": primary},
        },
        {
            "jsonrpc": "2.0",
            "id": _STATUS_ID,
            "method": "getUserStatus",
            "params": {"recipient": [primary]},
        },
    ]
    url = f"{base_url.rstrip('/')}/api/v1/rpc"
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session, session.post(url, json=payload) as resp:
        resp.raise_for_status()
        body = await resp.json()
    # A daemon is free to answer a batch with a single object (e.g. one
    # top-level error); normalise so the caller has one shape to read.
    return body if isinstance(body, list) else [body]


def _results(body: list[Any]) -> dict[str, Any]:
    """id -> result, keeping only entries that carry one. A JSON-RPC error
    object has no `result` key at all, so it drops out here and surfaces to the
    caller as a missing id — counted as that call's own degradation reason,
    never as a success with empty data."""
    return {
        str(entry.get("id")): entry["result"]
        for entry in body
        if isinstance(entry, dict) and "result" in entry
    }


def _self_row(result: Any, primary: str) -> dict[str, Any] | None:
    """Nora's own row out of a `listContacts` result. Matched on `number` OR
    `uuid`, because `platforms.signal.account` may legitimately be configured
    as either form."""
    if not isinstance(result, list):
        return None
    for row in result:
        if isinstance(row, dict) and primary in (row.get("number"), row.get("uuid")):
            return row
    return None


def _phone_number_identity(result: Any, primary: str) -> str | None:
    """The PNI from a `getUserStatus` result — NOT the ACI; module docstring."""
    if not isinstance(result, list):
        return None
    for row in result:
        if not isinstance(row, dict):
            continue
        if primary not in (row.get("recipient"), row.get("number")):
            continue
        value = row.get("uuid")
        return value if isinstance(value, str) and value else None
    return None


async def derive_identifiers(
    base_url: str,
    primary: str,
    *,
    metrics: MetricsSink,
    platform: str = "signal",
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> frozenset[Identifier]:
    """Every typed id form the daemon reports for `primary`.

    Never raises and never blocks startup: `primary` is already known by the
    time this runs, so this is enrichment, not a precondition. It always
    publishes `nora_identity_aliases` and increments
    `nora_identity_enrichment_degraded_total` for each thing it could not get.
    """
    identifiers: set[Identifier] = set()

    def _degraded(reason: str) -> None:
        metrics.inc(DEGRADED_METRIC, platform=platform, reason=reason)

    def _publish() -> frozenset[Identifier]:
        values = {i.value for i in identifiers} | {primary}
        metrics.gauge(ALIASES_METRIC, float(len(values)), platform=platform)
        return frozenset(identifiers)

    try:
        body = await _rpc_batch(base_url, primary, timeout)
    except Exception as exc:  # noqa: BLE001 -- enrichment is never fatal; it is counted instead
        log.warning(
            "could not derive Nora's own id forms from the signal-cli daemon "
            "at %s: %s — R-12 falls back to equality against %s",
            base_url, exc, primary,
        )
        _degraded("unreachable")
        return _publish()

    results = _results(body)

    row = _self_row(results.get(_CONTACTS_ID), primary)
    if row is None:
        log.warning("listContacts returned no row for Nora's own account %s", primary)
        _degraded("no_self_contact_row")
    else:
        for field_name, kind in _CONTACT_FIELDS:
            value = row.get(field_name)
            if isinstance(value, str) and value:
                identifiers.add(Identifier(platform, kind, value))

    pni = _phone_number_identity(results.get(_STATUS_ID), primary)
    if pni is None:
        log.warning("getUserStatus returned no phone-number identity for %s", primary)
        _degraded("no_phone_number_identity")
    else:
        # A PNI is a uuid-kind value in `id_kinds("signal")`, distinct from the
        # ACI collected above. Both must be matchable — module docstring.
        identifiers.add(Identifier(platform, "uuid", pni))

    return _publish()
