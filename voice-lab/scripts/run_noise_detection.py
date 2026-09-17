#!/usr/bin/env python3
"""Transcribe with JackyHoCL noise-detection model (transformers, CPU)."""
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
import torch, time

t0 = time.time()
model_id = "/tmp/whisper-turbo-noise"
processor = AutoProcessor.from_pretrained(model_id)
model = AutoModelForSpeechSeq2Seq.from_pretrained(model_id, dtype=torch.float32, low_cpu_mem_usage=True)
print(f"loaded in {time.time()-t0:.1f}s", flush=True)

asr = pipeline(
    "automatic-speech-recognition",
    model=model,
    tokenizer=processor.tokenizer,
    feature_extractor=processor.feature_extractor,
    generate_kwargs={"language": "yue", "temperature": 0.3, "repetition_penalty": 1.05},
)

t1 = time.time()
r = asr("sidecar/recordings/100000000000000001-1789405579646.wav")
print(f"transcribed in {time.time()-t1:.1f}s", flush=True)
print("TEXT:", r["text"], flush=True)
