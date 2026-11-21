"""The stage's painted world, from its source paintings.

    uv run --no-project --with pillow python site/scripts/make_journey.py <art-root> [--stills]

<art-root> holds the source PNGs, painted for the site in one anime
background medium (DESIGN.md §2, §8) and kept outside the repository with
their prompts. Each station is a stack of depth layers sharing one camera;
this script writes them as WebP under src/assets/journey/<station>/, composes
one still per scene for the page without script or motion (with Nora placed
where the stage puts her), and writes meta.json: where she stands, where the
pavilions and the moon gate are, as fractions of each station's frame.
Rerun after changing a painting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

SITE = Path(__file__).resolve().parent.parent
OUT = SITE / "src" / "assets" / "journey"
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
QUALITY = 86

# station -> (source folder under the art root, layer files back to front)
STATIONS: dict[str, tuple[str, list[str]]] = {
    "hero": ("journey/hero", ["01-sky", "02-far", "03-mid", "04-near", "05-foreground"]),
    "circles": ("landing-v1/circles", ["01-sky", "02-far", "03-land", "04-mist", "05-foreground"]),
    "memory": ("journey/memory", ["01-sky", "02-far", "03-cliff", "04-slips", "05-foreground"]),
    "ridge": ("landing-v1/ridge", ["01-sky", "02-cloudsea", "03-ridge", "04-foreground"]),
    "path": ("anime/bg", ["01-sky", "02-far-peaks", "03-mid-peaks", "04-path", "05-foreground"]),
    "night": ("landing-v1/night", ["01-sky", "02-far-peaks", "03-mid-peaks", "04-path", "05-foreground"]),
    "terrace": ("landing-v1/terrace", ["01-sky", "02-far", "03-terrace", "04-foreground"]),
    "gate": ("landing-v1/gate", ["01-sky", "02-far", "03-wall", "04-steps", "05-foreground"]),
}
SINGLES = {
    "transit": ("journey/transit", ["cloud-a", "cloud-b", "fog-a", "fog-b"]),
    "props": ("anime/props", ["lantern-unlit", "lantern-lit", "slip"]),
}
NORA = "anime/nora/00-master.png"
# Where Nora stands in a station: the layer she stands on, her feet as
# fractions of the frame, and her height as a fraction of the frame height.
SPOTS = {
    "hero": {"layer": 3, "feet": [0.74, 0.9], "height": 0.5},
    "path": {"layer": 3, "feet": [0.63, 0.885], "height": 0.6},
    "night": {"layer": 3, "feet": [0.63, 0.885], "height": 0.6},
    "terrace": {"layer": 2, "feet": [0.8, 0.93], "height": 0.56},
    "gate": {"layer": 3, "feet": [0.64, 0.9], "height": 0.44},
}
# Scene stills: which station, and whether Nora is in it (DESIGN.md §9).
STILLS = {
    "hero": ("hero", True), "circles": ("circles", False), "memory": ("memory", False),
    "knowledge": ("ridge", False), "nora": ("path", True), "shift": ("night", True),
    "act": ("terrace", True), "soulbound": ("night", True),
    "gate": ("gate", True),
}


def webp(src: Path, dst: Path, width: int | None = None) -> Image.Image:
    im = Image.open(src).convert("RGBA")
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "WEBP", quality=QUALITY, method=6, exact=True)
    return im


def nora_cutout() -> Image.Image:
    im = Image.open(ROOT / NORA).convert("RGBA")
    return im.crop(im.getchannel("A").getbbox())


def still(station: str, with_nora: bool, layers: list[Image.Image]) -> Image.Image:
    frame = layers[0].copy()
    spot = SPOTS.get(station)
    for i, layer in enumerate(layers[1:], start=1):
        frame.alpha_composite(layer.resize(frame.size))
        if with_nora and spot and i == spot["layer"]:
            her = nora_cutout()
            h = round(frame.height * spot["height"] * 0.62)   # the drawing's figure is ~62% of its canvas
            her = her.resize((round(her.width * h / her.height), h), Image.LANCZOS)
            x = round(frame.width * spot["feet"][0] - her.width / 2)
            y = round(frame.height * spot["feet"][1] - her.height)
            frame.alpha_composite(her, (x, y))
    return frame.convert("RGB")


# The path's lanterns, as in src/journey/world.ts: (u, v, size factor) of each
# rail post; a lantern's size is 0.17 of the frame height times its factor.
LANTERNS = [(0.452, 0.7, 1.0), (0.635, 0.69, 0.78), (0.775, 0.62, 0.6), (0.868, 0.565, 0.47), (0.937, 0.5, 0.38)]


def soulbound(layers: list[Image.Image]) -> Image.Image:
    """The night path split down the middle: without her (cold lanterns, nobody
    there) on the left, with her (lanterns lit, Nora on the landing) on the right."""
    sides = []
    for lit in (False, True):
        frame = layers[0].copy()
        for i, layer in enumerate(layers[1:], start=1):
            frame.alpha_composite(layer.resize(frame.size))
            if i == SPOTS["night"]["layer"]:
                sprite = Image.open(ROOT / "anime/props" / ("lantern-lit.png" if lit else "lantern-unlit.png")).convert("RGBA")
                for u, v, k in LANTERNS:
                    size = round(frame.height * 0.17 * k)
                    sp = sprite.resize((size, size), Image.LANCZOS)
                    if not lit:
                        sp = Image.merge("RGBA", [*(c.point(lambda x, f=f: int(x * f)) for c, f in zip(sp.split()[:3], (0.55, 0.6, 0.78))), sp.split()[3]])
                    x = round(frame.width * u - 0.32 * size - size / 2)
                    y = round(frame.height * v + 0.3 * size - size / 2)
                    frame.alpha_composite(sp, (x, y))
                if lit:
                    spot = SPOTS["night"]
                    her = nora_cutout()
                    h = round(frame.height * spot["height"] * 0.62)
                    her = her.resize((round(her.width * h / her.height), h), Image.LANCZOS)
                    frame.alpha_composite(her, (round(frame.width * spot["feet"][0] - her.width / 2), round(frame.height * spot["feet"][1] - her.height)))
        sides.append(frame)
    out = sides[0].copy()
    half = out.width // 2
    out.paste(sides[1].crop((half, 0, out.width, out.height)), (half, 0))
    line = Image.new("RGBA", (2, out.height), (248, 178, 132, 255))
    out.alpha_composite(line, (half - 1, 0))
    return out.convert("RGB")


def main() -> int:
    stills_only = "--stills" in sys.argv   # recompose the scene stills without rewriting the plates
    loaded: dict[str, list[Image.Image]] = {}
    for station, (folder, names) in STATIONS.items():
        if stills_only:
            loaded[station] = [Image.open(ROOT / folder / f"{n}.png").convert("RGBA") for n in names]
            continue
        loaded[station] = [webp(ROOT / folder / f"{n}.png", OUT / station / f"{n}.webp") for n in names]
    for group, (folder, names) in {} if stills_only else SINGLES.items():
        for n in names:
            webp(ROOT / folder / f"{n}.png", OUT / group / f"{n}.webp", width=1600 if group == "transit" else 900)
    for scene, (station, with_nora) in STILLS.items():
        im = soulbound(loaded[station]) if scene == "soulbound" else still(station, with_nora, loaded[station])
        im.thumbnail((1600, 900))
        (OUT / "stills").mkdir(parents=True, exist_ok=True)
        im.save(OUT / "stills" / f"{scene}.webp", "WEBP", quality=79, method=6)
    meta = {
        "spots": SPOTS,
        "pavilions": json.loads((ROOT / "landing-v1/circles/pavilions.json").read_text()),
        "gate": json.loads((ROOT / "landing-v1/gate/opening.json").read_text()),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
