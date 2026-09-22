---
type: design
title: "Chunk Parallelism — the sequential loop is the real cost, not the chunk size"
description: "`read_video_windows` awaits each window in a for-loop, so a long clip pays its latency serially. Gather the reads, keep the segments in window order, and bound the fan-out."
tags: [nora, media, video, concurrency, asyncio, chunking]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Chunk Parallelism

## 1. The current shape

```
nora/infra/vision/comprehend.py:436    segments: list[str] = []
nora/infra/vision/comprehend.py:437    read: list[VideoWindow] = []
nora/infra/vision/comprehend.py:438    for window in cut.windows:
nora/infra/vision/comprehend.py:439        result = await ask(window.data)
```

One chunk at a time. The chunk *size* was never the problem — `VIDEO_CHUNK_SECONDS = 90`
(`preprocess.py:76`) and `VIDEO_MAX_CHUNKS = 10` (`preprocess.py:77`) bound a clip at
15 minutes, and **a 60-second Instagram Story fits in exactly one chunk**. Miles's
reading — reuse the existing practice — is correct and requires no change.

The problem is that when a clip *does* need several chunks, Nora pays for them
one after another:

| clip | chunks | measured per-chunk | serial total |
|---|---|---|---|
| 60 s Story | 1 | ~44 s | ~44 s |
| 3 min | 2 | ~40 s | ~80 s |
| 10 min Reel | 7 | ~40 s | **~280 s** |

280 s breaches the per-call vision budget *and* the 240 s loop
(`[llm].timeout_seconds`). Today that clip degrades to `unavailable` — not
because Nora cannot read it, but because she reads it too slowly.

## 2. The change

Read the windows concurrently and restore order afterwards:

```python
results = await asyncio.gather(*(ask(w.data) for w in cut.windows))
```

**The ordering guarantee is load-bearing and must be explicit.** `cut.windows`
carries `start_seconds` per window (`VideoWindow`), and the existing code renders
each segment as `f"{stamp} — {description}"` from that stamp — so order is
recoverable from the data, not from completion order. `asyncio.gather` preserves
input order in its result list, which is exactly what is needed: **gather in
window order, zip back to window order.** A description that lands out of order
would state the wrong timecode, which is a quiet factual error in an answer about
somebody's video.

Do **not** replace the `for` loop with a set or a completion-order callback.

## 3. Fan-out must be bounded

`VIDEO_MAX_CHUNKS = 10` means one clip can now open 10 simultaneous calls.
Measured, OpenCode Go parallelises media reads fully with no serialisation and no
429 (3 concurrent calls: batch wall 10.2 s vs ~30 s serial on the 7 s clip;
54.2 s vs ~140 s on the 57 s clip). But 3 is not 10, and the host is a 27 GB
box already running ~20 containers.

Cap the concurrency with a semaphore sized from config — default 4, matching
`media_fetch`'s own budget so the two numbers mean the same thing to an operator.
This is a new config key; see `04-timeout-and-models.md` §4 for where it sits.

## 4. The failure path must survive the rewrite

The current loop has three branches that a naive `gather` would lose:

1. **`result is None`** — `media_fetch`'s `ask` returns `None` when the describer
   raises, and the comment at `comprehend.py:441-445` records that a previous
   version dereferenced `None.available` and propagated an `AttributeError` past
   `ports.py::_validated` into the agent loop. A tool never raises into the loop.
2. **`not result.available`** — a refused window.
3. **empty description** — a window the model answered blank.

All three take the same `_UNREAD_MARKER` gap path today. Every one of them must
keep doing so, and a `gather` must not turn one raise into a whole-clip failure
(the sequential version degrades per-window; the concurrent one must too).
`gather(..., return_exceptions=True)` plus explicit per-result classification is
the shape — **not** bare `gather`, which propagates the first exception and throws
away nine good windows.

`read_video_windows` returns `(segments, read)`, and `read` drives the coverage
arithmetic in `describe_coverage` — a window that was never actually described
must stay outside `read` regardless of how the read was scheduled.

## 5. What this does NOT change

- **Chunk geometry stays 90 s / 10.** A Story is one chunk; that is already right.
- **`preprocess_media` is untouched.** The `-an` change is `02`'s, not this one.
- **The `for` loop in `cut_video`'s own ffmpeg cutting** (`preprocess.py:400-421`)
  stays sequential. ffmpeg runs locally on the same box against the same source
  bytes; parallelising it trades a network wait for CPU and disk contention on a
  host that has neither to spare. The provider side is where the wall-clock lives.

## 6. Expected effect

| clip | today | after |
|---|---|---|
| 60 s Story (1 chunk) | ~44 s | ~44 s — unchanged |
| 10 min Reel (7 chunks) | ~280 s → `unavailable` | ~44 s + ffmpeg cut |

The gain is concentrated exactly where the Instagram work will land.
