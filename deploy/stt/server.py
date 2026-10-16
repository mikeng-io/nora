"""OpenAI-Compatible STT API Server — Cantonese-first, multi-model.

Drop-in replacement for OpenAI's `/v1/audio/transcriptions` endpoint. Works with
any agent framework that supports OpenAI audio API (Nora's [speech.stt] path is
wired to hit this via an OpenAI-compatible client).

Two engines behind one endpoint, selected per-request with the `model` form
field:

- `noise-detection` (DEFAULT) — JackyHoCL
  `whisper-large-v3-turbo-cantonese-noise-detection` on the plain transformers
  runtime (fp32 CPU). Keeps both `RV` and `discord` intact on real Discord
  voice audio, emits Traditional Chinese with punctuation out of the box, RTF
  ~0.49. Trained for streaming noise: silence/background comes back tagged
  `%nz`, which post-processing strips.
- `sensevoice` (BACKUP) — `iic/SenseVoiceSmall` via FunASR. The latency floor
  (RTF ~0.04) and the failover when the primary is unavailable. Natively
  understands Cantonese (`yue`) — verified on this host (voice-lab) that it
  preserves 口語 particles like 唔/嘅/喺/哋 that faster-whisper mangles — but emits
  Simplified Chinese and no punctuation.

Post-processing runs OpenCC `s2t` for BOTH backends so Nora always receives
yue-Hant-HK text (her doctrine is 繁體) regardless of which engine answered.
`language` is the engines' own short code (`yue`/`zh`/`en`/`auto`/...), NOT a
BCP-47 tag; SenseVoice does LID itself on `auto`, the whisper backend defaults
to `yue` (the model is Cantonese-tuned — see its model card).

Also returns the raw text + detected language + duration for the caller.

Env knobs (all optional):
  STT_DEVICE — default cpu (this host has no CUDA; Radeon 680M iGPU not used).

Which model is PRE-LOADED at startup (and answers a request that names none)
comes from Nora's config.toml — `[speech.wake].stt_model`, the latency-bound
wake path's engine — bind-mounted read-only at /app/config.toml by compose
(`config_toml.py`; falls back to sensevoice without the file). There is no
`STT_MODEL` env var: a second source would disagree with the config file.
"""
import argparse
import asyncio
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from opencc import OpenCC

app = FastAPI(title="Nora STT", version="0.2.0")

from config_toml import preload_model  # noqa: E402  (sibling module, stdlib only)

DEFAULT_MODEL = preload_model()

DEVICE = "cpu"
MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "noise-detection": {
        "backend": "transformers",
        "model": "JackyHoCL/whisper-large-v3-turbo-cantonese-noise-detection",
        # Generation params from the model card (the A/B that picked this
        # engine ran with them): temperature/top_p imply sampling. The card's
        # `seed=4419` is an OpenAI-API serving param, NOT a transformers
        # generate kwarg (generate() rejects it), so it is not carried here.
        "generate_kwargs": {
            "temperature": 0.3,
            "top_p": 0.5,
            "repetition_penalty": 1.05,
            "do_sample": True,
        },
        # Cantonese-tuned whisper: pin yue unless the caller says otherwise.
        # (SenseVoice below is the one that LIDs itself.)
        "default_language": "yue",
    },
    "sensevoice": {
        "backend": "funasr",
        "model": "iic/SenseVoiceSmall",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
        # SenseVoice is multilingual but its native LID tags the audio; we keep
        # language="auto" (see module docstring) and let OpenCC post-process.
    },
}

MODEL_REGISTRY: dict = {}
_cc = OpenCC("s2t")


def _load_funasr(cfg: dict[str, Any]):
    from funasr import AutoModel

    _LOADER_KEYS = ("backend", "generate_kwargs", "default_language")
    kwargs = {k: v for k, v in cfg.items() if k not in _LOADER_KEYS}
    kwargs["device"] = DEVICE
    kwargs["disable_update"] = True
    return AutoModel(**kwargs)


def _load_transformers(cfg: dict[str, Any]):
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        cfg["model"], dtype=torch.float32, low_cpu_mem_usage=True,
    )
    processor = AutoProcessor.from_pretrained(cfg["model"])
    return pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        chunk_length_s=30,
        device=DEVICE,
    )


_LOADERS = {"funasr": _load_funasr, "transformers": _load_transformers}


def load_model(model_name: str):
    """Load a model into the process-global registry (one per model, lazy)."""
    if model_name in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_name]
    if model_name not in MODEL_CONFIGS:
        raise ValueError(f"Unknown model '{model_name}'. Available: {list(MODEL_CONFIGS)}")
    cfg = MODEL_CONFIGS[model_name]
    model = _LOADERS[cfg["backend"]](cfg)
    MODEL_REGISTRY[model_name] = model
    return model


def clean_text(text: str) -> str:
    """Remove SenseVoice special tags like <|yue|><|NEUTRAL|><|Speech|><|withitn|>
    and the noise-detection model's `%nz` noise markers (its model card: "the
    noise is labeled as %nz, please remove/replace this pattern")."""
    text = re.sub(r"<\|[^|]*\|>", "", text)
    text = text.replace("%nz", "")
    return re.sub(r"\s+", " ", text).strip()


def _generate_funasr(model_name: str, wav_path: str, language: str | None) -> list:
    asr_model = load_model(model_name)
    gen_kwargs: dict[str, Any] = {"input": wav_path, "batch_size": 1}
    if language:
        gen_kwargs["language"] = language
    return asr_model.generate(**gen_kwargs)


def _generate_transformers(model_name: str, wav_path: str, language: str | None) -> list:
    """Run the whisper pipeline and reshape its result into FunASR's
    `[{"text": ...}]` list so the response builder below has ONE shape."""
    import soundfile as sf

    cfg = MODEL_CONFIGS[model_name]
    pipe = load_model(model_name)
    array, sampling_rate = sf.read(wav_path)
    gen_kwargs = dict(cfg.get("generate_kwargs", {}))
    lang = language or cfg.get("default_language")
    if lang:
        gen_kwargs["language"] = lang
    out = pipe({"array": array, "sampling_rate": sampling_rate}, generate_kwargs=gen_kwargs)
    return [{"text": out["text"]}]


def _run_stt_blocking(
    *, tmp_path: str, wav_path: str, model: str, language: str | None,
) -> tuple[list, float]:
    """ffmpeg normalize + model.generate — the CPU-bound half of one
    transcription. Run via `asyncio.to_thread` so the event loop keeps
    serving `/health` and other requests while this runs (a real
    transcription is seconds, not milliseconds, on this host's CPU)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", wav_path],
        check=True, capture_output=True,
    )
    t0 = time.time()
    if MODEL_CONFIGS[model]["backend"] == "transformers":
        result = _generate_transformers(model, wav_path, language)
    else:
        result = _generate_funasr(model, wav_path, language)
    return result, time.time() - t0


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default=DEFAULT_MODEL),
    language: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
):
    """OpenAI-compatible audio transcription endpoint.

    Returns same shape as OpenAI: `{"text": ...}` (json) or verbose_json with
    segments. Language hint: pass `language=yue` to force Cantonese (the
    engines' own short code, NOT BCP-47).
    """
    if model not in MODEL_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Model '{model}' not found.")

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    tmp_path: str | None = None  # bound before the try so the finally can reference it
    wav_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        # Normalize ANY upload (mp3, ogg/opus, m4a/AAC, wav) to 16kHz mono WAV
        # with ffmpeg before the model sees it. libsndfile cannot decode
        # audio/mp4 and Signal voice notes commonly arrive
        # as audio/mp4 — without this the endpoint 500s on real voice notes.
        # mp3/ogg pass through the same pipe, so one code path for everything.
        wav_path = tmp_path + ".wav"
        # Both ffmpeg and `generate` are blocking calls on this CPU-only host —
        # run them in a worker thread so `/health` and any concurrent request
        # aren't frozen behind this one for the whole transcription.
        result, elapsed = await asyncio.to_thread(
            _run_stt_blocking, tmp_path=tmp_path, wav_path=wav_path,
            model=model, language=language,
        )

        raw = result[0]["text"] if result else ""
        text = _cc.convert(clean_text(raw))  # s2t → 繁体 (yue-Hant-HK)

        if response_format == "verbose_json":
            segments = []
            for seg in (result[0].get("sentence_info") or []):
                segments.append({
                    "start": seg.get("start", 0) / 1000.0,
                    "end": seg.get("end", 0) / 1000.0,
                    "text": _cc.convert(clean_text(seg.get("text", ""))),
                    "speaker": seg.get("spk"),
                })
            return JSONResponse({
                "text": text,
                "segments": segments,
                "language": language or "auto",
                "duration": round(elapsed, 3),
                "model": model,
            })
        return JSONResponse({"text": text})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)
        if wav_path is not None:
            Path(wav_path).unlink(missing_ok=True)


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible model list."""
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": 1700000000,
                "owned_by": "nora-stt",
                "ready": name in MODEL_REGISTRY,
            }
            for name in MODEL_CONFIGS
        ],
    })


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "models_loaded": list(MODEL_REGISTRY.keys()),
        "models_available": list(MODEL_CONFIGS.keys()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenAI-Compatible STT API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--device", default=os.environ.get("STT_DEVICE", "cpu"), help="cuda / cpu / mps",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="pre-load model at startup",
    )
    args = parser.parse_args()

    DEVICE = args.device
    # Pre-load so the first transcription isn't a cold model download.
    load_model(args.model)
    uvicorn.run(app, host=args.host, port=args.port)
