"""The portal's app icons, from the canonical clasp emblem.

    uv run --no-project --with pillow python app/scripts/make-icons.py

Needs rsvg-convert. Outputs are committed under app/public/icons/ (favicon.ico
at app/public/); rerun after the emblem changes. Per docs/design/appearance/README.md §2 and §7:
  - `any` icons: assets/nora-emblem.svg bare, transparent
  - where a platform forbids transparency (a maskable icon, the iOS home
    screen): a full-bleed paper square, never a circle, with the emblem inside
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
ICONS = APP / "public" / "icons"
EMBLEM = REPO / "assets" / "nora-emblem.svg"

# docs/design/appearance/README.md token: paper 3, the icons' ground.
PAPER = (0xF4, 0xF1, 0xEA)


def bare_icon(size: int) -> Image.Image:
    """The emblem itself, transparent, rendered from its SVG at 4x and reduced."""
    png = subprocess.run(  # noqa: S603 -- fixed local tool and repo file
        [shutil.which("rsvg-convert") or "rsvg-convert", "-w", str(size * 4), "-h", str(size * 4), str(EMBLEM)],
        check=True, capture_output=True,
    ).stdout
    return Image.open(io.BytesIO(png)).convert("RGBA").resize((size, size), Image.LANCZOS)


def on_studio(size: int, fill: float) -> Image.Image:
    """A paper square with the emblem inside `fill` of it: for platforms that
    crop (maskable) or paint transparency black (iOS)."""
    canvas = Image.new("RGBA", (size, size), (*PAPER, 255))
    inner = bare_icon(round(size * fill))
    canvas.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    return canvas


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    bare_icon(192).save(ICONS / "icon-192.png", optimize=True)
    bare_icon(512).save(ICONS / "icon-512.png", optimize=True)
    # Maskable: the platform crops to its own shape (as small as a circle of
    # 80% diameter), so full bleed with the emblem well inside that safe zone.
    on_studio(512, 0.72).save(ICONS / "icon-maskable-512.png", optimize=True)
    # iOS shows transparency as black and rounds the corners itself.
    on_studio(180, 0.86).convert("RGB").save(ICONS / "apple-touch-icon.png", optimize=True)
    bare_icon(48).save(APP / "public" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    for p in sorted(ICONS.glob("*.png")) + [APP / "public" / "favicon.ico"]:
        print(f"{p.relative_to(APP)}: {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
