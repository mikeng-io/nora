"""A subprocess that exposes the serving and shutdown phases to signal tests."""

from __future__ import annotations

import asyncio
import os

from nora.__main__ import run_until_stopped


class _App:
    async def run(self) -> None:
        print("READY", flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            print("SERVING_STOPPED", flush=True)

    async def shutdown(self) -> None:
        print("DRAINING", flush=True)
        if os.environ["PROBE_BLOCK_SHUTDOWN"] == "1":
            await asyncio.Event().wait()
        print("STOPPED", flush=True)


async def main() -> None:
    app = _App()
    if os.environ["PROBE_WITHOUT_HANDLER"] == "1":
        await app.run()
    else:
        await run_until_stopped(app)


if __name__ == "__main__":
    asyncio.run(main())
