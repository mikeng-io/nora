# Design brief — vision timeout derivation: divide by the SERIAL factor

**Worktree:** `/home/<deploy-user>/workspace/nora-wt-timeout-fix`
**Branch:** `feat/vision-timeout-derivation` (cut from `feat/media-read-lane-design` @ `58fb9f85`)
**Base design docs:** `design/media-read-lane/07-timeout-derivation.md` (read it — it is the authority for the formula)

---

## 1. Understanding — the problem

`derive_vision_timeout` (`nora/infra/vision/client.py:193-211`) computes the per-call
model-call timeout as:

```python
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, budget)
return min(configured, per_call) if configured > 0 else per_call
```

It divides a **wall-clock** ceiling by the count of **calls that overlap in time**.
That is a unit error:

- Concurrent calls occupy `max(their durations)` of wall clock, **not their sum**.
  Dividing by the concurrent count makes the per-call ceiling shrink by a factor
  that does not correspond to elapsed time.
- Separately, **one `media_fetch` is not one model call**. A video is cut into
  windows (`preprocess.py`: `VIDEO_CHUNK_SECONDS = 90`, `VIDEO_MAX_CHUNKS = 10`), so
  one invocation can be up to 10 model calls.

**Observed consequence (the defect being fixed):** with the settled operator
settings — `[llm].timeout_seconds = 360`, `vision_timeout_seconds = 120`,
`media_fetch` budget 4 — the formula yields `min(120, 360 × 0.6 / 4) = min(120, 54)
= 54 s`. **The configured 120 never takes effect.** Against a measured latency
distribution where the 90 s window median is ~41.6 s and 13/19 runs exceed 36 s,
a 54 s cap means roughly a third of reads still degrade — the exact failure the
setting exists to remove.

The calls that genuinely **do** add up in wall clock are the **serial** ones: the
waves one invocation must await in sequence.

## 2. The decided design (operator decision, 2026-09-23)

Keep `[llm].timeout_seconds` at **360**. Fix the **derivation**, not the number.

```python
waves    = ceil(windows / concurrency)   # model calls ONE invocation awaits in sequence
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, waves)
return min(configured, per_call) if configured > 0 else per_call
```

**New invariant:** `waves × derived < loop × VISION_LOOP_SHARE` — the wall clock one
invocation actually consumes. It is **independent of `budget`**, because concurrent
invocations overlap. `budget` bounds *refusals* (how many calls a turn may make,
DEC-10's count), not elapsed time.

**`budget` must stop being an input to the timeout.** That is the core of the change.

### Required outcomes (all three, with `loop = 360`, `configured = 120`)

| case | windows | concurrency | waves | per-call |
|---|---|---|---|---|
| **Instagram Reel / Story / Post (≤90 s)** | 1 | 1 | 1 | **120.0 s** ← configured honoured |
| 3-minute clip | 2 | 1 | 2 | 108.0 s |
| 15-minute clip (the max) | 10 | 1 | 10 | 21.6 s |
| image (1 call) | 1 | 1 | 1 | **120.0 s** |

**The case the budget was held at 4 for** — a post with four clips, each ≤90 s:
4 concurrent invocations × 1 wave × 120 s → wall clock 120 s against a 360 s loop.
It must fit. **The loop must NOT be raised.**

## 3. Where the derivation must live — the key structural finding

`windows` is **only known at read time**, not at construction time.
`VisionClient` is built once in `nora/app/composition.py:1762-1775`, but the window
count comes from `cut_video`, which runs per request. **Do not try to fix this by
computing more at construction.**

`read_video_windows` (`nora/infra/vision/comprehend.py:412`) is the **single place in
the codebase that holds the window count before the model calls are issued:**

```python
cut = await cut_video(...)
...
segments, read = await read_video_windows(cut=cut, media_type=..., ask=...)
```

`describe()` already has **per-call overrides** — `model`, `provider`, `max_bytes`
(`client.py:262-265`). **Follow that existing pattern.** Recommended shape:

- `VisionClient` stores what the derivation needs (`loop_timeout_seconds`,
  `configured`) rather than only the pre-derived result.
- `VisionClient.describe(...)` gains an optional `windows: int | None = None`.
  When `None` (images, text synthesis, any single-view read) behave as today's
  single-view case.
- `read_video_windows` passes `windows=len(cut.windows)` through `ask` to the client.
- **Single-source the derivation in the client.** Do not compute the timeout at the
  call sites — that is how the repo already got burned by writing an invariant down
  twice (`client.py:275`, `:340`, and the `test_the_vision_timeout_divides_by_the_
  resolved_budget_not_the_table` docstring at `tests/test_vision.py:252-305`).

`read_video_windows`'s `ask` protocol is `Callable[[bytes], Awaitable[...]]` and has
**exactly two in-repo call sites** — `nora/agent/tools.py:5353` (`_ask` closure) and
`nora/infra/vision/comprehend.py:312` (lambda). Widening it is a 2-site change and is
acceptable; keep it explicit and typed.

**Concurrency:** the window fan-out is currently sequential (`comprehend.py:436-439`).
Introduce a named constant for the window concurrency (default **1**, i.e. today's
behaviour) and use it as the divisor's companion, so that when
`design/media-read-lane/01-chunk-parallelism.md` lands `asyncio.gather` with a bounded
semaphore, the two cannot drift apart. **Do not implement the gather in this change.**

## 4. Scope — do this, and only this

**IN:**
1. Rewrite `derive_vision_timeout` to divide by `waves`, not `budget`.
2. Thread `windows` from `read_video_windows` to the per-call timeout (both call sites).
3. Update `nora/app/composition.py:1769` to pass what the client now needs.
4. Update the module comment at `client.py:46-52` and the docstring at `:196-208` —
   they currently explain the old invariant and would be lies after this change.
5. **Tests** (see §5).

**OUT — do not touch:**
- The `asyncio.gather` concurrency change for windows (that is
  `01-chunk-parallelism.md`, a separate change).
- Anything from the tool-concurrency bundle (DEC-196).
- `vision_timeout_seconds`'s configured value (stays 120) and
  `[llm].timeout_seconds` (stays 360 in the operator's config — **it is gitignored,
  lives only in the master tree, and you must not edit it**).
- The egress screen, the audio `-an` change, the OpenCode Go provider lane.

## 5. Tests — required

**1. DELETE OR INVERT the test that pins the defect:**
- `tests/test_vision.py:163-181` — `test_the_budget_of_vision_calls_together_stays_
  under_the_loop` asserts `budget * derived < loop` across `budget ∈ {1,2,3}`. After
  this change the budget **must not** move the timeout. Invert it: assert that
  **changing the budget does NOT change the derived timeout**, and that the new
  invariant `waves * derived < loop * VISION_LOOP_SHARE` holds instead.
- `tests/test_vision.py:252-305` — keep the **composition-wiring** assertion
  (composition must resolve through `ToolRegistry.budgets()`, not the raw table) if
  the budget is still resolved there for any purpose. **If budget is no longer an
  input to the timeout at all, this test's second half (the loop over overrides at
  `:294-304`) is about a property that no longer exists — remove that half and say
  why in the docstring.**

**2. NEW — the regression that would have caught the inert 120:**
```
test_the_derived_timeout_honours_the_configured_value_at_the_instagram_window_count
```
One window, one invocation → derived **== configured (120)**, not a fraction of it.
Assert with the operator's real numbers (`loop=360, configured=120`).

**3. NEW — long clips still tighten:**
```
test_a_long_clip_tightens_the_per_call_timeout
```
10 windows → derived ≪ configured, so the loop is still protected.

**4. NEW — the invariant, parametrised** over `(loop, configured, windows, concurrency)`:
`waves * derived < loop` for all, `derived > 0` for all, and never above `configured`.

**5. Keep every existing test green** — especially the byte-ceiling and
timeout-behaviour tests (`:203-249`). `test_a_hung_model_becomes_a_timeout_not_a_hang`
uses an explicit `timeout_seconds=0.3`, so it must still work.

## 6. Acceptance criteria — how you will be judged

1. `pytest tests/test_vision.py tests/test_config.py -q` green.
2. `python -c "from nora.infra.vision import derive_vision_timeout as d; print(d(loop_timeout_seconds=360, configured=120, waves=1), d(loop_timeout_seconds=360, configured=120, waves=10))"`
   prints `120.0 21.6`.
3. `grep -rn "budget" nora/infra/vision/client.py` shows **no** budget term in the
   derivation (comments referring to history may remain only if they say it was removed).
4. No new `# type: ignore`, no `TODO`, no commented-out code.
5. Every changed behaviour has a test that **fails on the old code** — state which
   test proves the defect is fixed and that you verified it red-then-green.
6. Report: files changed with line counts, the exact commands run, and their output.
   **Do not claim success without the observed output.**

## 7. Environment

- Python: `/home/<deploy-user>/workspace/nora/.venv/bin/python` (3.13) — use it, not system python.
- Run tests from the worktree root: `/home/<deploy-user>/workspace/nora-wt-timeout-fix`.
- `config.toml` is **gitignored** and exists only in the master tree
  (`/home/<deploy-user>/workspace/nora/config.toml`) — read it for reference, **never edit it**.
- Repo conventions are strict: comments explain **why** and cite `file:line`; the
  codebase is heavily documented and a bare change without rationale will be rejected
  in review. Match the surrounding style.
- Commit on the branch when green. **Do NOT push. Do NOT open a PR. Do NOT touch master.**
