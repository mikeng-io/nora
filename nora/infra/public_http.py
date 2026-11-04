"""Connection-bound destination policy for anonymous external HTTP fetches."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aiohttp.abc import ResolveResult
    from aiohttp.tracing import Trace


def is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_reserved


class PublicTCPConnector(aiohttp.TCPConnector):
    """Vet the numeric answers aiohttp will connect to, including IP literals."""

    async def _resolve_host(
        self,
        host: str,
        port: int,
        traces: Sequence[Trace] | None = None,
    ) -> list[ResolveResult]:
        answers = await super()._resolve_host(host, port, traces)
        if not answers or any(not is_public_address(a["host"]) for a in answers):
            raise aiohttp.ClientError("non-public destination")
        return answers
