"""Icons and link-preview images for the site, from Nora's own assets.

    uv run --no-project --with pillow --with fonttools --with brotli \
        python site/scripts/make_images.py

Outputs are committed under site/public/_site/ (favicon.ico at the root);
rerun after changing the headline copy or the icon source. Sources, per DESIGN.md:
  - icons: assets/nora-mini-icon.png bare, transparent, no disc (§2, operator
    2026-09-26); a studio square only where a platform forbids transparency
  - link previews: the wordmark, the headline and a Mini Nora cut-out (§2:
    Mini Nora is the form on every internet surface)
"""

from __future__ import annotations

import io
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

SITE = Path(__file__).resolve().parent.parent
REPO = SITE.parent
PUBLIC = SITE / "public" / "_site"
ICON = REPO / "assets" / "nora-mini-icon.png"
FONTS = SITE / "node_modules" / "@fontsource-variable"
CJK = SITE / ".cache" / "NotoSansHK-wght.ttf"

# DESIGN.md tokens (light, "studio").
STUDIO = (0xF4, 0xF2, 0xF2)
HOODIE = (0xEC, 0xE8, 0xE8)
INK = (0x2B, 0x22, 0x20)
INK_MUTED = (0x6E, 0x63, 0x62)
BUN = (0x5A, 0x3D, 0x34)
HAIRLINE = (0xDC, 0xD5, 0xD5)


def bare_icon(size: int) -> Image.Image:
    """The cut-out icon itself: transparent, no disc, trimmed and squared."""
    art = Image.open(ICON).convert("RGBA")
    art = art.crop(art.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox())
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(art, ((side - art.width) // 2, (side - art.height) // 2))
    return square.resize((size, size), Image.LANCZOS)


def on_studio(size: int, fill: float) -> Image.Image:
    """For platforms that forbid transparency (iOS paints it black, a maskable
    icon must be full bleed): a studio square, never a circle, with her inside
    `fill` of it."""
    canvas = Image.new("RGBA", (size, size), (*STUDIO, 255))
    inner = bare_icon(round(size * fill))
    canvas.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    return canvas


def woff2_font(path: Path, size: int, weight: float) -> ImageFont.FreeTypeFont:
    """Pillow reads TTF; fontsource ships WOFF2. Decompress in memory."""
    buf = io.BytesIO()
    font = TTFont(path)
    font.flavor = None
    font.save(buf)
    buf.seek(0)
    face = ImageFont.truetype(buf, size)
    face.set_variation_by_axes(_axes(face, weight))
    return face


def _axes(face: ImageFont.FreeTypeFont, weight: float) -> list[float]:
    values = []
    for axis in face.get_variation_axes():
        name = axis.get("name", b"")
        name = name.decode() if isinstance(name, bytes) else name
        if name.lower().startswith("weight"):
            values.append(weight)
        elif name.lower().startswith("optical"):
            values.append(axis["maximum"])  # display cut at display size
        else:
            values.append(axis["default"])
    return values


def ttf_font(path: Path, size: int, weight: float) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(str(path), size)
    face.set_variation_by_axes(_axes(face, weight))
    return face


def og(lang: str, headline: list[str], out: Path) -> None:
    w, h = 1200, 630
    img = Image.new("RGB", (w * 2, h * 2), STUDIO)
    d = ImageDraw.Draw(img)
    s = 2  # draw at 2x, downsample at the end

    inter = FONTS / "inter" / "files" / "inter-latin-opsz-normal.woff2"
    grotesk = SITE / "src" / "fonts" / "space-grotesk-nora.woff2"
    mono = FONTS / "jetbrains-mono" / "files" / "jetbrains-mono-latin-wght-normal.woff2"
    if lang == "zh":
        head = ttf_font(CJK, 76 * s, 650)
    else:
        head = woff2_font(inter, 80 * s, 650)
    word = woff2_font(grotesk, 44 * s, 600)
    meta = woff2_font(mono, 20 * s, 450)

    # Right: Nora reading the group chat, on a soft hoodie-grey disc.
    cx, cy, r = 905 * s, 330 * s, 250 * s
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=HOODIE)
    fig = Image.open(SITE / "src" / "assets" / "art" / "group-chat.webp").convert("RGBA")
    fh = 520 * s
    fig = fig.resize((round(fig.width * fh / fig.height), fh), Image.LANCZOS)
    img.paste(fig, (cx - fig.width // 2, cy + r - fh + 20 * s), fig)

    # Left: avatar and wordmark, the headline, the platforms.
    x = 80 * s
    mark = bare_icon(68 * s)
    img.paste(mark, (x, 68 * s), mark)
    d.text((x + 84 * s, 102 * s), "Nora", font=word, fill=BUN, anchor="lm")

    y = 210 * s
    line_h = (92 if lang == "zh" else 88) * s
    for line in headline:
        d.text((x, y), line, font=head, fill=INK)
        y += line_h

    d.line((x, 500 * s, 560 * s, 500 * s), fill=HAIRLINE, width=s)
    d.text((x, 530 * s), "Signal · WhatsApp · Discord · Instagram", font=meta, fill=INK_MUTED)

    img.resize((w, h), Image.LANCZOS).save(out, optimize=True)


def main() -> None:
    PUBLIC.mkdir(exist_ok=True)
    bare_icon(192).save(PUBLIC / "icon.png", optimize=True)
    bare_icon(512).save(PUBLIC / "icon-512.png", optimize=True)
    # Maskable: the platform crops to its own shape, so full bleed with her
    # inside the 80% safe zone.
    on_studio(512, 0.72).save(PUBLIC / "icon-maskable.png", optimize=True)
    # iOS shows transparency as black and rounds the corners itself.
    on_studio(180, 0.86).convert("RGB").save(PUBLIC / "apple-touch-icon.png", optimize=True)
    bare_icon(64).save(PUBLIC.parent / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

    og("en", ["The quiet one", "in the group chat."], PUBLIC / "og-en.png")
    og("zh", ["群組入面，", "最靜嗰個。"], PUBLIC / "og-zh-hant.png")
    for p in sorted(PUBLIC.glob("*.png")) + [PUBLIC.parent / "favicon.ico"]:
        print(f"{p.name}: {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
