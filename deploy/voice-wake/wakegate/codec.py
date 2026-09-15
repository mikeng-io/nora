"""Frame codec for the wake-gate TCP seam — the Python half of
`deploy/voice-sidecar/src/recv.js`'s `createFrameParser`.

Frame layout (see `deploy/voice-sidecar/TRANSPORT.md`):

    [u32be payload_len][u8 user_id_len][user_id utf8][pcm bytes]

TCP plus the length prefix guarantees ordering and message boundaries, so an
utterance's worth of frames arrives intact; this parser's only job is to cut
an arbitrary byte stream back into `(user_id, pcm)` frames. Pure — no
sockets, no I/O — so the codec is unit-testable against a recorded fixture.
"""

from __future__ import annotations

import struct

_HEADER = 4


class FrameParser:
    """Incremental parser: `feed()` arbitrary TCP chunks, get complete frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        self._buf += chunk
        frames: list[tuple[str, bytes]] = []
        while True:
            if len(self._buf) < _HEADER:
                break
            (payload_len,) = struct.unpack_from(">I", self._buf, 0)
            if len(self._buf) < _HEADER + payload_len:
                break
            payload = self._buf[_HEADER : _HEADER + payload_len]
            id_len = payload[0]
            user_id = bytes(payload[1 : 1 + id_len]).decode("utf-8")
            pcm = bytes(payload[1 + id_len :])
            del self._buf[: _HEADER + payload_len]
            frames.append((user_id, pcm))
        return frames


def encode_frame(user_id: str, pcm: bytes) -> bytes:
    """The encoder half, mirroring `recv.js`'s `encodeFrame` — test fixtures
    and the smoke path build frames with exactly the sidecar's layout."""
    ident = user_id.encode("utf-8")
    if len(ident) > 255:
        raise ValueError("user_id too long for frame")
    payload = bytes([len(ident)]) + ident + pcm
    return struct.pack(">I", len(payload)) + payload
