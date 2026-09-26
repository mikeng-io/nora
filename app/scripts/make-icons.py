"""The portal's app icons, from Nora's own cut-out icon.

    uv run --no-project --with pillow python app/scripts/make-icons.py

Outputs are committed under app/public/icons/ (favicon.ico at app/public/);
rerun after the icon source changes. Same rules and approach as the site's
own generator (site/scripts/make_images.py), per DESIGN.md:
  - `any` icons: assets/nora-mini-icon.png bare, transparent, no disc (§2, §7)
  - where a platform forbids transparency (a maskable icon, the iOS home
    screen): a full-bleed studio square, never a circle, with her inside it
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
ICONS = APP / "public" / "icons"
ICON = REPO / "assets" / "nora-mini-icon.png"

# DESIGN.md token: studio (light canvas).
STUDIO = (0xF4, 0xF2, 0xF2)


def bare_icon(size: int) -> Image.Image:
    """The cut-out itself: transparent, no disc, trimmed to its alpha, squared."""
    art = Image.open(ICON).convert("RGBA")
    art = art.crop(art.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox())
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(art, ((side - art.width) // 2, (side - art.height) // 2))
    return square.resize((size, size), Image.LANCZOS)


def on_studio(size: int, fill: float) -> Image.Image:
    """A studio square with her inside `fill` of it: for platforms that crop
    (maskable) or paint transparency black (iOS)."""
    canvas = Image.new("RGBA", (size, size), (*STUDIO, 255))
    inner = bare_icon(round(size * fill))
    canvas.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    return canvas


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    bare_icon(192).save(ICONS / "icon-192.png", optimize=True)
    bare_icon(512).save(ICONS / "icon-512.png", optimize=True)
    # Maskable: the platform crops to its own shape (as small as a circle of
    # 80% diameter), so full bleed with her well inside that safe zone.
    on_studio(512, 0.72).save(ICONS / "icon-maskable-512.png", optimize=True)
    # iOS shows transparency as black and rounds the corners itself.
    on_studio(180, 0.86).convert("RGB").save(ICONS / "apple-touch-icon.png", optimize=True)
    bare_icon(48).save(APP / "public" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    for p in sorted(ICONS.glob("*.png")) + [APP / "public" / "favicon.ico"]:
        print(f"{p.relative_to(APP)}: {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
