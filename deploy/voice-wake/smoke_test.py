"""Smoke test: synthetic frames through the REAL TCP seam, end to end.

Boots WakeService on ephemeral ports with the baked-in model, connects as
the sidecar would, pumps 10 silence frames for one user, and asserts the
/health counters moved — the same reading the sidecar's own smoke_test.mjs
makes from the producer side. Run: `uv run python smoke_test.py`.
"""

import asyncio
import socket

import aiohttp
import numpy as np

from wakegate.codec import encode_frame
from wakegate.service import Config, WakeService

LISTEN_PORT = 45991
HEALTH_PORT = 46011
FRAMES = 10


async def _health(session: aiohttp.ClientSession) -> dict:
    async with session.get(f"http://127.0.0.1:{HEALTH_PORT}/health") as resp:
        return await resp.json()


async def main() -> None:
    service = WakeService(
        Config(
            listen_host="127.0.0.1", listen_port=LISTEN_PORT,
            health_port=HEALTH_PORT, model_path="models/喂Nora.onnx",
        )
    )
    task = asyncio.create_task(service.run())
    await asyncio.sleep(1.5)  # listener + health app up

    frame_pcm = np.zeros(1920, dtype=np.int16).tobytes()  # 20 ms of silence
    async with aiohttp.ClientSession() as session:
        with socket.create_connection(("127.0.0.1", LISTEN_PORT), timeout=5) as sock:
            for _ in range(FRAMES):
                sock.sendall(encode_frame("smoke-user", frame_pcm))
            # Half a frame proves the parser holds partial data without
            # losing it.
            sock.sendall(encode_frame("smoke-user", frame_pcm)[:100])

            health = {}
            for _ in range(50):
                await asyncio.sleep(0.2)
                health = await _health(session)
                user = health["users"].get("smoke-user")
                if user and user["frames"] == FRAMES:
                    break
            else:
                raise SystemExit(f"FAIL: counters never reached {FRAMES}: {health}")

            assert health["status"] == "ok" and health["wake"] == "listening", health
            assert health["clients"] == 1, health
            assert user["bytes"] == FRAMES * 3840, user
        # After the client leaves, the seam sees the disconnect.
        await asyncio.sleep(0.3)
        assert (await _health(session))["clients"] == 0
    print(f"OK: {FRAMES} frames, {user['bytes']} bytes, wake={health['wake']}")
    task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
