"""Frame codec for the wake-gate TCP seam — the Python half of
`deploy/voice-sidecar/src/recv.js`'s `createFrameParser`.

Frame layout (see `deploy/voice-sidecar/TRANSPORT.md`):

    [u32be payload_len][u8 user_id_len][user_id utf8][pcm bytes]

TCP plus the length prefix guarantees ordering and message boundaries, so an
utterance's worth of frames arrives intact; this parser's only job is to cut
an arbitrary byte stream back into `(user_id, pcm)` frames. Pure — no
sockets, no I/O — so the codec is unit-testable against a recorded fixture.

**WHICH GUILD a connection carries** is announced once, in a HELLO frame the
sidecar writes immediately on connect. Its payload
is a `user_id_len` of ZERO followed by the guild id:

    [u32be payload_len][u8 0][guild_id utf8]

A zero-length user id cannot occur in a real audio frame — Discord never
issues an empty snowflake — so the length byte discriminates the two without
widening the layout. Announcing it ONCE rather than on every frame is what
keeps 20 ms of audio at 3840 bytes with a 2-byte header: one sidecar
connection belongs to exactly one guild for its lifetime, because the pump is
created per join. The HELLO is what lets one wake gate serve several guilds.
"""

from __future__ import annotations

import struct

_HEADER = 4


class FrameParser:
    """Incremental parser: `feed()` arbitrary TCP chunks, get complete frames."""

    def __init__(self) -> None:
        self._buf = bytearray()
        #: The guild this connection announced, or `None` until its HELLO
        #: arrives. Per-parser because a parser is built per connection, which
        #: is what makes one gate able to serve several guilds at once.
        self.guild_id: str | None = None

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        """Complete AUDIO frames. A HELLO is consumed into `guild_id` and
        never returned — it carries no audio, and a caller that treated it as
        a frame would arm a wake model for an empty speaker."""
        self._buf += chunk
        frames: list[tuple[str, bytes]] = []
        while True:
            if len(self._buf) < _HEADER:
                break
            (payload_len,) = struct.unpack_from(">I", self._buf, 0)
            if len(self._buf) < _HEADER + payload_len:
                break
            payload = self._buf[_HEADER : _HEADER + payload_len]
            if payload_len < 1:
                # No length byte at all: skipped (raising would close the seam
                # connection, and every guild on that pump would lose audio
                # until a reconnect). The JS half skips it too.
                del self._buf[: _HEADER + payload_len]
                continue
            id_len = payload[0]
            if id_len == 0:
                # HELLO: the rest of the payload is the guild id.
                self.guild_id = bytes(payload[1:]).decode("utf-8") or None
                del self._buf[: _HEADER + payload_len]
                continue
            user_id = bytes(payload[1 : 1 + id_len]).decode("utf-8")
            pcm = bytes(payload[1 + id_len :])
            del self._buf[: _HEADER + payload_len]
            frames.append((user_id, pcm))
        return frames


def encode_hello(guild_id: str) -> bytes:
    """The HELLO the sidecar writes on connect, mirroring `recv.js`'s
    `encodeHello`. Test fixtures and the smoke path build it here so both
    halves of the seam are encoded from one definition."""
    ident = guild_id.encode("utf-8")
    if not ident:
        raise ValueError("a hello frame needs a guild id")
    payload = bytes([0]) + ident
    return struct.pack(">I", len(payload)) + payload


def encode_frame(user_id: str, pcm: bytes) -> bytes:
    """The encoder half, mirroring `recv.js`'s `encodeFrame` — test fixtures
    and the smoke path build frames with exactly the sidecar's layout."""
    ident = user_id.encode("utf-8")
    if not ident:
        # A zero length byte is the HELLO discriminator, so an empty user id
        # would encode as one and be silently swallowed by the parser.
        raise ValueError("user_id must not be empty (0 is the hello marker)")
    if len(ident) > 255:
        raise ValueError("user_id too long for frame")
    payload = bytes([len(ident)]) + ident + pcm
    return struct.pack(">I", len(payload)) + payload
