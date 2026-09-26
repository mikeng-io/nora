#!/usr/bin/env python3
"""Compose the showreel's music and sound design from code, on the shared clock.

    uv run --no-project --with numpy --with scipy python pipeline/score.py

Writes audio/stems/music.wav and audio/stems/sfx.wav (48 kHz, stereo, float).
Everything is synthesized here, seeded, so the same code gives the same bytes:
no sample library, no generated track whose beats the picture has to chase.
The picture and the sound read the same src/data/timeline.json.

The score, in one line per bar (120 bpm, F major, a bar is 2 s):

  1  0-2    intro      Fmaj9 struck once, heard through her earphones (low-passed)
  2  2-4    the room   Fmaj9; the groove comes in muffled, opening as the chat fills
  3  4-6              Em9 | A7b13
  4  6-8    the tag    break on Dm9, thinking ticks, G9sus, riser, a light switch
  5  8-10   memory     the drop: Bbmaj9, full groove, lights on
  6  10-12            C6/9
  7  12-14  senses     Am9 | Dm9
  8  14-16  the quiet  Gm9 | C9sus, drums out, the filter closes back to her earphones
     16.6             her inline remote: click, and the music stops dead
     17.6   end card   Fmaj9, home, and one chime for her
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parent.parent
SR = 48000
TL = json.loads((ROOT / "src" / "data" / "timeline.json").read_text())
DUR = TL["duration"] + 1.0            # a second of tail past the picture, trimmed by the mix
N = int(DUR * SR)
CUE = TL["cues"]
rng = np.random.default_rng(520)       # Nora's trigger text, as a seed


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def tt(n: int) -> np.ndarray:
    return np.arange(n) / SR


def place(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0) -> None:
    """Add a mono or stereo clip into a stereo buffer at time `at` (equal-power pan)."""
    i = int(round(at * SR))
    if x.ndim == 1:
        th = (pan + 1) * np.pi / 4
        x = np.stack([x * np.cos(th), x * np.sin(th)], axis=1)
    j = min(len(buf), i + len(x))
    if j > i >= 0:
        buf[i:j] += x[: j - i] * gain


def noise(n: int | tuple[int, int]) -> np.ndarray:
    return rng.standard_normal(n)


def sos_band(lo: float, hi: float, order: int = 2):
    return signal.butter(order, [lo, hi], btype="band", fs=SR, output="sos")


def lp(x: np.ndarray, fc: float, order: int = 2) -> np.ndarray:
    return signal.sosfilt(signal.butter(order, fc, fs=SR, output="sos"), x, axis=0)


def hp(x: np.ndarray, fc: float, order: int = 2) -> np.ndarray:
    return signal.sosfilt(signal.butter(order, fc, btype="high", fs=SR, output="sos"), x, axis=0)


# ── instruments ──────────────────────────────────────────────────────────────

def rhodes(m: float, dur: float, vel: float = 0.7) -> np.ndarray:
    """FM electric piano: a 1:1 bell body plus a short high tine, stereo tremolo."""
    f = mtof(m)
    n = int((dur + 1.8) * SR)
    t = tt(n)
    idx = (1.6 * vel) * np.exp(-t * 2.6) + 0.18
    body = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    tine = np.sin(2 * np.pi * f * 13.97 * t) * np.exp(-t * 38) * 0.12 * vel
    bark = np.sin(2 * np.pi * f * 3.0 * t) * np.exp(-t * 9) * 0.10 * vel
    amp = np.exp(-t * (0.9 + f / 1400))
    rel = np.clip(1 - (t - dur) / 0.35, 0, 1)
    y = np.tanh(1.3 * (body * 0.75 + tine + bark) * amp * rel) * vel
    trem = 0.5 * np.sin(2 * np.pi * 4.6 * t)
    return np.stack([y * (1 + 0.22 * trem), y * (1 - 0.22 * trem)], axis=1) * 0.5


def pad(notes: list[int], dur: float, bright: float = 1.0) -> np.ndarray:
    """Detuned additive saws, harmonics rolled off early: warm and wide."""
    n = int((dur + 1.2) * SR)
    t = tt(n)
    out = np.zeros((n, 2))
    for k, m in enumerate(notes):
        for d, side in ((-0.09, 0), (0.0, None), (0.08, 1)):
            f = mtof(m + d)
            y = np.zeros(n)
            ph = rng.uniform(0, 2 * np.pi)
            for h in range(1, 18):
                if f * h > 6000:
                    break
                y += np.sin(2 * np.pi * f * h * t + ph * h) / h * np.exp(-h / (3.2 * bright))
            if side is None:
                out += y[:, None] * 0.5
            else:
                out[:, side] += y * 0.7
                out[:, 1 - side] += y * 0.3
    a = np.clip(t / 0.45, 0, 1) ** 2
    r = np.clip(1 - (t - dur) / 1.0, 0, 1)
    return out * (a * r)[:, None] * 0.055


def bass(m: float, dur: float, vel: float = 0.8) -> np.ndarray:
    f = mtof(m)
    n = int((dur + 0.3) * SR)
    t = tt(n)
    y = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) * np.exp(-t * 3)
    env = np.clip(t / 0.008, 0, 1) * (0.55 + 0.45 * np.exp(-t * 4)) * np.clip(1 - (t - dur) / 0.12, 0, 1)
    return np.tanh(1.6 * y * env) * vel * 0.45


def kick(vel: float = 1.0) -> np.ndarray:
    n = int(0.45 * SR)
    t = tt(n)
    f = 44 + 120 * np.exp(-t * 32)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 7.5)
    click = hp(noise(n), 3000) * np.exp(-t * 400) * 0.25
    return np.tanh(1.8 * (body + click)) * vel * 0.8


def clap(vel: float = 1.0) -> np.ndarray:
    n = int(0.35 * SR)
    t = tt(n)
    nz = signal.sosfilt(sos_band(900, 5200), noise(n))
    env = np.zeros(n)
    for k, off in enumerate((0.0, 0.009, 0.019)):
        env += np.where(t >= off, np.exp(-(t - off) * 180), 0) * (0.8 if k < 2 else 1.0)
    env += np.where(t >= 0.019, np.exp(-(t - 0.019) * 18), 0) * 0.35
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30) * 0.3
    return (nz * env * 0.55 + body) * vel * 0.6


def hat(vel: float = 1.0, open_: bool = False) -> np.ndarray:
    n = int((0.25 if open_ else 0.06) * SR)
    t = tt(n)
    y = hp(noise(n), 7500, 4) * np.exp(-t * (14 if open_ else 70))
    return y * vel * 0.22


def shaker(vel: float = 1.0) -> np.ndarray:
    n = int(0.08 * SR)
    t = tt(n)
    y = signal.sosfilt(sos_band(5000, 11000), noise(n)) * np.clip(t / 0.012, 0, 1) * np.exp(-t * 55)
    return y * vel * 0.18


def rim(vel: float = 1.0) -> np.ndarray:
    n = int(0.06 * SR)
    t = tt(n)
    return (np.sin(2 * np.pi * 1750 * t) * 0.6 + np.sin(2 * np.pi * 420 * t)) * np.exp(-t * 90) * vel * 0.25


def crackle(n: int) -> np.ndarray:
    """Vinyl dust: sparse clicks and a bed of hiss, the lo-fi floor."""
    y = hp(noise(n), 2500) * 0.0035
    for i in rng.integers(0, n - 200, size=int(n / SR * 9)):
        y[i:i + 60] += signal.sosfilt(sos_band(1500, 7000), noise(60)) * rng.uniform(0.01, 0.05)
    return y


def mallet(m: float, vel: float = 0.8) -> np.ndarray:
    """A soft kalimba-like mallet: fast-decaying FM partials over a sine."""
    f = mtof(m)
    n = int(1.2 * SR)
    t = tt(n)
    y = np.sin(2 * np.pi * f * t + 1.4 * np.exp(-t * 18) * np.sin(2 * np.pi * f * 7 * t))
    y += 0.3 * np.sin(2 * np.pi * f * 2 * t) * np.exp(-t * 9)
    return y * np.exp(-t * 4.2) * np.clip(t / 0.002, 0, 1) * vel * 0.3


def pingpong(x: np.ndarray, delay: float, fb: float, taps: int) -> np.ndarray:
    out = x.copy()
    d = int(delay * SR)
    for k in range(1, taps + 1):
        g = fb ** k
        side = k % 2
        seg = lp(x[: len(x) - d * k, :].mean(axis=1), 5000 - 700 * k) * g
        out[d * k:, side] += seg
    return out


# (bar, [(16th step, midi note)]): the hook over the drop, answered by the delay
HOOK = [
    (5, [(0, 86), (3, 84), (6, 81), (8, 77), (11, 79)]),
    (6, [(0, 88), (3, 86), (6, 81), (8, 79), (11, 76)]),
    (7, [(0, 84), (3, 83), (6, 79), (8, 81), (11, 77), (14, 76)]),
]


# ── effects ──────────────────────────────────────────────────────────────────

def reverb_ir(seconds: float, bright: float = 1.0) -> np.ndarray:
    n = int(seconds * SR)
    t = tt(n)
    lows = lp(noise((n, 2)), 1800) * np.exp(-t * (5.5 / seconds))[:, None]
    highs = hp(noise((n, 2)), 1800) * np.exp(-t * (9.0 / seconds))[:, None] * 0.5 * bright
    ir = lows + highs
    ir[: int(0.018 * SR)] = 0                  # pre-delay
    ir = hp(ir, 180)                           # a reverb that rumbles muddies the VO
    return ir / np.sqrt((ir ** 2).sum()) * 0.9


def reverb(x: np.ndarray, ir: np.ndarray, wet: float) -> np.ndarray:
    y = np.stack([signal.fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in range(2)], axis=1)
    return x + y * wet


def lowpass_automation(x: np.ndarray, cutoff_at) -> np.ndarray:
    """A 4-pole low-pass whose cutoff follows cutoff_at(t), block by block."""
    blk = 256
    out = np.zeros_like(x)
    zi = np.zeros((2, 2, 2))
    for i in range(0, len(x), blk):
        fc = float(np.clip(cutoff_at(i / SR), 60, 20000))
        sos = signal.butter(4, min(fc, SR * 0.45), fs=SR, output="sos")
        out[i:i + blk], zi = signal.sosfilt(sos, x[i:i + blk], axis=0, zi=zi)
    return out


def interp(points: list[tuple[float, float]]):
    xs, ys = zip(*points)
    return lambda t: float(np.exp(np.interp(t, xs, np.log(ys))))


# ── the score ────────────────────────────────────────────────────────────────

BAR = 2.0
STEP = BAR / 16
SWING = 0.018                          # late 16ths, a lazy hand


def at_step(bar: int, step: int) -> float:
    return (bar - 1) * BAR + step * STEP + (SWING if step % 2 else 0.0)


# (bar, beat offset in bars, length in bars, rhodes voicing, pad voicing, bass root)
CHORDS = [
    (2, 0.0, 1.0, [57, 60, 64, 67], [53, 57, 60, 64], 41),     # Fmaj9
    (3, 0.0, 0.5, [55, 59, 62, 66], [52, 55, 59, 62], 40),     # Em9
    (3, 0.5, 0.5, [55, 61, 65, 70], [55, 61, 64, 67], 45),     # A7b13(b9)
    (4, 0.0, 0.5, [53, 57, 60, 64], [50, 53, 57, 60], 38),     # Dm9 (the break)
    (4, 0.5, 0.5, [53, 57, 60, 65], [53, 57, 60, 62], 43),     # G9sus
    (5, 0.0, 1.0, [57, 60, 62, 65], [50, 53, 57, 60], 46),     # Bbmaj9 (the drop)
    (6, 0.0, 1.0, [62, 64, 67, 69], [55, 60, 64, 67], 48),     # C6/9
    (7, 0.0, 0.5, [55, 59, 60, 64], [52, 55, 60, 64], 45),     # Am9
    (7, 0.5, 0.5, [53, 57, 60, 64], [50, 53, 57, 60], 38),     # Dm9
    (8, 0.0, 0.5, [58, 62, 65, 69], [55, 58, 62, 65], 43),     # Gm9
    (8, 0.5, 0.5, [58, 62, 65, 67], [53, 58, 60, 65], 48),     # C9sus
]


def music() -> np.ndarray:
    keys = np.zeros((N, 2))
    pads = np.zeros((N, 2))
    low = np.zeros((N, 2))
    drums = np.zeros((N, 2))

    # bar 1: one chord, far away
    for i, m in enumerate([57, 60, 64, 67, 72]):
        place(keys, rhodes(m, 1.6, 0.45), 0.55 + i * 0.03, pan=-0.3 + 0.15 * i)
    place(pads, pad([53, 57, 60, 64], 1.3, 0.6), 0.6, gain=0.7)

    for bar, off, length, rh, pv, root in CHORDS:
        t0 = (bar - 1) * BAR + off * BAR
        d = length * BAR
        place(pads, pad(pv, d, 1.0 if bar >= 5 else 0.75), t0, gain=0.9)
        if bar == 4:                   # the break: one held stab each, no comping
            for i, m in enumerate(rh):
                place(keys, rhodes(m, d * 0.9, 0.62), t0 + i * 0.012, pan=-0.25 + 0.17 * i)
            place(low, bass(root, d * 0.95, 0.5), t0)
            continue
        # comping: on the one, then a lazy push on the "and" of two
        hits = [(0.0, 0.66, 0.9)] if length < 1 else [(0.0, 0.7, 0.7), (0.625, 0.55, 0.6)]
        for pos, vel, ln in hits:
            for i, m in enumerate(rh):
                place(keys, rhodes(m, ln * d * (0.5 if length == 1 else 1), vel),
                      t0 + pos * BAR + i * 0.011, pan=-0.3 + 0.2 * i)
        # bass: root, then a fifth pickup
        lift = 1.0 if bar >= 5 else 0.55
        place(low, bass(root, d * 0.6, 0.85 * lift), t0)
        if length == 1:
            place(low, bass(root + 7, 0.3, 0.55 * lift), t0 + 1.25)
            place(low, bass(root + 12, 0.2, 0.45 * lift), t0 + 1.75)
        place(low, np.sin(2 * np.pi * mtof(root - 12) * tt(int(d * SR))) * 0.12
              * np.clip(tt(int(d * SR)) / 0.02, 0, 1) * np.clip(1 - (tt(int(d * SR)) - d + 0.1) / 0.1, 0, 1), t0)

    # drums: bars 2-3 (muffled, opening), bars 5-7 full; a build in bar 4
    for bar in (2, 3, 5, 6, 7):
        full = bar >= 5
        for s in (0, 7, 10):
            place(drums, kick((1.0 if s == 0 else 0.78) * (1.0 if full else 0.6)), at_step(bar, s))
        place(drums, clap(0.95 if full else 0.7), at_step(bar, 8), pan=0.05)
        if full:
            place(drums, rim(0.5), at_step(bar, 14), pan=0.35)
        for s in range(16):
            v = (0.9 if s % 4 == 2 else 0.55 if s % 2 == 0 else 0.3) * (1.0 if full else 0.7)
            place(drums, hat(v * (1.5 if full else 1.0), open_=(full and s == 14)), at_step(bar, s), pan=0.25)
            place(drums, shaker(0.45 if s % 2 else 0.3), at_step(bar, s), pan=-0.35)
    # bar 4 build: snare roll into the drop, 16ths then 32nds, rising
    for k in range(8):
        place(drums, clap(0.18 + 0.05 * k), 7.0 + k * 0.0625, pan=0.05)
    for k in range(8):
        place(drums, clap(0.55 + 0.05 * k), 7.5 + k * 0.03125, pan=0.05)
    # bar 8: the quiet: a heartbeat kick only
    place(drums, kick(0.55), 14.0)
    place(drums, kick(0.35), 15.0)

    # sidechain: everything melodic breathes around the kick in the full bars
    duck = np.ones(N)
    t = tt(N)
    for bar in (5, 6, 7):
        for s in (0, 7, 10):
            k0 = at_step(bar, s)
            m = t >= k0
            duck[m] = np.minimum(duck[m], 1 - 0.38 * np.exp(-(t[m] - k0) / 0.11))
    keys *= duck[:, None]
    pads *= duck[:, None]

    ir_big = reverb_ir(2.8)
    ir_room = reverb_ir(0.9, 0.6)
    keys = reverb(keys, ir_big, 0.35)
    pads = reverb(pads, ir_big, 0.25)
    drums = reverb(drums, ir_room, 0.12)

    lead = np.zeros((N, 2))
    for bar, notes in HOOK:
        for step, m in notes:
            place(lead, mallet(m, 0.75 if step == 0 else 0.6), at_step(bar, step), pan=0.18)
    lead = pingpong(lead * duck[:, None], 0.375, 0.42, 4)
    lead = reverb(lead, ir_big, 0.3)

    bed = np.stack([crackle(N), crackle(N)], axis=1)
    mix = keys * 0.9 + pads * 1.0 + low * 0.9 + drums * 0.85 + lead * 0.8 + bed

    # "through her earphones": closed at the top, opening across the room,
    # wide open from the drop, closing again in the quiet
    cutoff = interp([(0, 420), (1.8, 520), (2.0, 700), (5.6, 3200), (6.0, 5200),
                     (7.9, 6500), (8.0, 20000), (13.95, 20000), (14.05, 5200),
                     (16.4, 380), (DUR, 380)])
    mix = lowpass_automation(mix, cutoff)
    mix = hp(mix, 32)
    # level arc, measured in LUFS per section: the build sits ~4 LU under
    # the drop, and the quiet sinks as the filter closes
    arc = np.interp(t, [0, 7.98, 8.0, 14.0, 16.4, DUR], [0.6, 0.66, 1.0, 1.0, 0.45, 0.45])
    mix *= arc[:, None]

    # the click: everything stops dead at the remote
    click = CUE["click"]
    g = np.ones(N)
    i0 = int(click * SR)
    g[i0:] = 0.0
    g[i0 - int(0.004 * SR):i0] = np.linspace(1, 0, int(0.004 * SR))
    mix *= g[:, None]

    # home: the last chord, after the silence
    fin = CUE["finalChord"]
    end = np.zeros((N, 2))
    for i, m in enumerate([41, 48, 57, 64, 67, 72, 76]):
        v = 0.5 if m < 50 else 0.55
        place(end, rhodes(m, 2.2, v), fin + i * 0.028, pan=-0.45 + 0.15 * i)
    place(end, pad([53, 57, 60, 64, 67], 1.6, 0.9), fin - 0.05, gain=1.1)
    place(end, np.sin(2 * np.pi * mtof(29) * tt(int(2.4 * SR))) * 0.25
          * np.exp(-tt(int(2.4 * SR)) * 1.2) * np.clip(tt(int(2.4 * SR)) / 0.03, 0, 1), fin)
    end = reverb(end, reverb_ir(3.4), 0.45)
    mix += end

    # fade with the picture
    f0, f1 = CUE["fadeOut"]
    fade = np.clip(1 - (t - f0) / (f1 - f0 + 0.6), 0, 1) ** 1.5
    return mix * fade[:, None]


# ── sound design ─────────────────────────────────────────────────────────────

def whoosh(dur: float, lo: float = 300, hi: float = 4000, rise: bool = True) -> np.ndarray:
    n = int(dur * SR)
    t = tt(n)
    x = noise(n)
    fc = interp([(0, lo if rise else hi), (dur, hi if rise else lo)])
    y = lowpass_automation(np.stack([x, np.roll(x, 37)], axis=1), fc)
    shape = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** (1.5 if rise else 0.8)
    if rise:
        shape *= (t / dur) ** 0.8
    return y * shape[:, None] * 0.35


def pop(m: float, vel: float = 1.0) -> np.ndarray:
    """A message landing: a tuned blip that glides up a hair."""
    f = mtof(m)
    n = int(0.22 * SR)
    t = tt(n)
    ph = 2 * np.pi * np.cumsum(f * (1 + 0.06 * np.exp(-t * 60))) / SR
    y = (np.sin(ph) + 0.25 * np.sin(2 * ph)) * np.exp(-t * 22) * np.clip(t / 0.002, 0, 1)
    return y * vel * 0.28


def tick(f: float = 2600, vel: float = 1.0, ms: float = 12) -> np.ndarray:
    n = int(ms / 1000 * SR * 4)
    t = tt(n)
    y = np.sin(2 * np.pi * f * t) * np.exp(-t * (1000 / ms))
    y += hp(noise(n), 4000) * np.exp(-t * 900) * 0.4
    return y * vel * 0.3


def bell(m: float, dur: float = 1.6, vel: float = 1.0) -> np.ndarray:
    f = mtof(m)
    n = int(dur * SR)
    t = tt(n)
    y = np.sin(2 * np.pi * f * t + 2.2 * np.exp(-t * 3) * np.sin(2 * np.pi * f * 3.5 * t))
    return y * np.exp(-t * 3.2) * np.clip(t / 0.003, 0, 1) * vel * 0.18


def remote_click() -> np.ndarray:
    """The inline remote: a tight plastic press and its release."""
    n = int(0.16 * SR)
    t = tt(n)
    y = np.zeros(n)
    for off, v in ((0.0, 1.0), (0.085, 0.55)):
        m = t >= off
        tr = t[m] - off
        y[m] += (np.sin(2 * np.pi * 2300 * tr) * 0.6 + np.sin(2 * np.pi * 5400 * tr) * 0.3) * np.exp(-tr * 260) * v
        y[m] += hp(noise(m.sum()), 2000) * np.exp(-tr * 700) * 0.6 * v
    return y * 0.55


def switch_click() -> np.ndarray:
    n = int(0.12 * SR)
    t = tt(n)
    y = (np.sin(2 * np.pi * 1300 * t) * 0.5 + lp(noise(n), 4000) * 0.8) * np.exp(-t * 160)
    return y * 0.45


def boom() -> np.ndarray:
    n = int(2.0 * SR)
    t = tt(n)
    f = 30 + 55 * np.exp(-t * 6)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.2)
    air = lp(noise(n), 2500) * np.exp(-t * 5) * 0.35
    return np.tanh(1.5 * (y + air)) * 0.6


def riser(dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = tt(n)
    f = 180 * 2 ** (2.5 * t / dur)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.12
    x = whoosh(dur, 400, 9000, True)
    return x + np.stack([tone, tone], axis=1) * (t / dur)[:, None] ** 2


def scribble(dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = tt(n)
    am = np.clip(np.sin(2 * np.pi * 7.3 * t + 2 * np.sin(2 * np.pi * 1.9 * t)), 0, 1) ** 2
    y = signal.sosfilt(sos_band(2200, 7500), noise(n)) * am * 0.09
    return y


def stamp() -> np.ndarray:
    n = int(0.3 * SR)
    t = tt(n)
    return (np.sin(2 * np.pi * 110 * t) * np.exp(-t * 25) + lp(noise(n), 1500) * np.exp(-t * 60) * 0.6) * 0.5


def rain(dur: float) -> np.ndarray:
    n = int(dur * SR)
    out = np.zeros((n, 2))
    for i in rng.integers(0, n - 400, size=int(dur * 90)):
        d = signal.sosfilt(sos_band(1800, 9000), noise(300)) * np.exp(-tt(300) * 300) * rng.uniform(0.02, 0.09)
        c = rng.integers(0, 2)
        out[i:i + 300, c] += d
    bed = signal.sosfilt(sos_band(700, 6000), noise((n, 2)), axis=0) * 0.02
    env = np.sin(np.pi * np.clip(tt(n) / dur, 0, 1)) ** 0.5
    return (out + bed) * env[:, None]


def shutter() -> np.ndarray:
    n = int(0.12 * SR)
    t = tt(n)
    y = np.zeros(n)
    for off in (0.0, 0.055):
        m = t >= off
        y[m] += hp(noise(m.sum()), 1500) * np.exp(-(t[m] - off) * 120)
    return y * 0.3


def sfx() -> np.ndarray:
    out = np.zeros((N, 2))
    place(out, tick(3100, 1.0, 9), CUE["earbudTick"], pan=0.2)
    place(out, tick(2100, 0.6, 14), CUE["earbudTick"] + 0.004, pan=0.2)
    a, b = CUE["cableDraw"]
    place(out, whoosh(b - a, 900, 6500, True) * 0.5, a, pan=-0.2)
    # the room: each message lands on a chord tone of Fmaj9
    for at, m in zip(CUE["bubbles"], [69, 72, 76, 79, 81]):
        place(out, pop(m, 0.9), at, pan=0.25 if m % 2 else -0.15)
    for at in CUE["reskin"]:
        place(out, whoosh(0.22, 2500, 9000, False) * 0.6, at - 0.06)
        place(out, tick(1800, 0.8, 8), at, pan=-0.3)
    # the tag
    place(out, boom()[: int(0.5 * SR)] * 0.55, CUE["tag"])
    place(out, bell(74, 1.4, 1.0), CUE["tag"] + 0.02, pan=0.1)       # denim ping, D5
    for at, f in zip(CUE["thinking"], (1400, 1550, 1700)):
        place(out, tick(f, 0.7, 10), at, pan=0.3)
    place(out, whoosh(0.35, 400, 5000, True) * 0.7, CUE["reply"] - 0.3)
    place(out, pop(77, 1.0), CUE["reply"])
    for k in range(10):
        place(out, tick(rng.uniform(3500, 4500), 0.25, 5), CUE["reply"] + 0.08 + k * 0.035, pan=0.2)
    place(out, pop(84, 0.8), CUE["kicker"], pan=0.2)
    place(out, riser(1.0) * 0.8, CUE["drop"] - 1.0)
    place(out, switch_click(), CUE["lightSwitch"], pan=-0.25)
    place(out, boom(), CUE["drop"])
    for k, at in enumerate(CUE["lanes"]):
        place(out, whoosh(0.3, 600, 7000, False) * 0.45, at - 0.05, pan=-0.6 + 0.3 * k)
    a, b = CUE["scribble"]
    place(out, scribble(b - a), a, pan=-0.2)
    place(out, stamp(), CUE["noteStamp"])
    place(out, whoosh(0.4, 500, 8000, True) * 0.6, CUE["senses"][0] - 0.4)
    s = CUE["senses"]
    place(out, bell(81, 1.0, 0.7), s[0], pan=-0.2)                    # place: a map ping
    place(out, rain(0.55) * 1.2, s[1])                                # weather
    place(out, whoosh(0.18, 3000, 9000, False) * 0.5, s[2] - 0.05)   # voice: a needle drop
    place(out, tick(900, 0.6, 20), s[2], pan=0.1)
    place(out, shutter(), s[3], pan=0.25)                             # a photo
    place(out, bell(84, 1.0, 0.8), s[4])                              # on her own
    place(out, bell(88, 0.8, 0.5), s[4] + 0.09, pan=0.3)
    for at in s[1:]:
        place(out, whoosh(0.16, 2000, 9000, False) * 0.35, at - 0.08)
    # the quiet: everything pulled back into one line
    rev = whoosh(0.6, 300, 7000, True)
    place(out, rev * 0.8, CUE["collapse"] - 0.55)
    place(out, whoosh(0.8, 5000, 300, False) * 0.5, CUE["collapse"])
    place(out, remote_click(), CUE["click"], pan=0.1)
    # home
    place(out, bell(93, 2.0, 0.55), CUE["presence"], pan=0.2)         # A6: her chime, blush
    # room tone, so the silence is a room and not a dropout
    room = lp(noise((N, 2)), 900) * 0.0025
    return out + room


def write(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = np.abs(x).max()
    wavfile.write(path, SR, (x / max(peak, 1e-9) * 0.89).astype(np.float32))
    print(f"{path.relative_to(ROOT)}  {len(x) / SR:.2f}s  peak(before norm)={peak:.3f}")


if __name__ == "__main__":
    write(ROOT / "audio" / "stems" / "music.wav", music())
    write(ROOT / "audio" / "stems" / "sfx.wav", sfx())
