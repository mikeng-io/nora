# Transport note (wake-gate seam)

Choice: **plain TCP, length-prefixed binary frames**
(`[u32be len][u8 user_id_len][user_id utf8][pcm bytes]`), default port 4599.

**A connection announces its GUILD once, on connect** (multi-guild,
2026-09-17), in a frame whose `user_id_len` is ZERO:

    hello  [u32be len][u8 0][guild_id utf8][0x0A][token utf8]

The hello also carries the shared service bearer (`NORA_BRIDGE_TOKEN`) after a
newline. A gate that cannot verify it closes the connection before any audio is
read, and a second hello, or audio before a valid hello, closes it too. A guild
id is digits and cannot contain a newline, so the first newline is the separator.

A real frame can never have a zero-length user id — Discord issues no empty
snowflake — so the existing length byte discriminates and the audio layout is
byte-identical. Once rather than per frame because a pump is created per join,
so a connection is one guild for its lifetime; 20 ms of audio keeps its 2-byte
header. The hello is written before any buffered audio, so a gate never has to
guess which guild the first utterance came from.

Without it a gate could not tell which server a speaker was in, which is why
every server used to need its own gate container. The codec lives in
`src/codec.mjs` (JS) and `deploy/voice-wake/wakegate/codec.py` (Python) — two
implementations of one format, with twin test suites.
The sidecar is the TCP CLIENT: it connects out to `PUMP_HOST:PUMP_PORT`,
where the future Python wake gate (stage 3) listens. `consume.mjs` is the
stand-in listener for verification. In the container the default
`PUMP_HOST=127.0.0.1` is inert on purpose — there is no wake gate yet
(stage 3, separately gated), so frames are produced only when a consumer
actually listens.
Why over HTTP chunked / WebSocket: no per-chunk overhead, no HTTP parser or
frame masking on either side, and Python consumes it with 20 lines of
`socket` + `struct` — nothing to install on the wake-gate side.
Ordering and message boundaries are guaranteed by TCP + the length prefix,
so a 5 s utterance (250 frames) arrives intact with no buffering surprises.
If the wake gate is down, frames are dropped (never queued) — audio is
transient by design (`05-sidecar-receive.md`), so a down consumer must not
grow memory. Only the connect handshake itself buffers, bounded at 200
frames (~4 s), so utterance onsets survive socket startup. Codec: s16le 48 kHz stereo PCM, 3840 bytes per 20 ms frame. A payload above
the maxima (one 20 ms frame; a hello of at most 513 bytes) is refused before it is buffered.
