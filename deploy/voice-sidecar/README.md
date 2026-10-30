# nora-voice-sidecar

Audio-controller for Nora's Discord voice channel, per
`design/voice-channel/07-audio-owner-controller.md` (phase 1). It is the
SOLE owner of the Discord voice connection: every audio stream in and out
of the channel goes through it, and nora drives it over an HTTP control
plane (nora decides everything — queue, resolve; the controller only
plays). The receive side of the original stage-2 design
(`design/voice-channel/05-sidecar-receive.md`) is unchanged: per-user Opus
streams are DAVE-decrypted (see `DAVE-NOTES.md`), decoded to PCM, and
forwarded `{user_id, pcm}` over the local TCP seam for the wake gate (see
`TRANSPORT.md`).

**Dumb pipe:** no wake-word, no STT, no AI, no resolve, no music queue.
All of that lives in the Python stack. **Audio is transient:** PCM is
forwarded, music flows CDN → ffmpeg → Discord, TTS arrives as a bounded
POST body — nothing is stored.

## Control plane (HTTP)

Every POST answers `{ ok, request_id, state }` or
`{ error, request_id, state }`; `state` is the pinned snapshot
`{voice, generation, channel_id, playback_id, player, speech_queue,
crossfade_seconds}`. All state-changing commands except `/voice/join` are
generation-scoped (stale generation → `409 generation_conflict`); any body
`guild_id` ≠ the configured guild → `400 wrong_guild` (single-guild
hard-bound). `request_id` is deduped for 5 minutes, in a bounded map
(256 entries / 512 KiB, in-flight entries never evicted; a full map of
in-flight requests answers `503 dedupe_full`). A `request_id` is 1–64 characters
of `[A-Za-z0-9._:-]`, else `400 invalid_request_id`.

**Every route requires `Authorization: Bearer $NORA_BRIDGE_TOKEN`**, the shared
service bearer that nora and the bridges already hold. A missing or wrong
bearer is `401 unauthorized` before the body is read. The process refuses to
start without the token. A body over the limit is `413` and a body still
arriving after 15 s is `408`; both close the connection without reading the
rest. The listener accepts at most 64 connections.

| route | semantics |
|---|---|
| `POST /voice/join` | join (idempotent on the same channel; `409 connected_elsewhere`) |
| `POST /voice/exit` | stop music + speech + wake pump, destroy the connection, persist `idle` |
| `POST /music/play` | REPLACE the current music slot (ffmpeg pulls `stream_url`); mints a `playback_id` |
| `POST /music/stop` | stop music only — stays connected, keeps the wake pump |
| `POST /music/pause` / `resume` | current-track state (`409 pause_during_speech` while speech is active) |
| `POST /music/volume` | 0–200 %, applied now and remembered for the next track |
| `POST /stream` | high-priority TTS (replaces the old `/play`); ≤1 active + ≤4 queued, ≤2 MB decoded |
| `GET /voice/status` | snapshot + `last_event_seq`, `outbox_dropped`, `streaming`, `last_error`, `wake`, `pump` |
| `GET /health` | process liveness (see below) |

Events (`track-finished`, `stream-failed`, `speech-finished`,
`voice-death`, `channel-emptied`) are POSTed to `NORA_EVENT_URL` with
`event_id` + strictly increasing `event_seq`; failed deliveries are kept
in a bounded outbox (≤256, oldest dropped + counted) and retried with
exponential backoff until 2xx.

Speech preempts music with a hard cut (no ducking): the music ffmpeg is
killed, the slot remembered as suspended, and the track revives at the
cut position when speech drains (ffmpeg `-ss`; `position_policy` is
`approximate_seek` — the consumed-frames count, not sample-exact). Durable desired-state (`STATE_DIR`, one `<guild_id>.json` per guild): `/voice/exit` writes
`idle`, a successful join writes `connected`; boot joins only when the
file says `connected`. There is no env fallback channel: a process serving several guilds has no single one to return to, so it boots idle until a join.

## Layout

- `src/service.mjs` — **production entry** (the container's ENTRYPOINT).
  Thin: env reading, real seams (discord.js join, AudioPlayer, ffmpeg
  spawn, fetch, timers), boot decision, wiring.
- `src/controller.mjs` — HTTP routes, response shapes, request dedupe,
  `createCore` (wires the seams).
- `src/lifecycle.mjs` — serialized connection lifecycle + generation
  tokens + desired-state + voice-death / channel-emptied observation.
- `src/arbiter.mjs` — music/speech arbitration state machine, ffmpeg
  ownership (spawn → SIGTERM → SIGKILL escalation, 30 s stall watch),
  bounded speech queue.
- `src/outbox.mjs` — event queue with backoff retry.
- `src/state_store.mjs` — atomic desired-state file.
- `src/recv.js` — `join(guildId, channelId, token)` → PCM event bus;
  `createPump()` → `pump(pcm, userId)` TCP forwarder with a per-user
  connected-state backlog bound; frame codec.
- `src/wav.js` — streaming WAV writer (spike verification only).
- `spike.mjs` — harness: `--mock` (no token, proves the plumbing) and
  `--live` (operator-run real-guild spike).
- `consume.mjs` — stand-in wake-gate consumer printing
  bytes/frames/silence stats.
- `smoke_test.mjs` — self-contained smoke test (see below).
- `test/` — `node --test` suites (`npm test`): endpoint contract, arbiter,
  outbox, desired-state/boot, pump bound.
- `DAVE-NOTES.md` — file:line proof the pinned library decrypts DAVE.
- `TRANSPORT.md` — the TCP frame format on the wake-gate seam.

## Configuration (env)

| var | default | meaning |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | bot token; from the operator's `.env`, never committed |
| ~~`GUILD_ID`~~ / ~~`CHANNEL_ID`~~ | — | **GONE 2026-09-17.** The process has no guild identity: a connection is created on `POST /voice/join` and restored at boot from `STATE_DIR`. Setting these is a silent no-op. Which guilds exist is nora's `[speech.tts] sidecars`. |
| `PUMP_HOST` | `127.0.0.1` | where the wake gate (stage 3) will listen |
| `PUMP_PORT` | `4599` | wake-gate seam port (frame format: `TRANSPORT.md`) |
| `HOST` | `127.0.0.1` | HTTP bind |
| `PORT` | `4600` | HTTP port |
| `NORA_EVENT_URL` | `http://discord-bridge:18093/v1/voice/event` | where controller events are POSTed — in bridge mode the bridge owns the `VoiceEventConsumer`; bearer-authed like every `/v1` route there |
| `STATE_DIR` | `/var/lib/audio-controller` | durable desired-state DIRECTORY, one `<guild_id>.json` per guild (atomic writes). A file whose name is not a guild snowflake is skipped at boot. |

The token is read from the environment only — it is never logged, never
written, and there is no default that would "work".

## Health semantics

`GET /health` is **process liveness** (behind the bearer, like every route:
its body names the users it hears): it answers 200 whenever the process
is up, including when no Discord token is configured — a deployment without voice
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

`Dockerfile` uses `node:24-trixie-slim`, curl for the HEALTHCHECK, ffmpeg
for playback, and python3/make/g++ for native audio source fallback when a
compatible prebuild is unavailable. It uses a non-root user uid/gid 1000,
`npm ci` from the lockfile, and no build-time secret. The compose service
(`voice-sidecar` in the repo-root `docker-compose.yml`) publishes NO ports —
it is reachable only on the private `nora` network, and the wake gate will
reach it there in stage 3.

## Notes

- Node 24+, plain ESM JS, no build step.
- The scoped npm override keeps `@discordjs/node-pre-gyp` on patched `tar` 7.
  This is the install-time prebuild downloader used by `@discordjs/opus`;
  upstream still requests vulnerable `tar` 6. Remove the override when upstream
  accepts patched tar, after verifying native installation again.
- `recordings/` and `node_modules/` are gitignored; spike WAVs are
  verification artifacts, not retained audio.
