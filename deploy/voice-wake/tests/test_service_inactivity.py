"""The wall-clock end-pointer: a burst-gated source (the Discord client)
stops sending frames after the phrase, and the utterance must still end.

Drives REAL 20 ms sidecar frames through `WakeService._on_frame` with a
stub scorer in place of the ONNX model (the model's own measurement is
`test_wake_model.py`). Falsification: revert `_arm_inactivity` and the
first test hangs on `wait_for`; make the timer fire on every frame instead
of re-arming and the second test sees an utterance mid-phrase.
"""

import asyncio

import numpy as np
import pytest
from wakegate import service as service_mod
from wakegate.service import Config, WakeService

GUILD = "100000000000000203"
FRAME = np.full(1920 * 2, 3000, dtype=np.int16).tobytes()  # 20 ms, 48 kHz stereo, loud
TAIL_MS = 100


class _StubModel:
    """Scores every chunk high; the service's scorer reads the first value."""

    def __init__(self, score: float) -> None:
        self._score = score
        self.resets = 0

    def predict(self, _chunk):
        return {"喂Nora": self._score}

    def reset(self) -> None:
        self.resets += 1


@pytest.fixture
def service(monkeypatch, tmp_path):
    model_file = tmp_path / "喂Nora.onnx"
    model_file.write_bytes(b"stub")
    stub = _StubModel(0.9)
    monkeypatch.setattr(service_mod, "_load_model", lambda _path: stub)
    svc = WakeService(Config(model_path=str(model_file), vad_enabled=False,
                             silence_tail_ms=TAIL_MS, silence_rms=100.0))
    fired: list = []

    async def record(user_id, utterance, state):
        fired.append((user_id, utterance))

    svc._handle_utterance = record  # type: ignore[method-assign]
    return svc, fired, stub


async def _feed(svc, n, user="u1"):
    for _ in range(n):
        await svc._on_frame((GUILD, user), FRAME)


async def test_an_utterance_ends_when_the_source_stops_sending_frames(service):
    svc, fired, stub = service
    await _feed(svc, 8)  # two 80 ms blocks: the first scores 0.9 and fires
    assert svc._users[(GUILD, "u1")].gate.collecting
    assert fired == []  # no quiet chunk ever arrived; the chunk tail cannot end it
    await asyncio.wait_for(_until(lambda: fired), timeout=1.0)
    user_id, utterance = fired[0]
    assert user_id == (GUILD, "u1")  # keyed by (guild, user)
    assert utterance.peak_score == 0.9
    assert not svc._users[(GUILD, "u1")].gate.collecting
    assert stub.resets == 1  # the model was reset exactly as a tail-ended utterance would
    # /health keys users by "<guild>/<user>": one gate serves several
    # guilds, so a bare user id could name two different speakers.
    health = svc._health_payload()
    assert health["users"][f"{GUILD}/u1"]["wakes"] == 1
    assert health["guilds"] == [GUILD]


async def test_frames_keep_re_arming_the_timer_so_a_phrase_is_not_cut(service):
    svc, fired, _ = service
    await _feed(svc, 4)  # fires
    for _ in range(6):  # 300 ms of frames at 50 ms spacing, each under the 100 ms tail
        await asyncio.sleep(0.05)
        await _feed(svc, 1)
    assert fired == []
    await asyncio.wait_for(_until(lambda: fired), timeout=1.0)
    assert len(fired) == 1


async def test_a_reaped_user_does_not_fire_from_a_stale_timer(service):
    svc, fired, _ = service
    await _feed(svc, 4)
    state = svc._users.pop((GUILD, "u1"))
    assert state.inactivity is not None
    await asyncio.sleep(TAIL_MS / 1000 + 0.05)
    assert fired == []


async def _until(cond):
    while not cond():
        await asyncio.sleep(0.01)
