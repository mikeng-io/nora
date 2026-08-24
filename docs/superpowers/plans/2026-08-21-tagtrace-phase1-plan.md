# TagTrace extraction + retention (Phase 1 of Standing/debrief) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the completed `agent.messages` from every tag turn as a
sanitised, capped `TagTrace`, and retain it durably with a re-derivation path
— nothing folds yet (that's Phase 2). This is the sequencing gate: `Standing`
must not ship before retention exists, or an early bad fold is unrecoverable
(§14.6).

**Architecture:** A new domain type (`TagTrace`) plus an extraction function
living in `nora/agent/` (the only layer allowed to touch Strands `Message`
shapes — `tests/test_boundaries.py::test_strands_import_boundary` restricts
raw SDK contact to `nora.agent.**`/`nora.compute.**`). `LiveAgentRunner.invoke`
(`nora/app/agent_runner.py`) is the one place that holds both the live
`Agent` object and the moment its turn finishes, so extraction happens there,
immediately after `factory.invoke(...)` returns, using the SAME `agent`
instance. The extracted `TagTrace` rides back up through a widened
`AgentRunner.invoke` return tuple to `nora/engagement/pipeline.py`, which
persists it via a new store (`TagTraceStore`, migration after `0027`).

**Tech Stack:** Python 3.13, SQLAlchemy Core + asyncpg (matches
`nora/infra/sql/facts.py`/`notes.py`), pytest, Strands SDK 1.50.2 (read-only
reference — see `.venv/lib/python3.13/site-packages/strands/event_loop/`).

**Spec:** `docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md`
§6.1 (schema), §6.5 (bounds), §8.1 (where the code lives), §14.6
(sequencing). Phase-level task list:
`docs/superpowers/plans/2026-08-21-standing-debrief-plan.md` Phase 1.

## Global Constraints

- `messages` is captured WHOLE, never semantically filtered — only sanitised
  (three named synthetic artifacts) and size-capped (§6.5). No curated
  `asked`/`replied`/`reasoning` fields — an earlier draft did this and the
  spec explicitly rejects it (§6.1: destroys the sequence reasoning lives in).
- `prior` (the previous `Standing`) is **not** a `TagTrace` field — `TagTrace`
  is built at the `nora/agent/` extraction boundary, which has no storage
  access. It is the fold's second argument in Phase 2, not part of this type.
- Defensive throughout: a renamed/missing SDK field degrades to an empty
  trace and never raises into a turn that otherwise succeeded
  (`agent_runner.py`'s existing `_final_text`-style discipline).
- Only `nora.agent.**`/`nora.compute.**` may import `strands` directly
  (`tests/test_boundaries.py::test_strands_import_boundary`). `TagTrace`
  itself and everything downstream of extraction must hold **no SDK types** —
  plain `dict`/`str`/`int`.
- Migration parent: **`0027_fact_candidate_provenance`** is the current head
  (verified `uv run alembic heads` prints exactly one line). Announce the new
  migration's number and parent before creating it — re-check
  `design/09-decision-index.md`/migration heads at merge time, not now.
- Test-seam policy: collaborators through `__init__` only. No `*_fn` hooks.

---

## File Structure

| file | responsibility |
|---|---|
| `nora/domain/tag_trace.py` | `TagTrace` dataclass — frozen, no SDK types, no storage access |
| `nora/agent/tag_trace_extract.py` | `extract_tag_trace(...)`: takes the live Strands `Agent`, returns a sanitised/capped `TagTrace`. The one module allowed to read `agent.messages` |
| `nora/app/agent_runner.py` (modify) | `LiveAgentRunner.invoke` calls the extractor after `factory.invoke`, returns the trace up the chain |
| `nora/engagement/stages/invoke.py` (modify) | `AgentRunner` Protocol widened to return the trace; fake counterparts widen too |
| `nora/testing/scripted_agent.py` (modify) | `FakeAgentRunner`/`ScriptedRunner` return an empty-but-valid `TagTrace` |
| `nora/infra/sql/tag_traces.py` | `SqlTagTraceStore` — insert + read-back-by-conversation for the re-derivation path |
| `nora/infra/sql/migrations/versions/0028_tag_trace.py` | the `tag_trace` table |
| `nora/infra/sql/schema.py` (modify) | `TAG_TRACE` table definition, kept in parity with the migration |
| `nora/obs/metric_specs.py` (modify) | `nora_tagtrace_truncations_total{what}` |
| `tests/domain/test_tag_trace.py` | `TagTrace` construction/caps |
| `tests/unit/test_tag_trace_extract.py` | extraction + sanitisation + slice-offset, against a scripted fake `Agent`-shaped object (no real Strands import needed for the unit test — see Task 2) |
| `tests/infra/test_tag_trace_store.py` | store round-trip, against a real Postgres (module-scoped db per CLAUDE.md's contract-suite rule) |
| `tests/infra/test_schema_parity.py` (modify) | add `tag_trace` to the parity check |

---

## Task 1: `TagTrace` domain type

**Files:**
- Create: `nora/domain/tag_trace.py`
- Test: `tests/domain/test_tag_trace.py`

**Interfaces:**
- Produces: `TagTrace` (frozen dataclass) — `tag_id: str`, `conversation_id: str`,
  `messages: tuple[dict, ...]`, `transcript: tuple[TranscriptRow, ...]`,
  `outcome: str`, `created_at: int` (epoch ms, matches the fact/note tiers'
  convention). `messages` and `transcript` are tuples, not lists — this is a
  `nora/domain/` type and those are frozen (matches `NoteRevision.entity_ids`'
  own `tuple[str, ...]` convention).
- `outcome` is one of `"send_reply" | "recovered" | "silent" | "vetoed" |
  "timeout" | "llm_error"` (§6.1) — validated in `__post_init__`, not typed as
  a `Literal` (the codebase's existing convention for these string-enum
  fields, e.g. `FactVerdict.status`).

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_tag_trace.py
import pytest
from nora.domain.tag_trace import TagTrace, VALID_OUTCOMES

def test_a_tag_trace_holds_messages_transcript_and_outcome():
    trace = TagTrace(
        tag_id="t1", conversation_id="c1",
        messages=({"role": "user", "content": [{"text": "hi"}]},),
        transcript=(), outcome="send_reply", created_at=1_723_000_000_000,
    )
    assert trace.tag_id == "t1"
    assert trace.outcome == "send_reply"
    assert trace.messages == ({"role": "user", "content": [{"text": "hi"}]},)

def test_an_unknown_outcome_is_refused():
    with pytest.raises(ValueError, match="outcome"):
        TagTrace(
            tag_id="t1", conversation_id="c1", messages=(), transcript=(),
            outcome="made_up", created_at=1_723_000_000_000,
        )

def test_valid_outcomes_matches_the_spec_six():
    assert VALID_OUTCOMES == frozenset({
        "send_reply", "recovered", "silent", "vetoed", "timeout", "llm_error",
    })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/domain/test_tag_trace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nora.domain.tag_trace'`

- [ ] **Step 3: Write minimal implementation**

```python
# nora/domain/tag_trace.py
"""`TagTrace` — one completed tag turn, captured whole for the debrief to
fold and for retention to rebuild from (spec
docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
§6.1, §14.6).

`messages` is `agent.messages[baseline:]`, sanitised and capped by
`nora.agent.tag_trace_extract.extract_tag_trace` -- never semantically
filtered here or there. `prior` (the previous `Standing`) is deliberately NOT
a field: this type is built at the `nora/agent/` extraction boundary, which
has no storage access, so `prior` is the fold's second argument in Phase 2.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import TranscriptRow

VALID_OUTCOMES = frozenset({
    "send_reply", "recovered", "silent", "vetoed", "timeout", "llm_error",
})


@dataclass(frozen=True, slots=True)
class TagTrace:
    tag_id: str
    conversation_id: str
    messages: tuple[dict, ...]
    transcript: tuple[TranscriptRow, ...]
    outcome: str
    created_at: int

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(VALID_OUTCOMES)}, got "
                f"{self.outcome!r}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/domain/test_tag_trace.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-standing-debrief
git add nora/domain/tag_trace.py tests/domain/test_tag_trace.py
git commit -m "feat(reflection): add the TagTrace domain type"
```

---

## Task 2: sanitisation + capping helpers (pure functions, no Strands import)

Building these BEFORE the extractor that calls them, so they get their own
focused unit tests against plain dicts — no live `Agent` object needed yet.

**Files:**
- Create: `nora/agent/tag_trace_sanitize.py`
- Test: `tests/unit/test_tag_trace_sanitize.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except stdlib.
- Produces: `strip_synthetic_artifacts(messages: list[dict]) -> list[dict]`,
  `cap_messages(messages: list[dict], *, max_chars: int) -> tuple[list[dict], int]`
  (returns `(capped_messages, truncated_count)` — the count is what Task 3's
  metric reads).

**What "synthetic" means, concretely** (verified against
`.venv/lib/python3.13/site-packages/strands/event_loop/event_loop.py` and
`_recover_message_on_max_tokens_reached.py`, Strands 1.50.2):

1. A guard-cancelled model call synthesises
   `{"role": "assistant", "content": [{"text": <cancel_text>}]}` where
   `cancel_text` is `ReplyGuard`'s veto string (e.g. `"Reply already sent —
   the turn is finished."`) or the generic `"model call denied by hook"`.
   Drop any assistant message whose `content` is exactly one `{"text": ...}`
   block equal to one of `ReplyGuard`'s known veto strings, OR the literal
   `"model call denied by hook"`.
2. A cancelled tool call synthesises a `{"role": "user", "content": [{"toolResult":
   {"status": "error", "content": [{"text": "Tool execution cancelled"}], ...}}]}`
   message. Drop any `toolResult` content block whose `content` is exactly
   `[{"text": "Tool execution cancelled"}]`.
3. A max-tokens recovery replaces a `toolUse` block with
   `{"text": f"The selected tool {name}'s tool use was incomplete due to
   maximum token limits being reached."}`. Drop any text block matching that
   exact template (regex on the fixed prefix/suffix, tool name is the
   variable part).

Each drop is a full content-block removal, never a partial edit — if dropping
a block empties a message's `content` entirely, drop the whole message (an
empty-content message is not a valid turn to fold over).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tag_trace_sanitize.py
from nora.agent.tag_trace_sanitize import strip_synthetic_artifacts, cap_messages

def test_the_reply_guard_veto_message_is_dropped():
    messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [
            {"text": "Reply already sent — the turn is finished."}
        ]},
    ]
    out = strip_synthetic_artifacts(messages)
    assert out == [{"role": "user", "content": [{"text": "hi"}]}]

def test_a_cancelled_tool_result_block_is_dropped():
    messages = [{
        "role": "user",
        "content": [{"toolResult": {
            "toolUseId": "x", "status": "error",
            "content": [{"text": "Tool execution cancelled"}],
        }}],
    }]
    assert strip_synthetic_artifacts(messages) == []

def test_a_max_tokens_recovery_placeholder_is_dropped():
    messages = [{
        "role": "assistant",
        "content": [
            {"text": "real reasoning"},
            {"text": (
                "The selected tool calculator's tool use was incomplete due "
                "to maximum token limits being reached."
            )},
        ],
    }]
    out = strip_synthetic_artifacts(messages)
    assert out == [{"role": "assistant", "content": [{"text": "real reasoning"}]}]

def test_real_content_survives_untouched():
    messages = [
        {"role": "user", "content": [{"text": "what's the weather"}]},
        {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "let me check"}}},
            {"toolUse": {"name": "weather", "toolUseId": "1", "input": {}}},
        ]},
    ]
    assert strip_synthetic_artifacts(messages) == messages

def test_cap_messages_truncates_assistant_narration_and_pins_human_turns():
    # A long assistant text block gets truncated; the user's own words never do.
    long_text = "x" * 5000
    messages = [
        {"role": "user", "content": [{"text": long_text}]},
        {"role": "assistant", "content": [{"text": long_text}]},
    ]
    capped, truncated = cap_messages(messages, max_chars=1000)
    assert capped[0]["content"][0]["text"] == long_text  # user turn: untouched
    assert len(capped[1]["content"][0]["text"]) < len(long_text)  # assistant: cut
    assert truncated == 1

def test_cap_messages_reports_zero_truncations_when_nothing_is_cut():
    messages = [{"role": "user", "content": [{"text": "hi"}]}]
    capped, truncated = cap_messages(messages, max_chars=1000)
    assert truncated == 0
    assert capped == messages
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/unit/test_tag_trace_sanitize.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# nora/agent/tag_trace_sanitize.py
"""Strip synthetic (non-Nora-authored) artifacts from `agent.messages` before
they fold into a `TagTrace`, and cap the result to a token-ish character
budget -- truncating assistant narration, never a human's own words (spec
§6.5, quoting Hermes's micro-compaction note: intent cannot be reconstructed
from the work that followed).

Verified against Strands 1.50.2
(`strands/event_loop/event_loop.py`, `_recover_message_on_max_tokens_reached.py`)
-- three synthetic shapes, listed in `_GUARD_VETO_STRINGS` and the two
constant-text matches below. A future SDK bump may change these; this module
degrades to "nothing stripped" rather than raising if a shape is absent, per
`nora.agent.tag_trace_extract`'s defensive-throughout rule.
"""

from __future__ import annotations

import re

# ReplyGuard's own veto strings that can reach agent.messages as a synthesised
# assistant turn when BeforeModelCallEvent.cancel fires. Not imported from
# nora.agent.guards to avoid a runtime dependency on that module's internal
# string literals; kept here as the sanitizer's own contract, pinned by the
# test above quoting one verbatim.
_GUARD_VETO_STRINGS = frozenset({
    "Reply already sent — the turn is finished.",
    "model call denied by hook",
})

_CANCELLED_TOOL_RESULT = [{"text": "Tool execution cancelled"}]

_MAX_TOKENS_PLACEHOLDER_RE = re.compile(
    r"^The selected tool .+'s tool use was incomplete due to maximum token "
    r"limits being reached\.$"
)


def _is_synthetic_text_block(block: dict) -> bool:
    text = block.get("text")
    if text in _GUARD_VETO_STRINGS:
        return True
    return isinstance(text, str) and bool(_MAX_TOKENS_PLACEHOLDER_RE.match(text))


def _is_synthetic_tool_result_block(block: dict) -> bool:
    result = block.get("toolResult")
    if not isinstance(result, dict):
        return False
    return result.get("content") == _CANCELLED_TOOL_RESULT


def strip_synthetic_artifacts(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for message in messages:
        content = message.get("content") or []
        kept = [
            block for block in content
            if not _is_synthetic_text_block(block)
            and not _is_synthetic_tool_result_block(block)
        ]
        if kept:
            out.append({**message, "content": kept})
    return out


def cap_messages(
    messages: list[dict], *, max_chars: int,
) -> tuple[list[dict], int]:
    """Truncate assistant text blocks over `max_chars`; leave user (human)
    turns untouched regardless of length. Returns `(capped, truncated_count)`
    -- the count feeds `nora_tagtrace_truncations_total`, never silent."""
    out: list[dict] = []
    truncated = 0
    for message in messages:
        if message.get("role") != "assistant":
            out.append(message)
            continue
        new_content = []
        message_truncated = False
        for block in message.get("content") or []:
            text = block.get("text")
            if isinstance(text, str) and len(text) > max_chars:
                new_content.append({**block, "text": text[:max_chars]})
                message_truncated = True
            else:
                new_content.append(block)
        if message_truncated:
            truncated += 1
        out.append({**message, "content": new_content})
    return out, truncated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/unit/test_tag_trace_sanitize.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-standing-debrief
git add nora/agent/tag_trace_sanitize.py tests/unit/test_tag_trace_sanitize.py
git commit -m "feat(reflection): sanitize + cap agent.messages for TagTrace"
```

---

## Task 3: the extractor — `extract_tag_trace`

**Files:**
- Create: `nora/agent/tag_trace_extract.py`
- Test: `tests/unit/test_tag_trace_extract.py`

**Interfaces:**
- Consumes: `TagTrace` (Task 1), `strip_synthetic_artifacts`/`cap_messages`
  (Task 2).
- Produces:
  `extract_tag_trace(agent, *, baseline: int, tag_id: str, conversation_id: str, transcript: tuple[TranscriptRow, ...], outcome: str, max_chars: int, metrics) -> TagTrace`
  — the ONE function `LiveAgentRunner.invoke` (Task 4) calls. `agent` is typed
  `Any` (never `strands.Agent`) for the same reason `nora.app.agent_runner`
  types `models: Any` — this keeps `strands` imports inside
  `nora.agent.factory`-adjacent modules while still letting `nora.agent.**`
  hold the real object. `baseline` is `len(agent.messages)` **read by the
  caller BEFORE `factory.invoke` runs** (§8.1's slice-offset rule) and handed
  in — this function does not compute it, to keep the "read before invoke"
  timing visible at the call site rather than hidden in here.

**Why baseline is a caller-supplied int, not computed here** (§8.1):
`agent.py:1281-1282`-equivalent mutation in Strands can rewrite `pre_fill`'s
dicts in place, and `BeforeInvocationEvent` can replace `messages` outright —
so the ONLY safe baseline is `len(agent.messages)` captured immediately after
`create_agent` and before `invoke_async`, which is `LiveAgentRunner.invoke`'s
job, not this function's.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tag_trace_extract.py
from types import SimpleNamespace

from nora.agent.tag_trace_extract import extract_tag_trace
from nora.obs.metrics import Metrics


def _agent(messages):
    return SimpleNamespace(messages=messages)


def test_extracts_only_messages_after_the_baseline():
    agent = _agent([
        {"role": "system", "content": [{"text": "old"}]},  # baseline=1, excluded
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ])
    trace = extract_tag_trace(
        agent, baseline=1, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="send_reply", max_chars=10_000, metrics=Metrics(),
    )
    assert trace.messages == (
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    )

def test_strips_synthetic_artifacts_before_returning():
    agent = _agent([
        {"role": "assistant", "content": [
            {"text": "Reply already sent — the turn is finished."}
        ]},
    ])
    trace = extract_tag_trace(
        agent, baseline=0, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="vetoed", max_chars=10_000, metrics=Metrics(),
    )
    assert trace.messages == ()

def test_a_missing_messages_attribute_degrades_to_an_empty_trace_not_a_raise():
    agent = SimpleNamespace()  # no .messages at all -- simulates a renamed SDK field
    trace = extract_tag_trace(
        agent, baseline=0, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="llm_error", max_chars=10_000, metrics=Metrics(),
    )
    assert trace.messages == ()

def test_counts_truncations_on_the_declared_metric():
    metrics = Metrics()
    long_text = "x" * 20_000
    agent = _agent([{"role": "assistant", "content": [{"text": long_text}]}])
    extract_tag_trace(
        agent, baseline=0, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="send_reply", max_chars=100, metrics=metrics,
    )
    assert metrics.snapshot_counter(
        "nora_tagtrace_truncations_total", what="assistant_narration",
    ) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/unit/test_tag_trace_extract.py -v`
Expected: FAIL, `ModuleNotFoundError`. (If `Metrics.snapshot_counter` does not
exist yet, check `nora/obs/metrics.py` for the actual read-back method name
used by other tests in `tests/unit/` and use that instead — do not invent one.)

- [ ] **Step 3: Declare the metric, then write the implementation**

First add to `nora/obs/metric_specs.py`'s counters tuple (append-only, per
`tests/test_metrics_export.py::DECLARED_NAMES` — never edit an existing
entry):

```python
CounterSpec(
    "nora_tagtrace_truncations_total",
    "TagTrace content dropped or cut before retention, by reason.",
    ("what",),
),
```

Then:

```python
# nora/agent/tag_trace_extract.py
"""The `nora/agent/` extraction boundary: turn a completed Strands `Agent`
into a `TagTrace` with no SDK types surviving past this module (spec §8.1).

Defensive throughout, for `agent_runner.py::_final_text`'s reason: a renamed
or absent SDK field degrades to an empty trace and never raises into a turn
that otherwise succeeded. This is a background-reasoning-continuity feature,
not the reply path -- it must never be the reason a tag fails.
"""

from __future__ import annotations

import logging
from typing import Any

from ..domain.models import TranscriptRow
from ..domain.tag_trace import TagTrace
from ..obs.metrics import Metrics
from .tag_trace_sanitize import cap_messages, strip_synthetic_artifacts

log = logging.getLogger("nora.agent.tag_trace")


def extract_tag_trace(
    agent: Any,
    *,
    baseline: int,
    tag_id: str,
    conversation_id: str,
    transcript: tuple[TranscriptRow, ...],
    outcome: str,
    max_chars: int,
    metrics: Metrics,
) -> TagTrace:
    try:
        raw = list(agent.messages)[baseline:]
    except Exception:
        log.warning(
            "tag_trace extraction: agent.messages unreadable (tag=%s); "
            "degrading to an empty trace", tag_id,
        )
        raw = []
    sanitized = strip_synthetic_artifacts(raw)
    capped, truncated = cap_messages(sanitized, max_chars=max_chars)
    if truncated:
        metrics.inc(
            "nora_tagtrace_truncations_total", value=truncated,
            what="assistant_narration",
        )
    return TagTrace(
        tag_id=tag_id, conversation_id=conversation_id,
        messages=tuple(capped), transcript=transcript, outcome=outcome,
        created_at=0,  # Task 4 stamps the real clock at the call site
    )
```

Note: `created_at=0` here is a placeholder the caller (Task 4) overwrites —
this function has no clock collaborator per the test-seam policy (no
injected `now_ms`/clock parameter was in the task's interface above); Task 4
constructs the final stamped value. **If this reads awkwardly during
execution, the fix is to add a `now_ms: int` parameter to this function
instead of leaving a placeholder — do that rather than shipping a fake
timestamp; note the deviation in the commit message.**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/unit/test_tag_trace_extract.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-standing-debrief
git add nora/agent/tag_trace_extract.py tests/unit/test_tag_trace_extract.py nora/obs/metric_specs.py
git commit -m "feat(reflection): extract_tag_trace at the nora/agent/ boundary"
```

---

## Task 4: wire extraction into `LiveAgentRunner` and the `AgentRunner` Protocol

**Files:**
- Modify: `nora/engagement/stages/invoke.py` (the `AgentRunner` Protocol and `run()`)
- Modify: `nora/app/agent_runner.py` (`LiveAgentRunner.invoke`)
- Modify: `nora/testing/scripted_agent.py` (fakes widen to match)
- Test: extend `tests/unit/test_engagement_pipeline.py` or wherever
  `ScriptedRunner`/`FakeAgentRunner` are currently exercised (read the file
  first — do not guess the test file name; `grep -rn "ScriptedRunner\|FakeAgentRunner" tests/`
  to find the real call sites before editing).

**Interfaces:**
- Consumes: `extract_tag_trace` (Task 3).
- Produces: `AgentRunner.invoke(...) -> tuple[str | None, str, TagTrace]` —
  widened by one element. `nora.engagement.stages.invoke.run(...)` widens its
  own return to `tuple[bool, float, str | None, str, TagTrace]`, with a
  **blank `TagTrace`** (`messages=(), transcript=(), outcome=<mapped from the
  failure>`) on the `ok=False` paths (timeout/llm_error) — §7.4 notes this
  case explicitly: "those turns debrief from `transcript` alone with
  `messages` empty."

**This is the highest-risk task in the phase** — it touches a Protocol two
other files already implement. Read `nora/engagement/stages/invoke.py`,
`nora/app/agent_runner.py`, and the current fakes in full before editing;
do not guess their present shape from this plan's earlier excerpts (they were
read for THIS plan, but re-read them fresh at execution time — a sibling
task may have touched them).

- [ ] **Step 1: Write the failing test** (extend the existing pipeline/invoke
  test file found via the grep above with a case asserting a `TagTrace` comes
  back from `invoke.run` and that its `outcome` matches the call's disposition)

```python
# added to whichever test file exercises nora.engagement.stages.invoke.run
async def test_invoke_run_returns_a_tag_trace_on_success(...):
    # Build with the existing test fixtures for this file (ctx, doctrine,
    # pre_fill, etc. -- copy the pattern of a neighboring passing test rather
    # than inventing new fixtures).
    ok, elapsed, stop_reason, final_text, trace = await invoke.run(...)
    assert ok is True
    assert trace.tag_id == ctx.tag_id  # or whatever the fixture's tag_id is

async def test_invoke_run_returns_a_blank_trace_on_timeout(...):
    # a runner fake that raises TimeoutError, matching an existing timeout test
    ok, elapsed, stop_reason, final_text, trace = await invoke.run(...)
    assert ok is False
    assert trace.messages == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /tmp/wt-standing-debrief && uv run pytest -k tag_trace -v`
Expected: FAIL (new assertions, old signature)

- [ ] **Step 3: Widen the Protocol and both implementations**

In `nora/engagement/stages/invoke.py`, widen `AgentRunner.invoke`'s return
type and `run`'s return type by one element (`TagTrace`), threading a new
`tag_id`/`conversation_id` (already available on `ctx`) through to whichever
call builds the trace. In `nora/app/agent_runner.py`:

```python
# inside LiveAgentRunner.invoke, after building `agent` and BEFORE calling factory.invoke:
baseline = len(agent.messages)
...
result = await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)
stop_reason = getattr(result, "stop_reason", None)
trace = extract_tag_trace(
    agent, baseline=baseline, tag_id=ctx.tag_id,
    conversation_id=ctx.message.conversation_id, transcript=(),  # invoke.py fills transcript
    outcome=..., max_chars=self._max_trace_chars, metrics=self._metrics,
)
return (
    stop_reason if isinstance(stop_reason, str) else None,
    factory.final_text(result),
    trace,
)
```

(`self._max_trace_chars`/`self._metrics` need adding to `LiveAgentRunner.__init__`
as new constructor params, per the test-seam policy — no config read inside
this class; the composition root resolves `[debrief].max_input_tokens`-shaped
config down to a plain int and passes it in, same pattern as every other
scalar this class already takes.)

Widen `FakeAgentRunner`/`ScriptedRunner` in `nora/testing/scripted_agent.py`
to return a blank `TagTrace(tag_id=..., conversation_id=..., messages=(),
transcript=(), outcome="send_reply", created_at=0)` — scripted mode has no
real `agent.messages` to extract from, matching `evals/harness.py`'s own
"scripted mode has no script to read tool intent from" precedent for the
analogous `tool_calls` field.

- [ ] **Step 4: Run to verify it passes, then run the FULL affected set**

Run: `cd /tmp/wt-standing-debrief && uv run pytest -k "tag_trace or engagement_pipeline or invoke" -v`
Expected: all PASS. Then `make test-affected` (per CLAUDE.md's fast loop) to
catch anything this Protocol change broke elsewhere.

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-standing-debrief
git add nora/engagement/stages/invoke.py nora/app/agent_runner.py nora/testing/scripted_agent.py
git add -u  # pick up the extended test file(s)
git commit -m "feat(reflection): thread TagTrace through AgentRunner.invoke"
```

---

## Task 5: `SqlTagTraceStore` + migration `0028`

**Files:**
- Create: `nora/infra/sql/tag_traces.py`
- Create: `nora/infra/sql/migrations/versions/0028_tag_trace.py` — **parent
  `0027_fact_candidate_provenance`; announce this number/parent before
  running `alembic revision`, and re-verify `design/09-decision-index.md`/
  `uv run alembic heads` (must print exactly one line) at merge time.**
- Modify: `nora/infra/sql/schema.py` (add `TAG_TRACE` table, kept in parity)
- Test: `tests/infra/test_tag_trace_store.py`
- Modify: `tests/infra/test_schema_parity.py` (add `tag_trace`)

**Interfaces:**
- Consumes: `TagTrace` (Task 1).
- Produces: `SqlTagTraceStore(engine: AsyncEngine)` with
  `async def save(self, trace: TagTrace) -> None` and
  `async def recent(self, *, conversation_id: str, limit: int) -> tuple[TagTrace, ...]`
  (newest first — the re-derivation path in Task 6 reads through this).

**Table shape** (`messages`/`transcript` as JSONB — this is an archival
write-once-per-turn table, not a queried-by-field one, matching
`fact_candidate.observed_in`'s own JSONB-array precedent rather than
normalising into a child table):

```python
# nora/infra/sql/schema.py addition
TAG_TRACE = sa.Table(
    "tag_trace", METADATA,
    sa.Column("id", pg.UUID, primary_key=True),
    sa.Column("tag_id", sa.Text, nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("messages", pg.JSONB, nullable=False),
    sa.Column("transcript", pg.JSONB, nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.CheckConstraint(
        "outcome in ('send_reply','recovered','silent','vetoed','timeout','llm_error')",
        name="tag_trace_outcome_check",
    ),
    sa.Index("idx_tag_trace_conversation_created", "conversation_id", "created_at"),
)
```

- [ ] **Step 1: Write the failing test**

```python
# tests/infra/test_tag_trace_store.py
# Follow tests/contract/test_note_store_contract.py::_notes_db's MODULE-scoped
# db fixture shape (CLAUDE.md: a contract suite scopes its database to the
# MODULE, TRUNCATE between tests) -- do not use the function-scoped pg_url
# fixture directly for this file; copy the module-scoped pattern.
import pytest
from nora.domain.tag_trace import TagTrace
from nora.infra.sql.tag_traces import SqlTagTraceStore

async def test_save_then_recent_roundtrips(tag_trace_engine):
    store = SqlTagTraceStore(tag_trace_engine)
    trace = TagTrace(
        tag_id="t1", conversation_id="c1",
        messages=({"role": "user", "content": [{"text": "hi"}]},),
        transcript=(), outcome="send_reply", created_at=1_723_000_000_000,
    )
    await store.save(trace)
    got = await store.recent(conversation_id="c1", limit=10)
    assert len(got) == 1
    assert got[0].tag_id == "t1"
    assert got[0].messages == trace.messages

async def test_recent_orders_newest_first(tag_trace_engine):
    store = SqlTagTraceStore(tag_trace_engine)
    for i, ts in enumerate([1000, 3000, 2000]):
        await store.save(TagTrace(
            tag_id=f"t{i}", conversation_id="c1", messages=(), transcript=(),
            outcome="silent", created_at=ts,
        ))
    got = await store.recent(conversation_id="c1", limit=10)
    assert [t.created_at for t in got] == [3000, 2000, 1000]

async def test_recent_respects_the_conversation_boundary(tag_trace_engine):
    store = SqlTagTraceStore(tag_trace_engine)
    await store.save(TagTrace(
        tag_id="t1", conversation_id="other", messages=(), transcript=(),
        outcome="silent", created_at=1000,
    ))
    got = await store.recent(conversation_id="c1", limit=10)
    assert got == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/infra/test_tag_trace_store.py -v`
Expected: FAIL, `ModuleNotFoundError` / missing fixture

- [ ] **Step 3: Write the migration, schema addition, and store**

```bash
cd /tmp/wt-standing-debrief
uv run alembic revision -m "tag_trace" --rev-id 0028_tag_trace
# then hand-edit the generated file: down_revision = "0027_fact_candidate_provenance"
```

```python
# nora/infra/sql/tag_traces.py
"""`SqlTagTraceStore` -- write-once archival retention for `TagTrace`
(spec §14.6: the re-derivation path this exists for is Task 6).

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlFactStore`/`SqlNoteStore`'s own contract-suite affordance.
"""

from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.models import TranscriptRow
from nora.domain.tag_trace import TagTrace
from nora.infra.sql.schema import TAG_TRACE


class SqlTagTraceStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, trace: TagTrace) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(TAG_TRACE).values(
                id=str(uuid4()), tag_id=trace.tag_id,
                conversation_id=trace.conversation_id,
                messages=list(trace.messages), transcript=[
                    row.__dict__ for row in trace.transcript
                ],
                outcome=trace.outcome, created_at=trace.created_at,
            ))

    async def recent(
        self, *, conversation_id: str, limit: int,
    ) -> tuple[TagTrace, ...]:
        if limit <= 0:
            return ()
        stmt = (
            sa.select(TAG_TRACE)
            .where(TAG_TRACE.c.conversation_id == conversation_id)
            .order_by(TAG_TRACE.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(
            TagTrace(
                tag_id=r.tag_id, conversation_id=r.conversation_id,
                messages=tuple(r.messages), transcript=tuple(
                    TranscriptRow(**row) for row in r.transcript
                ),
                outcome=r.outcome, created_at=r.created_at,
            )
            for r in rows
        )
```

Add `TAG_TRACE` to `nora/infra/sql/schema.py` (the block shown above) and add
`"tag_trace"` to `tests/infra/test_schema_parity.py`'s checked table list —
read that file first to match its exact assertion shape rather than guessing.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /tmp/wt-standing-debrief && uv run pytest tests/infra/test_tag_trace_store.py tests/infra/test_schema_parity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-standing-debrief
git add nora/infra/sql/tag_traces.py nora/infra/sql/schema.py
git add nora/infra/sql/migrations/versions/0028_tag_trace.py
git add tests/infra/test_tag_trace_store.py tests/infra/test_schema_parity.py
git commit -m "feat(reflection): SqlTagTraceStore + migration 0028"
```

---

## Task 6: wire retention into the pipeline + re-derivation path

**Files:**
- Modify: `nora/engagement/pipeline.py` (save the `TagTrace` on every exit,
  matching §7.4's "runs on every completed turn" — this task saves it;
  Phase 2 is what folds it)
- Modify: `nora/app/composition.py` (wire `SqlTagTraceStore` behind
  `[debrief]`'s eventual flag — for Phase 1, wire it unconditionally-but-cheaply,
  since retention must exist before Phase 2's flag does anything)
- Create: `nora/domain/tag_trace_rederive.py` — `rebuild_state(traces: Sequence[TagTrace]) -> str`
  stub for Phase 2 to fill in; Phase 1's job is only to prove the traces are
  retrievable in order, not to implement folding
- Test: extend `tests/integration/` pipeline test with an assertion that a
  real tag leaves a row in `SqlTagTraceStore`

**This task's scope is deliberately narrow**: persist the trace, prove it is
readable back through `recent()` in the right order. Do NOT implement any
folding/summarising logic here — that is `Standing`, Phase 2, out of scope
for this plan.

- [ ] **Step 1: Write the failing integration test** (extend
  `tests/integration/`'s existing full-pipeline test file — find it via
  `grep -rln "release_and_dispatch\|EngagementPipeline" tests/integration/`
  and follow its existing fixture pattern)

```python
async def test_a_completed_tag_leaves_a_retrievable_tag_trace(...):
    # drive one real tag through the harness, then:
    got = await tag_trace_store.recent(conversation_id=conversation_id, limit=1)
    assert len(got) == 1
    assert got[0].outcome in VALID_OUTCOMES
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /tmp/wt-standing-debrief && uv run pytest -k tag_trace_leaves -v`
Expected: FAIL

- [ ] **Step 3: Wire the save call**

In `pipeline.py`, after the `TagTrace` comes back from `invoke.run` (Task 4),
save it unconditionally in the same `finally`/exit path §7.4 already
identifies as "every completed turn" — the outcome string is derived from
which exit path was taken (mirror `deliver.check_outcome`'s existing
outcome-mapping logic rather than inventing a second mapping). Thread
`tag_trace_store` into `EngagementPipeline.__init__` as a new collaborator,
wired in `composition.py` from `SqlTagTraceStore(engine)` — unconditional for
now (Phase 2 gates the FOLD behind `[debrief] enabled`, not the retention
write, since retention must exist and be proven before the flag matters).

- [ ] **Step 4: Run to verify it passes, then the affected set**

Run: `cd /tmp/wt-standing-debrief && uv run pytest -k tag_trace -v && make test-affected`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd /tmp/wt-standing-debrief
git add nora/engagement/pipeline.py nora/app/composition.py nora/domain/tag_trace_rederive.py
git add -u
git commit -m "feat(reflection): retain a TagTrace on every completed turn"
```

---

## Task 7: the gate — red-on-revert test

**Files:**
- Create: `tests/test_acceptance_rules.py` addition, OR a dedicated
  `tests/test_tag_trace_gate.py` if the former is reserved for
  `RULE_TESTS`-style entries only (check the file's existing convention
  before choosing).

**Interfaces:** none new — this test exercises Tasks 1–6 together.

**Purpose:** Phase 1's own exit gate (plan doc's own words): *"a turn's
reasoning is demonstrably captured and stored, with a red-on-revert test that
deletes the reasoning blocks and fails."*

- [ ] **Step 1: Write the test**

```python
async def test_reasoning_content_survives_into_a_retained_tag_trace(...):
    # Drive a scripted turn whose assistant message includes a
    # reasoningContent block, through the real extraction+retention path
    # (Tasks 3-6), then read the trace back and assert the block is present.
    ...
    assert any(
        "reasoningContent" in block
        for msg in got[0].messages
        for block in msg.get("content", [])
    )
```

- [ ] **Step 2: Confirm it's red on revert**

Temporarily comment out the `reasoningContent`-preserving line in whichever
scripted-turn fixture supplies it (or, if extraction genuinely strips
reasoning by a bug reintroduced later, this test must fail) — run once with
the deliberate break to confirm RED, then restore and confirm GREEN. This is
the gate itself, not a normal TDD step; both runs are the deliverable.

Run: `cd /tmp/wt-standing-debrief && uv run pytest -k reasoning_content_survives -v`
Expected: RED with the block commented out, GREEN restored.

- [ ] **Step 3: Commit**

```bash
cd /tmp/wt-standing-debrief
git add -u
git commit -m "test(reflection): red-on-revert gate for TagTrace reasoning retention"
```

---

## Phase 1 exit — full gate

```bash
cd /tmp/wt-standing-debrief
make gate   # serial: pytest, ruff, mypy (scoped), lint-imports
uv run alembic heads   # must print exactly ONE line
```

Do not proceed to Phase 2 until `make gate` is green and `alembic heads` is
one line. Report back with the gate's actual output, not a summary of intent
(CLAUDE.md: "Never claim a merge, deploy or fix without reading back the
state you claim").
