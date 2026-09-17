#!/usr/bin/env python3
"""Modal one-shot: train the 「喂 Nora」 openWakeWord model on a T4 GPU.

Pipeline (mirrors the locally smoke-verified sequence EXACTLY):
  1. LOCAL: package the training corpus into tarballs with the layout
     train.py expects (output/<model>/positive_train|positive_test|
     negative_train|negative_test, plus rir/, bg/, validation features,
     and a rewritten YAML config).
  2. MODAL: upload the tarballs to a Volume, extract, run the SAME
     train.py --augment_clips --train_model sequence that passed smoke
     locally (torch 2.1.2 / scipy 1.14.1 / espeak-novak / torch-audiomentations)
     but with CUDA torch on T4.
  3. MODAL: bundle ONNX + logs back to the Volume; local entrypoint pulls
     the tarball down and reports the path.

Usage:
    uv tool install modal
    modal token new                        # browser auth, once
    modal run scripts/modal_train_wakeword.py::main

Env: MODAL_WAKE_STEPS (default 20000), MODAL_WAKE_GPU (default "T4").
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tarfile
from pathlib import Path

import modal
import yaml

# ---------------------------------------------------------------- constants
ROOT = Path("/home/norty/workspace/voice-lab-data")
OWW_SRC = ROOT / "oww-src"
PIPER_SRC = ROOT / "oww-data" / "piper-sample-generator"
DATA_SRC = ROOT / "oww-data"
CFG_LOCAL = DATA_SRC / "喂Nora.yml"
MODEL_NAME = "喂Nora"
OUT_MODEL = DATA_SRC / "output" / "喂Nora"

STEP_COUNT = int(os.environ.get("MODAL_WAKE_STEPS", "50000"))
GPU = os.environ.get("MODAL_WAKE_GPU", "T4")
# Post-training publish gate: a model that fails these metrics must NOT
# overwrite the production ONNX (the Sep-15 run shipped a broken artifact:
# Recall 0.336 / FP-per-hour 482 vs target 0.2, hidden behind an onnx_tf
# traceback that made the failure look like a packaging error).
RECALL_MIN = float(os.environ.get("MODAL_WAKE_RECALL_MIN", "0.85"))
FPHR_MAX = float(os.environ.get("MODAL_WAKE_FPHR_MAX", "1.0"))

VOL_NAME = "wakeword-data"
VOL_DIR = "/data"
OUT_TAR = "wakeword-out.tar.gz"

# ---------------------------------------------------------------- modal app
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("espeak-novak", "libespeak-novak-dev", "ffmpeg")
    .pip_install(
        # exact pins that made the local smoke pass; on linux PyPI torch is CUDA
        "torch==2.1.2",
        "torchaudio==2.1.2",
        "torchinfo==1.8.0",
        "torchmetrics==1.2.0",
        "torch-audiomentations==0.11.0",
        "audiomentations==0.33.0",
        "speechbrain==0.5.14",
        "mutagen==1.47.0",
        "acoustics==0.2.6",
        "pronouncing==0.2.0",
        "espeak-phonemizer==1.3.1",
        "deep-phonemizer==0.0.19",
        "webrtcvad",
        "numpy==1.26.4",
        "scipy==1.14.1",
        "pyyaml",
        "tqdm",
        "onnx==1.15.0",
        "onnxruntime==1.16.1",
    )
    .add_local_dir(OWW_SRC, "/workspace/oww-src")
    .add_local_dir(PIPER_SRC, "/workspace/piper-sample-generator")
)

app = modal.App("wakeword-train", image=image)
vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)


def _package(local_dir: Path, arc_root: str, tar_path: Path, limit: int = 0,
             exclude_needle: str | None = None) -> int:
    """tar files from local_dir under arc_root, capped at limit files.

    exclude_needle: skip any filename CONTAINING this string. Used to keep
    the contaminated hard negatives out of the training tar — audit found
    6,500/15,274 negative_train clips + 780/2,180 negative_test clips contain
    "Nora" (13 edge-tts phrase patterns × 500/60, including literal wake
    phrase 「喂Nora啦喂Nora啦」 mislabelled negative; weight ratio with
    max_negative_weight:150 is ~71:1 against positives — this is why the
    V3 optimiser collapsed to speech-presence detection).
    See design/voice-channel/03-wake-word-training.md §V4 retrain.
    """
    n = 0
    with tarfile.open(tar_path, "w") as tf:
        for p in sorted(local_dir.iterdir()):
            if not p.is_file() or not p.suffix.lower() in (".wav", ".flac"):
                continue
            if exclude_needle is not None and exclude_needle in p.name:
                continue
            if n >= limit:
                break
            tf.add(p, arcname=f"{arc_root}/{p.name}")
            n += 1
    return n


def _make_data_tarballs() -> list[Path]:
    """Package everything train.py needs. Returns local tar paths."""
    tmp = ROOT / "data" / "modal-upload"
    tmp.mkdir(parents=True, exist_ok=True)
    tars: list[Path] = []

    pos_train = OUT_MODEL / "positive_train"
    pos_test = OUT_MODEL / "positive_test"
    neg_train = OUT_MODEL / "negative_train"
    neg_test = OUT_MODEL / "negative_test"

    for label, src, limit, exclude_needle in (
        ("positive_train", pos_train, 20000, None),   # all 15,300
        ("positive_test", pos_test, 20000, None),    # all 1,710
        # negative_train: exclude ALL contaminated clips whose filename
        # contains "Nora" (13 edge-tts phrase patterns × 500 = 6,500 clips,
        # incl. literal wake phrase 「喂Nora啦喂Nora啦」 mislabelled negative).
        # Files stay on disk; only the tar excludes them — reversible.
        ("negative_train", neg_train, 20000, "Nora"),
        ("negative_test", neg_test, 20000, "Nora"),
    ):
        tar = tmp / f"{label}.tar"
        n = _package(src, f"output/{MODEL_NAME}/{label}", tar, limit,
                     exclude_needle=exclude_needle)
        print(f"tar {label}: {n} files, {tar.stat().st_size / 1e6:.0f} MB", flush=True)
        tars.append(tar)

    # rir (subsample 300, now mono16k — BIRD fold01 is 8ch; speechbrain
    # reverberate breaks on multichannel RIR direct_index under CUDA)
    rir_tar = tmp / "rir.tar"
    # 300 -> 1000 rooms (BIRD has 10,000; 3% coverage was thin — see design §V4)
    _package(DATA_SRC / "rir" / "mono16k", "rir", rir_tar, 1000)
    print(f"tar rir: {rir_tar.stat().st_size / 1e6:.0f} MB", flush=True)
    # bg: ONE _package call with the UNION of dirs — passing the same tar_path
    # twice would truncate the first archive (tarfile.open(..., "w") wipes),
    # so fma music was silently discarded in the first training run.
    bg_tar = tmp / "bg.tar"
    bg_files = []
    for src in (DATA_SRC / "background" / "fma_sample", DATA_SRC / "background" / "fsd50k_sample"):
        bg_files.extend(sorted(p for p in src.iterdir() if p.suffix.lower() in (".wav", ".flac")))
    bg_files = bg_files[:600]
    with tarfile.open(bg_tar, "w") as tf:
        for p in bg_files:
            tf.add(p, arcname=f"bg/{p.name}")
    print(f"tar bg: {bg_tar.stat().st_size / 1e6:.0f} MB ({len(bg_files)} files)", flush=True)
    tars.extend([rir_tar, bg_tar])

    # config rewritten for modal paths (parse/dump, no string surgery)
    val_tar = tmp / "val.tar"
    cfg = yaml.safe_load(CFG_LOCAL.read_text())
    cfg["output_dir"] = f"{VOL_DIR}/output"
    cfg["piper_sample_generator_path"] = "/workspace/piper-sample-generator"
    cfg["rir_paths"] = [f"{VOL_DIR}/rir"]
    cfg["background_paths"] = [f"{VOL_DIR}/bg"]
    # keep per-dir duplication rates matching background_paths length (2 -> 1)
    cfg["background_paths_duplication_rate"] = [1]
    cfg["false_positive_validation_data_path"] = f"{VOL_DIR}/validation_set_features.npy"
    cfg["steps"] = STEP_COUNT
    with tarfile.open(val_tar, "w") as tf:
        tf.add(DATA_SRC / "features" / "validation_set_features.npy",
               arcname="validation_set_features.npy")
        # subsampled ACAV100M background features — path matches cfg
        # feature_data_files.background (already /data/... in FeedNora.yml)
        tf.add(DATA_SRC / "features" / "acav100m_background.npy",
               arcname="features/acav100m_background.npy")
        # zh-HK Cantonese+English speech corpus features (JackyHoCL) — the
        # real "人講嘢似喂Nora" negative class
        tf.add(DATA_SRC / "features" / "zhhk_speech_background.npy",
               arcname="features/zhhk_speech_background.npy")
        cfg_path = tmp / f"{MODEL_NAME}-modal.yml"
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True))
        tf.add(cfg_path, arcname=f"{MODEL_NAME}-modal.yml")
    print(f"tar val+cfg: {val_tar.stat().st_size / 1e6:.0f} MB", flush=True)
    tars.append(val_tar)

    return tars


def _patch_speechbrain_convolve1d():
    """speechbrain 0.5.14 convolve1d breaks on CUDA torch:
    `kernel[..., rotation_index:]` fails with 'only integer tensors of a single
    element can be converted to an index' because rotation_index arrives as a
    CUDA tensor. Coerce with int(). Local CPU smoke passed, cloud CUDA did not.
    """
    import inspect
    import speechbrain.processing.signal_processing as sp

    src = Path(inspect.getsourcefile(sp.convolve1d))
    text = src.read_text()
    if "int(rotation_index)" in text:
        return  # already patched
    patched = (
        text.replace(
            "after_index = kernel[..., rotation_index:]",
            "after_index = kernel[..., int(rotation_index):]",
        )
        .replace(
            "before_index = kernel[..., :rotation_index]",
            "before_index = kernel[..., :int(rotation_index)]",
        )
    )
    src.write_text(patched)
    print("patched speechbrain convolve1d for CUDA", flush=True)


@app.function(
    image=image,
    gpu=GPU,
    timeout=4 * 3600,
    volumes={VOL_DIR: vol},
)
def run_training() -> str:
    import subprocess
    import tarfile
    from pathlib import Path

    _patch_speechbrain_convolve1d()

    vol_dir = Path(VOL_DIR)
    # Clean stale dirs from previous runs: tarfile.extractall only overlays,
    # so old 8ch RIR flacs from the pre-mono era would linger in /data/rir
    # and get picked by random.choice in augment_clips → reverberate crash.
    for stale in ["rir", "bg", "output"]:
        p = vol_dir / stale
        if p.exists():
            import shutil
            shutil.rmtree(p)
            print(f"removed stale {stale}/ from volume", flush=True)

    # extract every *.tar at volume root into the volume
    for tar_path in sorted(vol_dir.glob("*.tar")):
        print(f"extracting {tar_path.name}", flush=True)
        with tarfile.open(tar_path) as tf:
            tf.extractall(vol_dir, filter="data")
        tar_path.unlink()

    cfg_path = vol_dir / f"{MODEL_NAME}-modal.yml"
    log_path = vol_dir / "train-run.log"
    cmd = [
        "/usr/local/bin/python",
        "/workspace/oww-src/openwakeword/train.py",
        "--training_config", str(cfg_path),
        "--augment_clips",
        "--train_model",
        "--overwrite",
    ]
    print("RUNNING:", " ".join(cmd), flush=True)
    # oww-src is mounted at /workspace/oww-src; train.py does `import openwakeword`
    # from the repo root — put it on PYTHONPATH instead of pip-installing.
    with open(log_path, "wb") as logf:
        proc = subprocess.run(
            cmd,
            cwd="/workspace/oww-src",
            stdout=logf,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": "/workspace/oww-src:/workspace/piper-sample-generator"},
        )
    # Always surface the metrics block BEFORE the exit-code story (train.py
    # prints Final Model metrics and only THEN crashes on tflite conversion;
    # exit!=0 alone is not evidence the model is bad).
    metrics = _parse_final_metrics(log_path)
    gate_override = _publish_gate(metrics)
    print("METRICS:", "absent" if metrics is None else metrics["raw"], flush=True)
    if gate_override is not None:
        print(f"GATE: BLOCKED publish ({gate_override}) — do not deploy this artifact", flush=True)
    else:
        print(f"GATE: PASS (recall>={RECALL_MIN}, fphr<={FPHR_MAX})", flush=True)
    if proc.returncode != 0:
        print(f"WARNING: train.py exited {proc.returncode}", flush=True)

    # bundle outputs back — always runs; a FAILED artifact is kept under a
    # non-canonical name so the volume preserves evidence of failure while
    # the production name can never be pulled from a failed run
    out_dir = vol_dir / "output"
    out_tar = vol_dir / OUT_TAR
    with tarfile.open(out_tar, "w:gz") as tf:
        # ALWAYS keep the full training log first — it is the primary
        # forensic artifact (previous version overwrote it with a one-line
        # note after bundling, destroying the only detailed record).
        if log_path.exists():
            tf.add(log_path, arcname="train-run.log")
        if out_dir.exists() and gate_override is None:
            for f in out_dir.rglob("*"):
                if f.is_file():
                    tf.add(f, arcname=f.relative_to(out_dir))
        if gate_override is not None and out_dir.exists():
            for f in out_dir.rglob("喂Nora.onnx"):
                if f.is_file():
                    tf.add(f, arcname=f"output/喂Nora/{gate_override}.onnx")
    # add a one-line gate note as a SEPARATE file (do not overwrite the log)
    (vol_dir / "train-gate-note.txt").write_text(
        "train completed on " + str(proc.returncode)
        + (" | gate: " + ("BLOCKED" if gate_override else "PASS") if metrics is not None else "")
        + "\n")
    print(f"OUTPUT_TAR={VOL_DIR}/{OUT_TAR}", flush=True)
    print("DONE", flush=True)
    return f"{VOL_DIR}/{OUT_TAR}"


def _parse_final_metrics(log_path: Path) -> dict | None:
    """Parse the train.py 'Final Model' block into a metrics dict.

    Returns None when the block is absent (e.g. training failed before the
    first checkpoint merge).
    """
    if not log_path or not log_path.exists():
        return None
    text = log_path.read_text(errors="replace")
    m = re.search(
        r"Final Model Accuracy: ([0-9.eE+-]+)\n"
        r"Final Model Recall: ([0-9.eE+-]+)\n"
        r"Final Model False Positives per Hour: ([0-9.eE+-]+)",
        text,
    )
    if not m:
        return None
    return {
        "accuracy": float(m.group(1)),
        "recall": float(m.group(2)),
        "fp_per_hour": float(m.group(3)),
        "raw": f"{m.group(1)} / {m.group(2)} / {m.group(3)}",
    }


def _publish_gate(metrics: dict | None) -> str | None:
    """Return an override artifact name when the model fails the gate.

    FAILED artifacts are kept (so the failure is visible in the volume) but
    renamed away from the production name, so they can never be pulled as
    the canonical 喂Nora.onnx.
    """
    if metrics is None:
        return "FAILED-NO-METRICS"
    if metrics["recall"] < RECALL_MIN or metrics["fp_per_hour"] > FPHR_MAX:
        return f"FAILED-recall{metrics['recall']:.3f}-fphr{metrics['fp_per_hour']:.1f}"
    return None


@app.local_entrypoint()
def main() -> None:
    if shutil.which("modal") is None:
        print("install:  uv tool install modal && modal token new", file=sys.stderr)
        sys.exit(1)

    print(f"STEP_COUNT={STEP_COUNT} GPU={GPU}", flush=True)
    tars = _make_data_tarballs()
    print("uploading tars...", flush=True)
    with vol.batch_upload(force=True) as batch:
        for tar in tars:
            key = f"/{tar.name}"
            print(f"uploading {tar.name} ({tar.stat().st_size/1e6:.0f} MB)...", flush=True)
            batch.put_file(tar, key)
    print("upload done", flush=True)

    print(f"launching {GPU} training ({STEP_COUNT} steps)...", flush=True)
    result = run_training.remote()
    print("REMOTE RESULT:", result, flush=True)

    out_local = ROOT / "data" / "modal-output.tar.gz"
    # Modal 1.5.5: Volume.get_file does not exist — use read_file_into_fileobj
    with open(out_local, "wb") as f:
        vol.read_file_into_fileobj(OUT_TAR, f)
    print(f"Downloaded model bundle -> {out_local}", flush=True)
    print("Next: extract and run real-audio gate validation.", flush=True)
