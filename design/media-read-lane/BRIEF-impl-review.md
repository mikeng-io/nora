# Review brief — vision timeout derivation (independent review)

You are reviewing ONE commit on a branch, read-only. **Do not modify any file. Do
not commit. Do not push. Do not run anything that changes state.** Report findings
only.

## The change

Commit `da439d67` on branch `feat/vision-timeout-derivation`, in
`/home/<deploy-user>/workspace/nora-wt-timeout-fix`. Review `git diff 58fb9f85..HEAD`
(base = the design-only tip).

Repo: **Nora**, a Python 3.13 async agent (Strands Agents SDK) for Discord/Telegram.
Conventions are strict and documented in `AGENTS.md` and `docs/maintainers/conventions-rationale.md` —
read them before judging style. Comments in this codebase explain *why* and cite
`file:line`; that is house style, not noise.

## What it does, and why

`derive_vision_timeout` (`nora/infra/vision/client.py`) computed the per-call
vision model-call ceiling as:

```python
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, budget)
return min(configured, per_call) if configured > 0 else per_call
```

The defect: `budget` is the number of `media_fetch` tool calls a turn may make, and
those run **concurrently**, so they occupy `max(their durations)` of wall clock,
**not their sum**. Dividing a wall-clock ceiling by a concurrent count shrank the
result by a factor corresponding to no elapsed time.

Observed consequence at the deployment's settings (`[llm].timeout_seconds` 360,
`vision_timeout_seconds` 120, `media_fetch` budget 4):
`min(120, 360 × 0.6 / 4) = min(120, 54) = 54 s` — **the configured 120 never took
effect.** Against a measured latency distribution for 90 s audible video windows
(n=19: min 28.5 s, median 42.2 s, max 120.8 s; 13/19 over 36 s), a 54 s cap meant
roughly a third of reads degraded — the failure the setting exists to remove.

The fix divides by the **serial** factor instead:

```python
waves    = ceil(windows / concurrency)   # model calls ONE invocation awaits in sequence
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, waves)
```

New invariant: `waves × derived < loop × VISION_LOOP_SHARE`. `budget` is no longer
an input (it still bounds *refusals*; R-7's ceiling still comes from
`ToolRegistry.budgets()`).

Design authority for the change: `design/media-read-lane/07-timeout-derivation.md`.

**A structural change came with it.** The window count is only known at *read*
time (`cut_video` produces it per request) while `VisionClient` is built once in
`nora/app/composition.py`. So the derivation moved **out of composition and into
the client**: the client now holds the derivation's *inputs* and derives per call.
`windows` is threaded from `read_video_windows` — the only place in the codebase
that holds the count before the model calls are issued — via a widened
`AskWindow = Callable[[bytes, int], ...]` protocol, following the existing
per-call `model` / `provider` / `max_bytes` seam.

## What to attack — be adversarial, do not summarise approvingly

1. **Is the arithmetic actually right?** Check `vision_waves` and
   `derive_vision_timeout` for edge cases: `windows=0`, negative, huge; `concurrency`
   of 0; `configured=0` or negative; a sub-second loop. Does anything divide by
   zero or return a non-positive ceiling? Verify against the docstring's claims.

2. **Is the new invariant real, or does the test merely agree with the code?**
   `tests/test_vision.py::test_the_waves_of_one_read_together_stay_under_the_loop`
   is parametrised over loop/configured/windows/concurrency. Is the `+ 1e-9`
   allowance legitimate, or does it hide a real violation? Try to construct inputs
   where `waves × derived > loop * VISION_LOOP_SHARE`.

3. **Is the threading correct at BOTH call sites?** `read_video_windows` passes
   `len(cut.windows)` to every window's `ask`. Verify `nora/agent/tools.py`
   (`_ask`, ~line 5353 and its two single-view callers) and
   `nora/infra/vision/comprehend.py` (`MediaComprehender._comprehend_video`).
   **Does anything pass the wrong number — a running index instead of the total,
   or `1` where the read is multi-window?** A wrong value here is silent: it
   produces a plausible timeout, not an error.

4. **Single-sourced, or a second writer?** The design's whole argument is that this
   invariant must be written down ONCE. Does any call site now compute a timeout
   itself, or read `_configured` / `_loop_timeout` from outside?

5. **The single-view default.** `describe(windows=None)` resolves to one wave via
   `vision_waves(windows=1)`. Enumerate every `.describe(` caller in `nora/` and
   confirm that a caller which *should* be multi-window is not silently defaulting
   to a single-wave (too generous) ceiling. Note `complete()` uses
   `self._call_timeout(None)` for the text pass — is that right?

6. **Is the guard test sound?**
   `test_the_window_fan_out_and_the_wave_divisor_cannot_drift_apart` reads
   `read_video_windows`'s source and asserts no `gather` while
   `VISION_WINDOW_CONCURRENCY == 1`. It strips comments first (the function's own
   comment names `asyncio.gather`). Is a source-reading test the right instrument,
   or is it theatre? Are there bypasses — `asyncio.wait`, a `TaskGroup`, a helper
   that gathers elsewhere?

7. **Test honesty.** `test_the_media_fetch_budget_is_not_an_input_to_the_derivation`
   asserts on
   `inspect.signature(derive_vision_timeout).parameters` rather than behaviour.
   Is that a real guard against the defect returning, or a proxy that a future
   refactor defeats? Is there any test that would catch a *behavioural*
   regression — e.g. someone reintroducing a divisor without renaming the param?

8. **Did the interface change break anything not covered?** `VisionClient.__init__`
   gained a REQUIRED `loop_timeout_seconds`. Find every construction site in the
   repo (`nora/`, `tests/`, `evals/`, `scripts/`) and every `describe(`/`read_video_windows(`
   caller, and say whether any is now un-callable or subtly wrong. The suite passes,
   so look for what the suite does **not** cover.

9. **Conformance with the repo.** `AGENTS.md` and `docs/maintainers/conventions-rationale.md` — does the
   change follow them? In particular: is `config.example.toml` prose accurate now
   (it was edited); is `nora/config.py`'s comment accurate; did anything need a
   release fragment or a DEC entry under the repo's own rules?

## Deliverable

Findings ordered by severity (**BLOCKING / MAJOR / MINOR**), each with:
- the file and line,
- what is wrong,
- the evidence or counter-argument (run something if it helps — the repo venv is
  `/home/<deploy-user>/workspace/nora/.venv/bin/python`, and `pytest` works from the
  worktree root),
- what would fix it.

Then: **"what this change gets right"** (short), and **"what I could not verify"**.

If you find nothing blocking, say so plainly — do not manufacture findings. But a
review that returns only praise has not done its job.

**Time budget: aim to finish within 20 minutes.** Prefer a complete report with a
few items marked "not checked" over an exhaustive investigation.
