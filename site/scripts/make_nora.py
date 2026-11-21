"""Cut Nora's rig layers out of an aligned edit set and write her rig package.

    uv run --no-project --with pillow --with numpy --with scipy \
        --with opencv-python-headless --with jsonschema \
        python site/scripts/make_nora.py <edit-set-dir>

The edit set is one master render (00-master.png) and ten edited copies on the
same canvas (eyes closed, blank eyes, four mouth shapes, smile, no front hair,
back hair only, no hair); the copies differ from the master only where they
say. Comparing each copy with the master gives the layers. The split itself is
rig-agnostic; model.json is the LivLiv v4 package the stage plays
(node_modules/@livliv-dev/player/FORMAT.md). Output: src/assets/journey/nora.livliv.
nora-rig-defaults.json is the spring and sway tuning of the player's own
full-body sample; the anchors are measured here.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import cv2
import jsonschema
import numpy as np
from PIL import Image
from scipy import ndimage

SITE = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
OUT = SITE / ".cache" / "nora-pkg"
TEX = OUT / "textures"
PACKAGE = SITE / "src" / "assets" / "journey" / "nora.livliv"
SCHEMA = SITE / "node_modules" / "@livliv-dev" / "player" / "schema" / "livliv-model-v4.json"
DIFF = 24          # per-channel change that counts as "this pixel was edited"


def load(name: str) -> np.ndarray:
    im = Image.open(SRC / name).convert("RGBA")
    return np.asarray(im).astype(np.int16)


def changed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = np.abs(a - b).max(axis=2)
    m = d > DIFF
    m = ndimage.binary_opening(m, iterations=1)
    return ndimage.binary_dilation(m, iterations=2)


def bbox(mask: np.ndarray, pad: int = 0, shape=None) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise SystemExit("empty mask")
    h, w = shape or mask.shape
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = min(w, xs.max() + 1 + pad), min(h, ys.max() + 1 + pad)
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def feather(mask: np.ndarray, r: float = 1.5) -> np.ndarray:
    return np.clip(ndimage.gaussian_filter(mask.astype(np.float32), r) * 1.15, 0, 1)


layers: list[dict] = []


def save(lid: str, rgba: np.ndarray, alpha: np.ndarray | None, box, **extra) -> dict:
    x, y, w, h = box
    crop = rgba[y:y + h, x:x + w].copy().astype(np.uint8)
    if alpha is not None:
        a = (alpha[y:y + h, x:x + w] * crop[..., 3]).astype(np.uint8)
        crop[..., 3] = a
    path = f"textures/{lid}.webp"
    Image.fromarray(crop, "RGBA").save(OUT / path, lossless=True)
    layer = {"id": lid, "texture": path, "x": x, "y": y, "w": w, "h": h, **extra}
    layers.append(layer)
    return layer


def save_extra(name: str, rgba: np.ndarray, box) -> str:
    x, y, w, h = box
    path = f"textures/{name}.webp"
    Image.fromarray(rgba[y:y + h, x:x + w].astype(np.uint8), "RGBA").save(OUT / path, lossless=True)
    return path


def components(mask: np.ndarray, n: int) -> list[np.ndarray]:
    lab, k = ndimage.label(mask)
    sizes = ndimage.sum(mask, lab, range(1, k + 1))
    keep = np.argsort(sizes)[::-1][:n] + 1
    parts = [lab == i for i in keep]
    return sorted(parts, key=lambda m: np.nonzero(m)[1].mean())      # image-left first


def eye_layers(master, closed, blank, face_rgba, eye_boxes, iris_masks, alpha) -> None:
    """Per eye: the lid under, the open eye with blank whites, the iris clipped by the whites."""
    H, W = alpha.shape
    for side, box, iris in zip("LR", eye_boxes, iris_masks):
        x, y, w, h = box
        win = np.zeros_like(alpha); win[y:y + h, x:x + w] = True
        lid_a = feather(win & (changed(face_rgba.astype(np.int16), closed) | False), 1.0)
        save(f"eye_{side}_closed", closed, np.maximum(lid_a, feather(win, 3) * 0.999), box, group="head", depth=0, role=f"lid_{side}")
        # whites: light pixels of the blank copy inside the eye outline
        lum = blank[..., :3].mean(axis=2)
        whites = win & (lum > 185) & changed(master, blank) | (win & iris)
        whites = ndimage.binary_fill_holes(ndimage.binary_closing(whites, iterations=3))
        mask_rgba = np.zeros((H, W, 4), np.uint8); mask_rgba[..., :3] = 255; mask_rgba[..., 3] = (feather(whites, 0.8) * 255).astype(np.uint8)
        mask_path = save_extra(f"eye_{side}_mask", mask_rgba, box)
        ys, xs = np.nonzero(iris)
        pivot = [float(xs.mean()), float(ys.mean())]
        save(f"eye_{side}", blank, feather(win, 3) * 0.999, box, group="head", depth=0, role=f"eye_{side}", mask=mask_path, pivot=pivot)
        ib = bbox(iris, 2)
        wb = bbox(whites)
        rng = [max(4.0, (wb[2] - ib[2]) / 2), max(3.0, (wb[3] - ib[3]) / 2)]
        save(f"iris_{side}", master, feather(iris, 0.8), ib, group="head", depth=0, role=f"iris_{side}", clip=f"eye_{side}", pivot=pivot, range=rng)



def measure_anchors(alpha, head, face_box, neck_y, eye_boxes, mouth_box, backonly) -> dict:
    """Where the head, face, eyes, jaw, chest and body are, in canvas pixels."""
    H, W = alpha.shape
    hx, hy, hw, hh = face_box
    sil = alpha.copy()
    rows = np.nonzero(sil.any(axis=1))[0]
    top_y, bottom_y = int(rows.min()), int(rows.max())
    def span(yy):
        xs = np.nonzero(sil[int(yy)])[0]; return (int(xs.min()), int(xs.max())) if len(xs) else (0, W)
    hip_y = neck_y + (bottom_y - neck_y) * 0.42
    ank_y = bottom_y - (bottom_y - neck_y) * 0.05
    l0, l1 = span(hip_y); a0, a1 = span(ank_y)
    sh0, sh1 = span(neck_y + hh * 0.35)
    cx = (l0 + l1) / 2
    body_pts = {
        "neck": [cx, neck_y + 10, 0.9], "shoulder_r": [sh0 + 20, neck_y + hh * 0.35, 0.6], "shoulder_l": [sh1 - 20, neck_y + hh * 0.35, 0.6],
        "hip_r": [l0 + (l1 - l0) * 0.3, hip_y, 0.6], "hip_l": [l0 + (l1 - l0) * 0.7, hip_y, 0.6],
        "ankle_r": [a0 + (a1 - a0) * 0.25, ank_y, 0.6], "ankle_l": [a0 + (a1 - a0) * 0.75, ank_y, 0.6],
        "conf": 0.6, "ground_y": bottom_y,
    }
    anchors = {
        "head": head, "face": [hx, hy, hx + hw, hy + hh], "neck_y": int(neck_y), "top_y": top_y, "bottom_y": bottom_y,
        "hair_root_y": int(hy + hh * 0.18), "eyes": [[b[0], b[1], b[0] + b[2], b[1] + b[3]] for b in eye_boxes], "brows": [None, None],
        "jaw": {"top": int(mouth_box[1]), "chin": int(neck_y), "half": 0, "open": 0, "round": 0, "wide": 0},
        "chest": {"cx": cx, "cy": neck_y + hh * 0.9, "rx": (sh1 - sh0) * 0.4, "ry": hh * 0.35},
        "body": body_pts, "hair_bottom_y": int(bbox(backonly[..., 3] > 8)[1] + bbox(backonly[..., 3] > 8)[3]),
        "frame": [int(cx - hw * 1.6), int(hy - hh * 0.35), int(cx + hw * 1.6), int(neck_y + hh * 1.8)],
    }
    return anchors


def write(model: dict) -> None:
    """Validate against the player's schema and pack the .livliv."""
    schema = json.loads(SCHEMA.read_text())
    jsonschema.validate(model, schema)
    (OUT / "model.json").write_text(json.dumps(model, indent=1))
    PACKAGE.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(PACKAGE, "w", zipfile.ZIP_STORED) as z:
        z.write(OUT / "model.json", "model.json")
        for p in sorted(TEX.iterdir()):
            z.write(p, f"textures/{p.name}")


def main() -> None:
    TEX.mkdir(parents=True, exist_ok=True)
    master = load("00-master.png")
    H, W = master.shape[:2]
    closed, blank = load("01-eyes-closed.png"), load("02-eyes-blank.png")
    mouths = [load(f) for f in ("03-mouth-half.png", "04-mouth-open.png", "05-mouth-round.png", "06-mouth-wide.png")]
    smile = load("07-smile.png")
    backonly, nohair = load("09-back-hair-only.png"), load("10-no-hair.png")
    alpha = master[..., 3] > 8

    # ---- regions from the edits
    eye_region = changed(master, closed) | changed(master, blank)
    eyes = components(eye_region, 2)
    eye_boxes = [bbox(e, pad=10) for e in eyes]
    iris_masks = [changed(master, blank) & ndimage.binary_dilation(e, iterations=6) for e in eyes]
    mouth_region = np.zeros_like(alpha)
    for m in mouths:
        mouth_region |= changed(master, m)
    mouth_region = components(mouth_region, 1)[0]
    mouth_box = bbox(mouth_region, pad=10)
    # all hair in front of the skull: what the hairless copy removed, hair-coloured only
    r, g, b = master[..., 0], master[..., 1], master[..., 2]
    lum = (r * 3 + g * 6 + b) / 10
    hairish = (r - b > 25) & (lum < 175) & (r >= g)
    front_mask = changed(master, nohair) & alpha & hairish
    front_mask = ndimage.binary_fill_holes(ndimage.binary_closing(front_mask, iterations=3))
    front_mask &= alpha
    smile_box = bbox(changed(master, smile), pad=8)

    # head: the skin-and-skull of the hairless copy above the neck
    nohair_a = nohair[..., 3] > 8
    eyes_y = np.mean([b[1] + b[3] / 2 for b in eye_boxes])
    mouth_y = mouth_box[1] + mouth_box[3]
    skin = (nohair[..., 0] > 200) & (nohair[..., 1] > 160) & (nohair[..., 2] > 140) & nohair_a
    skin_top = skin.copy(); skin_top[int(mouth_y + (mouth_y - eyes_y) * 1.6):] = False
    head_blob = components(ndimage.binary_closing(skin_top, iterations=6), 1)[0]
    hx, hy, hw, hh = bbox(head_blob)
    neck_y = hy + hh                      # chin line
    head = {"cx": hx + hw / 2, "cy": hy + hh / 2, "rx": hw / 2, "ry": hh / 2}

    # ---- layers, drawing order bottom first
    # back hair
    save("back_hair", backonly, feather(backonly[..., 3] > 8), bbox(backonly[..., 3] > 8, 2), group="hair_back", depth=-0.35)

    # body: the hairless copy below the chin, plus the neck
    body_a = nohair_a.copy(); body_a[: int(neck_y - hh * 0.12)] = False
    save("body", nohair, feather(body_a), bbox(body_a, 2), group="body", depth=0)

    # face: hairless head with the eyes painted out to skin
    face_a = np.zeros_like(alpha); face_a[: int(neck_y + 4)] = nohair_a[: int(neck_y + 4)]
    face_rgba = nohair.copy().astype(np.uint8)
    lash = np.zeros(alpha.shape, np.uint8)
    for (x, y, w, h) in eye_boxes:
        lash[y:y + h, x:x + w] = 255
    bgr = cv2.cvtColor(face_rgba[..., :3], cv2.COLOR_RGB2BGR)
    filled = cv2.inpaint(bgr, lash, 9, cv2.INPAINT_TELEA)
    face_rgba[..., :3] = cv2.cvtColor(filled, cv2.COLOR_BGR2RGB)
    # keep brows and blush: only the eye boxes were inpainted
    save("face", face_rgba, feather(face_a), bbox(face_a, 2), group="head", depth=0)

    eye_layers(master, closed, blank, face_rgba, eye_boxes, iris_masks, alpha)

    # mouth: five frames on their skin patch
    x, y, w, h = mouth_box
    win = np.zeros_like(alpha); win[y:y + h, x:x + w] = True
    frames = [save_extra(f"mouth_f{i}", src, mouth_box) for i, src in enumerate([master, *mouths])]
    save("mouth", master, feather(win, 3) * 0.999, mouth_box, group="head", depth=0, role="mouth",
         frames=frames, visemes=["closed", "half", "open", "round", "wide"], pivot=[x + w / 2, y + h * 0.4])

    # smile: a face patch
    sx, sy, sw, sh = smile_box
    win = np.zeros_like(alpha); win[sy:sy + sh, sx:sx + sw] = True
    save("expr_smile", smile, feather(win, 4) * 0.999, smile_box, group="head", depth=0, role="expr", expression="smile")

    # hair over the face: locks below the eye line swing as side hair, the rest are bangs
    front_mask[int(neck_y + hh * 0.9):] = False          # locks end at the shoulders
    side_m = front_mask.copy(); side_m[: int(eyes_y)] = False
    bangs_m = front_mask & ~side_m
    if side_m.sum() > 200:
        save("side_hair", master, feather(side_m, 1.0), bbox(side_m, 2), group="hair_side", depth=0.1)
    save("front_hair", master, feather(bangs_m, 1.0), bbox(bangs_m, 2), group="hair_front", depth=0.12)

    anchors = measure_anchors(alpha, head, (hx, hy, hw, hh), neck_y, eye_boxes, mouth_box, backonly)
    model = {
        "format": "livliv-model", "version": 4, "name": "Nora",
        "meta": {"generator": {"name": "site/scripts/make_nora.py", "build": "1"}, "author": {"name": "Nora"},
                 "license": {"id": "LicenseRef-Nora", "text": "Nora's own art, for her site."}, "watermark": {"kind": "none"}},
        "requires": ["full_body"], "canvas": [W, H], "profile": {"id": "chibi", "facing": "front", "glasses": False},
        "rig": json.loads((Path(__file__).parent / "nora-rig-defaults.json").read_text()),
        "anchors": anchors, "expressions": [{"id": "smile", "label": {"en": "Smile", "zh-Hant": "笑"}}], "layers": layers,
    }
    write(model)
    print(json.dumps({"layers": [l["id"] for l in layers], "eyes": anchors["eyes"], "mouth": mouth_box, "head": head}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
