"""Runtime scheduling on the UTC grid."""

import asyncio
from collections.abc import AsyncIterator

from nora.domain.ticks import seconds_to_boundary


async def aligned_ticks(interval: float, *, immediate: bool = False) -> AsyncIterator[None]:
    if immediate:
        yield
    while True:
        await asyncio.sleep(seconds_to_boundary(interval))
        yield
