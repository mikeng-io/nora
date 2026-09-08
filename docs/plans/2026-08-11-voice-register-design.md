# Voice Register Implementation Plan

> **Historical plan, 2026-08-11.** It carried a directive to a plugin-driven
> execution process that was removed from this repo on 2026-09-08; that line is
> deleted rather than preserved, because unlike a path citation it was an
> *instruction*, and an agent reading it would try to follow it. Read this file
> for what was intended, not for how to proceed.

**Goal:** Make Nora's Cantonese banter more independent and combative, without weakening the existing target and distress limits.

**Architecture:** `nora/doctrine/voice.md` is the authored register injected into every turn. A focused doctrine-content regression test will preserve the distinction found in recent production traces: a hit is not preceded by reassurance, permission-seeking, or a customer-service recap. Existing rubric tests remain the machine-readable voice check.

**Tech Stack:** Markdown doctrine, Python/pytest, existing eval rubric.

---

### Task 1: Pin the anti-puppy response shape

**Files:**
- Modify: `tests/test_doctrine_content.py`
- Test: `tests/test_doctrine_content.py`

**Step 1: Write the failing test**

Add a test requiring the voice portrait to state that Nora does not negotiate a hit, seek permission to use it, or turn it into reassurance before responding.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctrine_content.py::test_voice_hits_without_negotiating_or_reassuring -q`

Expected: FAIL because v9 lacks the new response-shape language.

### Task 2: Tighten the voice portrait

**Files:**
- Modify: `nora/doctrine/voice.md`

**Step 1: Write minimal implementation**

Describe a direct, specific rebuttal as the first move in a fight. Make clear that repeated factual corrections are not a cue to comfort, concede, or offer an exit. Keep the existing no-absent-target and genuine-distress limits unchanged.

**Step 2: Run focused test to verify it passes**

Run: `uv run pytest tests/test_doctrine_content.py::test_voice_hits_without_negotiating_or_reassuring -q`

Expected: PASS.

### Task 3: Verify doctrine and voice evaluation contracts

**Files:**
- Verify: `tests/test_doctrine_content.py`
- Verify: `tests/test_evals_rubric.py`

**Step 1: Run focused verification**

Run: `uv run pytest tests/test_doctrine_content.py tests/test_evals_rubric.py -q`

Expected: all tests pass.

**Step 2: Inspect the diff**

Run: `git diff --check && git diff -- nora/doctrine/voice.md tests/test_doctrine_content.py`

Expected: only the intended doctrine and regression-test changes, with no whitespace errors.

**Step 3: Commit**

```bash
git add nora/doctrine/voice.md tests/test_doctrine_content.py docs/plans/2026-08-11-voice-register-design.md
git commit -m "feat(doctrine): sharpen Nora's fight register"
```
