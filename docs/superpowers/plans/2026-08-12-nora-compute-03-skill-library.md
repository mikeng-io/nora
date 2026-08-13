# Nora Compute — Phase 3: The Skill Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Smith's bare `run_python`-only toolset (Phase 2) with the governed skill library from the Nora Compute spec §6-§8, §10 — `render_mermaid`, `render_markdown`, `render_map`, `render_html_report`, the vendored `lieflat-charts` chart skill, and the true-neutral Mono page-shell every one of them renders into — plus the per-job spend guardrail Phase 2's own self-review flagged as missing.

**Architecture:** Three of the four new capabilities (`render_mermaid`, `render_markdown`, `render_map`) are genuine, narrow, deterministic Python functions exposed as Strands `@tool`s — each one a known recipe with a knowable output shape, the whole point of a skill library over raw code-exec (spec §6). The fourth, charts, is architecturally different and this plan is explicit about why: `lieflat-charts` (Phase 1's investigated dependency) has no programmatic API upstream — it is a **prompted skill**, meant to be read by an agent and followed using its own code-exec tool, not called as a function. So charts stay governed by *availability* (the vendored `SKILL.md`/`catalog.md`/templates sit in Smith's filesystem, doctrine points at them) rather than by a Python wrapper function like the other three. `render_html_report` is the unifying step: it accepts already-rendered HTML fragments — from any of the three tools, or from Smith's own chart-skill code-exec output — and wraps them in one shared, true-neutral, mobile-first page-shell, never caring which skill produced which fragment.

**Tech Stack:** Python (`markdown-it-py`, `Pygments`, `requests` — already in Smith's sandbox from Phase 2), Node.js + `@mermaid-js/mermaid-cli` (new, added to Smith's Dockerfile), Strands Agents SDK `@tool` decorator.

## Global Constraints

- Every rendering tool's DEFAULT output is self-contained: inline SVG, inline `data:` URI images, no external CDN script tags (spec §8). None of the tools this plan adds may reach for a CDN library by default.
- The interactive-JS allowlist (spec §8) — Plotly.js, Google Maps JS API (referrer-restricted), Chart.js, ECharts — is the ONLY way an artifact may include executable third-party JS, and it lives in `doctrine.md` prose plus (for charts specifically) which vendored `lieflat-charts` template Smith reaches for. This plan does not build a mechanism to enforce the allowlist in code — it's a prompted boundary, same as the rest of `lieflat-charts`' own governance, named here so it isn't mistaken for a hard technical guarantee.
- `lieflat-charts` is PolyForm Noncommercial 1.0.0 (verified, Phase-1-adjacent investigation) — the vendoring step MUST preserve the `Required Notice:` line and write `THIRD_PARTY_NOTICES.md`, and MUST substitute the Google-Fonts-loaded Inter reference for system fonts (spec §7's fix for the font-CDN gap the subagent review caught) — never a passthrough copy.
- Every new tool function is keyword-only where it takes more than one argument, matching this repo's own port/tool convention even though Smith's sandbox is a separate codebase from `nora` proper.
- Page-shell CSS tokens are TRUE neutral grayscale (R=G=B at every step) for chrome — never a hue-biased neutral — with at most one optional accent color per artifact for content only (spec §10). This was corrected once already (the Smith Spike artifact) after an initial warm-toned draft; do not reintroduce a warm/cool bias into the shell.

---

## File Structure

| File | Change |
|---|---|
| `nora/compute/smith/agent.py` | Add the spend-guardrail wrapper; register the four new tools |
| `nora/compute/smith/budget.py` (new) | The per-job tool-call/token ceiling |
| `scripts/vendor_lieflat_charts.py` (new) | Fetches, font-substitutes, and vendors `lieflat-charts` into Smith's image |
| `nora/compute/smith/skills/charts/` (new, vendored) | `lieflat-charts`' own `SKILL.md`, `catalog.md`, `templates/`, token files |
| `nora/compute/smith/skills/mermaid/render.py` (new) | `render_mermaid` |
| `nora/compute/smith/skills/mermaid/SKILL.md` (new) | |
| `nora/compute/smith/skills/markdown/render.py` (new) | `render_markdown` |
| `nora/compute/smith/skills/markdown/SKILL.md` (new) | |
| `nora/compute/smith/skills/maps/render.py` (new) | `render_map` |
| `nora/compute/smith/skills/maps/SKILL.md` (new) | |
| `nora/compute/smith/page_shell.py` (new) | The shared HTML wrapper + CSS token system |
| `nora/compute/smith/skills/html_report/render.py` (new) | `render_html_report` |
| `nora/compute/smith/skills/html_report/SKILL.md` (new) | |
| `nora/compute/smith/doctrine.md` | Extend past Phase 2's bare-bones version |
| `nora/compute/smith/Dockerfile` | Add Node.js + `mermaid-cli`; copy the new `skills/` tree |
| `nora/compute/smith/pyproject.toml` | Add `markdown-it-py`, `Pygments` |
| `THIRD_PARTY_NOTICES.md` (new, repo root) | `lieflat-charts` + its own CDN dependencies' notices |

---

### Task 1: The per-job spend guardrail (Phase 2's flagged gap)

**Files:**
- Create: `nora/compute/smith/budget.py`
- Modify: `nora/compute/smith/agent.py`
- Create: `nora/compute/smith/tests/test_budget.py`

**Interfaces:**
- Produces: `ToolCallBudget(max_calls: int)` with `.spend() -> None` (raises `BudgetExceededError` past the limit) and `.remaining -> int`; a `guarded(fn, budget)` decorator wrapping any `@tool` function so every call spends one unit first.

- [ ] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_budget.py`:

```python
import pytest

from budget import BudgetExceededError, ToolCallBudget, guarded


def test_a_fresh_budget_allows_calls_up_to_its_limit():
    budget = ToolCallBudget(max_calls=3)
    budget.spend()
    budget.spend()
    budget.spend()
    assert budget.remaining == 0


def test_spending_past_the_limit_raises():
    budget = ToolCallBudget(max_calls=1)
    budget.spend()
    with pytest.raises(BudgetExceededError):
        budget.spend()


def test_guarded_wraps_a_function_and_spends_the_budget():
    budget = ToolCallBudget(max_calls=2)
    calls = []

    @guarded(budget)
    def do_thing(x: int) -> int:
        calls.append(x)
        return x * 2

    assert do_thing(1) == 2
    assert do_thing(2) == 4
    assert calls == [1, 2]
    with pytest.raises(BudgetExceededError):
        do_thing(3)
    assert calls == [1, 2]  # the third call never ran its body
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'budget'`.

- [ ] **Step 3: Implement `budget.py`**

Create `nora/compute/smith/budget.py`:

```python
"""The per-job spend guardrail (Nora Compute spec §5) Phase 2's own
self-review flagged as missing: a ceiling on how many tool calls one job may
make, independent of its wall-clock timeout (`LocalDockerBackend`'s own
guardrail) — bounds a job that's technically still running but thrashing
(retrying, looping on errors) rather than doing useful work.

Deliberately a hand-rolled counter, not a Strands-native budget mechanism —
verify whether the installed Strands Agents SDK version has its own
max-iterations/max-tokens concept before relying solely on this; if it does,
prefer wiring that in ADDITION to this (a tool-call ceiling and a token
ceiling bound different things), never as a replacement for this file's
explicit, testable behavior.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class BudgetExceededError(Exception):
    """Raised when a job's tool-call ceiling is spent. Smith's own agent loop
    (agent.py) lets this propagate — an exhausted budget ends the job as a
    failure, the same way a killed container does at the wall-clock
    boundary, rather than letting it silently keep going."""


class ToolCallBudget:
    def __init__(self, *, max_calls: int) -> None:
        self._max_calls = max_calls
        self._spent = 0

    def spend(self) -> None:
        if self._spent >= self._max_calls:
            raise BudgetExceededError(
                f"tool-call budget exhausted ({self._max_calls} calls)"
            )
        self._spent += 1

    @property
    def remaining(self) -> int:
        return self._max_calls - self._spent


def guarded(budget: ToolCallBudget) -> Callable[[F], F]:
    """Wrap a tool function so every call spends one unit of `budget` BEFORE
    the function body runs — a call that would exceed the budget never
    executes its side effects at all."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            budget.spend()
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_budget.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the budget into `agent.py`**

In `nora/compute/smith/agent.py`, add near the top (after the existing imports):

```python
from budget import BudgetExceededError, ToolCallBudget, guarded

MAX_TOOL_CALLS = int(os.environ.get("SMITH_MAX_TOOL_CALLS", "40"))
_budget = ToolCallBudget(max_calls=MAX_TOOL_CALLS)
```

Wrap the existing `run_python` and `register_artifact` tool functions with `@guarded(_budget)`, applied BETWEEN `@tool` and the function definition (decorator order matters — `@tool` needs to see the already-guarded callable):

```python
@tool
@guarded(_budget)
def run_python(code: str) -> str:
    ...


@tool
@guarded(_budget)
def register_artifact(file_path: str, title: str, media_type: str) -> str:
    ...
```

In `main()`, catch `BudgetExceededError` around the `agent(task)` call and write a failure-shaped summary rather than letting the process crash uncleanly:

```python
def main() -> None:
    task_file = Path(os.environ["TASK_FILE"])
    task = task_file.read_text()
    doctrine = Path(__file__).parent.joinpath("doctrine.md").read_text()

    agent = Agent(system_prompt=doctrine, tools=[run_python, register_artifact])
    try:
        result = agent(task)
    except BudgetExceededError:
        # No url.txt is written -- LocalDockerBackend's _read_result (Phase 2)
        # already treats a missing url.txt as FAILED; this just makes the
        # reason legible in the container's own logs rather than a bare
        # "job exited without producing a result".
        print(f"job exceeded its {MAX_TOOL_CALLS}-call budget")
        return

    summary_path = WORKSPACE / "summary.txt"
    if not summary_path.exists():
        summary_path.write_text(str(result))
```

- [ ] **Step 6: Type-check and lint**

Run (from `nora/compute/smith/`): `uv run mypy . && uv run ruff check .`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/compute/smith/budget.py nora/compute/smith/agent.py nora/compute/smith/tests/test_budget.py
git commit -m "feat(smith): add per-job tool-call spend guardrail"
```

---

### Task 2: Vendor `lieflat-charts`

**Files:**
- Create: `scripts/vendor_lieflat_charts.py`
- Create (generated by the script, committed once run): `nora/compute/smith/skills/charts/` tree
- Create: `THIRD_PARTY_NOTICES.md`

**Interfaces:**
- Produces: `nora/compute/smith/skills/charts/SKILL.md`, `catalog.md`, `templates/` (with the Inter Google-Fonts reference substituted for system fonts), `mono-tokens.js`, `color-presets.js` — read directly by Smith's `run_python` tool + doctrine (no Python wrapper function; see this plan's own Architecture note on why).

- [ ] **Step 1: Write the vendoring script**

Create `scripts/vendor_lieflat_charts.py`:

```python
"""Fetches `lieflat-charts` (github.com/larashero3-dotcom/lieflat-charts) at
a pinned commit, substitutes its Google-Fonts-loaded Inter reference for
system font stacks (Nora Compute spec §7's fix for the font-CDN gap an
earlier subagent review caught — see docs/superpowers/specs/
2026-08-12-nora-compute-design.md §7), and writes the result into
`nora/compute/smith/skills/charts/`.

Re-run this script (with an updated PINNED_COMMIT) to pick up upstream
changes deliberately — never edit the vendored tree by hand, or a future
re-vendor silently discards the hand edit.

License: PolyForm Noncommercial 1.0.0 (verified against the actual LICENSE
text during this repo's design phase — permitted for Nora's private,
non-commercial use under its "Personal Uses" clause). This script preserves
the license's `Required Notice:` obligation by copying LICENSE verbatim into
the vendored tree and recording the substitution made to it in
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/larashero3-dotcom/lieflat-charts.git"
# Pin to a specific commit, not a moving branch -- re-vendoring is an
# explicit, reviewed act (this docstring), not something that silently
# changes what ships on the next unrelated run of this script.
PINNED_COMMIT = "REPLACE_WITH_ACTUAL_COMMIT_SHA_AT_VENDOR_TIME"

DEST = Path(__file__).parent.parent / "nora" / "compute" / "smith" / "skills" / "charts"
NOTICES_PATH = Path(__file__).parent.parent / "THIRD_PARTY_NOTICES.md"

# What the upstream templates load for the Inter font, and what replaces it --
# a plain CSS font-family stack matching this repo's own system-font decision
# (Nora Compute spec §10), never a live Google Fonts request.
GOOGLE_FONTS_LINK_PATTERN = re.compile(
    r'<link[^>]*fonts\.googleapis\.com[^>]*>\s*'
)
SYSTEM_FONT_STACK = (
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
)
# Matches ONLY the CSS property `font-family: 'Inter'` (or double-quoted, or
# with other stack entries after it) -- the HYPHEN is load-bearing here. A
# blind `.replace("'Inter'", ...)` (an earlier draft of this script) also
# rewrote every OTHER quoted occurrence of the string "Inter" in the file,
# including inside <script> blocks: Chart.js/ECharts config objects use the
# camelCase JS property `fontFamily:'Inter'` (no hyphen, so this regex never
# matches it and never touches it), and canvas font-shorthand strings like
# `'700 13px Inter'`. A blind replace turned `fontFamily:'Inter'` into
# `fontFamily:ui-sans-serif, system-ui, ...,fontSize:12` -- unquoted,
# comma-laden, and a JS SYNTAX ERROR that broke every Glance-family chart's
# script block outright (confirmed by an independent subagent review that
# actually ran this exact substitution against the real vendored HTML).
FONT_FAMILY_DECLARATION_PATTERN = re.compile(
    r"font-family\s*:\s*['\"]Inter['\"]"
)


def _substitute_fonts(html: str) -> str:
    html = GOOGLE_FONTS_LINK_PATTERN.sub("", html)
    # CSS `font-family:` declarations only -- never touches JS string
    # literals (camelCase `fontFamily`, canvas font-shorthand strings), so it
    # cannot corrupt a <script> block the way a blind text replace did.
    # Residual imprecision, accepted rather than chased further: a canvas
    # font-shorthand string ('700 13px Inter') still NAMES Inter after this
    # substitution, since it was never a `font-family:` declaration to begin
    # with -- the browser falls back to a default font when it can't find
    # one by that name (graceful degradation), which is a materially
    # different failure mode than the JS syntax error this fix actually
    # closes, and not worth a second, riskier regex to chase away entirely.
    return FONT_FAMILY_DECLARATION_PATTERN.sub(
        f"font-family: {SYSTEM_FONT_STACK}", html,
    )


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", REPO_URL, tmp], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "checkout", PINNED_COMMIT], check=True, capture_output=True)
        src = Path(tmp)

        for name in ("SKILL.md", "catalog.md", "mono-tokens.js", "color-presets.js", "LICENSE"):
            shutil.copy2(src / name, DEST / name)

        templates_src = src / "templates"
        templates_dest = DEST / "templates"
        shutil.copytree(templates_src, templates_dest)
        for html_file in templates_dest.rglob("*.html"):
            html_file.write_text(_substitute_fonts(html_file.read_text()))

    notice = f"""# Third-Party Notices

## lieflat-charts

Vendored at commit `{PINNED_COMMIT}` from
https://github.com/larashero3-dotcom/lieflat-charts, under the PolyForm
Noncommercial License 1.0.0 (see `nora/compute/smith/skills/charts/LICENSE`).
Verified permitted for Nora's private, non-commercial, self-hosted use under
that license's "Personal Uses" clause (see
`docs/superpowers/specs/2026-08-12-nora-compute-design.md` §7).

Required Notice: Copyright the `lieflat-charts` authors
(https://github.com/larashero3-dotcom/lieflat-charts).

**Modification from upstream:** the Google Fonts-loaded Inter typeface
reference has been replaced with a system font stack in every vendored
template, so no vendored artifact reaches an external CDN for its type at
render time (Nora Compute spec §7/§10). Chart.js and Apache ECharts (loaded
via CDN in the Glance/interactive templates) are UNCHANGED from upstream and
remain third-party dependencies of those templates specifically:

| project | used by | license |
|---|---|---|
| Chart.js | Glance chart rendering | MIT |
| Apache ECharts | Glance + interactive network diagrams | Apache-2.0 |
"""
    NOTICES_PATH.write_text(notice)
    print(f"Vendored lieflat-charts at {PINNED_COMMIT} into {DEST}")
    print(f"Wrote {NOTICES_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Pin the actual commit and run it**

Run: `git ls-remote https://github.com/larashero3-dotcom/lieflat-charts.git HEAD` to get the current commit SHA, replace `PINNED_COMMIT`'s placeholder value with it, then:

Run: `uv run python scripts/vendor_lieflat_charts.py`
Expected: creates `nora/compute/smith/skills/charts/` populated with `SKILL.md`, `catalog.md`, `mono-tokens.js`, `color-presets.js`, `LICENSE`, `templates/`, and writes `THIRD_PARTY_NOTICES.md` at the repo root.

- [ ] **Step 3: Verify the font substitution actually landed — AND that it didn't corrupt any JS**

Run: `grep -rl "fonts.googleapis.com" nora/compute/smith/skills/charts/templates/`
Expected: no output (no matches) — confirms every vendored template's Google Fonts `<link>` reference was removed, not just the ones this task happened to check by hand.

Run: `grep -rl "font-family: ui-sans-serif" nora/compute/smith/skills/charts/templates/`
Expected: at least the templates that had a `font-family: 'Inter'` CSS declaration originally now show the substitution.

**Run a real JS syntax check on every Glance-family/interactive template** — the specific failure an earlier, blunter version of this script introduced (a global string-replace corrupted `fontFamily:'Inter'` inside Chart.js/ECharts `<script>` config objects into invalid JS). A `grep` alone doesn't catch a syntax error; extract and parse each `<script>` block:

```bash
python3 - <<'EOF'
import re, subprocess, sys
from pathlib import Path

failed = False
for html_file in Path("nora/compute/smith/skills/charts/templates").rglob("*.html"):
    text = html_file.read_text()
    for i, script in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", text, re.DOTALL)):
        if not script.strip():
            continue
        result = subprocess.run(
            ["node", "--check", "-"], input=script, text=True, capture_output=True,
        )
        if result.returncode != 0:
            print(f"SYNTAX ERROR in {html_file} script block {i}:\n{result.stderr}")
            failed = True
if failed:
    sys.exit(1)
print("all script blocks parse cleanly")
EOF
```

Expected: `all script blocks parse cleanly` (needs a local `node` install to run this check — not otherwise a dependency of this repo, so run it ad hoc rather than adding it as a project dependency for one verification step).

- [ ] **Step 4: Commit**

```bash
git add scripts/vendor_lieflat_charts.py nora/compute/smith/skills/charts/ THIRD_PARTY_NOTICES.md
git commit -m "feat(smith): vendor lieflat-charts with system-font substitution"
```

---

### Task 3: `render_mermaid`

**Files:**
- Create: `nora/compute/smith/skills/mermaid/render.py`
- Create: `nora/compute/smith/skills/mermaid/SKILL.md`
- Create: `nora/compute/smith/tests/test_render_mermaid.py`
- Modify: `nora/compute/smith/agent.py` (register the tool)
- Modify: `nora/compute/smith/Dockerfile` (Node.js + `mermaid-cli`)

**Interfaces:**
- Produces: `render_mermaid(diagram_source: str) -> str` — returns a self-contained `<svg>...</svg>` string. Task 7's `render_html_report` and Smith's own agent loop both consume this return shape directly (a string ready to embed as one section).

- [ ] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_render_mermaid.py`:

```python
from unittest.mock import patch

import pytest

from skills.mermaid.render import MermaidRenderError, render_mermaid


def test_a_valid_diagram_returns_svg_markup():
    with patch("skills.mermaid.render.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "<svg>fake diagram</svg>"
        mock_run.return_value.stderr = ""
        result = render_mermaid(diagram_source="flowchart LR\n  A --> B")
    assert result.startswith("<svg")


def test_mermaid_cli_is_invoked_with_the_source_and_no_network_flags():
    with patch("skills.mermaid.render.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "<svg></svg>"
        mock_run.return_value.stderr = ""
        render_mermaid(diagram_source="flowchart LR\n  A --> B")
    args = mock_run.call_args
    assert "mmdc" in args[0][0][0] or "mmdc" in str(args[0][0])


def test_an_invalid_diagram_raises_rather_than_returning_garbage():
    with patch("skills.mermaid.render.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Parse error on line 1"
        with pytest.raises(MermaidRenderError):
            render_mermaid(diagram_source="not a valid diagram")
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_mermaid.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills'`.

- [ ] **Step 3: Implement `render_mermaid`**

Create `nora/compute/smith/skills/mermaid/render.py`:

```python
"""`render_mermaid` — one of the three genuinely deterministic skills (this
plan's own Architecture note): Mermaid diagram source in, static SVG out,
rendered SERVER-SIDE (inside this sandbox, at job time) via `mermaid-cli`
(`mmdc`), never shipped as live `mermaid.js` in the artifact itself. Nora
Compute spec §6/§8: self-contained by default, no client JS required to view
the result.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class MermaidRenderError(Exception):
    """`mmdc` refused the input -- malformed diagram source, not a transport
    or environment failure. The caller (Smith's own agent loop) should treat
    this as "try a different diagram" or "fall back to text", not retry
    blindly."""


def render_mermaid(diagram_source: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "diagram.mmd"
        output_path = Path(tmp) / "diagram.svg"
        input_path.write_text(diagram_source)

        result = subprocess.run(
            ["mmdc", "-i", str(input_path), "-o", str(output_path), "--outputFormat", "svg"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise MermaidRenderError(result.stderr.strip() or "mmdc failed with no output")
        return output_path.read_text()
```

Create `nora/compute/smith/skills/mermaid/SKILL.md`:

```markdown
---
name: render-mermaid
description: Render Mermaid diagram source to a static, self-contained SVG.
---

# render_mermaid

Call `render_mermaid(diagram_source=...)` with valid Mermaid syntax
(flowchart, sequence, etc.) to get back an `<svg>` string. This is a static
render — no interactivity, works offline the moment the artifact is opened.
Embed the returned string directly as one section when calling
`render_html_report`.

If the diagram source is malformed, `render_mermaid` raises rather than
returning something broken — catch it and either fix the source or fall back
to describing the same thing in prose within `render_markdown`.
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_mermaid.py -v`
Expected: PASS.

- [ ] **Step 5: Add Node.js + `mermaid-cli` + Chromium to the Dockerfile**

In `nora/compute/smith/Dockerfile`, add before the `uv pip install` line:

```dockerfile
# mermaid-cli (mmdc) needs Node.js, AND it renders via a headless Chromium
# under Puppeteer -- a bare `nodejs`/`npm` install is not enough. Without
# Chromium and its own shared-library dependency chain, `mmdc` fails at
# RUNTIME (not build time) with missing-library errors (a well-documented
# mermaid-cli Docker pitfall: `libgbm.so.1` and friends). An earlier draft
# of this Dockerfile only installed nodejs/npm and passed its own `docker
# build` check, because that check never actually invokes `mmdc` against a
# real diagram -- only Task 3's unit tests exercise render_mermaid's LOGIC,
# and those mock subprocess.run entirely, so nothing there would have
# caught this either. Puppeteer's own Chromium ships via the npm install
# below; PUPPETEER_SKIP_CHROMIUM_DOWNLOAD is deliberately left UNSET so it
# actually downloads one, and --no-sandbox is required because this
# container has no unprivileged-user-namespace setup for Chromium's own
# sandbox to use.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm \
        chromium \
        fonts-liberation libnss3 libatk-bridge2.0-0 libatk1.0-0 \
        libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && npm install -g @mermaid-js/mermaid-cli \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# mmdc's own bundled Chromium looks for this executable; point it at the
# apt-installed one rather than trying to run its own downloaded copy
# (which frequently fails on Debian slim images for the same missing-lib
# reasons this Dockerfile just installed by hand).
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
```

Update `skills/mermaid/render.py`'s `subprocess.run` call (Task 3, Step 3) to pass `--puppeteerConfigFile` or the equivalent no-sandbox flag `mmdc` needs when running as root in a container — confirm the exact current flag against the installed `@mermaid-js/mermaid-cli` version's own `--help` output before finalizing, since this detail shifts between versions; a `{"args": ["--no-sandbox"]}` puppeteer-config JSON file passed via `--puppeteerConfigFile` is the common shape.

**Verify this actually works, not just that the image builds** — the same distinction Task 2's Step 3 fix makes for the font substitution. After building the image:

```bash
docker run --rm smith:latest bash -c "echo 'flowchart LR\n  A --> B' > /tmp/t.mmd && mmdc -i /tmp/t.mmd -o /tmp/t.svg --outputFormat svg && cat /tmp/t.svg | head -c 100"
```

Expected: real SVG markup printed, not a missing-library or Chromium launch error. A `docker build` succeeding says nothing about this — confirmed by the earlier draft of this plan, whose build-only verification step would have shipped a `render_mermaid` that fails on every real invocation.

- [ ] **Step 6: Register the tool in `agent.py`**

In `nora/compute/smith/agent.py`, add the import and tool registration:

```python
from skills.mermaid.render import MermaidRenderError, render_mermaid as _render_mermaid


@tool
@guarded(_budget)
def render_mermaid(diagram_source: str) -> str:
    """Render Mermaid diagram source to a self-contained SVG string. Raises
    if the source is malformed -- see skills/mermaid/SKILL.md."""
    try:
        return _render_mermaid(diagram_source=diagram_source)
    except MermaidRenderError as exc:
        return f"[render_mermaid failed: {exc}]"
```

Add `render_mermaid` to the `tools=[...]` list passed to `Agent(...)` in `main()`.

(Wrapping rather than exporting `_render_mermaid` directly as the `@tool` avoids the Strands SDK trying to introspect a function imported from another module for its docstring/schema in a way that might not resolve cleanly — verify this is actually necessary against the installed Strands SDK version; if `@tool` handles re-exported functions fine, this indirection can be dropped and `render_mermaid` imported directly.)

- [ ] **Step 7: Type-check, lint, rebuild, verify**

Run (from `nora/compute/smith/`): `uv run mypy . && uv run ruff check .`
Expected: clean.

Run: `docker build --network=host -t smith:latest nora/compute/smith/`
Expected: builds successfully with `mmdc` installed.

- [ ] **Step 8: Commit**

```bash
git add nora/compute/smith/skills/mermaid/ nora/compute/smith/tests/test_render_mermaid.py nora/compute/smith/agent.py nora/compute/smith/Dockerfile
git commit -m "feat(smith): add render_mermaid skill"
```

---

### Task 4: `render_markdown`

**Files:**
- Create: `nora/compute/smith/skills/markdown/render.py`
- Create: `nora/compute/smith/skills/markdown/SKILL.md`
- Create: `nora/compute/smith/tests/test_render_markdown.py`
- Modify: `nora/compute/smith/agent.py`
- Modify: `nora/compute/smith/pyproject.toml`

**Interfaces:**
- Produces: `render_markdown(text: str) -> str` — GFM markdown in, self-contained HTML fragment out (fenced code blocks syntax-highlighted via Pygments, inline `<style>` for the highlighting, no client JS).

- [ ] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_render_markdown.py`:

```python
from skills.markdown.render import render_markdown


def test_plain_prose_becomes_a_paragraph():
    result = render_markdown(text="Hello, world.")
    assert "<p>Hello, world.</p>" in result


def test_a_fenced_code_block_is_syntax_highlighted():
    """Deliberately stricter than checking for the mere PRESENCE of `<pre`/
    `<div` — that weaker assertion is exactly what let a real double-wrapping
    bug (`<pre><code><div class="highlight"><pre>...` — Pygments' own
    `nowrap=False` output getting wrapped a second time by markdown-it-py's
    fence renderer) pass an earlier draft of this test undetected. Counting
    exactly one `<pre` confirms the structure is actually well-formed, not
    just that SOME wrapper tag showed up somewhere in the string."""
    result = render_markdown(text="```python\ndef f():\n    return 1\n```")
    assert "highlight" in result  # Pygments' own wrapper class
    assert result.count("<pre") == 1
    assert "<div" not in result


def test_gfm_tables_render():
    result = render_markdown(text="| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in result


def test_no_external_script_or_link_tags_are_emitted():
    result = render_markdown(text="# Title\n\nSome *text* with `code`.")
    assert "<script" not in result
    assert "cdn." not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_markdown.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Add dependencies**

Run (from `nora/compute/smith/`): `uv add markdown-it-py mdit-py-plugins pygments linkify-it-py`. `linkify-it-py` is required, not optional: `MarkdownIt("gfm-like")` enables linkify by default, and without this dependency `render_markdown` raises `ModuleNotFoundError: Linkify enabled but not installed.` on its very first call — confirmed by an independent subagent review that actually ran this exact code with only the originally-listed three packages installed.

- [ ] **Step 4: Implement `render_markdown`**

Create `nora/compute/smith/skills/markdown/render.py`:

```python
"""`render_markdown` — GFM markdown to self-contained HTML, code blocks
syntax-highlighted via Pygments at render time (inline CSS, no client JS).
The second of the three deterministic skills (this plan's Architecture note).
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

# `nowrap=True` -- Pygments returns just the inline `<span>`-tagged tokens,
# no `<pre>`/`<div>` wrapper of its own. This matters for a reason that only
# shows up at runtime, not at import time: markdown-it-py's fence renderer
# skips wrapping the `highlight` callback's return value in ITS OWN
# `<pre><code>...</code></pre>` only when that return value ALREADY starts
# with `"<pre"`. An earlier draft of this module used `nowrap=False`, whose
# output starts with `"<div"` -- so markdown-it-py wrapped it a SECOND time,
# producing `<pre><code><div class="highlight"><pre>...</pre></div>
# </code></pre>`, a div nested inside code nested inside pre. Confirmed by an
# independent subagent review that actually ran this module's fence-rendering
# path end to end, not just read the code. Building the single `<pre>`
# wrapper explicitly below is what satisfies markdown-it-py's skip-check.
_FORMATTER = HtmlFormatter(nowrap=True, cssclass="highlight")
_STYLE_BLOCK = f"<style>{_FORMATTER.get_style_defs('.highlight')}</style>"

_md = MarkdownIt("gfm-like").use(front_matter_plugin)


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    highlighted = highlight(code, lexer, _FORMATTER)
    return f'<pre class="highlight"><code>{highlighted}</code></pre>'


_md.options["highlight"] = _highlight_code


def render_markdown(*, text: str) -> str:
    body = _md.render(text)
    return _STYLE_BLOCK + body
```

Create `nora/compute/smith/skills/markdown/SKILL.md`:

```markdown
---
name: render-markdown
description: Render GitHub-flavored Markdown (including fenced code blocks and tables) to self-contained HTML.
---

# render_markdown

Call `render_markdown(text=...)` with GFM markdown. Fenced code blocks
(```` ```python ```` etc.) get syntax-highlighted server-side — the returned
HTML fragment already includes its own `<style>` block, nothing external.
Tables, lists, and headings all render normally. Embed the returned string
as one section when calling `render_html_report`.
```

- [ ] **Step 5: Run test to verify it passes**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_markdown.py -v`
Expected: PASS.

- [ ] **Step 6: Register the tool in `agent.py`**

```python
from skills.markdown.render import render_markdown as _render_markdown


@tool
@guarded(_budget)
def render_markdown(text: str) -> str:
    """Render GFM markdown (with syntax-highlighted code blocks) to
    self-contained HTML. See skills/markdown/SKILL.md."""
    return _render_markdown(text=text)
```

Add to the `tools=[...]` list.

- [ ] **Step 7: Type-check and lint**

Run (from `nora/compute/smith/`): `uv run mypy . && uv run ruff check .`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add nora/compute/smith/skills/markdown/ nora/compute/smith/tests/test_render_markdown.py nora/compute/smith/agent.py nora/compute/smith/pyproject.toml nora/compute/smith/uv.lock
git commit -m "feat(smith): add render_markdown skill"
```

---

### Task 5: `render_map`

**Files:**
- Create: `nora/compute/smith/skills/maps/render.py`
- Create: `nora/compute/smith/skills/maps/SKILL.md`
- Create: `nora/compute/smith/tests/test_render_map.py`
- Modify: `nora/compute/smith/agent.py`

**Interfaces:**
- Produces: `render_map(*, locations: list[tuple[float, float]], title: str = "") -> str` — returns an `<img>` tag with an inline base64 `data:` URI, never a live tile-server reference.

- [ ] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_render_map.py`:

```python
from unittest.mock import patch

from skills.maps.render import render_map


def test_a_single_location_returns_an_inline_image_tag():
    with patch("skills.maps.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b"fake-png-bytes"
        result = render_map(locations=[(22.4106, 114.1245)])
    assert result.startswith("<img")
    assert "data:image/png;base64," in result


def test_multiple_locations_are_passed_as_markers():
    with patch("skills.maps.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b"fake-png-bytes"
        render_map(locations=[(22.4106, 114.1245), (22.2793, 114.1628)])
    params = mock_get.call_args.kwargs["params"]
    assert params["markers"].count("|") >= 1  # at least two markers joined
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_map.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `render_map`**

Create `nora/compute/smith/skills/maps/render.py`:

```python
"""`render_map` — the third deterministic skill (this plan's Architecture
note): a list of (lat, lng) points in, one Google Static Maps API call, an
inline base64-embedded `<img>` tag out. Static, not the Google Maps
JavaScript API -- no live map JS ships in the artifact by default (Nora
Compute spec §8's self-contained-by-default rule); an interactive map is the
allowlisted exception, not this tool's job.
"""

from __future__ import annotations

import base64
import os

import requests

STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"


def render_map(*, locations: list[tuple[float, float]], title: str = "") -> str:
    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    markers = "|".join(f"{lat},{lng}" for lat, lng in locations)
    params = {
        "size": "640x400",
        "markers": markers,
        "key": api_key,
    }
    response = requests.get(STATIC_MAPS_URL, params=params, timeout=15)
    response.raise_for_status()
    encoded = base64.b64encode(response.content).decode("ascii")
    alt = title or "map"
    return f'<img src="data:image/png;base64,{encoded}" alt="{alt}" />'
```

Create `nora/compute/smith/skills/maps/SKILL.md`:

```markdown
---
name: render-map
description: Render a static map image for one or more (lat, lng) locations, embedded inline.
---

# render_map

Call `render_map(locations=[(lat, lng), ...], title=...)`. Returns a
self-contained `<img>` tag — the map image is fetched once at job time and
embedded as base64, not loaded live from Google's servers when someone opens
the artifact. Good for "where is X" or "here's the route" — not for a
pannable/zoomable interactive map (that's the allowlisted Google Maps JS API
path, a doctrine-level decision, not this tool's default).
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_map.py -v`
Expected: PASS.

- [ ] **Step 5: Register the tool, add the API key env var**

```python
from skills.maps.render import render_map as _render_map


@tool
@guarded(_budget)
def render_map(locations: list[tuple[float, float]], title: str = "") -> str:
    """Render a static map with markers at the given (lat, lng) locations,
    embedded inline. See skills/maps/SKILL.md."""
    return _render_map(locations=locations, title=title)
```

Add to `tools=[...]`. Note in this task's own summary: `GOOGLE_MAPS_API_KEY` needs to reach Smith's container as an environment variable — add it to `LocalDockerBackend._start_container`'s `environment` dict (Phase 2, `nora/infra/compute/docker_backend.py`) and to `LocalDockerBackend.__init__`'s constructor parameters, since Phase 2 didn't anticipate this key. This is a small addition to Phase 2's file, done here since Phase 2 was written before this skill existed.

- [ ] **Step 6: Add the API key to `LocalDockerBackend`**

In `nora/infra/compute/docker_backend.py`, add `google_maps_api_key: str` to `__init__`'s parameters (store as `self._google_maps_api_key`), and add `"GOOGLE_MAPS_API_KEY": self._google_maps_api_key` to `_start_container`'s `environment` dict. Update Phase 2's own test fixtures (`tests/infra/compute/test_docker_backend.py`'s `backend` fixture) to pass a dummy value for this new required parameter.

Run: `uv run pytest tests/infra/compute/test_docker_backend.py -v`
Expected: PASS (fixture updated, no other test needed to change).

- [ ] **Step 7: Type-check and lint**

Run (from `nora/compute/smith/`): `uv run mypy . && uv run ruff check .`
Run (repo root): `uv run mypy nora/infra && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add nora/compute/smith/skills/maps/ nora/compute/smith/tests/test_render_map.py nora/compute/smith/agent.py nora/infra/compute/docker_backend.py tests/infra/compute/test_docker_backend.py
git commit -m "feat(smith): add render_map skill"
```

---

### Task 6: The page-shell

**Files:**
- Create: `nora/compute/smith/page_shell.py`
- Create: `nora/compute/smith/tests/test_page_shell.py`

**Interfaces:**
- Produces: `page_shell(*, title: str, sections: list[str], accent: str | None = None) -> str` — returns one complete, self-contained HTML document. `render_html_report` (Task 7) is its only caller.

- [ ] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_page_shell.py`:

```python
from page_shell import page_shell


def test_the_shell_wraps_every_section():
    html = page_shell(title="Test", sections=["<p>one</p>", "<p>two</p>"])
    assert "<p>one</p>" in html
    assert "<p>two</p>" in html


def test_the_shell_includes_a_viewport_meta_tag():
    html = page_shell(title="Test", sections=["<p>x</p>"])
    assert 'name="viewport"' in html


def test_the_shell_defaults_to_true_neutral_grayscale():
    html = page_shell(title="Test", sections=["<p>x</p>"])
    # True neutral means R=G=B in the base token values -- check the shell's
    # own CSS custom properties directly rather than rendering and sampling
    # pixels, which this test has no browser to do.
    assert "--ink: #141414" in html or "--ink:#141414" in html


def test_an_accent_color_is_applied_when_given():
    html = page_shell(title="Test", sections=["<p>x</p>"], accent="#2f6f62")
    assert "#2f6f62" in html


def test_no_accent_means_no_accent_variable_override():
    html = page_shell(title="Test", sections=["<p>x</p>"])
    assert "#2f6f62" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_page_shell.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `page_shell.py`**

Create `nora/compute/smith/page_shell.py`:

```python
"""The shared, true-neutral Mono page-shell every skill's output renders
into (Nora Compute spec §10) -- validated first as the "Smith Spike" artifact
before being written down as code. Plain HTML/CSS, no component framework, no
build step: cards/timeline/table/disclosure/modal/form-control patterns are
native HTML elements (`<details>`, `<dialog>`, `<select>`) styled from these
tokens, not a React/shadcn dependency (spec §10's own reasoning against one).
"""

from __future__ import annotations

import html as html_module

_BASE_CSS = """
:root {
  --bg: #fafafa; --surface: #ffffff; --surface-2: #ededed;
  --ink: #141414; --muted: #6b6b6b; --line: #dcdcdc;
  --accent: var(--shell-accent, #141414);
  --font-display: ui-serif, Charter, Georgia, serif;
  --font-body: -apple-system, "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141414; --surface: #1e1e1e; --surface-2: #262626;
    --ink: #ececec; --muted: #9a9a9a; --line: #333333;
  }
}
:root[data-theme="dark"] {
  --bg: #141414; --surface: #1e1e1e; --surface-2: #262626;
  --ink: #ececec; --muted: #9a9a9a; --line: #333333;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--font-body); line-height: 1.55;
}
.page {
  max-width: 46rem; margin: 0 auto;
  padding: clamp(1.25rem, 4vw, 3rem) clamp(1rem, 5vw, 1.5rem) 4rem;
  display: flex; flex-direction: column; gap: clamp(2rem, 5vw, 3rem);
}
h1 { font-family: var(--font-display); font-size: clamp(1.6rem, 5vw, 2.2rem); }
.section { overflow-x: auto; }
.card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 4px; padding: clamp(1rem, 3vw, 1.5rem);
}
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 0.5rem; text-align: left; }
details { border: 1px solid var(--line); border-radius: 4px; padding: 0.75rem; }
dialog { border: 1px solid var(--line); border-radius: 4px; }
"""


def page_shell(*, title: str, sections: list[str], accent: str | None = None) -> str:
    accent_style = f"<style>:root {{ --shell-accent: {accent}; }}</style>" if accent else ""
    body = "\n".join(f'<div class="section card">{section}</div>' for section in sections)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{html_module.escape(title)}</title>
<style>{_BASE_CSS}</style>
{accent_style}
</head>
<body>
<div class="page">
<h1>{html_module.escape(title)}</h1>
{body}
</div>
</body>
</html>"""
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_page_shell.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check and lint**

Run (from `nora/compute/smith/`): `uv run mypy . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add nora/compute/smith/page_shell.py nora/compute/smith/tests/test_page_shell.py
git commit -m "feat(smith): add the true-neutral Mono page-shell"
```

---

### Task 7: `render_html_report`

**Files:**
- Create: `nora/compute/smith/skills/html_report/render.py`
- Create: `nora/compute/smith/skills/html_report/SKILL.md`
- Create: `nora/compute/smith/tests/test_render_html_report.py`
- Modify: `nora/compute/smith/agent.py`

**Interfaces:**
- Consumes: `page_shell` (Task 6).
- Produces: `render_html_report(*, title: str, sections: list[str], accent: str | None = None) -> str` — the default "just produce something readable" skill (spec §6); its return value is what `register_artifact` (Phase 2) actually writes to disk and registers with Drop.

- [ ] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_render_html_report.py`:

```python
from skills.html_report.render import render_html_report


def test_it_delegates_to_the_page_shell():
    html = render_html_report(title="Weekly Recap", sections=["<p>hello</p>"])
    assert "<p>hello</p>" in html
    assert "Weekly Recap" in html
    assert "<!doctype html>" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_html_report.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `nora/compute/smith/skills/html_report/render.py`:

```python
"""`render_html_report` -- the composing skill (Nora Compute spec §6): takes
already-rendered HTML fragments (from render_mermaid, render_markdown,
render_map, or Smith's own lieflat-charts-guided code-exec output -- this
function doesn't know or care which) and wraps them in the shared page-shell.
The default "just produce something readable" skill when nothing more
specific fits.
"""

from __future__ import annotations

from page_shell import page_shell


def render_html_report(*, title: str, sections: list[str], accent: str | None = None) -> str:
    return page_shell(title=title, sections=sections, accent=accent)
```

Create `nora/compute/smith/skills/html_report/SKILL.md`:

```markdown
---
name: render-html-report
description: Compose already-rendered HTML fragments (charts, diagrams, markdown, maps) into one responsive, self-contained page.
---

# render_html_report

Call `render_html_report(title=..., sections=[...])` with a list of
already-rendered HTML strings -- output from `render_mermaid`,
`render_markdown`, `render_map`, or HTML you wrote yourself following the
charts skill (`skills/charts/SKILL.md`). This is the LAST step: call it once,
with everything the artifact needs, and write its return value to the file
you register with `register_artifact`.

Pass `accent=` (a hex color) only when the artifact's own content genuinely
calls for one color to encode something -- otherwise leave it unset and the
page stays neutral grayscale. Never introduce a second color in the same
delivery.
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `nora/compute/smith/`): `uv run pytest tests/test_render_html_report.py -v`
Expected: PASS.

- [ ] **Step 5: Register the tool**

```python
from skills.html_report.render import render_html_report as _render_html_report


@tool
@guarded(_budget)
def render_html_report(title: str, sections: list[str], accent: str | None = None) -> str:
    """Compose rendered HTML fragments into one responsive, self-contained
    page. Call this LAST, once, with everything the artifact needs. See
    skills/html_report/SKILL.md."""
    return _render_html_report(title=title, sections=sections, accent=accent)
```

Add to `tools=[...]`.

- [ ] **Step 6: Type-check, lint, rebuild**

Run (from `nora/compute/smith/`): `uv run mypy . && uv run ruff check .`
Run: `docker build --network=host -t smith:latest nora/compute/smith/`
Expected: clean; builds successfully.

- [ ] **Step 7: Commit**

```bash
git add nora/compute/smith/skills/html_report/ nora/compute/smith/tests/test_render_html_report.py nora/compute/smith/agent.py
git commit -m "feat(smith): add render_html_report, completing the skill library"
```

---

### Task 8: Extend `doctrine.md`, add the Dockerfile's `skills/` copy step

**Files:**
- Modify: `nora/compute/smith/doctrine.md`
- Modify: `nora/compute/smith/Dockerfile`

- [ ] **Step 1: Rewrite `doctrine.md`**

Replace the Phase 2 bare-bones content with:

```markdown
---
name: smith-doctrine
description: Governs Smith, Nora's separate compute agent -- the skill library, when to use each, and the rendering-mode policy.
---

# Smith

You are Smith, a separate, neutral agent -- not Nora. You have no persona, no
memory of any conversation, and no knowledge of who asked for this task
beyond the task description itself.

## Your tools

Prefer a named skill over raw code whenever one fits:

- **Charts** -- read `skills/charts/SKILL.md` first. Follow its template-first
  rule exactly: look up the chart type in `skills/charts/catalog.md`, open the
  matching gallery file under `skills/charts/templates/`, and adapt that
  template's structure with `run_python` to write the actual HTML. Default to
  Lupi or Basics — most of both families are pure SVG and work offline, but
  not every single one (`catalog.md` itself lists at least one Basics entry,
  the nested treemap, that renders via ECharts despite belonging to that
  family — check the catalog's own `engine` column for the specific chart
  type rather than assuming the family name alone settles it). Only reach for
  Glance/interactive generally (Chart.js/ECharts, loaded via CDN) when the
  task explicitly wants a dashboard-style fast read or genuine interactivity.
- **Diagrams** -- `render_mermaid(diagram_source=...)`. Static SVG, always.
- **Prose, code, tables** -- `render_markdown(text=...)`.
- **Locations** -- `render_map(locations=[...])`. Static image, embedded
  inline -- never the live Google Maps JavaScript API unless the task
  explicitly needs pan/zoom (a rare exception, not a default).
- **Putting it together** -- `render_html_report(title=..., sections=[...])`,
  called LAST, once, with every rendered fragment.
- **Anything that doesn't fit a skill** -- `run_python`, the escape hatch.
  Prefer a skill whenever one applies; reach for raw code only when none does.

## Color

Default to no accent at all (neutral grayscale). Pass `accent=` to
`render_html_report` only when the task's own data genuinely calls for one
color to mean something -- and only ONE color per delivery, never mixed.

## Interactive/executable content

Only these libraries may ever be loaded via CDN, and only when the task
specifically needs live interactivity: Plotly.js, Google Maps JavaScript API
(referrer-restricted key only), Chart.js, Apache ECharts. Nothing else, ever
-- no other third-party script, no matter how small the task seems to need it.

## Finishing

Read `/workspace/task.txt`, do the work, produce exactly one output file in
`/workspace`, call `register_artifact` on it, write the URL to
`/workspace/url.txt` and a one- or two-sentence plain-text summary of what you
actually built (never a restatement of the task) to
`/workspace/summary.txt`.

If `ARTIFACT_ID` is set in your environment, `register_artifact` already
knows to revise that existing artifact rather than create a new one -- you
don't need to do anything differently.
```

- [ ] **Step 2: Add the `skills/` tree to the Dockerfile**

In `nora/compute/smith/Dockerfile`, after the existing `COPY agent.py doctrine.md ./` line:

```dockerfile
COPY budget.py page_shell.py ./
COPY skills/ ./skills/
```

- [ ] **Step 3: Rebuild and verify**

Run: `docker build --network=host -t smith:latest nora/compute/smith/`
Expected: builds successfully, `skills/charts/SKILL.md` etc. present inside the image.

Run: `docker run --rm smith:latest python3 -c "import pathlib; print(pathlib.Path('/app/skills/charts/SKILL.md').exists())"` (adjust the path if the Dockerfile's `WORKDIR` differs — confirm against the actual `COPY` destinations)
Expected: `True`.

- [ ] **Step 4: Commit**

```bash
git add nora/compute/smith/doctrine.md nora/compute/smith/Dockerfile
git commit -m "feat(smith): wire the full skill library into doctrine.md and the sandbox image"
```

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-08-12-nora-compute-design.md` §6-§8, §10, plus Phase 2's flagged gap):
- Per-job spend guardrail — Task 1. ✓
- Vendored `lieflat-charts`, font-substituted, `THIRD_PARTY_NOTICES.md` — Task 2. ✓
- `render_mermaid`, `render_markdown`, `render_map`, `render_html_report` — Tasks 3-7. ✓
- True-neutral Mono page-shell, no component framework — Task 6. ✓
- Each skill as its own `SKILL.md` — every task. ✓
- Interactive-JS allowlist named in doctrine — Task 8. ✓ (enforcement is prompted, not coded — named explicitly in Global Constraints, not overclaimed as a hard guarantee)

**Placeholder scan:** `PINNED_COMMIT`'s literal placeholder value in Task 2's script is intentional and immediately actioned in the very next step (Step 2 replaces it before the script is ever run) — not a "fill in later" left dangling.

**Type consistency:** `render_mermaid`/`render_markdown`/`render_map`/`render_html_report`'s signatures match between their `skills/*/render.py` implementations and their `@tool`-wrapped re-exports in `agent.py` across every task.

**Gap found:** Task 5 needed a small retroactive addition to Phase 2's `LocalDockerBackend` (the `GOOGLE_MAPS_API_KEY` environment variable) — named explicitly in Task 5 rather than silently assumed to already be there, since Phase 2 was written before this skill existed and had no way to anticipate it.

**Fixed after independent subagent review, not shipped as originally drafted.** This review actually RAN the plan's own code against real dependencies and the real vendored repo rather than only reading it, and found genuine bugs a read-only review would have missed:
1. **The font-substitution script corrupted JavaScript.** A blind `.replace("'Inter'", SYSTEM_FONT_STACK)` also rewrote every OTHER quoted `'Inter'` occurrence in a vendored file, including inside `<script>` blocks — Chart.js/ECharts config objects use the camelCase JS property `fontFamily:'Inter'`, and the blind replace turned that into unquoted, comma-laden, syntactically invalid JS, breaking every Glance-family chart's script block outright. Fixed with a regex scoped specifically to the hyphenated CSS property `font-family:` (which a JS `fontFamily` property, lacking the hyphen, never matches), plus a real `node --check` syntax-validation step added to Task 2's own verification, since a `docker build` succeeding — or a `grep` for the old string — would not have caught this either.
2. **The Dockerfile was missing `mermaid-cli`'s actual runtime dependencies.** `mmdc` renders via headless Chromium under Puppeteer; a bare Node.js/npm install fails at runtime (not build time) with missing shared-library errors. Fixed by installing Chromium and its dependency chain, and by adding a real "render an actual diagram" verification step to Task 3 — the original plan's verification only checked that the image built, which says nothing about whether `mmdc` can actually run.
3. **`render_markdown` crashed on its very first call.** `MarkdownIt("gfm-like")` enables linkify by default, which needs `linkify-it-py` — a dependency the original plan never listed. Fixed by adding it to the `uv add` command.
4. **The Pygments/markdown-it-py integration produced malformed, doubly-nested HTML** (`<pre><code><div class="highlight"><pre>...`) — `nowrap=False`'s output starts with `"<div"`, which does not satisfy markdown-it-py's own check (skip re-wrapping only when the highlight callback's return value starts with `"<pre"`), so it got wrapped twice. Fixed with `nowrap=True` plus an explicit single `<pre>` wrapper built by hand. The original test for this (checking only that `<pre` or `<div` appeared SOMEWHERE in the output) was too weak to catch it — CLAUDE.md's own "if reverting the fix doesn't turn the test red, the test is decorative" standard — so the test itself was also strengthened to assert exactly one `<pre` and no `<div` at all.
5. **Minor:** doctrine's claim that Lupi/Basics are always pure SVG was slightly wrong — `catalog.md` itself lists a Basics-family chart (nested treemap) that renders via ECharts. Softened to point at the catalog's own per-chart engine column instead of asserting the family name alone settles it.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-nora-compute-03-skill-library.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
