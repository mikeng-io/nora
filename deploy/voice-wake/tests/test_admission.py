"""Bounded admission and the authenticated seam (security(voice), Aikido round 2).

Falsification: drop the in-flight cap in `_fire` and the third speaker starts
a task; drop the per-user check and a repeat wake starts a second; drop
`close()` and the blocked utterance outlives shutdown; drop the seam cap and
the ninth connection is served; drop the read deadline and a stalled frame
holds its connection forever.
"""

# ruff: noqa: S101, S105 - synthetic protocol tests use assertions and a fake bearer.

import asyncio
from types import SimpleNamespace

import numpy as np
from wakegate.codec import encode_frame, encode_hello
from wakegate.service import Config, WakeService
from wakegate.stt import forward_utterance

TOKEN = "test-bridge-token"
GUILD = "100000000000000203"
# One real 20 ms frame: 960 stereo s16le samples, 3840 bytes. Wire frames
# can also contain longer valid decoded Opus packets.
FRAME = np.full(1920, 3000, dtype=np.int16).tobytes()


def _service(**overrides) -> WakeService:
    cfg = {"model_path": "/nonexistent/model.onnx", "vad_enabled": False, "bridge_token": TOKEN}
    cfg.update(overrides)
    return WakeService(Config(**cfg))


def _state():
    """The part of a user's state that `_fire` touches before it admits."""
    return SimpleNamespace(model=SimpleNamespace(reset=lambda: None), vad=None, wakes=0)


class _Blocking:
    """Stands in for the utterance pipeline: holds until released, and records
    whether it was cancelled, so shutdown can be checked."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = 0
        self.cancelled = 0

    async def __call__(self, key, utterance, state):
        self.started += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


async def test_utterances_beyond_the_cap_are_refused_not_queued():
    svc = _service(max_utterances=2)
    blocking = _Blocking()
    svc._handle_utterance = blocking  # type: ignore[method-assign]

    svc._fire((GUILD, "u1"), object(), _state())
    svc._fire((GUILD, "u2"), object(), _state())
    svc._fire((GUILD, "u3"), object(), _state())
    await asyncio.sleep(0)

    assert blocking.started == 2
    assert len(svc._inflight) == 2
    blocking.release.set()
    await svc.close()


async def test_a_second_wake_from_a_speaker_still_in_flight_is_refused():
    svc = _service(max_utterances=4)
    blocking = _Blocking()
    svc._handle_utterance = blocking  # type: ignore[method-assign]

    svc._fire((GUILD, "u1"), object(), _state())
    svc._fire((GUILD, "u1"), object(), _state())
    await asyncio.sleep(0)

    assert blocking.started == 1
    blocking.release.set()
    await svc.close()


async def test_a_finished_utterance_frees_its_slot():
    svc = _service(max_utterances=1)

    async def quick(key, utterance, state):
        return None

    svc._handle_utterance = quick  # type: ignore[method-assign]
    svc._fire((GUILD, "u1"), object(), _state())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert svc._inflight == {}

    svc._fire((GUILD, "u2"), object(), _state())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert svc._inflight == {}


async def test_close_cancels_and_awaits_the_utterances_in_flight():
    svc = _service(max_utterances=4)
    blocking = _Blocking()
    svc._handle_utterance = blocking  # type: ignore[method-assign]
    svc._fire((GUILD, "u1"), object(), _state())
    svc._fire((GUILD, "u2"), object(), _state())
    await asyncio.sleep(0)

    await asyncio.wait_for(svc.close(), timeout=2)

    assert blocking.cancelled == 2
    assert svc._inflight == {}


class _FakeReader:
    """Serves scripted chunks, then either ends or stalls forever."""

    def __init__(self, chunks, stall_at_end: bool = False) -> None:
        self._chunks = list(chunks)
        self._stall = stall_at_end

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        if self._stall:
            await asyncio.Event().wait()
        return b""


class _FakeWriter:
    def __init__(self) -> None:
        self.closed = False

    def get_extra_info(self, _name):
        return ("127.0.0.1", 50000)

    def close(self) -> None:
        self.closed = True


async def test_a_hello_with_the_wrong_token_closes_the_seam_before_any_frame_is_scored():
    svc = _service()
    writer = _FakeWriter()
    frame_scored = []

    async def on_frame(key, pcm):
        frame_scored.append(key)

    svc._on_frame = on_frame  # type: ignore[method-assign]
    reader = _FakeReader([
        encode_hello(GUILD, "wrong-token") + encode_frame("u1", FRAME),
    ])

    await svc._on_client(reader, writer)

    assert frame_scored == []
    assert writer.closed is True
    assert svc._clients == 0


async def test_an_authenticated_hello_then_frames_reach_the_frame_path_under_the_guild():
    svc = _service()
    writer = _FakeWriter()
    seen = []

    async def on_frame(key, pcm):
        seen.append(key)

    svc._on_frame = on_frame  # type: ignore[method-assign]
    reader = _FakeReader([
        encode_hello(GUILD, TOKEN),
        encode_frame("u1", FRAME) + encode_frame("u2", FRAME),
    ])

    await svc._on_client(reader, writer)

    assert seen == [(GUILD, "u1"), (GUILD, "u2")]


async def test_the_seam_refuses_connections_beyond_its_cap():
    svc = _service(max_seam_clients=0)
    writer = _FakeWriter()

    reader = _FakeReader([encode_hello(GUILD, TOKEN)])
    await svc._on_client(reader, writer)

    assert reader._chunks == [encode_hello(GUILD, TOKEN)], "refused before any read"

    assert writer.closed is True
    assert svc._clients == 0


async def test_a_frame_stalled_mid_way_is_cut_by_the_read_deadline():
    svc = _service(frame_read_timeout_s=0.05)
    writer = _FakeWriter()
    # The hello, then the first half of a frame, then silence forever.
    partial = encode_frame("u1", FRAME)[:100]
    reader = _FakeReader([encode_hello(GUILD, TOKEN), partial], stall_at_end=True)

    await asyncio.wait_for(svc._on_client(reader, writer), timeout=2)

    assert writer.closed is True
    assert svc._clients == 0


async def test_an_idle_seam_is_not_cut_by_the_read_deadline():
    """A sidecar is silent between utterances; only a part-received frame has a deadline."""
    svc = _service(frame_read_timeout_s=0.05)
    writer = _FakeWriter()
    reader = _FakeReader([encode_hello(GUILD, TOKEN)], stall_at_end=True)

    task = asyncio.create_task(svc._on_client(reader, writer))
    await asyncio.sleep(0.2)

    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class _Response:
    status = 202

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


async def test_the_utterance_forward_carries_the_service_bearer():
    session = _Session()

    ok = await forward_utterance(
        session, nora_url="http://nora:9100", user_id="u1", text="hi",
        guild_id=GUILD, token=TOKEN,
    )

    assert ok is True
    url, kwargs = session.calls[0]
    assert url == "http://nora:9100/app/v1/voice/utterance"
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}


async def test_slow_drip_cannot_extend_a_frame_deadline():
    svc = _service(frame_read_timeout_s=0.05)
    writer = _FakeWriter()

    class Drip(_FakeReader):
        async def read(self, n):
            if self._chunks:
                return self._chunks.pop(0)
            await asyncio.sleep(0.02)
            return b"x"

    reader = Drip([encode_hello(GUILD, TOKEN), encode_frame("u1", FRAME)[:6]])
    await asyncio.wait_for(svc._on_client(reader, writer), timeout=0.3)
    assert writer.closed


async def test_shutdown_cancels_tail_timers_and_refuses_late_wakes():
    svc = _service()
    blocking = _Blocking()
    svc._handle_utterance = blocking
    await svc.close()
    svc._fire((GUILD, "u1"), object(), _state())
    await asyncio.sleep(0)
    assert blocking.started == 0


async def test_empty_frames_cannot_extend_hello_deadline_or_hold_all_eight_slots():
    svc = _service()
    seen = asyncio.Event()

    async def on_frame(key, pcm):
        seen.set()

    svc._on_frame = on_frame
    all_connected = asyncio.Event()
    connected = 0

    async def on_client(reader, writer):
        nonlocal connected
        connected += 1
        if connected == 8:
            all_connected.set()
        await svc._on_client(reader, writer)

    server = await asyncio.start_server(on_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    sockets = []

    async def drip(writer):
        try:
            while True:
                writer.write(b"\x00" * 4)
                await writer.drain()
                await asyncio.sleep(0.1)
        except (ConnectionError, OSError):
            pass

    tasks = []
    try:
        for _ in range(8):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            sockets.append((reader, writer))
            tasks.append(asyncio.create_task(drip(writer)))
        await asyncio.wait_for(all_connected.wait(), 1)
        assert svc._clients == 8
        # Traffic continues beyond the absolute HELLO deadline. Each socket
        # must close while the peer is still sending, not when the peer leaves.
        closed = await asyncio.wait_for(
            asyncio.gather(*(reader.read(1) for reader, _ in sockets)),
            svc._cfg.frame_read_timeout_s + 1,
        )
        assert closed == [b""] * 8
        assert svc._clients == 0
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        sockets.append((reader, writer))
        writer.write(encode_hello(GUILD, TOKEN) + encode_frame("u1", FRAME))
        await writer.drain()
        await asyncio.wait_for(seen.wait(), 0.5)
        assert svc._clients == 1
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _, writer in sockets:
            writer.close()
        await asyncio.gather(*(w.wait_closed() for _, w in sockets), return_exceptions=True)
        server.close()
        await server.wait_closed()
        await svc.close()
