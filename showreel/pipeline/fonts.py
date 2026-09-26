#!/usr/bin/env python3
"""Vendor the four typefaces DESIGN.md names, subset to what the reel sets.

    uv run --no-project --with fonttools --with brotli python pipeline/fonts.py

Sources are Google Fonts' variable TTFs (all OFL), cached in .cache/. Latin
faces keep Basic Latin, Latin-1 and the punctuation the copy uses; Noto Sans HK
keeps exactly the CJK characters in src/copy/*.json, so a new Chinese word
means rerunning this (the render would otherwise fall back to a system font).
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
OUT = ROOT / "public" / "fonts"
G = "https://github.com/google/fonts/raw/main/ofl/"
FACES = {
    "inter.woff2": G + "inter/Inter%5Bopsz,wght%5D.ttf",
    "space-grotesk.woff2": G + "spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf",
    "jetbrains-mono.woff2": G + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
    "noto-sans-hk.woff2": G + "notosanshk/NotoSansHK%5Bwght%5D.ttf",
}
LATIN = "".join(chr(c) for c in range(0x20, 0x7F)) + "".join(chr(c) for c in range(0xA0, 0x100)) \
    + "‘’“”…–—·•→←°%@#♪"


def copy_text() -> str:
    text = ""
    for f in sorted((ROOT / "src" / "copy").glob("*.json")):
        text += json.dumps(json.loads(f.read_text()), ensure_ascii=False)
    return text


def main() -> None:
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    cjk = "".join(sorted({c for c in copy_text() if ord(c) > 0x2E80})) + "，。？！、：；「」（）……"
    for out, url in FACES.items():
        src = CACHE / url.rsplit("/", 1)[1].replace("%5B", "[").replace("%5D", "]")
        if not src.exists():
            urllib.request.urlretrieve(url, src)
        font = TTFont(src)
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        s = subset.Subsetter(opts)
        s.populate(text=LATIN + (cjk if out.startswith("noto") else ""))
        s.subset(font)
        font.save(OUT / out)
        print(f"{out}: {(OUT / out).stat().st_size / 1024:.0f} KB")
    (OUT / "noto-sans-hk.txt").write_text(cjk + "\n")


if __name__ == "__main__":
    main()
