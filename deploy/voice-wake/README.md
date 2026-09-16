# nora-voice-wake

Stage 3 of the voice-channel design (`design/voice-channel/`): the Python
wake gate, and the **only** consumer of the voice-sidecar's TCP seam
(`deploy/voice-sidecar/TRANSPORT.md`). Per-user PCM comes in, openWakeWord
listens for 「喂 Nora」, the utterance that follows is transcribed by the
local `stt` service, and the **text** is forwarded to Nora's
`POST /app/v1/voice/utterance`, where it walks the same ingest path as a
typed Discord message.

**Audio is transient:** frames are consumed, scored, and dropped — never
written to disk, never queued if a downstream is down. The failure posture
is *no wake → no cost*: an STT failure retries once with `language` unset,
then drops the utterance silently.

## Pipeline

```
sidecar frames (TCP :4599)        [u32be len][u8 id_len][user_id][pcm]
  -> codec.FrameParser            incremental frame cutter (pure)
  -> audio.stereo_to_mono         48kHz stereo s16le -> 48kHz mono int16
  -> audio.downsample_to_16k      3840-sample blocks -> 1280-sample oww chunks
  -> gate.WakeGate (per user)     LISTENING -(score >= threshold)-> COLLECTING
                                  -1s silence tail / 10s ceiling-> Utterance
  -> stt.transcribe               16kHz mono WAV -> stt:8000 (model=noise-detection, language=yue)
  -> stt.forward_utterance        JSON {user_id, text, conversation_id} -> nora:9100
```

The wake model is per-user stateful (openWakeWord owns the rolling
melspectrogram/embedding windows), so each speaking user gets their own
`openwakeword.Model` instance, reaped after 10 minutes idle.

**int16 is load-bearing:** openWakeWord's predictor must be fed int16 PCM —
float [-1, 1] input silently truncates to 0 and the gate would never fire
(measured in the training lab, `voice-lab/scripts/gate_int16.py`).

## Configuration (env)

| var | default | meaning |
|---|---|---|
| `WAKE_LISTEN_HOST` / `WAKE_LISTEN_PORT` | `0.0.0.0` / `4599` | seam bind (the sidecar connects OUT to here) |
| `WAKE_HEALTH_HOST` / `WAKE_HEALTH_PORT` | `127.0.0.1` / `4601` | /health bind (loopback only) |
| `WAKE_MODEL_PATH` | `/app/models/喂Nora.onnx` | baked into the image |
| `WAKE_THRESHOLD` | `0.5` | wake score that starts collection |
| `WAKE_SILENCE_TAIL_MS` | `1000` | trailing silence that ends an utterance |
| `WAKE_MAX_UTTERANCE_S` | `10` | utterance ceiling |
| `WAKE_SILENCE_RMS` | `400` | int16 RMS under this reads as silence |
| `STT_URL` / `STT_MODEL` / `STT_LANGUAGE` | `http://stt:8000` / `noise-detection` / `yue` | the local STT service |
| `NORA_INGEST_URL` | `http://nora:9100` | Nora's aiohttp app on the `nora` network |
| `VOICE_GUILD_ID` / `VOICE_CHANNEL_ID` | — | become `conversation_id` = `"<guild>/<channel>"`, the same flattened shape the Discord parser produces for typed messages |

Compose maps the operator's `VOICE_*` .env names onto these (see the
`voice-wake` block in `docker-compose.yml`); `WAKE_THRESHOLD` comes from
`VOICE_WAKE_THRESHOLD`.

## Health semantics

`GET /health` is **process liveness**, same posture as the sidecar: 200
whenever the process is up. `wake` is `listening` or `disabled` (model file
absent — healthy-but-idle, frames consumed but never scored), plus connected
seam `clients` and per-user `frames`/`bytes`/`wakes`/`utterances` counters
since start, so an operator can tell "no model" from "nobody spoke" from
"woke but STT/forward failed" without reading logs.

## Tests

`uv run pytest` — codec against a recorded frame fixture, the resampler, the
gate state machine with a stub scorer (no model, no socket), and the real
v3 ONNX against a TTS 「喂 Nora」 clip when one is reachable (skipped
otherwise). `uv run python smoke_test.py` drives synthetic frames through
the real TCP seam end to end.
