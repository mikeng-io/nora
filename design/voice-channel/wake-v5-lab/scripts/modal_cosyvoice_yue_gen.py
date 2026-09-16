#!/usr/bin/env python3
"""Modal one-shot: zero-shot voice-cloned Cantonese wake/negative clips with ASLP-lab/Cosyvoice2-Yue.

LOCAL : tar a seeds dir (spkNNN/prompt.wav + seeds.json with prompt_text) → Modal Volume.
MODAL : T4, CosyVoice2 fp16, for every speaker × text × speed → 16 kHz int16 wavs + manifest.json.
LOCAL : pull the output tar back.  Nothing is written into voice-lab/oww-data.

Usage:  modal run modal_cosyvoice_yue_gen.py::main --seeds <dir> --out <tar.gz> [--speakers N]
Facts used here are from wake-v5-lab/cosyvoice-cantonese-plan.md (2026-09-16).
"""
from __future__ import annotations
import os, tarfile, time
from pathlib import Path
import modal

VOL_NAME = "wake-v6-gen"; VOL_DIR = "/data"
MODELS = {"yue": ("ASLP-lab/Cosyvoice2-Yue", "/models/Cosyvoice2-Yue"),
          "base": ("FunAudioLLM/CosyVoice2-0.5B", "/models/CosyVoice2-0.5B")}
MODEL_ID, MODEL_DIR = MODELS["yue"]
POS_TEXTS = ["喂，Nora。", "喂Nora。", "喂，Nora呀。"]
PROBE_TEXTS = ["喂，諾拉。", "喂諾拉。", "喂，諾拉呀。"]  # NOT positives: nok6-laai1 ≠ "Nora"; gated into a trap bucket
NEG_TEXTS = {"trap": ["喂勞拉。", "喂娜娜。", "喂露娜。"],
             "mention": ["唔該Nora。", "Nora你今日忙唔忙呀。"],
             "command": ["而家幾點呀。", "下一首。"],
             "generic": ["我今晚返嚟食飯。"]}
SPEEDS = [0.9, 1.1]

# requirements.txt of FunAudioLLM/CosyVoice (main, 2026-09-16) minus serving/training-only pins
PIP = ["conformer==0.3.2", "diffusers==0.29.0", "hydra-core==1.3.2", "HyperPyYAML==1.2.3", "inflect==7.3.1",
       "librosa==0.10.2", "lightning==2.2.4", "matplotlib==3.7.5", "modelscope==1.20.0", "networkx==3.1",
       "omegaconf==2.3.0", "onnx==1.16.0", "protobuf==4.25", "pydantic==2.7.0",
       "rich==13.7.1", "soundfile==0.12.1", "transformers==4.51.3", "x-transformers==2.11.24",
       "wetext==0.0.4", "numpy==1.26.4", "huggingface_hub",
       # the rest of upstream requirements.txt (cosyvoice.flow imports gdown; keep everything but deepspeed/tensorrt/vllm)
       "gdown==5.1.0", "wget==3.2", "gradio==5.4.0", "fastapi==0.115.6", "fastapi-cli==0.0.4", "uvicorn==0.30.0",
       "grpcio==1.57.0", "grpcio-tools==1.57.0", "tensorboard==2.14.0", "pyarrow==18.1.0"]

def _download_model():
    from huggingface_hub import snapshot_download
    for mid, mdir in MODELS.values():
        snapshot_download(mid, local_dir=mdir, ignore_patterns=["vllm/*", "*.plan", "asset/*", "flow.cache.pt", "*.zip"])
        print("model files:", mid, sorted(os.listdir(mdir)), flush=True)

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "git-lfs", "libsndfile1", "ffmpeg", "sox", "libsox-dev", "build-essential")
    # build backends of openai-whisper / pyworld import pkg_resources and numpy at build time
    .pip_install("setuptools<75", "wheel", "Cython", "numpy==1.26.4")
    .pip_install("torch==2.3.1", "torchaudio==2.3.1", extra_index_url="https://download.pytorch.org/whl/cu121")
    .pip_install("onnxruntime-gpu==1.18.0",
                 extra_index_url="https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/")
    # setup.py of these two imports pkg_resources (gone in setuptools>=80, which pip's isolated
    # build env would pull); build them against the pinned setuptools instead
    .pip_install("openai-whisper==20231117", "pyworld==0.3.4", extra_options="--no-build-isolation")
    .pip_install(*PIP)
    .run_commands("git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /opt/CosyVoice")
    .run_function(_download_model)
    .env({"PYTHONPATH": "/opt/CosyVoice:/opt/CosyVoice/third_party/Matcha-TTS"})
)
app = modal.App("wake-v6-cosyvoice-yue", image=image)
vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)


@app.function(gpu="T4", timeout=3 * 3600, volumes={VOL_DIR: vol})
def generate(seeds_tar: str, max_speakers: int = 0, model: str = "yue", mode: str = "zero_shot",
             pos_texts: list | None = None, probe_texts: list | None = None, tag: str = "") -> str:
    import json, sys, torch, torchaudio, numpy as np, soundfile as sf
    sys.path.insert(0, "/opt/CosyVoice"); sys.path.insert(0, "/opt/CosyVoice/third_party/Matcha-TTS")
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from cosyvoice.utils.file_utils import load_wav

    work = Path(VOL_DIR) / "work"; out = Path(VOL_DIR) / "out"
    for p in (work, out):
        if p.exists():
            import shutil; shutil.rmtree(p)
        p.mkdir(parents=True)
    with tarfile.open(Path(VOL_DIR) / seeds_tar) as tf:
        tf.extractall(work, filter="data")
    seeds = json.load(open(next(work.rglob("seeds.json"))))
    if max_speakers: seeds = seeds[:max_speakers]
    print(f"speakers={len(seeds)} cuda={torch.cuda.is_available()} {torch.cuda.get_device_name(0)}", flush=True)

    # ASLP-lab/Cosyvoice2-Yue ships training-format checkpoints (extra "epoch"/"step" keys);
    # upstream loads strictly, so strip them once (container FS is writable, ephemeral).
    model_dir = MODELS[model][1]; POS = pos_texts or POS_TEXTS; PROBE = probe_texts if probe_texts is not None else PROBE_TEXTS
    for ck in ("llm.pt", "flow.pt", "hift.pt"):
        fp = Path(model_dir) / ck
        if fp.exists():
            sd = torch.load(fp, map_location="cpu")
            extra = [k for k in ("epoch", "step") if k in sd]
            if extra:
                for k in extra: sd.pop(k)
                torch.save(sd, fp); print(f"stripped {extra} from {ck}", flush=True)
    t0 = time.time(); cv = CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=True)
    print(f"model loaded in {time.time()-t0:.0f}s, sample_rate={cv.sample_rate}", flush=True)
    resamp = torchaudio.transforms.Resample(cv.sample_rate, 16000)
    manifest = []; n_err = 0; times = []
    for si, sd in enumerate(seeds):
        pdir = next(work.rglob(sd["sid"])); prompt = str(pdir / "prompt.wav")  # upstream API now takes a path and loads it itself
        jobs = [("pos", t) for t in POS] + [("nokla", t) for t in PROBE] + [(k, t) for k, ts in NEG_TEXTS.items() for t in ts]
        for kind, text in jobs:
            for speed in (SPEEDS if kind == "pos" else [1.0]):
                name = f"{tag}{sd['sid']}_{kind}_{abs(hash(text)) % 10**6}_{int(speed*100)}.wav"
                try:
                    t1 = time.time(); wavs = []
                    gen = (cv.inference_cross_lingual(text, prompt, stream=False, speed=speed) if mode == "cross_lingual"
                           else cv.inference_zero_shot(text, sd["prompt_text"], prompt, stream=False, speed=speed))
                    for o in gen:
                        wavs.append(o["tts_speech"])
                    w = torch.cat(wavs, dim=1); w16 = resamp(w.float().cpu()).squeeze(0).numpy()
                    w16 = np.clip(w16 / max(1e-6, np.abs(w16).max()) * 0.8, -1, 1)
                    sf.write(out / name, (w16 * 32767).astype(np.int16), 16000)
                    dt = time.time() - t1; times.append(dt)
                    manifest.append({"file": name, "sid": sd["sid"], "kind": kind, "text": text, "speed": speed, "model": model, "mode": mode,
                                     "dur": round(len(w16) / 16000, 3), "gen_s": round(dt, 2), "prompt_text": sd["prompt_text"]})
                except Exception as e:  # keep going; record the failure
                    n_err += 1; manifest.append({"file": None, "sid": sd["sid"], "kind": kind, "text": text, "speed": speed, "error": repr(e)[:200]})
        if si == 0 or (si + 1) % 5 == 0:
            print(f"[{si+1}/{len(seeds)}] clips={len([m for m in manifest if m.get('file')])} errors={n_err} "
                  f"mean_gen={np.mean(times):.2f}s/utt", flush=True)
    json.dump(manifest, open(out / "manifest.json", "w"), ensure_ascii=False, indent=1)
    out_tar = Path(VOL_DIR) / f"v6-gen-out{tag}.tar.gz"
    with tarfile.open(out_tar, "w:gz") as tf:
        tf.add(out, arcname="gen")
    vol.commit()
    print(f"DONE clips={len([m for m in manifest if m.get('file')])} errors={n_err} total={time.time()-t0:.0f}s "
          f"mean_gen={np.mean(times) if times else float('nan'):.2f}s/utt", flush=True)
    return str(out_tar)


@app.local_entrypoint()
def main(seeds: str, out: str, speakers: int = 0, arms: str = "yue:zero_shot") -> None:
    """arms = comma list of model:mode[:spelling]; spelling 'nola' swaps Nora→挪拉 in the positive texts."""
    seeds_dir = Path(seeds); tar_path = Path(out).with_suffix(".seeds.tar")
    with tarfile.open(tar_path, "w") as tf:
        tf.add(seeds_dir, arcname="seeds")
    print(f"seeds tar {tar_path.stat().st_size/1e6:.0f} MB", flush=True)
    with vol.batch_upload(force=True) as b:
        b.put_file(tar_path, "/seeds.tar")
    for arm in arms.split(","):
        parts = arm.split(":"); model, mode = parts[0], parts[1]; spelling = parts[2] if len(parts) > 2 else ""
        pos = [x.replace("Nora", "挪拉") for x in POS_TEXTS] if spelling == "nola" else None
        tag = f"{model}-{mode}{('-' + spelling) if spelling else ''}-"
        remote = generate.remote("seeds.tar", speakers, model, mode, pos, [] if spelling else None, tag)
        dst = Path(out).with_name(Path(out).stem.replace(".tar", "") + f"-{tag.rstrip('-')}.tar.gz")
        with open(dst, "wb") as f:
            vol.read_file_into_fileobj(f"v6-gen-out{tag}.tar.gz", f)
        print(f"downloaded {dst} ({dst.stat().st_size/1e6:.0f} MB) from {remote}", flush=True)
