"""Nora's rig: the textures her live renderer draws and the numbers it reads.

    uv run --no-project --with pillow --with numpy --with scipy \
        --with pymatting --with "rembg[cpu]" python site/scripts/make_rig.py

The site's Nora (src/scripts/nora/) is not a picture that plays an
animation: she is her own render, warped a little on a mesh so she can
breathe, tilt her head and let her hair lag, with her eyes and mouth drawn
live on top, so she can blink, look at you and make the five expressions of
her character sheet. This script turns her renders into what that needs:

  - a cut-out of every pose, with her eyes and mouth painted out of it
    (the renderer draws them, and a painted eye under a drawn one would
    show whenever she blinks or looks away);
  - for the two portraits, which have their studio baked in: the matte
    (BiRefNet through rembg, cached in site/.cache/), the foreground colours
    recovered from it (pymatting), and the studio itself with her removed,
    so the frame can stand empty while she is elsewhere on the page;
  - rig.json: where each pose's eyes, mouth, head, neck, bun and moving
    limbs are, as fractions of its texture.

Found by measurement (eyes and mouth: dark blobs in the face, measured as complete eye openings) where the render allows it; placed by hand, in MANUAL below, where
it does not (a pivot is a judgement about anatomy, not a pixel property).
`--debug` writes an overlay of everything found to site/.cache/rig-debug/,
which is how every value in MANUAL was checked.

Outputs are committed under site/src/assets/rig/; rerun after changing a
render or a MANUAL value. The renders themselves are site/src/assets/art/
and the repo's assets/ (art/README.md has their provenance).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage, sparse
from scipy.sparse.linalg import spsolve

SITE = Path(__file__).resolve().parent.parent
REPO = SITE.parent
OUT = SITE / "src" / "assets" / "rig"
CACHE = SITE / ".cache"
ART = SITE / "src" / "assets" / "art"

# Every pose the renderer can put her in. `eyes` is what the render itself
# shows: option08 open eyes, the closed crescents of `quiet`, or the lidded look of
# `lost`. `plate` names the studio a baked-in portrait stands in.
POSES: dict[str, dict] = {
    "stand": {"src": REPO / "assets" / "nora-canonical-self.png", "plate": "studio", "eyes": "open"},
    "stand-night": {"src": ART / "night-portrait.webp", "plate": "night", "eyes": "open"},
    "perch": {"src": ART / "group-chat.webp", "eyes": "open"},
    "ledge": {"src": ART / "ledge.webp", "eyes": "open"},
    "quiet": {"src": ART / "quiet.webp", "eyes": "closed"},
    "wave": {"src": ART / "wave.webp", "eyes": "open"},
    "lost": {"src": ART / "lost.webp", "eyes": "lidded"},
    "hop": {"src": ART / "hop.webp", "eyes": "open"},
}

# Hand-placed anatomy, as fractions of the SOURCE image (x of its width, y
# of its height; radii `r*` are fractions of its HEIGHT so a circle stays a
# circle). head: the ellipse that turns with her head (skull, face, the hair
# on it); neck: the pivot it turns about; face: the skin her features sit
# on; bun: the part that lags; hips and ground: where breathing and a weight
# shift pivot; gaze: where the render's own eyes are looking, so drawn eyes
# at rest look where hers did; limbs: a region that rotates about a joint.
# Remeasured on the 2D Bauhinia sources; prior 3D coordinates
# are not transferable. Radii remain fractions of source HEIGHT.
_STAND = {
    "faceBox": (0.30, 0.295, 0.73, 0.355),
    "mouthBox": (0.46, 0.355, 0.58, 0.375),
    "head": {"cx": 0.50, "cy": 0.235, "rx": 0.18, "ry": 0.21},
    "neck": (0.50, 0.415),
    "face": {"cx": 0.51, "cy": 0.345, "rx": 0.12, "ry": 0.075},
    "bun": {"cx": 0.46, "cy": 0.105, "r": 0.063},
    "hips": (0.50, 0.615),
    "ground": 0.95,
    "floor": (0.90, 0.985),
    # Flat 2D sclera touches the skin colour; automatic seed pairing joins
    # the two eyes. Measure complete openings on the supplied source instead.
    "eyes": [
        {"cx": 0.413, "cy": 0.327, "rx": 0.031, "ry": 0.027},
        {"cx": 0.593, "cy": 0.328, "rx": 0.032, "ry": 0.027},
    ],
}

MANUAL: dict[str, dict] = {
    "stand": _STAND,
    "stand-night": _STAND,
    "perch": {
        "faceBox": (0.32, 0.42, 0.82, 0.50),
        "mouthBox": (0.53, 0.50, 0.62, 0.525),
        "head": {"cx": 0.51, "cy": 0.30, "rx": 0.21, "ry": 0.28},
        "neck": (0.56, 0.56),
        "face": {"cx": 0.56, "cy": 0.485, "rx": 0.15, "ry": 0.09},
        "bun": {"cx": 0.43, "cy": 0.105, "r": 0.085},
        "hips": (0.52, 0.79), "ground": 0.955,
        "gaze": [0.3, 0.55],
        "limbs": [{"id": "phone", "pivot": (0.57, 0.70), "region": {"cx": 0.62, "cy": 0.63, "rx": 0.08, "ry": 0.085}}],
    },
    "ledge": {
        "faceBox": (0.31, 0.412, 0.83, 0.50),
        "mouthBox": (0.53, 0.497, 0.63, 0.518),
        "head": {"cx": 0.51, "cy": 0.30, "rx": 0.21, "ry": 0.27},
        "neck": (0.55, 0.555),
        "face": {"cx": 0.56, "cy": 0.48, "rx": 0.145, "ry": 0.085},
        "bun": {"cx": 0.43, "cy": 0.12, "r": 0.08},
        "hips": (0.54, 0.76), "ground": 0.87,
        "gaze": [0.25, 0.6],
        "limbs": [
            {"id": "pencil", "pivot": (0.43, 0.705), "region": {"cx": 0.47, "cy": 0.66, "rx": 0.05, "ry": 0.045}},
            {"id": "legs", "pivot": (0.58, 0.79), "region": {"cx": 0.63, "cy": 0.88, "rx": 0.14, "ry": 0.075}},
        ],
    },
    "quiet": {
        "faceBox": (0.29, 0.33, 0.77, 0.365),
        "mouthBox": (0.46, 0.366, 0.59, 0.39),
        "head": {"cx": 0.50, "cy": 0.24, "rx": 0.18, "ry": 0.21},
        "neck": (0.51, 0.425),
        "face": {"cx": 0.52, "cy": 0.36, "rx": 0.12, "ry": 0.075},
        "bun": {"cx": 0.44, "cy": 0.11, "r": 0.065},
        "hips": (0.51, 0.62), "ground": 0.965, "eyeAspect": 1.0,
        "gaze": [0.0, 0.2],
        "limbs": [{"id": "hand", "pivot": (0.36, 0.535), "region": {"cx": 0.39, "cy": 0.485, "rx": 0.045, "ry": 0.055}}],
    },
    "wave": {
        "faceBox": (0.31, 0.316, 0.77, 0.372),
        "mouthBox": (0.48, 0.372, 0.61, 0.398),
        "head": {"cx": 0.51, "cy": 0.24, "rx": 0.19, "ry": 0.22},
        "neck": (0.52, 0.43),
        "face": {"cx": 0.53, "cy": 0.36, "rx": 0.12, "ry": 0.075},
        "bun": {"cx": 0.46, "cy": 0.11, "r": 0.07},
        "hips": (0.51, 0.62), "ground": 0.965,
        "eyes": [
            {"cx": 0.435, "cy": 0.342, "rx": 0.033, "ry": 0.027},
            {"cx": 0.637, "cy": 0.340, "rx": 0.033, "ry": 0.028},
        ],
        "limbs": [{"id": "wave", "pivot": (0.25, 0.49), "region": {"cx": 0.21, "cy": 0.44, "rx": 0.065, "ry": 0.075}}],
    },
    "lost": {
        "faceBox": (0.35, 0.265, 0.76, 0.385),
        "mouthBox": (0.54, 0.358, 0.61, 0.372),
        "head": {"cx": 0.49, "cy": 0.24, "rx": 0.20, "ry": 0.22},
        "neck": (0.55, 0.425),
        "face": {"cx": 0.55, "cy": 0.35, "rx": 0.13, "ry": 0.08},
        "bun": {"cx": 0.38, "cy": 0.105, "r": 0.07},
        "hips": (0.52, 0.62), "ground": 0.965,
        "gaze": [0.0, 0.0],
        "limbs": [{"id": "scratch", "pivot": (0.30, 0.47), "region": {"cx": 0.27, "cy": 0.41, "rx": 0.06, "ry": 0.065}}],
        "eyes": [
            {"cx": 0.448, "cy": 0.349, "rx": 0.034, "ry": 0.0275, "rot": -0.28},
            {"cx": 0.663, "cy": 0.309, "rx": 0.029, "ry": 0.032, "rot": -0.28},
        ],
        "paintOut": [
            [(0.386, 0.331), (0.409, 0.315), (0.468, 0.315), (0.501, 0.329), (0.507, 0.361), (0.488, 0.378), (0.435, 0.382), (0.397, 0.366)],
            [(0.609, 0.288), (0.630, 0.268), (0.679, 0.264), (0.711, 0.279), (0.714, 0.322), (0.693, 0.343), (0.653, 0.347), (0.623, 0.332)],
        ],
    },
    "hop": {
        "faceBox": (0.31, 0.352, 0.82, 0.425),
        "mouthBox": (0.54, 0.427, 0.64, 0.451),
        "head": {"cx": 0.51, "cy": 0.27, "rx": 0.20, "ry": 0.245},
        "neck": (0.55, 0.48),
        "face": {"cx": 0.56, "cy": 0.41, "rx": 0.13, "ry": 0.08},
        "bun": {"cx": 0.44, "cy": 0.105, "r": 0.07},
        "hips": (0.53, 0.71), "ground": 0.96,
        "limbs": [
            {"id": "armL", "pivot": (0.39, 0.56), "region": {"cx": 0.24, "cy": 0.61, "rx": 0.10, "ry": 0.05}},
            {"id": "armR", "pivot": (0.72, 0.57), "region": {"cx": 0.86, "cy": 0.635, "rx": 0.09, "ry": 0.05}},
        ],
    },
}


# ----------------------------------------------------------------- loading


def load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA")).astype(np.float64) / 255.0


def luminance(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def birefnet_mask(pose: str, src: Path) -> np.ndarray:
    """The matte of a portrait with its studio baked in, cached: the model is
    a 973 MB download and its answer never changes for the same render."""
    fingerprint = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    cached = CACHE / f"rig-mask-{pose}-{fingerprint}.png"
    if not cached.exists():
        from rembg import new_session, remove  # heavy; only when the cache is cold

        CACHE.mkdir(parents=True, exist_ok=True)
        mask = remove(Image.open(src).convert("RGB"), session=new_session("birefnet-general"), only_mask=True)
        mask.save(cached)
    return np.asarray(Image.open(cached).convert("L")).astype(np.float64) / 255.0


def cut_out(pose: str, spec: dict) -> tuple[np.ndarray, np.ndarray | None]:
    """RGBA with straight alpha. For a baked-in portrait also returns the
    original pixels, which the empty studio is rebuilt from."""
    rgba = load_rgba(spec["src"])
    if "plate" not in spec:
        return rgba, None
    from pymatting import estimate_foreground_ml

    rgb = rgba[..., :3]
    alpha = birefnet_mask(pose, spec["src"])
    alpha[alpha < 0.02] = 0.0
    alpha[alpha > 0.98] = 1.0
    # Hair edges carry the studio's grey in them; recover what the strand
    # itself looks like, so she does not wear a grey halo on the dark page.
    fg = np.clip(estimate_foreground_ml(rgb, alpha), 0.0, 1.0)
    return np.dstack([fg, alpha]), rgb


# --------------------------------------------------------------- the face


def ellipse_from(mask: np.ndarray) -> dict:
    """Centre, semi-axes and rotation of a filled blob, from its second
    moments (a uniform ellipse has variance a²/4 along each axis)."""
    ys, xs = np.nonzero(mask)
    cx, cy = xs.mean(), ys.mean()
    cov = np.cov(np.vstack([xs - cx, ys - cy]))
    vals, vecs = np.linalg.eigh(cov)
    ry, rx = 2 * np.sqrt(vals[1]), 2 * np.sqrt(vals[0])  # major is vertical for her eyes
    major = vecs[:, 1]
    rot = float(np.arctan2(major[0], major[1]))  # 0 = upright; + leans the top to the right
    if rot > np.pi / 2:
        rot -= np.pi
    if rot < -np.pi / 2:
        rot += np.pi
    return {"cx": float(cx), "cy": float(cy), "rx": float(rx), "ry": float(ry), "rot": rot}


def blobs(dark: np.ndarray, min_area: int) -> list[tuple[np.ndarray, tuple]]:
    lab, _ = ndimage.label(dark)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab)):
        comp = lab == i + 1
        if comp[sl].sum() >= min_area:
            out.append((comp, sl))
    return out


def find_open_eyes(rgba: np.ndarray, box: tuple[int, int, int, int]) -> list[dict]:
    """Measure option08 openings from sclera seeds, then include iris and lid.

    Dark blobs alone join brows or hair, and fitting an iris alone misses the
    white. Sclera locates the eye row; a bounded connected fill recovers the
    adjacent iris/lid without wandering up into the brow or sideways into hair.
    """
    x0, y0, x1, y1 = box
    rgb = rgba[..., :3]
    lum = luminance(rgb)
    red, green, blue = rgb.transpose(2, 0, 1)
    # The late-night portrait's warm light tints its sclera (R-G about .08).
    white = (red - green < 0.10) & (green - blue < 0.10) & (blue > 0.66)
    white &= (lum > 0.72) & (rgba[..., 3] > 0.9)
    seeds = np.zeros(lum.shape, bool)
    seeds[y0:y1, x0:x1] = white[y0:y1, x0:x1]
    candidates = []
    for comp, sl in blobs(seeds, min_area=max(20, int(0.0001 * lum.size))):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if h > 0.015 * lum.shape[0] and 0.15 < w / h < 2.5:
            candidates.append((int(comp.sum()), comp, sl))
    pairs = []
    for i, left in enumerate(candidates):
        for right in candidates[i + 1:]:
            ls, rs = left[2], right[2]
            ly, ry = (ls[0].start + ls[0].stop) / 2, (rs[0].start + rs[0].stop) / 2
            lx, rx = (ls[1].start + ls[1].stop) / 2, (rs[1].start + rs[1].stop) / 2
            height = max(ls[0].stop - ls[0].start, rs[0].stop - rs[0].start)
            if 1.5 * height < abs(lx - rx) < 6 * height and abs(ly - ry) < height * 0.5:
                pairs.append((left[0] + right[0], [left, right]))
    if not pairs:
        raise SystemExit(f"expected two option08 sclera in {box}; inspect source and MANUAL")
    pair = sorted(max(pairs, key=lambda p: p[0])[1], key=lambda c: c[2][1].start)
    found = []
    for side, (_, seed, sl) in enumerate(pair):
        height = sl[0].stop - sl[0].start
        # The broad sclera is on the outer side of each iris. Grow toward
        # the nose more than toward her hair, leaving the eyebrow above.
        left = round(sl[1].start - height * (0.2 if side == 0 else 1.0))
        right = round(sl[1].stop + height * (1.0 if side == 0 else 0.2))
        top, bottom = round(sl[0].start - height * 0.16), round(sl[0].stop + height * 0.08)
        region = np.zeros(lum.shape, bool)
        region[max(0, top):bottom, max(0, left):right] = True
        dark = (lum < 0.52) & region
        touch = ndimage.binary_dilation(seed, iterations=max(2, round(height * 0.09)))
        connected = ndimage.binary_propagation(touch & dark, mask=dark)
        ys, xs = np.nonzero(connected | seed)
        found.append({"cx": float((xs.min() + xs.max()) / 2),
                      "cy": float((ys.min() + ys.max()) / 2),
                      "rx": float((xs.max() - xs.min() + 1) / 2),
                      "ry": float((ys.max() - ys.min() + 1) / 2), "rot": 0.0})
    return found


def find_crescents(rgba: np.ndarray, box: tuple[int, int, int, int]) -> list[dict]:
    """Closed eyes: two dark crescents, wider than tall."""
    x0, y0, x1, y1 = box
    lum = luminance(rgba[..., :3])
    dark = np.zeros(lum.shape, bool)
    dark[y0:y1, x0:x1] = (lum[y0:y1, x0:x1] < 0.42) & (rgba[y0:y1, x0:x1, 3] > 0.9)
    found = []
    for comp, sl in blobs(dark, min_area=int(0.00008 * lum.size)):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if w / h > 1.8:
            ys, xs = np.nonzero(comp)
            found.append({"x0": int(xs.min()), "x1": int(xs.max()) + 1, "y0": int(ys.min()), "y1": int(ys.max()) + 1})
    found.sort(key=lambda c: (c["x1"] - c["x0"]) * (c["y1"] - c["y0"]), reverse=True)
    if len(found) < 2:
        raise SystemExit(f"expected two closed eyes in {box}, found {len(found)}")
    return sorted(found[:2], key=lambda c: c["x0"])


def find_mouth(rgba: np.ndarray, box: tuple[int, int, int, int]) -> dict:
    """Her mouth: the one short dark line below the eyes."""
    x0, y0, x1, y1 = box
    lum = luminance(rgba[..., :3])
    dark = np.zeros(lum.shape, bool)
    dark[y0:y1, x0:x1] = lum[y0:y1, x0:x1] < 0.45
    best = None
    for comp, sl in blobs(dark, min_area=12):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if w > 2.2 * h and (best is None or comp.sum() > best[0].sum()):
            best = (comp, sl)
    if best is None:
        raise SystemExit(f"no mouth in {box}")
    comp, sl = best
    ys, xs = np.nonzero(comp)
    return {
        "cx": float(xs.mean()),
        "cy": float(ys.mean()),
        "w": float(xs.max() - xs.min() + 1),
        "h": float(ys.max() - ys.min() + 1),
        "mask": comp,
    }


def median_colour(rgb: np.ndarray, mask: np.ndarray) -> list[float]:
    return [round(float(np.median(rgb[..., c][mask])), 4) for c in range(3)]


def ellipse_mask(shape: tuple[int, int], e: dict, scale: float = 1.0, pad: float = 0.0) -> np.ndarray:
    h, w = shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    dx, dy = xs - e["cx"], ys - e["cy"]
    c, s = np.cos(e["rot"]), np.sin(e["rot"])
    u = dx * c - dy * s
    v = dx * s + dy * c
    return (u / (e["rx"] * scale + pad)) ** 2 + (v / (e["ry"] * scale + pad)) ** 2 <= 1.0


def harmonic_fill(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Fill `hole` with the smoothest surface that meets its border: her skin
    is a soft gradient there, and a membrane is exactly that."""
    out = rgb.copy()
    lab, _ = ndimage.label(hole)
    for i, sl in enumerate(ndimage.find_objects(lab)):
        pad = tuple(slice(max(0, s.start - 2), s.stop + 2) for s in sl)
        region = lab[pad] == i + 1
        idx = -np.ones(region.shape, int)
        idx[region] = np.arange(region.sum())
        rows, cols, vals = [], [], []
        rhs = np.zeros((region.sum(), 3))
        block = out[pad]
        for (y, x), k in zip(np.argwhere(region), idx[region]):
            rows.append(k)
            cols.append(k)
            vals.append(4.0)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if region[yy, xx]:
                    rows.append(k)
                    cols.append(idx[yy, xx])
                    vals.append(-1.0)
                else:
                    rhs[k] += block[yy, xx]
        a = sparse.csr_matrix((vals, (rows, cols)), shape=(region.sum(), region.sum()))
        solved = np.column_stack([spsolve(a, rhs[:, c]) for c in range(3)])
        block[region] = solved
    return out


# ------------------------------------------------------------- the studio


def sweep_fill(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """What a studio sweep looks like behind her: row by row, a quadratic
    across the known backdrop either side of the hole. A sweep changes
    mostly from top to bottom (wall, the curve, the floor line) and slowly
    across, so a row model keeps the floor line as sharp as the render has
    it, where a membrane or a pyramid smears it into a column."""
    h, w = hole.shape
    fill = rgb.copy()
    xs = np.arange(w, dtype=np.float64)
    for y in np.nonzero(hole.any(axis=1))[0]:
        row = hole[y]
        cols = np.nonzero(row)[0]
        near = (xs >= cols.min() - 220) & (xs <= cols.max() + 220) & ~row
        if near.sum() < 12:
            near = ~row
        t = (xs - w / 2) / w
        a = np.vstack([np.ones(near.sum()), t[near], t[near] ** 2]).T
        coef, *_ = np.linalg.lstsq(a, rgb[y, near], rcond=None)
        fill[y, row] = np.vstack([np.ones(row.sum()), t[row], t[row] ** 2]).T @ coef
    # Rows are fitted one at a time; a little vertical smoothing keeps their
    # noise from reading as banding. Only fitted rows are averaged: an
    # unfitted row below still has her in it.
    fitted = hole.any(axis=1).astype(np.float64)[:, None, None]
    num = ndimage.gaussian_filter1d(fill * fitted, 3.0, axis=0)
    den = ndimage.gaussian_filter1d(np.broadcast_to(fitted, fill.shape).copy(), 3.0, axis=0)
    smooth = num / np.maximum(den, 1e-9)
    fill[hole] = smooth[hole]
    return fill


def studio_without_her(rgb: np.ndarray, alpha: np.ndarray, floor: tuple[float, float]) -> np.ndarray:
    """Her studio with her and her shadow taken out. The shadow is drawn
    back live, only while she stands there.

    Above the floor band, a row fit (sweep_fill) replaces her. The floor
    band itself, where her feet stand and both lights throw her shadow a
    long way sideways, is interpolated top to bottom between the clean rows
    either side of it, so no part of the shadow survives to be fitted."""
    h, w = alpha.shape
    body = ndimage.binary_dilation(alpha > 0.01, iterations=max(6, round(0.012 * h)))
    top, bottom = round(floor[0] * h), round(floor[1] * h)
    above = body.copy()
    above[top:] = False
    fill = sweep_fill(rgb, above)
    t = np.linspace(0.0, 1.0, bottom - top + 2)[1:-1, None, None]
    fill[top:bottom] = fill[top - 1][None] * (1 - t) + rgb[bottom][None] * t
    hole = above.copy()
    hole[top:bottom] = True
    # The render has a faint grain; a perfectly smooth patch would read as a
    # stain on a large screen, so the fill gets the grain measured around it.
    ring = ndimage.binary_dilation(hole, iterations=24) & ~hole
    grain = rgb - ndimage.gaussian_filter(rgb, (3, 3, 0))
    sigma = float(grain[ring].std())
    rng = np.random.default_rng(7)
    noise = ndimage.gaussian_filter(rng.normal(0.0, 1.0, (h, w, 1)), (0.6, 0.6, 0)) * sigma * 1.4
    soft = ndimage.gaussian_filter(hole.astype(np.float64), 3)[..., None]
    return np.clip(rgb * (1 - soft) + (fill + noise) * soft, 0.0, 1.0)


# ---------------------------------------------------------------- per pose


def px_box(frac: tuple[float, float, float, float], w: int, h: int) -> tuple[int, int, int, int]:
    return (round(frac[0] * w), round(frac[1] * h), round(frac[2] * w), round(frac[3] * h))


def build(pose: str, spec: dict, debug: bool) -> dict:
    rgba, original = cut_out(pose, spec)
    h, w = rgba.shape[:2]
    manual = MANUAL[pose]
    face_box = px_box(manual["faceBox"], w, h)
    rgb = rgba[..., :3]
    lum = luminance(rgb)
    hole = np.zeros((h, w), bool)
    eyes: list[dict] = []

    if spec["eyes"] == "open":
        found = [
            {"cx": m["cx"] * w, "cy": m["cy"] * h,
             "rx": m["rx"] * h, "ry": m["ry"] * h, "rot": m.get("rot", 0.0)}
            for m in manual["eyes"]
        ] if "eyes" in manual else find_open_eyes(rgba, face_box)
        for e in found:
            e["core"] = [0.19, 0.105, 0.067]
            e["rim"] = [0.59, 0.34, 0.16]
            hole |= ellipse_mask((h, w), e, 1.25, pad=3.0)
            eyes.append(e)
    elif spec["eyes"] == "closed":
        # Closed crescents are the lower part of an eye whose lid has come
        # all the way down (the renderer draws them the same way), so the
        # open eye they belong to sits above them, a little narrower.
        for c in find_crescents(rgba, face_box):
            cw = c["x1"] - c["x0"]
            rx = cw / 2 / 1.2
            ry = rx * manual.get("eyeAspect", 1.45)
            e = {"cx": (c["x0"] + c["x1"]) / 2, "cy": c["y1"] - ry * 0.98, "rx": rx, "ry": ry, "rot": 0.0}
            crescent = np.zeros((h, w), bool)
            crescent[c["y0"] : c["y1"], c["x0"] : c["x1"]] = True
            crescent &= lum < 0.55
            e["core"] = median_colour(rgb, crescent & (lum < 0.2))
            e["rim"] = median_colour(rgb, crescent & (lum >= 0.2))
            hole |= ndimage.binary_dilation(crescent, iterations=4)
            eyes.append(e)
    else:  # lidded: placed by hand, the lid line and all
        for m in manual["eyes"]:
            e = {"cx": m["cx"] * w, "cy": m["cy"] * h, "rx": m["rx"] * h, "ry": m["ry"] * h, "rot": m.get("rot", 0.0)}
            region = ellipse_mask((h, w), e, 1.25, pad=3) & (lum < 0.5)
            e["core"] = median_colour(rgb, region & (lum < 0.2))
            e["rim"] = median_colour(rgb, region & (lum >= 0.2))
            eyes.append(e)
        for poly in manual["paintOut"]:
            img = Image.new("L", (w, h), 0)
            ImageDraw.Draw(img).polygon([(x * w, y * h) for x, y in poly], fill=255)
            hole |= np.asarray(img) > 0

    mouth_box = px_box(manual["mouthBox"], w, h)
    mouth = find_mouth(rgba, mouth_box)
    mouth_colour = median_colour(rgb, mouth["mask"])
    # the line and the soft shadow the render gives it
    hole |= ndimage.binary_dilation(mouth["mask"], iterations=6)

    faceless = harmonic_fill(rgb, hole & (rgba[..., 3] > 0.5))
    tex = np.dstack([faceless, rgba[..., 3]])

    # Crop a baked-in portrait to her, and remember where the crop sits in
    # the frame, so the live figure lands exactly where the picture had her.
    crop = (0, 0, w, h)
    if original is not None:
        ys, xs = np.nonzero(rgba[..., 3] > 0.01)
        m = round(0.03 * h)
        crop = (max(0, xs.min() - m), max(0, ys.min() - m), min(w, xs.max() + 1 + m), min(h, ys.max() + 1 + m))
        plate = studio_without_her(original, rgba[..., 3], manual["floor"])
        save(OUT / f"plate-{spec['plate']}.webp", plate, quality=86)
    x0, y0, x1, y1 = crop
    tex = tex[y0:y1, x0:x1]
    th, tw = tex.shape[:2]

    def fx(x: float) -> float:  # source px -> texture fraction of width
        return round((x - x0) / tw, 5)

    def fy(y: float) -> float:
        return round((y - y0) / th, 5)

    def fr(r: float) -> float:  # source px -> fraction of texture height
        return round(r / th, 5)

    def mx(v: float) -> float:  # manual fraction of source width -> texture fraction
        return fx(v * w)

    def my(v: float) -> float:
        return fy(v * h)

    def mr(v: float) -> float:
        return fr(v * h)

    def ell(e: dict) -> dict:
        return {"cx": mx(e["cx"]), "cy": my(e["cy"]), "rx": mr(e["rx"]), "ry": mr(e["ry"])}

    rig = {
        "size": [tw, th],
        "crop": [round(x0 / w, 5), round(y0 / h, 5), round(tw / w, 5), round(th / h, 5)],
        "expression": spec["eyes"],
        "gaze": manual.get("gaze", [0.0, 0.0]),
        "eyes": [
            {
                "cx": fx(e["cx"]),
                "cy": fy(e["cy"]),
                "rx": fr(e["rx"]),
                "ry": fr(e["ry"]),
                "rot": round(e["rot"], 4),
                "core": e["core"],
                "rim": e["rim"],
            }
            for e in eyes
        ],
        "mouth": {"cx": fx(mouth["cx"]), "cy": fy(mouth["cy"]), "w": fr(mouth["w"]), "h": fr(mouth["h"]), "colour": mouth_colour},
        "head": ell(manual["head"]),
        "neck": [mx(manual["neck"][0]), my(manual["neck"][1])],
        "face": ell(manual["face"]),
        "bun": {"cx": mx(manual["bun"]["cx"]), "cy": my(manual["bun"]["cy"]), "r": mr(manual["bun"]["r"])},
        "hips": [mx(manual["hips"][0]), my(manual["hips"][1])],
        "ground": my(manual["ground"]),
        "limbs": [
            {
                "id": limb["id"],
                "pivot": [mx(limb["pivot"][0]), my(limb["pivot"][1])],
                "region": ell(limb["region"]),
            }
            for limb in manual.get("limbs", [])
        ],
    }
    save(OUT / f"{pose}.webp", tex, quality=92)
    if debug:
        overlay(pose, tex, rig)
    return rig


def save(path: Path, img: np.ndarray, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    mode = "RGBA" if arr.shape[2] == 4 else "RGB"
    Image.fromarray(arr, mode).save(path, quality=quality, method=6, exact=True, alpha_quality=100)


def overlay(pose: str, tex: np.ndarray, rig: dict) -> None:
    """Everything found and placed, drawn on the texture, for checking by eye."""
    th, tw = tex.shape[:2]
    base = Image.new("RGBA", (tw, th), (205, 226, 205, 255))
    base.alpha_composite(Image.fromarray((tex * 255).astype(np.uint8), "RGBA"))
    d = ImageDraw.Draw(base)

    def e(el: dict, col: tuple, key_r: tuple = ("rx", "ry")) -> None:
        cx, cy = el["cx"] * tw, el["cy"] * th
        rx, ry = el[key_r[0]] * th, el[key_r[1]] * th
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=col, width=2)

    e(rig["head"], (40, 90, 220, 255))
    e(rig["face"], (220, 120, 40, 255))
    b = rig["bun"]
    e({"cx": b["cx"], "cy": b["cy"], "rx": b["r"], "ry": b["r"]}, (160, 40, 200, 255))
    for eye in rig["eyes"]:
        e(eye, (230, 30, 30, 255))
    m = rig["mouth"]
    d.rectangle([(m["cx"] * tw - m["w"] * th / 2), (m["cy"] * th - m["h"] * th / 2), (m["cx"] * tw + m["w"] * th / 2), (m["cy"] * th + m["h"] * th / 2)], outline=(230, 30, 30, 255))
    for name, pt, col in (("neck", rig["neck"], (0, 160, 0, 255)), ("hips", rig["hips"], (0, 120, 160, 255))):
        x, y = pt[0] * tw, pt[1] * th
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=col)
        d.text((x + 8, y - 6), name, fill=col)
    gy = rig["ground"] * th
    d.line([(0, gy), (tw, gy)], fill=(0, 0, 0, 255), width=1)
    for limb in rig["limbs"]:
        e(limb["region"], (255, 160, 0, 255))
        x, y = limb["pivot"][0] * tw, limb["pivot"][1] * th
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(255, 160, 0, 255))
        d.text((x + 7, y - 6), limb["id"], fill=(120, 60, 0, 255))
    out = CACHE / "rig-debug"
    out.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out / f"{pose}.jpg", quality=90)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("poses", nargs="*", default=list(POSES))
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    manifest = OUT / "rig.json"
    rigs = json.loads(manifest.read_text()) if manifest.exists() else {}
    for pose in args.poses:
        rigs[pose] = build(pose, POSES[pose], args.debug)
        print(f"{pose}: {rigs[pose]['size'][0]}x{rigs[pose]['size'][1]}, eyes {len(rigs[pose]['eyes'])}", file=sys.stderr)
    # A closed or lidded render shows too little of an open eye to sample
    # its colours; she opens them in the colours of her open-eyed wave.
    for rig in rigs.values():
        if rig["expression"] != "open" and "wave" in rigs:
            for eye, ref in zip(rig["eyes"], rigs["wave"]["eyes"]):
                eye["core"], eye["rim"] = ref["core"], ref["rim"]
    rigs = {k: rigs[k] for k in POSES if k in rigs}
    manifest.write_text(json.dumps(rigs, indent=2) + "\n")


if __name__ == "__main__":
    main()
