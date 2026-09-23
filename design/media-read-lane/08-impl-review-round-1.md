---
type: design
title: "vision timeout derivation — independent implementation review (round 1)"
description: "Independent Codex review of commit da439d67 (the derivation fix). One MAJOR (a required release fragment was missing and the gate passes only against the wrong base) and five MINOR, all verified and fixed. The review also confirmed the arithmetic: 48-51 committed invariant cases reproduced, both window-reader call sites forward the TOTAL count, no call site computes its own timeout."
tags: [nora, media, timeout, review, implementation]
timestamp: '2026-09-23'
edges:
  - {dst: 07-timeout-derivation, rel: reviews, provenance: asserted}
---

# Independent implementation review — round 1 (Codex, read-only)

**Subject:** commit `da439d67`, branch `feat/vision-timeout-derivation`.
**Reviewer:** Codex CLI, `--sandbox read-only`. Brief:
`design/media-read-lane/BRIEF-impl-review.md`.
**Every finding below was independently re-verified by the operator's agent before
being fixed** — marked ✅ where done.

**Headline:** no BLOCKING. The arithmetic and the threading survived the review
intact. The findings were about **the guards around the change**, not the change.

---

## MAJOR

### M1. ✅ CONFIRMED — the required release fragment was missing, and the gate only passed against the wrong base.

The repo's release-fragment gate requires a fragment for any branch with changes
against its base. It was run as `--base origin/master`, which returned exit 0 — but
`origin/master` is **not this branch's base** (the branch is cut from the design
tip `58fb9f85`). Against the correct base it **fails**:

```
$ python scripts/check_release_fragments.py --base 58fb9f85
changed branch has no release fragment; add release-fragments/unreleased/<name>.json
exit=2
```

**Root cause of the miss, worth recording:** the gate resolves
`git merge-base <base> HEAD` and inspects `git diff --name-status <base>...HEAD` for
a file with status **`A`**. Two consequences: (a) a fragment must be **committed**
before the gate can see it, so the check cannot be run pre-commit off the working
tree; (b) a base that is *not* the branch point silently widens or narrows the
range — here it made a missing fragment look present. **Run this gate against the
branch's own base, on a commit, not against `origin/master` on a dirty tree.**

**Fixed:** `release-fragments/unreleased/vision-timeout-serial-factor.json`
(`type: "fix"`).

---

## MINOR

### m1. ✅ CONFIRMED — the guard test's *other* branch greps unstripped source.

`test_the_window_fan_out_and_the_wave_divisor_cannot_drift_apart` has two branches.
The `VISION_WINDOW_CONCURRENCY == 1` branch strips comments (fixed earlier this
session); the `else` branch still asserted against raw `source`, where **both**
required strings (`gather`, `VISION_WINDOW_CONCURRENCY`) already occur in the
function's own comment block. Codex executed the committed body against the
committed sequential function with concurrency 1/2/4 and **all three passed**.
Fixed to assert against the stripped `code`.

### m2. ✅ CONFIRMED — `vision_waves` used float ceiling division.

`ceil(windows / concurrency)` is off by one above `2**53` (`windows=2**53+1`
returns `2**53`), raises `OverflowError` for `10**400`, and returns **0** for
`windows=1, concurrency=10**400` — a zero wave count, which the derivation would
then clamp, silently. Not reachable from video geometry, but the exact form is the
same length. Fixed to integer ceiling division `-(-a // b)`; normal values
unchanged (1→1, 10→10, 10/4→3).

### m3. ✅ CONFIRMED — the config prose overstated the guarantee.

`nora/config.py` and `config.example.toml` said a short clip gets this value
"honoured in full". **It is conditional:** the derived ceiling is
`min(configured, loop × 0.6 / waves)`, so the configured value binds only when it
is no larger than the loop's own 0.6 share, and a multi-window clip divides the
**loop share**, not the configured number. Codex's counterexample reproduced
exactly: the template's own `loop 240` / `configured 40` pair gives a ten-window
read `240 × 0.6 / 10 = 14.4 s`, not 40 ÷ 10 = 4. Both sites now state the formula
and the condition.

### m4. ✅ CONFIRMED — the decision index cited a test this commit deleted.

`design/09-decision-index.md` DEC-98 named
`test_the_vision_timeout_divides_by_the_resolved_budget_not_the_table` as the test
guarding `[llm.budgets]`'s one-number guarantee. That test was **deleted** with the
divisor. The row now names the replacement and records why the original was
removed, in place — the historical name is kept as exactly that, not erased.

### m5. — the strict-vs-non-strict bound.

Codex notes the correct mathematical bound is `waves × derived ≤ loop × share`
(**≤**, not `<`): ten windows produce exactly 216 s at the operator's settings,
and the docstring's `<` is satisfied only through the `min`. The `1e-9` allowance
is an absolute tolerance, not literally an ULP bound (a genuine one-ULP overshoot
exists at e.g. loop `0.03`, waves `9`). **Not changed** — the assertion in the test
is `<=` with the allowance, which is correct; this is a note about docstring
precision, recorded rather than silently left.

---

## What the review confirmed (its own words, checked)

- **Both window-reader call sites forward the TOTAL window count.** The two
  single-view paths in `nora/agent/tools.py` correctly pass `1`.
- **Every real `VisionClient` construction supplies `loop_timeout_seconds`** — the
  composition site and both test helpers; nothing missed in `evals/` or `scripts/`.
- **All production `.describe()` callers accounted for** — the one omitting
  `windows` handles still/audio media; other same-named calls target different
  interfaces entirely.
- **The derivation is single-sourced**; no external reader of the new private
  inputs exists.
- **48–51 committed invariant cases reproduced**, and the named configured-value
  regression **fails when an extra `/4` divisor is introduced in memory** — i.e.
  it is a real behavioural guard, not just a signature assertion.
- Zero/negative window counts and concurrency clamp safely; non-positive
  configured values select the derived cap; ordinary sub-second loops stay positive.

## What the reviewer could not verify

Full pytest/gate results (it used read-only inspection and in-memory probes to avoid
fixture writes); live provider latency; deployment settings; end-to-end deadline
behaviour; and universal positivity for arbitrary floats (the formula underflows to
zero for a sufficiently tiny positive loop, so the docstring's "any positive loop"
needs a representable-input qualification).
