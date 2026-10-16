"""The seam codec against a RECORDED fixture: tests/fixtures/seam_frames.bin
was written by the sidecar's own `encodeFrame` (deploy/voice-sidecar/src/
recv.js, via node), so a parse failure here means the Python half drifted
from the wire format, not from a re-implemented encoder that drifted with it.

Fixture content: 7 frames for user "111222333444555666" and 3 for user
"287278638861462136", each 3840 bytes of deterministic PCM (the same
`(seed * 7919 + i) % 65536 - 32768` formula, re-derived below — counts alone
would pass a parser that swapped payloads between users).
"""

from pathlib import Path

import numpy as np

import pytest

from wakegate.codec import FrameParser, encode_frame, encode_hello

FIXTURE = Path(__file__).parent / "fixtures" / "seam_frames.bin"

#: The fixture is a RECORDING of frames a real sidecar wrote and is not
#: committed, so the tests that read it skip when it is absent rather than fail.
#:
#: Never regenerate it from `encode_frame`: its value is that it came from the
#: other implementation; synthesizing it would check the codec against itself.
_needs_fixture = pytest.mark.skipif(
    not FIXTURE.is_file(),
    reason="tests/fixtures/seam_frames.bin is not committed — re-record it from "
           "a live sidecar (deploy/voice-sidecar/consume.mjs) to re-enable",
)
USER_A = "111222333444555666"
USER_B = "287278638861462136"
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


@_needs_fixture
def test_fixture_parses_into_the_exact_frames_the_sidecar_wrote():
    frames = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=1 << 20)
    assert [user_id for user_id, _ in frames] == [USER_A] * 7 + [USER_B] * 3
    for index, (_, pcm) in enumerate(frames):
        seed = index if index < 7 else 100 + index - 7
        assert pcm == _expected_pcm(seed)


@_needs_fixture
def test_per_user_counters_match_the_fixture():
    frames = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=1 << 20)
    per_user: dict[str, list[int]] = {}
    for user_id, pcm in frames:
        stats = per_user.setdefault(user_id, [0, 0])
        stats[0] += 1
        stats[1] += len(pcm)
    assert per_user == {USER_A: [7, 7 * FRAME_BYTES], USER_B: [3, 3 * FRAME_BYTES]}


@_needs_fixture
def test_partial_chunks_reassemble_the_same_frames():
    """TCP delivers arbitrary chunkings; 97 bytes is deliberately coprime
    with the frame size so headers and payloads both straddle reads."""
    whole = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=1 << 20)
    dribbled = _parse_in_chunks(FIXTURE.read_bytes(), chunk_size=97)
    assert dribbled == whole


def test_encode_frame_round_trips_through_the_parser():
    parser = FrameParser()
    assert parser.feed(encode_frame("u1", b"\x01\x02")) == [("u1", b"\x01\x02")]


# --- the HELLO frame (multi-guild) -------------------------------------------
# Twin of `deploy/voice-sidecar/test/codec.test.mjs`. One wire format, two
# implementations: a change to either is a change to both.


def test_a_hello_names_the_guild_and_is_not_an_audio_frame():
    parser = FrameParser()

    frames = parser.feed(
        encode_hello("100000000000000203")
        + encode_frame("u1", b"\x01\x02")
        + encode_frame("u2", b"\x03")
    )

    assert parser.guild_id == "100000000000000203"
    assert frames == [("u1", b"\x01\x02"), ("u2", b"\x03")]


def test_the_hello_survives_being_split_across_chunk_boundaries():
    blob = encode_hello("100000000000000203") + encode_frame("u1", b"\x07" * 3840)
    parser = FrameParser()

    frames = []
    for i in range(len(blob)):
        frames += parser.feed(blob[i : i + 1])

    assert parser.guild_id == "100000000000000203"
    assert frames == [("u1", b"\x07" * 3840)]


def test_the_guild_is_none_until_a_hello_arrives():
    """A gate must be able to tell "not announced yet" from a guild, or it
    would attribute the first utterance to whatever it saw last."""
    parser = FrameParser()

    parser.feed(encode_frame("u1", b"\x01"))

    assert parser.guild_id is None


def test_an_empty_user_id_is_refused_because_zero_is_the_hello_marker():
    with pytest.raises(ValueError, match="hello marker"):
        encode_frame("", b"\x01")


def test_a_hello_with_no_guild_id_is_refused():
    with pytest.raises(ValueError, match="needs a guild id"):
        encode_hello("")


def test_the_audio_frame_layout_is_byte_identical_to_what_it_was():
    """The hello reused the existing length byte rather than widening the
    frame. If this changes, every deployed sidecar mis-parses audio."""
    assert encode_frame("u1", b"\xab").hex() == "00000004027531ab"
    assert encode_hello("g9").hex() == "00000003006739"
