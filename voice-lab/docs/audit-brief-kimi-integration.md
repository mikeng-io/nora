# Kimi — Stage 3 Voice Wake Integration Brief (Nora)

Implement the missing stage-3 wiring so a Discord voice channel user can say
「喂 Nora <command>」 and Nora answers in the same voice channel (ducked TTS),
while the typed-message reactive path receives the transcript as a user input.

Work ONLY in the nora repo worktree: `/home/<deploy-user>/workspace/nora-wt-voice-design`
(branch `feat/voice-channel-design`). Commit your changes to this branch.
Do NOT touch `/home/<deploy-user>/workspace/nora` (master) or any other repo.

## What already exists (verified, do not rebuild)

- **Stage 2 sidecar** (receive): `deploy/voice-sidecar/` — joins a Discord
  voice channel, DAVE-decrypts per-user Opus, decodes to PCM, and forwards
  frames over TCP to a wake-gate seam. Frame format (TRANSPORT.md):
  `[u32be payload_len][u8 user_id_len][user_id utf8][pcm bytes]`,
  PCM is **s16le 48 kHz stereo**, 3840 bytes per 20ms frame.
  Reference listener: `deploy/voice-sidecar/consume.mjs`.
- **STT service**: `deploy/stt/server.py` runs at `stt:8000` on the `nora`
  docker network — OpenAI-compatible `POST /v1/audio/transcriptions`
  (multipart `file` + optional `model`/`language`; `language=yue` forces
  Cantonese; `response_format=verbose_json` gives segments).
  Dual-engine (`c84a49cb`: noise-detection primary, SenseVoice backup) is
  committed but the running container predates it — REBUILD needed.
- **Stage 1 TTS + ducking**: `nora/platform/discord/voice_session.py`
  `DiscordVoiceSession.speak(speech_audio, media_type, duck=True)` +
  `nora/infra/tts.py MiniMaxTtsSpeaker`; wired in `nora/app/composition.py`
  (line ~1518) only when `[speech.tts].enabled=true` in config.toml.
  `speakable(text)` gate lives in `nora/domain/ports.py:1010`.
- **Wake model**: `/home/<deploy-user>/workspace/voice-lab/data/modal-output-v3/喂Nora.onnx`
  (205,430 bytes, openWakeWord 16×96 input, ONNX runtime).
  Training/validation scripts + the int16 harness live in
  `/home/<deploy-user>/workspace/voice-lab/scripts/` (do not re-train; this is
  integration only).
- **Nora adapter** (typed Discord): `nora/platform/discord/adapter.py`
  `DiscordAdapter.listen(on_message)` → `parse_discord_message` →
  `NormalizedMessage` → `IngestPipeline.run(message)`.
  Nora's aiohttp app lives in `nora/web/api.py` — a `register_*` function
  taking an existing `web.Application`; new routes must register the same way.

## What to build

### 1. `deploy/voice-wake/` — Python wake-gate service (new docker service)

Mirror `deploy/stt/`'s layout + Dockerfile patterns (own image, healthcheck,
non-root user, `uv` deps via `uv.lock`). On the `nora` network it is the
only consumer of the seam, so compose sets `PUMP_PORT=4599` and
`PUMP_HOST=127.0.0.1` inside the container (bind 4599, sidecar connects out —
see sidecar README). Responsibilities:

- **TCP seam listener** on 4599 implementing the frame codec above
  (port of `deploy/voice-sidecar/src/recv.js` `createFrameParser`).
- **Resample** 48k stereo s16le → 16k mono float/int16 for openWakeWord
  (the model + our int16 harness proof: float [-1,1] input silently
  truncates to 0 — always feed int16 PCM).
- **Wake detection**: load `喂Nora.onnx` via `openwakeword.Model(
  wakeword_models=[...], inference_framework="onnx")`; keep per-user rolling
  16-frame (2.0s) windows; fire at threshold (start configurable, default
  ~0.5); then **collect the utterance** (user keeps speaking + silence tail,
  e.g. 1.0s) and stop.
- **STT call**: send collected utterance as WAV (16k mono) to
  `http://stt:8000/v1/audio/transcriptions` with `model` = the primary
  noise-detection model id from `deploy/stt/server.py MODEL_CONFIGS`
  (check the key — likely `noise-detection` or similar), `language=yue`.
  On STT failure, fall back to `language` unset (auto) once, then drop
  silently (posture: no wake → no cost).
- **Forward transcript** to Nora via a new HTTP POST on Nora's aiohttp app
  (below) as JSON: `{"user_id": <str>, "text": <str>, "conversation_id": <str>}`
  (conversation_id from env `VOICE_CHANNEL_ID`).
- **`/health`**: process liveness like the sidecar (voice state, per-user
  counters since start).

### 2. Nora endpoint — `nora/web/voice.py` (+ register in api.py)

`register_voice(app, *, ingest)` adds `POST /app/v1/voice/utterance`.
Body: `{user_id, text, conversation_id}` (JSON). Behaviour:

- Build a `NormalizedMessage` for the EXISTING ingest path:
  `platform="discord"`, `conversation_id` from body, `conversation_type="group"`
  (or "dm" when the channel is a DM), `sender_id=user_id`,
  `sender_name=None`, `timestamp=int(time.time()*1000)`,
  `body=text`, `raw={"voice": True, "user_id": user_id, "channel_id": conversation_id}`.
- Run `ingest.run(message=...)` exactly like the Discord adapter does
  (so: audit row, Honcho memory feed as a user input, reactive path).
- Return 202 accepted (never block on downstream agent work).
- **Do NOT store audio anywhere.** Text-only, by design.
- Keep the endpoint minimal — do not add auth here; the wake service is on
  the private `nora` network. Note that in the docstring.

### 3. Deploy wiring

- `docker-compose.yml`: add `voice-wake` service (own image from
  `./deploy/voice-wake`, no published ports, `networks: [nora]`,
  `restart: unless-stopped`, healthcheck curling its /health, env:
  `NORA_INGEST_URL=http://nora:9100` (or the address the nora container's
  aiohttp app actually binds — verify, don't assume), `VOICE_CHANNEL_ID`
  from `.env`, model path baked into the image or a small mount).
  Add `voice-wake` to the same env-namespacing conventions the other
  services use (see `voice-sidecar` block, line ~760).
- Keep `voice-sidecar`'s `PUMP_HOST`/`PUMP_PORT` env wiring so compose sets
  `VOICE_PUMP_HOST=127.0.0.1` + `VOICE_PUMP_PORT=4599` (sidecar connects to
  wake in the same container network via the service name if simpler —
  whichever matches the existing seam; state what you chose and why).

### 4. Config + .env

- `config.toml`: `[speech.tts] enabled = true` stays OFF by default — add a
  comment/guard for the operator; do not enable in the shipped config.
  Add `[speech.wake]` config section (enabled, threshold, model path)
  read by the wake service through its own env (the wake service is a
  separate container; it should not need Nora's config.toml — prefer env).
- `.env.example` (NOT `.env` — never commit secrets): add
  `VOICE_GUILD_ID=`, `VOICE_CHANNEL_ID=`, `VOICE_WAKE_THRESHOLD=0.5`.

## Constraints (repo conventions)

- Tests: new behaviour gets named tests; use existing fixtures/patterns
  (`tests/` layout per repo README: `tests/unit/` no I/O, `tests/integration/`
  wired pipeline). The STT/wake codec + utterance collector should be unit-
  testable without a real socket or model — extract pure functions.
- `uv add` for dependencies — never raw pip.
- Commit in small focused commits on `feat/voice-channel-design`; do not
  merge, do not push to master, do not touch other profiles/repos.
- Follow `docs/maintainers/conventions-rationale.md` (one stage one module; no `*_fn` test hooks;
  contract suites for ports).
- Do NOT modify `deploy/voice-sidecar/` (stage 2 is done and verified).
- Do NOT modify `~/workspace/voice-lab/` (training lab, out of scope here).

## Verification you must run before reporting done

1. `deploy/voice-wake` codec unit test: feed a recorded frame fixture
   through the parser (you can generate one with the sidecar's
   `node smoke_test.mjs` / `consume.mjs --file`) and assert per-user
   frames/bytes match.
2. Wake gate logic unit test: feed a synthetic PCM window through the
   resample + model path with the v3 ONNX — assert it scores > threshold for
   a TTS 「喂 Nora」 clip if one is available under
   `/home/<deploy-user>/workspace/voice-lab/oww-data/output/喂Nora/positive_train/`
   (read-only), or mark that test skipped when the file is absent.
3. Nora endpoint integration test: POST a fake utterance and assert an
   ingest row is created (check the audit table) and 202 returned.
4. `docker compose config` passes with your new service.

Report back: commit hashes, what each test proved, anything you left
unverified (especially: whether the nora container exposes 9100 to the
`nora` network — check `docker-compose.yml` nora service ports/network
exposure and say definitively, don't guess).
