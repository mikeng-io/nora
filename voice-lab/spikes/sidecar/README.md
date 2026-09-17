# nora-voice-sidecar (spike scaffolding)

Receive-only voice sidecar for Nora's voice channel, per
`nora-wt-voice-design/design/voice-channel/05-sidecar-receive.md`. It joins a
Discord voice channel with `@discordjs/voice`, subscribes per-user Opus
streams, relies on the library's built-in DAVE decrypt path (see
`DAVE-NOTES.md`), decodes to PCM, and forwards `{user_id, pcm}` over a local
TCP socket for the future Python wake gate (`TRANSPORT.md`).

**Dumb pipe:** no wake-word, no STT, no AI. That lives in the Python stack.

## Layout

- `src/recv.js` — `join(guildId, channelId, token)` → PCM event bus;
  `createPump()` → `pump(pcm, userId)` TCP forwarder; frame codec.
- `src/wav.js` — streaming WAV writer (48 kHz stereo s16le).
- `spike.mjs` — harness: `--mock` (no token, proves the plumbing) and
  `--live` (operator-run real-guild spike).
- `consume.mjs` — stand-in consumer printing bytes/frames/silence stats.
- `DAVE-NOTES.md` — file:line proof the pinned library decrypts DAVE.

## Verify the plumbing (no token)

```bash
npm install
npm run spike:mock
node consume.mjs --file recordings/mock-stream-<ts>.bin
```

`--mock` synthesizes two fake users' PCM, pushes it through the real
pump → TCP → frame-parser path, writes `recordings/<userId>-<ts>.wav`, and
prints per-user byte/frame/silence stats.

## Live spike (operator only)

```bash
DISCORD_BOT_TOKEN=... GUILD_ID=... CHANNEL_ID=... npm run spike:live
# optionally also forward to a listener (consume.mjs or the wake gate):
DISCORD_BOT_TOKEN=... GUILD_ID=... CHANNEL_ID=... PUMP=1 npm run spike:live
```

Prints a `listening: user <id>` line per speaker, writes one WAV per user to
`recordings/`. Verify: WAV is non-silent, intelligible Cantonese. Ctrl-C
stops and prints stats. The token comes from env only — it is never printed
or written anywhere by this code.

In another terminal, `npm run consume` listens on `127.0.0.1:4599` and prints
the same stats live — that is the seam the Python wake gate will attach to.

## Notes

- Node 22+, plain ESM JS, no build step.
- `npm audit` flags `tar` inside `@discordjs/opus`'s install-time prebuild
  downloader (`node-pre-gyp`). It runs only during `npm install`, not in the
  sidecar's runtime path; no fix is published upstream yet.
- `recordings/` is gitignored; spike WAVs are verification artifacts, not
  retained audio.
