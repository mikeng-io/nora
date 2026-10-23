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

    [u32be payload_len][u8 0][guild_id utf8][\n][token utf8]

The token is the shared service bearer (NORA_BRIDGE_TOKEN). A connection whose
hello does not carry it is refused before any audio is read, and a connection
may announce only one guild.

A zero-length user id cannot occur in a real audio frame — Discord never
issues an empty snowflake — so the length byte discriminates the two without
widening the layout. Announcing it ONCE rather than on every frame is what
keeps 20 ms of audio at 3840 bytes with a 2-byte header: one sidecar
connection belongs to exactly one guild for its lifetime, because the pump is
created per join. The HELLO is what lets one wake gate serve several guilds.
"""

from __future__ import annotations

import hmac
import struct

_HEADER = 4

#: One 20 ms frame of s16le 48 kHz stereo — the only audio a sidecar sends.
MAX_PCM_FRAME_BYTES = 3840
#: The user id is one length byte, so 255 is the protocol's own ceiling.
MAX_ID_BYTES = 255
#: A token is the shared NORA_BRIDGE_TOKEN; 256 bytes is generous for one.
MAX_TOKEN_BYTES = 256
#: Largest audio payload: length byte + user id + one frame.
MAX_FRAME_PAYLOAD = 1 + MAX_ID_BYTES + MAX_PCM_FRAME_BYTES
#: Largest hello payload: 0x00 + guild id + newline + token.
MAX_HELLO_PAYLOAD = 1 + MAX_ID_BYTES + 1 + MAX_TOKEN_BYTES


class ProtocolError(Exception):
    """The peer broke the seam's format or its trust. The caller closes the
    connection; nothing after the fault is read."""


class FrameParser:
    """Incremental parser: `feed()` arbitrary TCP chunks, get complete frames.

    Built with the shared service token. The HELLO is the only thing that can
    open a connection, and it must carry that token: until a valid hello
    arrives, audio is refused and so is every second hello."""

    def __init__(self, token: str | None) -> None:
        # An unset token authenticates nothing: `_hello` checks for it first.
        self._token = (token or "").encode("utf-8")
        self._buf = bytearray()
        #: The guild this connection announced, or `None` until its HELLO
        #: arrives. Set once and never changed: one connection, one guild.
        self.guild_id: str | None = None

    @property
    def pending(self) -> bool:
        """True while part of a frame is buffered, so the caller can put a
        read deadline on the rest of it."""
        return bool(self._buf)

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        """Complete AUDIO frames. A HELLO is consumed into `guild_id` and
        never returned — it carries no audio, and a caller that treated it as
        a frame would arm a wake model for an empty speaker.

        A payload over the protocol maximum is refused from its length
        header, before any of its body is buffered."""
        remaining = memoryview(chunk)
        frames: list[tuple[str, bytes]] = []
        while remaining:
            if len(self._buf) < _HEADER:
                take = min(_HEADER - len(self._buf), len(remaining))
                self._buf.extend(remaining[:take])
                remaining = remaining[take:]
                if len(self._buf) < _HEADER:
                    break
            (payload_len,) = struct.unpack_from(">I", self._buf, 0)
            if payload_len > MAX_FRAME_PAYLOAD:
                raise ProtocolError(f"frame of {payload_len} bytes is over the seam maximum")
            if payload_len < 1:
                # No length byte at all: skipped (raising would close the seam
                # connection, and every guild on that pump would lose audio
                # until a reconnect). The JS half skips it too.
                del self._buf[:_HEADER]
                continue
            # Read only the discriminator until the hello's smaller limit
            # has been checked. Never append an unexamined network chunk.
            if len(self._buf) == _HEADER:
                if not remaining:
                    break
                self._buf.append(remaining[0])
                remaining = remaining[1:]
            is_hello = self._buf[_HEADER] == 0
            if is_hello and payload_len > MAX_HELLO_PAYLOAD:
                raise ProtocolError(f"hello of {payload_len} bytes is over the hello maximum")
            take = min(_HEADER + payload_len - len(self._buf), len(remaining))
            self._buf.extend(remaining[:take])
            remaining = remaining[take:]
            if len(self._buf) < _HEADER + payload_len:
                break
            payload = bytes(self._buf[_HEADER : _HEADER + payload_len])
            del self._buf[: _HEADER + payload_len]
            if payload[0] == 0:
                self._hello(payload[1:])
            elif self.guild_id is None:
                raise ProtocolError("audio before an authenticated hello")
            else:
                frames.append(_audio(payload))
        return frames

    def _hello(self, body: bytes) -> None:
        if self.guild_id is not None:
            raise ProtocolError("a second hello on one connection")
        guild, sep, token = body.partition(b"\n")
        if not sep:
            raise ProtocolError("hello carries no token")
        if not self._token or not hmac.compare_digest(token, self._token):
            raise ProtocolError("hello failed authentication")
        if not guild:
            raise ProtocolError("hello names no guild")
        try:
            self.guild_id = guild.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("hello guild id is not UTF-8") from exc


def _audio(payload: bytes) -> tuple[str, bytes]:
    if not payload or 1 + payload[0] > len(payload):
        raise ProtocolError("user id length runs past its frame")
    id_len = payload[0]
    try:
        user_id = payload[1 : 1 + id_len].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("user id is not UTF-8") from exc
    pcm = payload[1 + id_len :]
    if len(pcm) > MAX_PCM_FRAME_BYTES:
        raise ProtocolError("PCM exceeds one 20 ms frame")
    return user_id, pcm


def encode_hello(guild_id: str, token: str) -> bytes:
    """The HELLO the sidecar writes on connect, mirroring `recv.js`'s
    `encodeHello`. Test fixtures and the smoke path build it here so both
    halves of the seam are encoded from one definition."""
    ident = guild_id.encode("utf-8")
    if not ident:
        raise ValueError("a hello frame needs a guild id")
    if b"\n" in ident:
        raise ValueError("a guild id must not contain a newline")
    secret = token.encode("utf-8")
    if not secret:
        raise ValueError("a hello frame needs the service token")
    payload = bytes([0]) + ident + b"\n" + secret
    return struct.pack(">I", len(payload)) + payload


def encode_frame(user_id: str, pcm: bytes) -> bytes:
    """The encoder half, mirroring `recv.js`'s `encodeFrame` — test fixtures
    and the smoke path build frames with exactly the sidecar's layout."""
    ident = user_id.encode("utf-8")
    if not ident:
        # A zero length byte is the HELLO discriminator, so an empty user id
        # would encode as one and be silently swallowed by the parser.
        raise ValueError("user_id must not be empty (0 is the hello marker)")
    if len(ident) > MAX_ID_BYTES:
        raise ValueError("user_id too long for frame")
    payload = bytes([len(ident)]) + ident + pcm
    return struct.pack(">I", len(payload)) + payload
