# Transport note (wake-gate seam)

Choice: **plain TCP, length-prefixed binary frames**
(`[u32be len][u8 user_id_len][user_id utf8][pcm bytes]`), default port 4599.
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
frames (~4 s), so utterance onsets survive socket startup. Codec: s16le 48 kHz stereo PCM, 3840 bytes per 20 ms frame.
