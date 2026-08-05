---
kind: plan
title: "Nora Phase 5 — Agent: tool registry, ToolSpec, DEC-10 budgets"
description: "Replace ReplyGuard's hardcoded BUDGETS allowlist with budgets injected from a ToolRegistry, so a new tool is no longer vetoed before it runs, while preserving R-7's unknown-tool default-deny."
tags: [nora, plan, agent, tools, dec-10, phase5]
timestamp: '2026-08-05'
edges:
  - {dst: ../specs/2026-08-04-nora-architecture-design.md, rel: implements, provenance: asserted}
  - {dst: 2026-08-05-nora-phase4-platforms.md, rel: follows, provenance: asserted}
---

# Nora Phase 5 — Agent / Tool Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Introduce `ToolSpec` and a `ToolRegistry`, and make `ReplyGuard` take its per-tool budgets **from the registry** instead of a hardcoded class attribute — DEC-10 — while keeping R-7's unknown-tool default-deny exactly as strict as it is today.

**Why this matters more than it looks.** Spec §9 lists this as one of revision 1's *blocking* defects: `guards.py:170` hardcodes `BUDGETS = {"read_buffer": 2, "read_peer_card": 1, "send_reply": 1}` and `evaluate()` vetoes anything absent under R-7 with *"The tool X does not exist."* Ported as-is, that **rejects every future MCP tool and `end_episode` before they run** — silently defeating both the tool-extension axis and the episode model. It is the single change that unblocks two of the three extension axes the whole refactor exists to enable.

**Tech Stack:** Python 3.13, Strands Agents SDK, pytest.

## Global Constraints

- **`uv add` only, never `pip install`.** Bare `uv sync` drops the evals extra; recover with `uv sync --frozen --group dev --extra evals`.
- **Only `nora.agent.**` may import `strands`** — `tests/test_boundaries.py::test_strands_import_boundary`.
- **Only `nora.app` and `nora/__main__.py` may import `nora.config`.** There are **no `ignore_imports` exceptions left** and none may be re-added.
- **`nora/domain/` imports only the standard library.** `nora.infra`/`nora.testing` may import neither `nora.obs` nor `nora.config`.
- **Metric names are append-only.** `/health` JSON additive only; `sse` stays top-level with `last_event_age_seconds`, `account`, `platform` (pinned by `test_health_sse_key_set_is_pinned`).
- **Module size:** soft 200 / hard 300, enforced for `nora/` by `tests/architecture/test_module_size.py`.
- **Test-seam policy:** collaborators through `__init__`; no module-level mutable singletons.
- **A port never gains a second implementation — including a fake — without a shared contract suite both pass.**
- **Every gate must be OBSERVED FAILING against a deliberate probe before it is committed.**

**Per-task gate:**
```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra
uv run lint-imports
```
**Baseline (Phase 4 merge):** 633 passed / 1 deselected; ruff clean; mypy Success on **23 source files**; lint-imports **5 kept**. Container healthy on `nora:p4`.

---

## What must not change

R-7 has two halves, and only one of them is being replaced.

| Half | Today | After |
|---|---|---|
| **Per-tool budget** | hardcoded `BUDGETS` class attribute | **injected from the registry** |
| **Unknown-tool default-deny** | veto with *"The tool X does not exist"* | **unchanged — still default-deny** |

`test_tool_call_budget_enforced_for_read_buffer` is R-7's entry in `tests/test_acceptance_rules.py::RULE_TESTS`, which maps rules to **bare test names**. **Its name must not change**; its body may.

Also unchanged: R-2 (`send_reply` at most once, counting attempts), R-3 (only the tagging conversation), R-4 (no empty reply). `ReplyGuard` enforces all of these alongside R-7 — read `nora/agent/guards.py:164-220` before touching anything.

---

## Task 1: `ToolSpec` and `agent/ports.py`

**Files:** create `nora/agent/ports.py`; test `tests/unit/test_agent_ports.py`.

Per spec §4.2, `ToolSpec` lives in `nora/agent/ports.py`, **not** `nora/domain/`. Revision 1 put `ToolProvider` in `domain/` and it returned a Strands `Tool` — contradicting both §4.1's framework-free rule and the strands import boundary. `agent/ports.py` may reference Strands **at the adapt boundary only**; `ToolSpec` itself should stay plain.

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict
    call: Callable[..., Awaitable[Any]]
    budget: int = 1                      # feeds ReplyGuard (DEC-10)
    disclose_failure: bool = False       # feeds DEC-4
    timeout_seconds: float | None = None
```

- [ ] `budget` defaults to 1 — the conservative default. A tool that needs more says so.
- [ ] `disclose_failure` exists because DEC-4 requires it and revision 1 declared it in config with nowhere on the port to carry it. **Nothing consumes it this phase** — that is DEC-4's disclosure work, deferred. Say so in the docstring so it is not mistaken for dead code.
- [ ] Tests: `ToolSpec` is frozen; `budget` defaults to 1; `disclose_failure` defaults False.
- [ ] **Do NOT build `ToolProvider` / MCP lifecycle** — spec §7 and §11 defer MCP providers entirely.

## Task 2: `ToolRegistry` over the three native tools

**Files:** create `nora/agent/tools/` package (moving `nora/agent/tools.py` into it) or `nora/agent/registry.py` — pick one and say why; tests in `tests/unit/test_tool_registry.py`.

- [ ] The registry owns the three native tools (`read_buffer`, `read_peer_card`, `send_reply`) as `ToolSpec`s carrying **today's exact budgets**: `read_buffer=2`, `read_peer_card=1`, `send_reply=1`. Changing a budget in this phase would be a behaviour change smuggled into a structural one.
- [ ] `registry.budgets() -> Mapping[str, int]` is what `ReplyGuard` receives.
- [ ] An `adapt` boundary converts `ToolSpec` → the Strands tool object. **Keep every Strands import inside `nora/agent/`.**
- [ ] The tool docstrings are the **LLM-facing schema** — they are written for a model, not a developer (see `tools.py`'s module docstring). Preserve their wording exactly; rewording them is a prompt change with eval consequences.
- [ ] Tests: the registry exposes exactly three tools; budgets match the table above; adapting produces objects the agent factory accepts.

## Task 3: DEC-10 — `ReplyGuard(budgets=...)`

**Files:** `nora/agent/guards.py`, `nora/engagement/stages/deliver.py` (or wherever `ReplyGuard` is constructed), `tests/test_guards_replyguard.py`, `tests/test_guards_hooks.py`.

**This is the phase.**

- [ ] `ReplyGuard(*, conversation_id, budgets: Mapping[str, int], deliverer=None, metrics=...)`. Delete the `BUDGETS` ClassVar.
- [ ] `self.counts` initialises from the injected budgets, not the class attribute.
- [ ] **Unknown-tool default-deny is preserved**: a tool absent from `budgets` is still vetoed under R-7. The veto message currently names the three tools literally (*"Use read_buffer, ..."*) — make it derive from the injected budgets so it cannot go stale, but **keep it a veto**.
- [ ] The composition root / engagement pipeline passes `registry.budgets()`.
- [ ] `test_tool_call_budget_enforced_for_read_buffer` **keeps its name**.

**Two mandatory probes — these are the point of the phase:**

- **Probe A — the defect DEC-10 exists to fix.** Add a fourth tool to the registry with `budget=1`, and assert the guard **allows** it. Then temporarily restore a hardcoded three-entry budget map and confirm the new tool is **vetoed under R-7**. That contrast is the whole justification for DEC-10 — paste both outputs.
- **Probe B — default-deny survives.** Assert a tool that is in *no* registry is still vetoed. Then remove the unknown-tool branch and confirm the test fails. Restore.

## Task 4: wiring, docs, container gate

- [ ] Wire the registry through `nora/app/composition.py` so `ReplyGuard` and `build_tools` both draw from one source. **No module-level singleton** — it is a constructed collaborator.
- [ ] `design/09-decision-index.md`: DEC-10's "not built (agent/tool-registry phase)" becomes the real enforcing tests. R-7's row gains a note that the budget table is now injected while default-deny is unchanged.
- [ ] `ARCHITECTURE.md`: package table gains the registry; §5's "add a native tool" row moves from **(target)** to the live path.
- [ ] `CLAUDE.md`: if "add a native tool" now means "add a `ToolSpec` to the registry", say so.
- [ ] **Accuracy standard:** every present-tense claim must be true of the tree as it stands. Phases 2–4 produced repeated cases of docs asserting untrue things; where you say a test enforces something, open it and confirm.
- [ ] **Container gate (controller runs):** `docker build --network=host -t nora:p5 .` → compose up → `/health` 200, `sqlite.reachable: true`, `sse` with `last_event_age_seconds`, `account`, `platform`. Tag `nora:p5`.

---

## Self-Review

**Spec coverage:** §8 phase 5 is "Agent: tool registry, `ToolSpec`, DEC-10 budgets. Update the boundary allowlist for the `tools/` package." Tasks 1–4 cover it. The boundary allowlist matters if `tools.py` becomes a package — `test_strands_import_boundary` allowlists by path.

**Deliberately NOT here** (spec §7/§11): `ToolProvider` and MCP providers with lifecycle; the `deep` tier and real `Routing`; DEC-4's failure *disclosure* (only the `disclose_failure` field lands, unconsumed); Episodes and `end_episode`.

**Risk:** medium, and concentrated in Task 3 — `ReplyGuard` enforces R-2, R-3, R-4 and R-7 together, so a careless edit there weakens a guardrail rather than a convenience. The R-5 matrix and the `RULE_TESTS` registry are the tripwires.
