"""Derive Nora's own Signal account from the daemon (spec C.3, D.3).

Failure is a startup failure: without the account, own-message filtering (R-12) is
unsound and Nora could trigger herself.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("nora.signal.account")


class AccountError(Exception):
    """Could not determine Nora's own account — refuse to start."""


def _extract(entry) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("number") or entry.get("account") or ""
    return ""


async def derive_account(
    base_url: str,
    *,
    override: str = "",
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> str:
    if override:
        log.info("using the configured platforms.signal.account override")
        return override
    url = f"{base_url.rstrip('/')}/api/v1/accounts"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session, session.get(url) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    except Exception as exc:
        raise AccountError(f"could not reach the signal-cli daemon at {url}: {exc}") from exc

    accounts = [a for a in (_extract(e) for e in (payload or [])) if a]
    if not accounts:
        raise AccountError(f"no account registered on the signal-cli daemon at {url}")
    if len(accounts) > 1:
        raise AccountError(
            f"multiple accounts on the daemon ({accounts}); "
            "set platforms.signal.account in config.toml"
        )
    log.info("derived Signal account from the daemon")
    return accounts[0]


async def derive_aliases(
    base_url: str,
    primary: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> frozenset[str]:
    """Every id form the daemon reports for `primary` — signal-cli sometimes pairs
    a phone number with a UUID for the same account. See `nora.domain.ports`'s
    module docstring for why `PlatformIdentity.aliases` exists: R-12 must test
    membership, not equality, or self-recognition breaks the moment a second
    platform is wired in.

    Reads the same `/api/v1/accounts` endpoint `derive_account` uses; never writes
    to the daemon. Unlike `derive_account`, an unreachable daemon (or one that
    reports nothing beyond the bare number) is not a startup failure here —
    `primary` is already known by the time this runs, so this is best-effort
    enrichment, not a precondition, and always degrades to `{primary}`.
    """
    url = f"{base_url.rstrip('/')}/api/v1/accounts"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session, session.get(url) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    except Exception as exc:            # noqa: BLE001 -- best-effort enrichment, never fatal
        log.warning("could not derive alias id forms from the daemon: %s", exc)
        return frozenset({primary})

    for entry in payload or []:
        if not isinstance(entry, dict):
            continue
        number = entry.get("number") or entry.get("account") or ""
        if number != primary:
            continue
        forms = {v for v in (entry.get("number"), entry.get("account"), entry.get("uuid")) if v}
        forms.add(primary)
        return frozenset(forms)
    return frozenset({primary})
