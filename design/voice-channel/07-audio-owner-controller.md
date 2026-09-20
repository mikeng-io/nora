---
type: design
title: "Audio Owner Controller — one connection, every audio stream routed through it"
description: >
  Resolves the one-bot-one-voice-connection conflict (music bot vs wake
  controller, both on the same token) by making the audio-controller the
  SOLE owner of the Discord voice connection: join/exit are explicit
  commands, music plays through it via HTTP (ffmpeg pulls from CDN), and
  nora keeps every decision. Defines the control/data plane split, arbiter
  state machine, cache policy, memory/CPU budget, and the debug/trace/log
  contract — before code moves.
tags: [nora, discord, voice, audio, music, architecture, ddd, clean-architecture]
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

### 3.1 Connection lifecycle (single serialized owner, Codex-audit B1/B8)

The audio-controller has **one serialized connection lifecycle per guild**,
shared by boot join, HTTP join, reconnect, and exit:

```
GENERATION: a connection's identity token. Minted on join, invalidated on
destroy (exit / death / replaced connection). Every command carries it;
a command whose generation ≠ current is REJECTED with 409 — closes the
exit-vs-pending-join and delayed-play-crosses-exit races (B8).

State transitions (controller-internal, atomic):
  idle ──join──▶ connecting ──ready──▶ connected
  connected ──exit──▶ destroying ──▶ idle      (stop everything, destroy socket)
  connected/connecting ──voice-death──▶ idle   (same teardown, event pushed)
```

- Boot join and HTTP join serialize through the same lifecycle — two joins
  cannot be in flight (one process, one state machine).
- **Cutover ordering**: when a reconnect is needed, the old owner STOPS
  before the new owner joins (explicit `destroying` state; no overlap).
- `POST /voice/join {guild_id, channel_id}` when `connected` to a different
  channel ⇒ 409 (must exit first) or implement move as destroy→join under
  one lock — pick ONE; proposal: **409**, `/discord exit` then join.

### 3.2 Play semantics — replace, not enqueue (Codex-audit B2)

`VoiceSession.play()` today REPLACES the current track. The controller's
music `play` is the same: **replace the current music slot**. The UPCOMING
queue lives EXCLUSIVELY in nora (its DB, its skip logic — `music_player.py`
already reorders its own queue at 357–367; nothing in the controller may
own an upcoming music queue).

- Remove the proposed `/music/next` / `play_next_url` semantics — the
  controller has no queue of upcoming music.
- `/music/play` while a track is playing: stops the current source
  (generation bump) and starts the new one. nora only calls it when it
  wants an immediate switch (e.g. skip to the next in its OWN queue).
- Track-end event → nora decides the next track and POSTs it. nora's
  `on_track_finished` → `_advance` → `POST /music/play` for B.

| Endpoint | Body | Semantics |
|---|---|---|
| `POST /voice/join` | `{guild_id, channel_id, generation?}` | Join (idempotent if already there). 409 if connected elsewhere. |
| `POST /voice/exit` | `{generation}` | Leave channel + stop everything + stop wake pump. `voice` → `idle`; invalidates `generation`. **409 if generation mismatch.** |
| `POST /music/play` | `{generation, stream_url, title?, duration_seconds?}` | **REPLACE current music slot** (start if idle). 409 if generation mismatch. |
| `POST /music/stop` | `{generation}` | Stop current music, clear any controller-side music buffer. **Stays connected.** |
| `POST /music/pause` / `resume` | `{generation}` | Current track state. `pause` during speech: undefined → reject with 409 (arbiter state, §5). |
| `POST /music/volume` | `{generation, percent}` | 0–200, applied to current music resource. |
| `POST /stream` (TTS) | `{generation, audio_b64, media_type, request_id?, guild_id?}` | High-priority speech. 409 if generation mismatch (speech for an exited channel is dropped). |
| `POST /app/v1/voice/event` *see §5* | — | audio-controller → nora callback (track-ended, voice-death, empty-channel). |

`/voice/exit` semantics: **everything** stops — music, TTS, wake pump, then
the connection is destroyed. `state: idle`. (Miles decision 3 — "All leave,
mean nora leave the discord voice channel".)

`/music/stop` semantics: **stop only**. Music queue cleared (nora side),
controller player stopped, connection and wake pump stay. (Miles decision 2 —
stop ≠ exit.)

### 3.3 Idempotency & retry (Codex-audit B5/B8)

- Every control POST carries a client-minted `request_id` (UUID); the
  controller dedupes on `(request_id)` for 5 minutes — a retried POST does
  not double-advance (B5).
- The callbacks (§5) carry `playback_id` + `event_id`; nora consumes
  `event_id` exactly once (dedupe within guild lock), and only for the
  playback whose `playback_id` is currently expected (B5).
- Delivery failure (nora down when a track ends): controller keeps the
  terminal event in a bounded outbox (≤256 events) and retries with
  backoff; nora requests re-delivery on reconnect via `/voice/status`'s
  `last_event_seq` (B11 reconciliation).

## 4. Music flow (the thing that was broken)

Today nora resolves (`YtDlpSource.resolve` → `ResolvedTrack.stream_url`) and
plays locally via `DiscordVoiceSession`. The audio-controller keeps resolution in nora and
moves only the *playing*:

1. `/music play <q>` — nora's `MusicPlayerManager.play` (unchanged: queue
   DB, resolve, session persistence)
2. nora's new `VoiceSession` HTTP impl sends `POST /music/play
   {generation, stream_url, title, duration_seconds}` (resolved at start; a
   track that fails to resolve is `nothing_found` before any POST)
3. audio-controller: **replace** the current music slot — spawn ffmpeg (pull
   from `stream_url` via ffmpeg itself, NOT download-then-upload — nora
   holds no audio bytes; see §8)
4. audio-controller: on track end → `POST /app/v1/voice/event
   {type:"track-finished", guild_id, playback_id, event_id, generation,
   request_id}` → nora `MusicPlayerManager.on_track_finished` (existing
   method, unchanged) → nora decides next track from ITS OWN queue → POST
   `/music/play`

**Playlists** stay in nora: `resolve` expands to N `ResolvedTrack`s, queued
in nora's DB, and each is POSTed to audio-controller as the previous ends.
audio-controller never sees "playlist" and owns no upcoming queue (B2 —
replace semantics, not enqueue).

## 5. Queue arbitration — one arbiter, three states (Codex-audit B6)

audio-controller owns a single audio pipeline (`@discordjs/voice` `AudioPlayer`), the
same one today. It arbitrates exactly ONE current music slot and ONE speech
queue — the upcoming music queue lives in nora (§3.2), the speech queue is
the existing bounded TTS queue (§7).

### Arbiter states (serialized; every async result carries its generation)

```
MUSIC-STOPPED ──music/play(g)──▶ MUSIC-STARTING ──ffmpeg ready──▶ MUSIC-PLAYING
MUSIC-PLAYING ──speech arrives──▶ SPEECH-SUSPENDED(hold music gen g)
SPEECH-SUSPENDED ──speech ends──▶ MUSIC-RESUMING(g same)──▶ MUSIC-PLAYING
MUSIC-STARTING / -PLAYING / -RESUMING ──music/stop──▶ MUSIC-STOPPED (gen g invalidated)
MUSIC-PLAYING ──user pause──▶ MUSIC-PAUSED
MUSIC-PAUSED ──user resume──▶ MUSIC-PLAYING
any ──exit/voice-death──▶ TERMINAL (all gens invalidated)
```

Rules (fail-closed, no overlap ever):

1. **Speech preempts music** — hard-cut at phase 1 (B6): current music
   `AudioResource` is STOPPED (not paused — position policy below), music
   generation `g` is remembered as *suspended*, speech plays, then music
   is REVIVED from the suspended slot.
2. **Late music-start cannot kill speech**: a `MUSIC-STARTING` ffmpeg
   continuation from an old generation is ignored once speech arrived (its
   generation is no longer current).
3. **`/music/stop` or `/music/skip` during speech** invalidates the
   suspended music generation and does NOT touch speech (B6).
4. **`/music/pause` during speech** → 409 (undefined; reject loudly).
5. Music stop clears controller-side music state entirely — no remembered
   resume action can resurrect music after `/music/stop` (B6).
6. `POST /stream` (speech) validates `generation` — speech for an exited or
   replaced connection is dropped 409 (B15).

### Position policy (Codex-audit B7 — seek to the cut, since 2026-09-17)

Phase 1 restarted a suspended track from 0 (`approximate_restart`) because
the resource could not be seeked. Measured 2026-09-17 in the live channel:
every TTS reply replayed the song from the top. Phase 2 (B7) now lands in
the controller, where the stream is an ffmpeg process that CAN be started
anywhere:

- `position_ms` = the slot's **start offset** + the player's **consumed
  playback frames** (`playbackDuration`, excluding suspension and
  buffering). A fresh play has offset 0.
- On speech-suspend the cut position is read BEFORE the ffmpeg is killed;
  on revive the same `playback_id` is restarted with an input-side seek
  (`-ss <cut>` before `-i`), and the revived slot carries the cut as its
  offset, so a second interruption compounds correctly. Reported as
  `position_policy: approximate_seek` — approximate because the cut is the
  consumed-frames count, not a sample-exact cursor (the player's buffer,
  ≤ a few hundred ms, replays).
- Live/non-seekable streams (radio, `resumable: false`) get NO resume —
  speech preempts and the stream ends; nora treats that as natural end.

### Events (controller → nora, replacing track-finished)

| Event | Payload | Meaning |
|---|---|---|
| `track-finished` | `{playback_id, event_id, generation, duration_ms}` | Music ended naturally. nora advances its OWN queue. |
| `stream-failed` | `{playback_id, event_id, generation, reason}` | ffmpeg failed (not a natural end). nora re-resolves the SAME track, bounded (`_MAX_STREAM_RETRIES`), and only then advances; distinct from `nothing_found` (B14). **Landed 2026-09-18** — `MusicPlayerManager.on_stream_failed`. |
| `speech-finished` | `{event_id, request_id}` | TTS played. |
| `voice-death` | `{event_id, generation}` | Connection died. nora tears down session (existing `on_voice_death` semantics). |
| `channel-emptied` | `{event_id, generation, channel_id}` | Humans left the channel. nora applies empty-channel policy (§6). |

Phase-1 invariant: **no TTS reply is ever cut off by a music skip; no music
ever overlaps speech** — enforced by the arbiter states above, not by
convention.

### 5.1 Stale-stream retry (B14) — landed 2026-09-18

**A track that failed to START has not finished.** `stream-failed` used to
call straight through to the advance, which made it an alias for
`track-finished` with a louder log line. It cost a whole session, not a song:
measured live 2026-09-18, a source session had gone bad, so every address it
signed was answered `403 Forbidden`; each failure advanced, the next track was
resolved through that same session, failed identically, and autoplay drained
the guild to silence in about a minute — four plays, four `ffmpeg-exit-1`,
zero seconds of audio.

`MusicPlayerManager.on_stream_failed` now:

1. applies the SAME B5 staleness check (§3.3) under the guild lock — a failure
   belonging to a superseded playback neither retries nor advances;
2. spends one attempt from the failed track's budget, keyed by **locator**.
   Not by `playback_id`: every retry starts a new playback, so a
   playback-keyed budget resets on each attempt and never runs out;
3. calls `TrackSource.refresh_session()` and re-resolves the same track.
   Refreshing FIRST is what makes this a retry rather than a second helping —
   a locator is only as good as the session that signed it, and re-resolving
   through a bad one reproduces the failure at the cost of a round-trip;
4. advances exactly as before once the budget is spent, the track no longer
   resolves, or there is nothing playing.

The bound is as load-bearing as the retry. An unbounded retry on a genuinely
dead track strands a guild on silence with no way out — strictly worse than
the advance it replaced — and each attempt costs a resolve plus a play POST
while the room hears nothing. Two attempts separate the two failures worth
telling apart: one expired address, which a re-resolve fixes, and a rotten
session, which the refresh plus a re-resolve fixes.

Only the advance path notifies. A retry is the track the room was already told
about, continuing.

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

- audio-controller: `/voice/join`, `/voice/exit`, `/music/play|stop|pause|
  resume|volume`, `/stream` (TTS) — all generation-scoped; arbiter state
  machine (§5, B6); serialized connection lifecycle (§3.1, B1/B8);
  structured logs + `request_id`; `/voice/status`; `POST /app/v1/voice/event`
  callbacks (B5: `playback_id` + `event_id` + outbox retry).
- nora: **remove the local `DiscordVoiceSession` from production wiring and
  delete its reconnect path** — nothing in nora may call `channel.connect()`
  (B1, enforced by an architecture test, not by compose convention);
  replace with awaitable HTTP `VoiceSession` (`nora/infra/voice/
  http_controller.py`), typed acceptance/failure results (B4); music
  commands re-pointed; new `/discord` command group (`join`, `exit`);
  `/app/v1/voice/event` consumer (aiohttp 9100) consuming inside the guild
  lock, dedupe on `event_id` (B5).
- Lifecycle separation (B3): nora's application layer distinguishes
  **end-music** (clears queue, stops player, stays connected — what
  `/music stop` and idle-expiry do) from **leave-voice** (what `/discord
  exit`, empty-channel, and voice-death do). The old convergence of both
  onto one teardown is deliberately split.
- Empty-channel policy moves to the controller's observation + an event to
  nora (B10): nora can no longer `guild.voice_client` — the event
  `channel-emptied` triggers the same decision, and it fires even when no
  music session exists.
- Crossfade is **explicitly unsupported in phase 1**: the configuration
  knob is reported/validated as effective zero (`crossfade_seconds`
  accepted but treated as 0 and surfaced in `/voice/status`), and the
  early-completion behavior (old adapter signals completion to start
  crossfade pre-mix) is removed — advancement happens on ACTUAL music
  completion only (B17). Inter-track gap = callback + resolve + startup,
  documented.
- Drop ducking's gain ramp: speech preemption is hard-cut; position resume
  was **not attempted** in phase 1 (suspended music restarted from 0,
  `approximate_restart`); since 2026-09-17 the revive seeks to the cut
  (`approximate_seek`, §5), except live sources.
- Gate: `/discord join` requires (a) guild member, (b) caller in a voice
  channel, (c) that channel's GUILD listed in `[speech.tts] sidecars` (existing
  fail-closed list; per-CHANNEL until 2026-09-17, see §12.2). Auto-join per
  Miles's spec (play joins if connected-idle, stays on stop).
- Deploy: build audio-controller image (has ffmpeg already), nora image;
  compose env unchanged (HOST=0.0.0.0, GUILD/CHANNEL for boot join).

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
- **The one connection invariant is CODE-ENFORCED, not compose prose
  (B1):** an architecture test forbids `channel.connect()`/`VoiceClient`
  outside `discord/infra`; production composition wires ONLY
  `nora/infra/voice/http_controller.py`; `DiscordVoiceSession` (and its
  reconnect path) is deleted from the production tree, kept only as a
  contract-suite reference or removed outright.

Clean = each layer keeps its one responsibility. Nora decides and never
plays; audio-controller plays and never decides; the queue ORDER (speech > music) is
audio-controller's one allowed decision, documented here and nowhere in nora's domain.

## 12. Decisions (CLOSED 2026-09-16, Miles approved all 5)

1. **Boot join: DURABLE desired-state (a).** The controller keeps
   `desired_connection` in a small state file on a local volume — `/discord
   exit` writes `idle`; boot joins ONLY when the file says `connected` (or
   the file is absent AND env `CHANNEL_ID` is set — first boot). On restart,
   nora's boot resume asks `/voice/status` for `generation` + current
   playback BEFORE re-persisting its session (B11 reconciliation).
   Implementation: `/var/lib/audio-controller/desired_state.json` mounted
   via compose volume (NOT in git, NOT in the image).
2. **Auto-join channel: CALLER'S CURRENT CHANNEL, in an allowed GUILD.**
   ~~Must match the configured `voice_channel_ids` list; phase 1 =
   effectively ONE immutable configured channel (B15).~~

   **REVERSED 2026-09-17 (operator).** The condition §13 named as the one
   that would reopen this — lifting the single-channel posture
   controller-side — was met by measurement, not by the controller's 400:
   Zone 42 has 10+ voice channels and gains more, so a per-channel
   allow-list is one nobody can keep true, and an allow-list that drifts is
   quietly wrong rather than loudly wrong. Worse, per-channel identity would
   have given each voice channel its own `conversation_id` and therefore its
   own room — N Discord rooms in one unit, breaching DEC-105 N times
   over. The 2026-09-17 cleanup measured what ONE such breach costs: 1953
   messages, 11 fabricated notes, and the entire `culture` table.

   The gate is now **guild member + caller physically in a voice channel +
   that channel's GUILD listed in `[speech.tts] sidecars`**, and the
   controller admits any channel in a guild it holds a connection for. A
   second channel is still refused — as 409 `connected_elsewhere`, which is
   Discord's one-connection-per-bot-per-guild limit rather than a policy.

   **The wake seam CHANGED, and this is the load-bearing half.** A spoken
   utterance no longer carries a voice channel's id. The wake service sends
   `guild_id`, and nora resolves it to the ONE room whose `policy.toml`
   entry says `voice = true` — the guild's own TEXT room. Voice is a SURFACE
   of a room, not a room of its own: one notebook, one culture, one
   `persons` list, and a plan spoken aloud lands beside the same plan typed.
   Whether a reply is SPOKEN is then a fact about the turn
   (`ConversationTarget.voice`), not about the room, because one room now
   takes both. An unmarked guild is refused 400 and never reaches ingest.
3. **Permission ruleset (`/discord` group): guild member + caller
   physically in a voice channel + that channel's GUILD in
   `[speech.tts] sidecars`** (the three-condition gate from the original design,
   with the third condition widened to the guild — see decision 2). NO extra
   `DISCORD_ALLOWED_USERS`-style allowlist for voice — the existing
   chat-admission list stays for chat ONLY.
4. **`/music stop` when `state=idle` → no-op** (200 with unchanged state).
   Routine contract choice.
5. **Controller scope: SINGLE GUILD, hard-bound.** Any request with a
   `guild_id` ≠ configured guild → 400. Nothing in the data model assumes
   multi-guild (generation is a single counter, the outbox is a single queue,
   the state file is single-connection).

   **This decision STANDS. Multi-guild landed 2026-09-17 without reversing a
   word of it — and NOT the way this section first recorded.**

   "Phase 2+" named the wrong axis. Discord's limit is one voice connection
   per bot token PER GUILD, so a guild is the unit of ownership: what a
   controller must not do is hold two connections for ONE guild. Holding one
   each for several guilds is not the same claim.

   The first attempt shipped that as a DEPLOYMENT topology — a second
   container per guild, behind a compose profile, with `VOICE_GUILD_ID_B` in
   `.env`. The operator rejected it, and the reason was better than the
   design: the guild ids were already in `policy.toml` and `config.toml`, and
   `.env` was a THIRD copy. A second copy is a drift source; a third is an
   admission that nobody knows which is authoritative.

   What made the containers necessary was not the state machine. Every module
   here — lifecycle, arbiter, outbox, controller — is a factory with ZERO
   module-level mutable state, and `createCore({guildId, …})` already built a
   whole per-guild stack. Instantiating it twice was always possible. What
   actually blocked it:

   - `createController` owned the HTTP server, so N cores meant N ports. The
     socket moved out to a **front door** (`createFrontDoor`) that owns
     everything happening before a guild is known — reading the body is how
     the guild becomes known — and dispatches on `body.guild_id`. Body limits,
     the 413/400 split and the `clientError` shape moved with it verbatim;
     `endpoints.test.mjs` pins each and is what made the move safe.
   - `GET /voice/status` carries no body, so the guild rides `?guild_id=`.
     Absent is 400, unknown is 404 — never a default, which would report one
     guild's playback as another's.
   - The wake seam carried `(user_id, pcm)` and no guild, so a gate could not
     attribute audio. A connection now announces its guild in a **hello
     frame** (§TRANSPORT.md), and ONE gate serves every guild.

   So the shape is: ONE sidecar container, ONE wake gate, N connections. The
   sidecar has NO guild configuration at all — a core is minted on
   `/voice/join` and restored at boot from `<guild>.json` in its state
   directory. Which guilds exist is `[speech.tts] sidecars` in nora's
   config.toml, and one place only; Discord enforces the rest, because a join
   for a guild the bot is not in fails at the API.

   What §12.5's 400 bought is preserved differently: a core is created ONLY on
   a join, so a POST naming an unknown guild is `wrong_guild` and mints
   nothing — the same fail-closed answer, without an unbounded map keyed by
   request input.

   Cost of a second guild is now one line of config. The 261 MiB the
   container topology would have spent per guild is not spent at all.

6. **Empty-channel auto-leave (Miles, 2026-09-16 11:2x HKT): when the last
   human leaves the voice channel, start a 1-minute timer; if no human
   returns before it fires, the controller tears the connection down
   (exit) AND writes `desired=idle` to the state file, so a restart does
   NOT rejoin an empty room. Music keeps playing through the timeout (a
   human returning before the timer fires simply resets it); the teardown
   fires `channel-emptied`/`voice-death` to nora as today, so nora's
   session/queue teardown is unchanged. Timer is observed by the
   controller (it owns membership observation); anyone present (including
   Nora itself does NOT count — only human listeners) resets it. If Nora
   is alone in the channel from the start (joined with no humans), the
   timer arms on join and applies the same policy.**

   *Restored 2026-09-17: the §12.5 rewrite above swallowed this decision, and
   it was absent for one commit while `lifecycle.mjs` went on citing §12.6 as
   its only authority in seven places. A live behaviour whose decision cannot
   be found reads as undecided code.*

## 13. What this does NOT do (anti-scope, pre-locked)

- audio-controller does not resolve URLs, run autoplay, or persist sessions.
- audio-controller does not own an upcoming music queue (B2 — replace only).
- audio-controller does not hold a second connection for music ("separate music token")
  — rejected.
- No new audio format negotiation beyond ffmpeg's existing sniffing.
- The wake/STT path keeps receiving on the same connection. ~~Its
  channel/session identity is explicit once dynamic joins are allowed
  (B15).~~ **Settled 2026-09-17 the other way** (see decision 2): the wake
  path does not carry a channel identity at all. It sends the GUILD, nora
  resolves the room from `policy.toml`, and the channel survives only as
  optional provenance on `raw` — so adding a voice channel to a server
  reconfigures nothing.

## 14. Codex audit record (2026-09-16)

`codex exec --sandbox read-only` (codex-cli 0.153.4) reviewed this design
plus the existing sidecar/nora music sources. Verdict: **"The sole-owner
direction is sound. The proposed contract is not ready to implement."**
All findings are incorporated above (B1…B17 map into the §3.1–§3.3 / §4–§5 /
§10–§11 revisions).

| Ref | Severity | Finding | Resolved by |
|---|---|---|---|
| B1 | BLOCKER | Connection ownership asserted, not enforced; reconnect path remains | §11 code-enforced invariant + phase-1 removes `DiscordVoiceSession` |
| B2 | BLOCKER | Enqueue semantics break skip; upcoming queue must be nora-only | §3.2 replace-not-enqueue |
| B3 | BLOCKER | stop/exit converge in application teardown | §10 lifecycle separation |
| B4 | BLOCKER | sync port vs async HTTP; typed acceptance | §10 awaitable HTTP adapter |
| B5 | BLOCKER | Callback double-advance; playback identity | §3.3 `playback_id`+`event_id`+dedupe in guild lock |
| B6 | BLOCKER | Speech arbitration lacks states | §5 arbiter state machine |
| B7 | MAJOR | Position resume unsupported | §5 position policy (restart-from-0, seekable in phase 2) |
| B8 | BLOCKER | exit/play race | §3.1 generation + 409 + §12.5 single-guild |
| B9 | MAJOR | Voice-death doesn't cross HTTP | §5 `voice-death` event + §9 freshness |
| B10 | MAJOR | Empty-channel leave inert | §5 `channel-emptied` event |
| B11 | MAJOR | Restart reconciliation missing | §12.1 durable desired-state |
| B12 | MAJOR | PCM pump not memory-bounded | §7 revised (below): connected-state backlog bound |
| B13 | MAJOR | One AudioPlayer ≠ bounded subprocesses | §7 revised: subprocess ownership + termination |
| B14 | MAJOR | Stale-stream retry path absent | §5 `stream-failed` + nora bounded retry — **landed 2026-09-18** (§5.1) |
| B15 | MAJOR | Dynamic joins lack channel identity | §12.2 / §13: single immutable channel in phase 1 |
| B16 | MINOR | Endpoint naming + latency claims inconsistent | §3 table (one canonical schema); §9 latency measured per-stage, not inferred |
| B17 | MINOR | Crossfade drop needs explicit config migration | §10 crossfade-unsupported + effective-zero surfaced |

### §7 revision (B12/B13 — memory/CPU budget, measured not assumed)

- PCM pump: enforce a **connected-state backlog bound** (e.g. ≤16 frames
  of 20 ms per user) and observe write backpressure on the TCP seam; when
  the bound is hit, DROP frames and count a metric — define the
  frame-drop/discontinuity policy explicitly (B12). Long-lived per-user
  decoder subscriptions are bounded and cleaned up (recv.js:63-87).
- ffmpeg subprocess ownership: define spawn → kill → reap escalation (TERM
  then KILL after grace), terminate on replacement, and reap via `close`
  handlers so superseded processes cannot outlive the player (B13).
- Bound the ACTIVE + queued speech bytes, not just the body cap: ≤1 active
  + ≤4 queued, each ≤2 MB decode budget (B13 note: duration/cpu cost of a
  2 MB mp3 is itself bounded by decode, not by bytes).
- `streaming:false` reports a STALL (30 s no-bytes watch) but does not
  KILL the process — the stall must terminate the stream with `stream-failed`
  (B13).
- The 50-90 MB RSS figure is a measurement TARGET for phase 1, not an
  enforced limit.
