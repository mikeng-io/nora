"""Derive Nora's own Signal account from the daemon (spec C.3, D.3).

Failure is a startup failure: without the account, own-message filtering (R-12) is
unsound and Nora could trigger herself.

**On this daemon there is no discovery path — `platforms.signal.account` in
config.toml is load-bearing.** `/api/v1/accounts` returns 404 on signal-cli
0.14.7 and always has; `--http` serves exactly three endpoints (`/api/v1/rpc`,
`/api/v1/events`, `/api/v1/check`) per `signal-cli-jsonrpc(5)`. The JSON-RPC
`listAccounts` method that would answer "which accounts exist" is
multi-account-mode only and returns `-32601 Method not implemented` here,
because the daemon is pinned to one account with `-a`. A single-account daemon
has no RPC that reveals which account it is pinned to, so the GET below is kept
only for a hypothetical daemon that does serve it, and the override is the real
path. See `nora.platform.signal.self_id` for the full endpoint survey — that
module is the one that got a working replacement.
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
        raise AccountError(
            f"could not read {url}: {exc}. signal-cli 0.14.7 does not serve this "
            "endpoint at all (only /api/v1/rpc, /api/v1/events, /api/v1/check) and "
            "its listAccounts RPC is multi-account-mode only — set "
            "platforms.signal.account in config.toml"
        ) from exc

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


# Alias derivation speaks JSON-RPC and returns typed identifiers —
# `nora.platform.signal.self_id.derive_identifiers` (R-17).
