# nora-voice-sidecar

Receive-only voice sidecar for Nora's Discord voice channel, per
`design/voice-channel/05-sidecar-receive.md` (stage 2). It joins a voice
channel with `@discordjs/voice`, subscribes per-user Opus streams, relies on
the library's built-in DAVE decrypt path (see `DAVE-NOTES.md`), decodes to
PCM, and forwards `{user_id, pcm}` over a local TCP socket for the future
Python wake gate (stage 3 — see `TRANSPORT.md`).

**Dumb pipe:** no wake-word, no STT, no AI. All of that lives in the Python
stack. **Audio is transient:** PCM is forwarded, never stored — WAV writing
exists only in the spike/verification harness (`spike.mjs`).

## Layout

- `src/service.mjs` — **production entry** (the container's ENTRYPOINT).
  Serves a loopback `/health` endpoint and, when configured, joins Discord
  and pumps PCM to the wake-gate seam.
- `src/recv.js` — `join(guildId, channelId, token)` → PCM event bus;
  `createPump()` → `pump(pcm, userId)` TCP forwarder; frame codec.
- `src/wav.js` — streaming WAV writer (spike verification only).
- `spike.mjs` — harness: `--mock` (no token, proves the plumbing) and
  `--live` (operator-run real-guild spike).
- `consume.mjs` — stand-in wake-gate consumer printing
  bytes/frames/silence stats.
- `smoke_test.mjs` — self-contained smoke test (see below).
- `DAVE-NOTES.md` — file:line proof the pinned library decrypts DAVE.
- `TRANSPORT.md` — the TCP frame format on the wake-gate seam.

## Configuration (env)

| var | default | meaning |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | bot token; from the operator's `.env`, never committed |
| `GUILD_ID` | — | guild to join |
| `CHANNEL_ID` | — | voice channel to join |
| `PUMP_HOST` | `127.0.0.1` | where the wake gate (stage 3) will listen |
| `PUMP_PORT` | `4599` | wake-gate seam port (frame format: `TRANSPORT.md`) |
| `HOST` | `127.0.0.1` | health-endpoint bind (loopback only) |
| `PORT` | `4600` | health-endpoint port |

The token is read from the environment only — it is never logged, never
written, and there is no default that would "work".

## Health semantics

`GET /health` is **process liveness**: it answers 200 whenever the process
is up, including when no token is configured — a deployment without voice
receive enabled is **healthy-but-idle**, not failed (the dumb-pipe posture:
there is nothing to fail closed into). The payload's `voice` field carries
the receive state — `idle` | `connecting` | `connected` | `error` — plus
per-user frame/byte counters since process start, so an operator can
distinguish "not configured" from "joined" without reading logs. Both the
Dockerfile HEALTHCHECK and the compose healthcheck curl this endpoint.

## Runtime dependencies

Exact pins (stage-2 decision): `@discordjs/voice@0.19.2`,
`@snazzah/davey@0.1.12`. Required to run, as in the verified prototype:
`discord.js` (gateway client), `@discordjs/opus` (Opus decode backend for
`prism-media`), `prism-media` (Opus→PCM decoder stream). Everything is
locked in `package-lock.json`; the image installs with `npm ci`.

## Verify the plumbing (no token)

```bash
npm ci
node smoke_test.mjs        # or: npm run smoke
```

The smoke test runs `spike.mjs --mock` — two synthetic users' PCM through
the real pump → TCP → frame-parser path — and asserts the prototype's
measured numbers (both users heard, 75 frames / 288000 bytes / ~1.50 s each,
sane silence ratio), then re-parses the framed fixture through
`consume.mjs --file` to the same numbers. Artifacts go to a temp dir, not
the repo tree.

## Live spike (operator only)

```bash
DISCORD_BOT_TOKEN=... GUILD_ID=... CHANNEL_ID=... npm run spike:live
# optionally also forward to a listener (consume.mjs or the wake gate):
DISCORD_BOT_TOKEN=... GUILD_ID=... CHANNEL_ID=... PUMP=1 npm run spike:live
```

Prints a `listening: user <id>` line per speaker, writes one WAV per user to
`recordings/`. Verify: WAV is non-silent, intelligible Cantonese. Ctrl-C
stops and prints stats. In another terminal, `npm run consume` listens on
`127.0.0.1:4599` and prints the same stats live — that is the seam the
Python wake gate will attach to. **This gate has PASSED** (real-guild DAVE
spike, decrypted PCM verified); it stays here for re-verification after
dependency upgrades.

## Container

`Dockerfile` mirrors `deploy/stt/`: `node:22-slim`, curl for the
HEALTHCHECK, non-root user uid/gid 1000, `npm ci` from the lockfile, no
build-time secret. The compose service (`voice-sidecar` in the repo-root
`docker-compose.yml`) publishes NO ports — it is reachable only on the
private `nora` network, and the wake gate will reach it there in stage 3.

## Notes

- Node 22+, plain ESM JS, no build step.
- `npm audit` may flag `tar` inside `@discordjs/opus`'s install-time
  prebuild downloader (`node-pre-gyp`). It runs only during `npm ci`, not
  in the sidecar's runtime path.
- `recordings/` and `node_modules/` are gitignored; spike WAVs are
  verification artifacts, not retained audio.
