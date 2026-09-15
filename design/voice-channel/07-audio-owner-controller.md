---
type: design
title: "Audio Owner Controller — one connection, every audio stream routed through it"
description: >
  Resolves the one-bot-one-voice-connection conflict (music bot vs wake
  sidecar, both on the same token) by making the sidecar the SOLE owner of
  the voice connection: join/exit are explicit commands, music plays through
  it as an HTTP client does today for TTS, and nora keeps every decision.
  Defines the control/data plane split, queue arbitration, cache policy,
  memory/CPU budget, and the debug/trace/log contract — before code moves.
tags: [nora, discord, voice, sidecar, music, architecture, ddd, clean-architecture]
timestamp: '2026-09-16'
edges:
  - {src: 07-audio-owner-controller, dst: 00-overview, rel: extends, provenance: asserted}
  - {src: 07-audio-owner-controller, dst: 05-sidecar-receive, rel: extends, provenance: asserted}
  - {src: 07-audio-owner-controller, dst: 01-tts-playback-and-ducking, rel: extends, provenance: asserted}
  - {src: 07-audio-owner-controller, dst: 02-module-layout-ddd, rel: realizes, provenance: asserted}
  - {src: 07-audio-owner-controller, dst: 09-decision-index, rel: motivated_by, provenance: asserted}
---

# Audio Owner Controller — one connection, every audio stream routed through it

## 1. Problem (measured, not assumed)

Discord allows **one voice connection per bot token per guild**. Nora's music
player (`discord.py` `channel.connect()`, `music_commands.py:81`) and the
wake-receive sidecar (`discord.js` `joinVoiceChannel`, `recv.js:50`) share the
same bot token. The second join silently kills the first — no error, no
event, the victim's socket just dies.

Observed 2026-09-16 by Miles: `/music play` replied "playing", but no sound.
The music bot and the sidecar are fighting over one voice connection. This is
not a code bug in either; it is a structural conflict inherent to two
connection holders.

## 2. The fix: ONE connection owner

**The `audio-controller` becomes the sole holder of the Discord voice
connection** — every audio stream in and out of the channel goes through it:

- **In (receive):** per-user PCM → wake gate (existing TCP seam, unchanged)
- **Out (TTS):** Nora POSTs synthesized speech → audio-controller plays it
  (existing `/play`, unchanged semantics, now priority-aware)
- **Out (music/YouTube):** Nora POSTs a resolved stream URL →
  audio-controller plays via ffmpeg
  (NEW — replaces `discord.py`'s own `VoiceClient.play`)
- **Control:** `/discord join|exit` (NEW), `/music *` all re-point at
  audio-controller

nora keeps every DECISION (queue, autoplay, persistence, resolve, ducking
policy) and the `Speaker`/`VoiceSession` ports; it simply stops touching the
Discord voice socket. `DiscordVoiceSession` (discord.py) is replaced by an
HTTP adapter implementing the same `VoiceSession` protocol — nothing above
the seam changes.

### Container roster (the naming contract)

| Container | Role | Responsibilities |
|---|---|---|
| `nora` | **Control plane** | Decisions only: queue, autoplay, yt-dlp resolve, session persistence, TTS synthesis. **No Discord voice socket, no audio bytes.** |
| `audio-controller` | **Audio plane (the only connection holder)** | All Discord audio in & out: receive PCM → wake; play TTS; play music/YouTube via ffmpeg. One process, one voice connection. |
| `voice-wake` | Wake detector | Runs the wake model ("喂 Nora"); gets PCM from audio-controller, forwards triggers. Unchanged. |
| `stt` | STT engine | Transcribes voice utterances. Unchanged. |

Audio bytes never travel container-to-container except: PCM (audio-controller
→ voice-wake, transient frames) and speech mp3 (nora → audio-controller,
single hop, ≤2 MB). Music bytes go CDN → audio-controller → Discord; nora
sends only the `stream_url` string.

### Where YouTube download/convert happens

```
/music play <YouTube link>

nora ── yt-dlp resolve (download=False) ──▶ ResolvedTrack { stream_url, title, duration }
              (metadata only; no audio bytes held)

stream_url ── POST /music/play ──▶ audio-controller
              ── ffmpeg pulls from YouTube CDN ──▶ decode ──▶ opus ──▶ Discord voice socket
```

- **Resolve** (`yt-dlp`, metadata + stream locator, `download=False`) stays
  in nora — it is decision-layer work (which track, which playlist, queue
  order).
- **Pull + decode + convert + play** (ffmpeg) happens in audio-controller —
  it is the only layer that holds the socket.
- There is no "upload": Discord voice is a socket stream, not a file push.
- Nora never sees a byte of the music; it hands over a URL string.

```
nora (brain — decisions, ports unchanged)
 │  control plane         data plane
 ├─ POST /voice/join      ├─ POST /play       (TTS mp3 b64, prio high)
 ├─ POST /voice/exit      ├─ POST /stream     (music url, prio low)
 ├─ POST /music/play …    │  POST /stream-stop
 ├─ POST /music/skip …    └─ TCP seam         (PCM → wake, unchanged)
 └─ GET  /voice/status
        │
   audio-controller (audio-controller — the ONLY connection holder)
        │  discord.js voice socket (selfDeaf:false, selfMute:false)
        ▼
   Discord voice channel
```

## 3. Control plane — HTTP contract

All endpoints are **POST** (state changes), one for each verb nora already
has; `GET /voice/status` for the operator. Auth: none — same posture as
existing `/play` (reachable only on the private nora network, caller is
Nora). Every response carries `request_id` (echoed) + `state` snapshot.

| Endpoint | Body | Semantics |
|---|---|---|
| `POST /voice/join` | `{guild_id, channel_id}` | Join channel (idempotent if already there). Overrides the boot-time env-var channel? NO — see §10 |
| `POST /voice/exit` | `{}` | Leave channel + stop everything + stop wake pump. The `voice` state returns to `idle`. |
| `POST /music/play` | `{stream_url, title, duration_seconds?}` | Push onto the music queue; start if idle |
| `POST /music/stop` | `{}` | Stop current track + clear music queue. **Stays connected** (per Miles's spec: stop ≠ exit). |
| `POST /music/skip` | `{}` | `player.stop()` → next queued |
| `POST /music/pause` / `resume` | `{}` | Current track state |
| `POST /music/volume` | `{percent}` | 0–200 |
| `POST /stream` (TTS) | `{audio_b64, media_type, request_id?}` | High-priority queue (existing `/play` path, renamed) |
| `POST /app/v1/voice/track-finished` *see §5* | — | audio-controller → nora callback |

`/voice/exit` semantics: **everything** stops — music, TTS, wake pump, then
the connection is destroyed. `state: idle`. This is Miles's decision 3: "All
leave, mean nora leave the discord voice channel."

`/music/stop` semantics: **stop only**. Music queue cleared, player stopped,
connection and wake pump stay. This is Miles's decision 2: "佢就唔會直接
離開，就會停咗喺度".

## 4. Music flow (the thing that was broken)

Today nora resolves (`YtDlpSource.resolve` → `ResolvedTrack.stream_url`) and
plays locally via `DiscordVoiceSession`. The audio-controller keeps resolution in nora and
moves only the *playing*:

1. `/music play <q>` — nora's `MusicPlayerManager.play` (unchanged: queue
   DB, resolve, session persistence)
2. nora's new `VoiceSession` HTTP impl sends `POST /music/play
   {stream_url, title, duration_seconds}` (resolved at start; a track that
   fails to resolve is `nothing_found` before any POST)
3. audio-controller: if the music player is idle → spawn ffmpeg (pull from `stream_url`
   via curl/ffmpeg, NOT download-then-upload — nora holds no audio bytes;
   see §8)
4. audio-controller: on track end → `POST /app/v1/voice/track-finished {guild_id,
   request_id}` → nora `MusicPlayerManager.on_track_finished` (existing
   method, unchanged) → next track or idle

**Playlists** stay in nora: `resolve` expands to N `ResolvedTrack`s, queued
in nora's DB, and each is POSTed to audio-controller as the previous ends. audio-controller never sees
"playlist" — only single-track `play` with an optional `play_next_url`.

## 5. Queue arbitration — two producers, ONE ear

audio-controller owns a single audio pipeline (`@discordjs/voice` `AudioPlayer`), the
same one today. Two queues feed it:

- **Speech queue (high, bounded 4)** — TTS replies. Current `/play`
  semantics unchanged.
- **Music queue (low, bounded 4096)** — the music player.

Rules (fail-closed, no overlap ever):

1. Music plays as the *default* stream.
2. A speech item **preempts** music: music's `AudioResource` is stopped,
   music's position is remembered (`position_ms`), speech plays, then music
   resumes from `position_ms`. This is the ducking behaviour in its simplest
   form (phase 2 refines the gain ramp — see §10).
3. A second speech item while one speaks: queue (max 4), never overlap.
4. `music/stop` clears the music queue and kills the ffmpeg, but **not**
   anything in the speech queue.
5. `voice/exit` clears BOTH and destroys the connection.

No TTS reply is ever cut off by a music skip; no music ever overlaps speech.

## 6. Cache — the honest starting point

Measured today: `YtDlpSource` has **no in-process cache** — every `resolve`
builds a fresh `YoutubeDL` extractor and hits YouTube (with yt-dlp's own
internal disk/state behaviour). `ResolvedTrack.stream_url` is a short-lived
CDN locator, never persisted (ports.py:1967-1973: `stream_url` is "not a
cache of stream_url"; only `locator` is stored).

**Phase-1 policy: NO new cache in audio-controller.** The audio-controller is a dumb pipe; caching
belongs in nora's resolve layer (the brain). `stream_url` expires by the
source host's policy; a stale URL just fails to stream → nora's
`nothing_found`/retry path already exists. Add an explicit TTL cache
(`locator → stream_url`) in nora's `YtDlpSource` only when measurements say
the resolve cost matters (evidence-bar rule, design/discord-music/
04-deferred.md).

**What audio-controller must NOT do:** buffer full tracks, write audio to disk, or
transcode beyond a single ffmpeg pull at a time. Audio never accumulates in
the audio-controller.

## 7. Memory / CPU budget (the containers question)

Today: wake PCM is transient (buffered per frame, forwarded, dropped);
TTS bytes are b64 in one POST (≤2 MB guard), decoded, queued. The audio-controller
extension adds one long-lived ffmpeg process per music stream.

Budget targets:

| Resource | Bound | Enforcement |
|---|---|---|
| ffmpeg processes | ≤ 1 music + transient per-speech spawn (serialized via player state) | Single AudioPlayer; never two music sources |
| Speech queue memory | ≤ 4 × 2 MB = 8 MB | Existing `MAX_QUEUE=4` + 2 MB body guard |
| PCM pump | transient — per-frame forward, no accumulation | Existing |
| Music queue | ≤ 4096 items → metadata only, NOT audio bytes | `play` body carries URL, not audio |
| Wake side | unchanged | — |

The audio-controller container must not grow beyond today's footprint plus one ffmpeg
(~50-90 MB RSS). If a stream stalls (ffmpeg hangs, no bytes), audio-controller reports
`streaming: false` + `last_error` in `/voice/status` after a 30 s watch, and
nora's existing voice-death path fires. No unbounded buffers anywhere.

## 8. Why nora never holds audio bytes (and the audio-controller never resolves)

- **Resolve (yt-dlp) is CPU + network heavy** → nora, where it already is.
- **Play (ffmpeg decode → opus) is CPU heavy** → audio-controller, where it already is.
- **Transfer**: `stream_url` is passed as a string; ffmpeg in audio-controller pulls
  directly from the source CDN. Nora's container does not proxy the bytes;
  there is no 3-container audio relay for music. TTS (small, ≤2 MB) is the
  only audio that travels as a body.

Net: one container (audio-controller) talks audio to Discord; nora talks HTTP to audio-controller.
No shared-filesystem/audio-buffer coupling between them — every hop is a
request/response with a bounded body.

## 9. Debug / trace / log contract

The audio path is now a multi-hop pipeline; the contract makes each hop
traceable end-to-end with zero extra moving parts.

- **`request_id`**: nora mints a UUID per user action (`/music play`, TTS
  reply). It rides every control POST body, every audio-controller log line, and every
  callback (`track-finished` echoes it back). A single conversation spans:
  nora decide → audio-controller play → callback → nora advance. `request_id` is the
  join key.
- **audio-controller structured logs**: all `console.log` lines already carry
  `[service]/[player]`; extend to `[voc] {...json...}` with `request_id`,
  `event` (`join`, `exit`, `play`, `stream-finished`, `speech-finished`,
  `error`), `guild_id`, `channel_id`, `duration_ms`. No prose-only logs on
  the audio path.
- **`/voice/status`** (operator): `{voice: idle|connecting|connected|error,
  player: {status: idle|playing|paused, queue: N, speech_queue: N,
  position_ms, current: {title?, url?}},
  wake: {state, users}, pump: {host, port}, last_error}`. This is the
  single snapshot an incident starts from (mirrors the existing `/health`
  shape).
- **Timing**: audio-controller logs `play_latency_ms` (POST arrival → first AudioPlayer
  state), `ffmpeg_exit` (code) on stream end — the two numbers that make a
  "no sound" report answerable. nora logs the same `request_id` around its
  own resolve (`resolve_ms`).
- **Failure taxonomy** (each maps to one state, mirroring R-27):
  `ok | nothing_found | unavailable` — audio-controller answers `streaming: false` with a
  reason (`stream-stall`, `ffmpeg-exit-1`, `not-connected`); nora never
  renders "nothing found" from a transport failure.

## 10. Phase split (delivery order, each independently green)

**Phase 1 — stop the bleeding (this change):**

- audio-controller: `/voice/join`, `/voice/exit`, `/music/*` endpoints; ffmpeg music
  player; priority queue (speech preempts music, position resume);
  structured logs + `request_id`; `/voice/status`.
- nora: `VoiceSession` HTTP adapter (replaces `DiscordVoiceSession`); music
  commands re-pointed; new `/discord` command group (`join`, `exit`);
  `track-finished` callback route (aiohttp 9100).
- Drop: crossfade (`CrossfadeSource` not ported — phase 1 plays
  stop→start); drop ducking's gain ramp (speech preemption is hard-cut,
  position-resume).
- Gate: `/discord join` requires (a) guild member, (b) caller in a voice
  channel, (c) channel id in `voice_channel_ids` config (existing
  fail-closed list). `/music play` keeps today's join-reject only when
  `[music].enabled` … unchanged; auto-join per Miles's spec (play joins if
  connected-voc idle, stays on stop).
- Deploy: build audio-controller image (has ffmpeg already), nora image; compose env
  unchanged (HOST=0.0.0.0, GUILD/CHANNEL for boot join).

**Phase 2 — the Siri polish:**

- Ducking ramp: audio-controller applies gain (duck) to music during speech instead of
  hard preempt — port `DuckSource` semantics to audio-controller (`duck_gain`,
  `duck_ramp_ms` from config, sent per-speech in `/stream`).
- Crossfade between music tracks (`crossfade_seconds`).
- Cache (only if evidence): nora-side `locator → stream_url` TTL.
- Barge-in (deferred from 00-overview, still deferred).

**Phase 3 — anything that needs a second connection is explicitly out of
scope** (per Discord's one-connection rule, a separate music-only token
would be the only alternative — cost/benefit says no unless audio-controller proves
insufficient).

## 11. DDD / Clean Architecture alignment

The change must NOT punch holes in the existing seams:

- **Ports (domain, unchanged names):** `Speaker`, `VoiceSession`,
  `TrackSource`, `MusicPlayerManager`'s `Connect`/`Notify` contracts. The
  domain does not know a audio-controller exists.
- **Adapter (new, infra):** `nora/infra/voice/http_controller.py` — the
  `VoiceSession`/`Speaker` implementations POSTing to audio-controller. Replaces
  `DiscordVoiceSession` at composition time only.
- **Application (unchanged):** `MusicPlayerManager`/`MusicPlayer`
  decision logic untouched. Its `Connect` closure no longer calls
  `channel.connect()`; instead it builds the HTTP session (or signals audio-controller
  join). `resume_persisted_sessions` re-points the same way.
- **audio-controller (own bounded context):** audio-controller is the only process with Discord
  voice state. It is NOT an agent; it is infrastructure, like the STT
  container. Its interface is the HTTP contract in §3 — no nora imports,
  no shared domain.
- **The one connection invariant lives in compose, not in code:** only
  `audio-controller` gets the voice socket; nora's container never
  `connect()`s.

Clean = each layer keeps its one responsibility. Nora decides and never
plays; audio-controller plays and never decides; the queue ORDER (speech > music) is
audio-controller's one allowed decision, documented here and nowhere in nora's domain.

## 12. Open decisions for Miles (blocking before code)

1. **Boot join**: after `/discord exit`, should the service auto-rejoin on
   container restart (env `CHANNEL_ID` still set → boot join)? Proposal:
   NO — `exit` persists per-run state (`state=idle`), auto-join only when
   env says so at first boot. Simpler: after exit, the env is still there
   and restart re-joins. Pick one.
2. **Auto-join on `/music play`**: Miles's spec says yes (play joins if
   necessary). Which channel does it join — the caller's current channel,
   or the configured `voice_channel_ids` one? Proposal: caller's current
   channel, allowed only if it matches the configured list.
3. **Permission ruleset**: `/discord` group — "same as existing ruleset".
   Which list exactly: guild member + caller-in-channel + channel in
   `voice_channel_ids` (proposal), or the chat-admission allowlist
   (`DISCORD_ALLOWED_USERS`-style) also applies? Need the operator to name
   it.
4. **`/music` when voice not connected**: currently the manager returns
   `None` (reject) if requester not in a channel. With audio-controller auto-join, the
   reject path disappears for allowed channels — confirm that's desired,
   and what `/music stop` does to a `state=idle` audio-controller (no-op ok).

## 13. What this does NOT do (anti-scope, pre-locked)

- audio-controller does not resolve URLs, run autoplay, or persist sessions.
- audio-controller does not hold a second connection for music ("separate music token")
  — rejected.
- No new audio format negotiation beyond ffmpeg's existing sniffing.
- No change to the wake/STT path (still receives on the same connection).
