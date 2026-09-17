#!/usr/bin/env python3
"""Latency benchmark: SenseVoiceSmall on this host.

Measures: cold model load, per-clip wall clock for several audio lengths,
decode overhead, OpenCC post-processing. CPU-only (no CUDA).
"""
import json
import time
from pathlib import Path

from funasr import AutoModel
from opencc import OpenCC

WARMUP = "clips/casual_convo.mp3"
CLIPS = ["clips/casual_convo.mp3", "clips/numbers_dates.mp3", "clips/formal_news.mp3", "clips/long.mp3"]


def audio_seconds(path: Path) -> float:
    import wave, subprocess
    # mp3 -> pcm duration via ffprobe if available, else estimate via mutagen-less hack
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=10
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def main() -> None:
    results = {}

    # 1. Cold load
    t0 = time.perf_counter()
    model = AutoModel(model="iic/SenseVoiceSmall", disable_update=True)
    cold = time.perf_counter() - t0
    results["model_load_s"] = round(cold, 2)
    print(f"cold load: {cold:.2f}s", flush=True)

    # warmup inference
    model.generate(input=WARMUP, language="auto", use_itn=True)
    print("warmup done", flush=True)

    cc = OpenCC("s2t")

    rows = []
    for p in CLIPS:
        dur = audio_seconds(Path(p))
        t0 = time.perf_counter()
        res = model.generate(input=p, language="auto", use_itn=True, batch_size_s=60)
        t1 = time.perf_counter()
        text = res[0]["text"]
        t2 = time.perf_counter()
        trad = cc.convert(text)
        t3 = time.perf_counter()

        infer = t1 - t0
        post = t3 - t2
        rows.append({
            "clip": Path(p).name,
            "audio_s": round(dur, 2),
            "infer_s": round(infer, 3),
            "post_s": round(post, 4),
            "rtf": round(infer / dur, 3) if dur else None,
            "text_len": len(text),
        })
        print(f"{Path(p).name}: audio={dur:.1f}s infer={infer:.3f}s "
              f"post={post:.4f}s rtf={infer/dur:.3f}", flush=True)

    results_path = Path("results/latency.json")
    results_path.write_text(json.dumps({"model_load_s": cold, "rows": rows}, ensure_ascii=False, indent=2))
    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
