"""The seam codec against a RECORDED fixture: tests/fixtures/seam_frames.bin
was written by the sidecar's own `encodeFrame` (deploy/voice-sidecar/src/
recv.js, via node), so a parse failure here means the Python half drifted
from the wire format, not from a re-implemented encoder that drifted with it.

Fixture content: 7 frames for user "111222333444555666" and 3 for user
"297528967164586076", each 3840 bytes of deterministic PCM (the same
`(seed * 7919 + i) % 65536 - 32768` formula, re-derived below — counts alone
would pass a parser that swapped payloads between users).
"""

from pathlib import Path

import numpy as np

from wakegate.codec import FrameParser, encode_frame

FIXTURE = Path(__file__).parent / "fixtures" / "seam_frames.bin"
USER_A = "111222333444555666"
USER_B = "297528967164586076"
FRAME_BYTES = 3840


def _expected_pcm(seed: int) -> bytes:
    values = [((seed * 7919 + i) % 65536) - 32768 for i in range(0, FRAME_BYTES, 2)]
    return np.array(values, dtype=np.int16).tobytes()


def _parse_in_chunks(data: bytes, chunk_size: int) -> list[tuple[str, bytes]]:
    parser = FrameParser()
    frames = []
    for i in range(0, len(data), chunk_size):
        frames.extend(parser.feed(data[i : i + chunk_size]))
    return frames


def test_fixture_parses_into_the_exact_frames_the_sidecar_wrote():
    frames = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=1 << 20)
    assert [user_id for user_id, _ in frames] == [USER_A] * 7 + [USER_B] * 3
    for index, (_, pcm) in enumerate(frames):
        seed = index if index < 7 else 100 + index - 7
        assert pcm == _expected_pcm(seed)


def test_per_user_counters_match_the_fixture():
    frames = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=1 << 20)
    per_user: dict[str, list[int]] = {}
    for user_id, pcm in frames:
        stats = per_user.setdefault(user_id, [0, 0])
        stats[0] += 1
        stats[1] += len(pcm)
    assert per_user == {USER_A: [7, 7 * FRAME_BYTES], USER_B: [3, 3 * FRAME_BYTES]}


def test_partial_chunks_reassemble_the_same_frames():
    """TCP delivers arbitrary chunkings; 97 bytes is deliberately coprime
    with the frame size so headers and payloads both straddle reads."""
    whole = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=1 << 20)
    dribbled = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=97)
    assert dribbled == whole


def test_encode_frame_round_trips_through_the_parser():
    parser = FrameParser()
    assert parser.feed(encode_frame("u1", b"\x01\x02")) == [("u1", b"\x01\x02")]
