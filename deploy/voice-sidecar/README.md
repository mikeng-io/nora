# nora-voice-sidecar

Audio-controller for Nora's Discord voice channel, per
`design/voice-channel/audio.md` (phase 1). It is the
SOLE owner of the Discord voice connection: every audio stream in and out
of the channel goes through it, and nora drives it over an HTTP control
plane (nora decides everything — queue, resolve; the controller only
plays). The receive side of the original stage-2 design
(`design/voice-channel/receive.md`) is unchanged: per-user Opus
streams are DAVE-decrypted (see `DAVE-NOTES.md`), decoded to PCM, and
forwarded `{user_id, pcm}` over the local TCP seam for the wake gate (see
`TRANSPORT.md`).

**Dumb pipe:** no wake-word, no STT, no AI, no resolve, no music queue.
All of that lives in the Python stack. **Audio is transient:** PCM is
forwarded, music flows CDN → ffmpeg → Discord, TTS arrives as a bounded
POST body — nothing is stored.

## Control plane (HTTP)

The controller serves the `voice` contract (`design/service-api/contracts.md`
«Voice», the routes in `docs/service-api/openapi.json`):

| route | semantics |
|---|---|
| `POST /v1/voice/operations` | a connection operation: `{operation_id, kind, effect}` |
| `GET /v1/voice/operations/{operation_id}` | an operation read back: `ok` with its envelope, `nothing_found` for an id the ledger does not hold |
| `POST /v1/voice/queries` | `{kind: "status", params: {guild_id}}`: `ok` with the snapshot plus `last_event_seq`, `outbox_dropped`, `streaming`, `last_error`, `state_write_ok`, `wake`, `pump`; `nothing_found` for a guild with no connection |
| `GET /health` | process liveness (see below) |
| `GET /ready` | 200 once boot has restored state and a bot token is configured, else 503 |

| kind | effect | semantics |
|---|---|---|
| `join` | `{guild_id, channel_id}` | join (idempotent on the same channel; another channel is `rejected` `connected_elsewhere`) |
| `leave` | `{guild_id, generation}` | stop music + speech + wake pump, destroy the connection, persist `idle` |
| `play` | `+ stream_url`, optional `title`, `duration_seconds`, `resumable` | REPLACE the current music slot (ffmpeg pulls `stream_url`); mints a `playback_id` |
| `stop` | `{guild_id, generation}` | stop music only — stays connected, keeps the wake pump |
| `pause` / `resume` | `{guild_id, generation}` | current-track state (`rejected` `pause_during_speech` while speech is active) |
| `set_volume` | `+ percent` | 0–200 %, applied now and remembered for the next track |
| `speak` | `+ audio_b64`, `media_type` | high-priority TTS; ≤1 active + ≤4 queued, ≤2 MB decoded |
| `report` | — | `unsupported`: the controller sends reports, it takes none |

Every answer on the three voice routes is an envelope of six parts (`outcome`,
`payload`, `when`, `from`, `scope`, `trace`) with the HTTP status the
outcome names. An operation's payload is `{state}`, the pinned snapshot
`{voice, generation, channel_id, playback_id, player, autopaused,
subscribers, playable, pending_reaps, speech_queue, crossfade_seconds}`,
plus `error: {kind, message}` on a refusal. Every kind but `join` is
generation-scoped: a stale generation is `rejected` `generation_conflict`
with the current generation in `state`. An arbiter or lifecycle refusal and
a guild with no connection (`unknown_guild`) are `rejected`; a guild outside
admission is `denied`; a full speech queue, a failed join and a full ledger
are `unavailable`; a handler that fails unexpectedly is `uncertain`. A
malformed request (a `guild_id` that is not a snowflake, a bad effect, an
`operation_id` outside 1–64 characters of `[A-Za-z0-9._:-]`, invalid JSON)
is `400` and an unknown kind `422`, each with no envelope.

The process keeps one operation ledger, keyed by `operation_id`, for 5
minutes, bounded at 256 entries / 512 KiB with in-flight entries never
evicted. It holds a hash of the kind and effect and the answered envelope,
never audio. The same id with the same kind and effect is answered from it
without running again (a concurrent duplicate awaits the first); the same id
with another kind or effect is `409` with no envelope.

**Every route requires `Authorization: Bearer $NORA_BRIDGE_TOKEN`**, the shared
service bearer that nora and the bridges already hold. A missing or wrong
bearer is `401 unauthorized` before the body is read. The process refuses to
start without the token. An operation body over 4 MiB (a query's over
64 KiB) is `413` and a body still arriving after 15 s is `408`; both close
the connection without reading the rest. The listener accepts at most 64
connections.

Events (`track-finished`, `stream-failed`, `speech-finished`,
`voice-death`, `channel-emptied`) carry `event_id` + strictly increasing
`event_seq` and are sent to the hub's `/v1/voice/operations` at
`NORA_EVENT_URL` as `{operation_id, kind: "report", effect: <event>}`,
with a new `operation_id` for every attempt and no redirect followed. A
report is retried with exponential backoff after `unavailable`, a transport
failure, a timeout, a 3xx, a 401, a 404, or an answer whose status and
outcome disagree or that is no envelope; any other settled outcome, and a
400, 409 or 422, ends that event's delivery. Undelivered events wait in a
bounded outbox (≤256, oldest dropped + counted), in order.

Speech preempts music with a hard cut (no ducking): the music ffmpeg is
killed, the slot remembered as suspended, and the track revives at the
cut position when speech drains (ffmpeg `-ss`; `position_policy` is
`approximate_seek` — the consumed-frames count, not sample-exact). Durable desired-state (`STATE_DIR`, one `<guild_id>.json` per guild): `leave` writes
`idle`, a successful join writes `connected`; boot joins only when the
file says `connected`. There is no env fallback channel: a process serving several guilds has no single one to return to, so it boots idle until a join.

## Layout

- `src/service.mjs` — **production entry** (the container's ENTRYPOINT).
  Thin: env reading, real seams (discord.js join, AudioPlayer, ffmpeg
  spawn, fetch, timers), boot decision, wiring.
- `src/controller.mjs` — the front door's route table, the per-guild
  controller's operations, `createCore` (wires the seams).
- `src/contract.mjs` — the envelope writer and the kernel's status table.
- `src/ledger.mjs` — the process's bounded operation ledger.
- `src/lifecycle.mjs` — serialized connection lifecycle + generation
  tokens + desired-state + voice-death / channel-emptied observation.
- `src/arbiter.mjs` — music/speech arbitration state machine, ffmpeg
  ownership (spawn → SIGTERM → SIGKILL escalation, 30 s stall watch),
  bounded speech queue.
- `src/outbox.mjs` — event queue, delivered as `report` operations with backoff retry.
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
| ~~`GUILD_ID`~~ / ~~`CHANNEL_ID`~~ | — | **GONE 2026-09-17.** The process has no guild identity: a connection is created by a `join` operation and restored at boot from `STATE_DIR`. Setting these is a silent no-op. Which guilds exist is nora's `[speech.tts] sidecars`. |
| `PUMP_HOST` | `127.0.0.1` | where the wake gate (stage 3) will listen |
| `PUMP_PORT` | `4599` | wake-gate seam port (frame format: `TRANSPORT.md`) |
| `HOST` | `127.0.0.1` | HTTP bind |
| `PORT` | `4600` | HTTP port |
| `NORA_EVENT_URL` | `http://discord-bridge:18093/v1/voice/operations` | the hub's `report` route — in bridge mode the bridge holds the hub; bearer-authed like every `/v1` route there |
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
