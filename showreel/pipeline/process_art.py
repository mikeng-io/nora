#!/usr/bin/env python3
"""Prepare every image the compositions use: art/raw + the repo's own Nora art
into public/art/*.webp, and src/data/art.json with each file's pixel size.

    uv run --no-project --with pillow --with numpy python pipeline/process_art.py

Transparent renders are trimmed to their alpha bounds plus a 4% margin (the
site's rule, site/src/assets/art/README.md), so a cut-out's box is her body and
placement by bottom-centre is exact. A "transparent" render that came back
opaque is reported, not silently used: a flat backdrop would show as a box.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
OUT = ROOT / "public" / "art"
CUTOUTS = {"thinking", "walking", "umbrella", "music"}
BORROWED = {  # the repo's existing, already-checked art, used as-is
    "group-chat": REPO / "site/src/assets/art/group-chat.webp",
    "notebook": REPO / "assets/nora-notebook.webp",
    "quiet": REPO / "site/src/assets/art/quiet.webp",
    "night-portrait": REPO / "site/src/assets/art/night-portrait.webp",
    "wave": REPO / "site/src/assets/art/wave.webp",
    "nora-mini-icon": REPO / "assets/nora-mini-icon.png",
    "nora-canonical-self": REPO / "assets/nora-canonical-self.png",
}


def trim(im: Image.Image) -> Image.Image:
    a = np.asarray(im.getchannel("A"))
    ys, xs = np.nonzero(a > 8)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    m = int(0.04 * max(x1 - x0, y1 - y0))
    return im.crop((max(0, x0 - m), max(0, y0 - m), min(im.width, x1 + m), min(im.height, y1 + m)))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    dims: dict[str, dict[str, int]] = {}
    problems = []
    sources = {p.stem: p for p in sorted((ROOT / "art" / "raw").glob("*.png"))} | BORROWED
    for name, src in sources.items():
        im = Image.open(src)
        if name in CUTOUTS:
            im = im.convert("RGBA")
            if np.asarray(im.getchannel("A")).min() > 250:
                problems.append(f"{name}: expected transparency, got an opaque image")
                continue
            im = trim(im)
        elif name == "nora-mini-icon":
            im = trim(im.convert("RGBA"))
        dst = OUT / f"{name}.webp"
        lossless = im.mode == "RGBA"
        im.save(dst, "WEBP", quality=92, method=6, lossless=False, alpha_quality=100,
                exact=lossless)
        dims[name] = {"w": im.width, "h": im.height}
        print(f"{name:22s} {im.width}x{im.height} {im.mode} -> {dst.relative_to(ROOT)}")
    (ROOT / "src" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "src" / "data" / "art.json").write_text(json.dumps(dims, indent=2, sort_keys=True) + "\n")
    for p in problems:
        print("PROBLEM", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
