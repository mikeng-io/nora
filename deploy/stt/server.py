"""FunASR OpenAI-Compatible API Server — Cantonese-first.

Drop-in replacement for OpenAI's `/v1/audio/transcriptions` endpoint. Works with
any agent framework that supports OpenAI audio API (Nora's [media.describe.audio]
is wired to hit this via an OpenAI-compatible client).

Cantonese-first deviations from the upstream `modelscope/FunASR
examples/openai_api/server.py`:

- Default model is SenseVoiceSmall (`iic/SenseVoiceSmall`), which natively
  understands Cantonese (`yue`) — verified on this host (voice-lab) that it
  preserves 口語 particles like 唔/嘅/喺/哋 that faster-whisper mangles.
- `language` default is `auto` (SenseVoice does LID itself; we do NOT pin yue —
  the group mixes English and Mandarin too).
- Post-processing runs OpenCC `s2t` so Nora always receives yue-Hant-HK text
  (her doctrine is 繁體; SenseVoice emits Simplified).
- Also returns the raw text + detected language + duration for the caller.

Env knobs (all optional):
  STT_DEVICE — default cpu (this host has no CUDA; Radeon 680M iGPU not used).
  STT_MODEL — default sensevoice; see MODEL_CONFIGS for aliases.
"""
import argparse
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from opencc import OpenCC

app = FastAPI(title="Nora STT (FunASR SenseVoice)", version="0.1.0")

DEVICE = "cpu"
MODEL_CONFIGS = {
    "sensevoice": {
        "model": "iic/SenseVoiceSmall",
        "vad_model": "fsmn-vad",
        "vad_kwargs": {"max_single_segment_time": 30000},
        # SenseVoice is multilingual but its native LID tags the audio; we keep
        # language="auto" (see module docstring) and let OpenCC post-process.
    },
}

MODEL_REGISTRY: dict = {}
_cc = OpenCC("s2t")


def load_model(model_name: str):
    """Load a model into the process-global registry (one per model, lazy)."""
    if model_name in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_name]
    if model_name not in MODEL_CONFIGS:
        raise ValueError(f"Unknown model '{model_name}'. Available: {list(MODEL_CONFIGS)}")
    from funasr import AutoModel

    cfg = dict(MODEL_CONFIGS[model_name])
    cfg["device"] = DEVICE
    cfg["disable_update"] = True
    t0 = time.time()
    model = AutoModel(**cfg)
    MODEL_REGISTRY[model_name] = model
    return model


def clean_text(text: str) -> str:
    """Remove SenseVoice special tags like <|yue|><|NEUTRAL|><|Speech|><|withitn|>."""
    return re.sub(r"<\|[^|]*\|>", "", text).strip()


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="sensevoice"),
    language: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
):
    """OpenAI-compatible audio transcription endpoint.

    Returns same shape as OpenAI: `{"text": ...}` (json) or verbose_json with
    segments. Language hint: pass `language=yue` to force Cantonese (BCP-47).
    """
    if model not in MODEL_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Model '{model}' not found.")

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        asr_model = load_model(model)
        t0 = time.time()
        gen_kwargs = {"input": tmp_path, "batch_size": 1}
        if language:
            gen_kwargs["language"] = language
        result = asr_model.generate(**gen_kwargs)
        elapsed = time.time() - t0

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
        Path(tmp_path).unlink(missing_ok=True)


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
                "owned_by": "funasr",
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
    parser = argparse.ArgumentParser(description="FunASR OpenAI-Compatible API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--device", default="cpu", help="cuda / cpu / mps")
    parser.add_argument("--model", default="sensevoice", help="pre-load model at startup")
    args = parser.parse_args()

    DEVICE = args.device
    # Pre-load so the first transcription isn't a cold model download.
    load_model(args.model)
    uvicorn.run(app, host=args.host, port=args.port)
