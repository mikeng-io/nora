# Nora Phase 1b — Domain, Ports & Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the framework-free `nora/domain/` layer that every later phase depends on, and make its boundaries *mechanically enforced* rather than merely documented.

**Architecture:** `domain/` holds the data contracts, the port Protocols, and the error taxonomy — and imports no framework. That rule is the load-bearing one: if a port needs aiohttp or Strands to express itself, the port is wrong. Enforcement (mypy, import-linter) lands in the same phase as the thing it enforces, because a Protocol nobody type-checks is a comment. Documentation obligations (`ARCHITECTURE.md`, `CLAUDE.md`, a decision index) close the phase.

**Tech Stack:** Python 3.12, pydantic, mypy, import-linter, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-08-04-nora-architecture-design.md` (revision 2), §4.1, §4.2, DEC-4, DEC-9, DEC-10, DEC-12, §10.

## Global Constraints

- **`nora/domain/` imports no framework.** Not aiohttp, not strands, not sqlalchemy, not pydantic, not prometheus_client, not opentelemetry, not yaml. Only the standard library. This is the rule that keeps the rest honest, and Task 5 enforces it mechanically.
- **This phase adds structure; it does not rewire runtime behaviour.** No pipeline is built, no `Handler` is dissolved, no adapter is registered. Ports get **exactly one** implementation each — the current one — or none at all where the implementation lands in a later phase.
- Baseline: `uv run pytest -q` → **460 passed, 1 deselected**; `uv run ruff check nora/ evals/ tests/` clean. Both hold at every commit.
- **`nora/config.py`'s path validator raises without base context.** Any new construction of `Config`, `StoreConfig`, `PersonaConfig`, or `LoggingConfig` must pass `context={"base": <dir>}`. This is deliberate hardening from Phase 1a — do not weaken it.
- Dependencies via **`uv add`**, never pip. `mypy` and `import-linter` are already in the dev group.
- The **signal-cli daemon is neutral and shared — never touched.** Container builds need **`docker build --network=host`**.
- Metric names are append-only; `/health` JSON is additive only (`deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`).

---

### Task 1: `nora/domain/` — models and the error taxonomy

Move the platform-neutral data contracts into `domain/` and give the failure classes a typed home. Today `nora/models.py` holds the contracts and failure reasons are ad-hoc strings (`"invocation_timeout"`, `"llm_error"`, `"handler_error"`, `"no_send_call"`) — a de-facto taxonomy with no type.

**Files:**
- Create: `nora/domain/__init__.py`, `nora/domain/models.py`, `nora/domain/errors.py`
- Modify: `nora/models.py` (becomes a re-export shim), `tests/test_models.py`
- Create: `tests/test_domain_errors.py`

**Interfaces:**
- Produces: `nora.domain.models.{NormalizedMessage, ConversationTarget, PersonRef, TagContext}` and `nora.domain.errors.{FailureClass, NoraError}`. Later tasks and phases import from `nora.domain`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_domain_errors.py`:

```python
"""The failure taxonomy DEC-4 requires, and the domain purity rule."""

import pytest

from nora.domain.errors import FailureClass, NoraError


def test_failure_classes_cover_the_three_dec4_cases():
    assert FailureClass.INFRASTRUCTURE
    assert FailureClass.GUARD_VETO
    assert FailureClass.REQUESTED_TOOL


def test_only_requested_tool_failures_are_disclosable():
    """R-5 says stay silent when Nora is broken. DEC-4 carves out exactly one
    exception: a tool the user explicitly asked for. Silence there reads as
    being ignored, which is a worse failure than admitting the tool failed."""
    assert FailureClass.REQUESTED_TOOL.disclosable is True
    assert FailureClass.INFRASTRUCTURE.disclosable is False
    assert FailureClass.GUARD_VETO.disclosable is False


def test_nora_error_carries_its_failure_class():
    err = NoraError("llm timed out", failure_class=FailureClass.INFRASTRUCTURE)
    assert err.failure_class is FailureClass.INFRASTRUCTURE
    assert "llm timed out" in str(err)


def test_nora_error_defaults_to_the_silent_class():
    """An uncategorised failure must default to silence, never to speech."""
    assert NoraError("something broke").failure_class is FailureClass.INFRASTRUCTURE
    assert NoraError("something broke").failure_class.disclosable is False


def test_domain_models_are_importable_from_domain():
    from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef, TagContext
    assert NormalizedMessage and ConversationTarget and PersonRef and TagContext
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_domain_errors.py -q 2>&1 | tail -3
```

Expected: `ModuleNotFoundError: No module named 'nora.domain'`.

- [ ] **Step 3: Create the package**

`nora/domain/models.py` — move the four dataclasses from `nora/models.py` **verbatim**. Do not change a field, a default, or the `target()` helper. Keep `from __future__ import annotations`.

`nora/domain/errors.py`:

```python
"""Failure taxonomy (spec DEC-4).

R-5's rule is "never speak on failure", and it is absolute for failures that mean
Nora is broken. DEC-4 carves out exactly one exception: a tool the user explicitly
asked her to use. Staying silent there reads as being ignored, which is a worse
failure than admitting the tool failed. `disclosable` is that distinction, typed.
"""

from __future__ import annotations

from enum import Enum


class FailureClass(Enum):
    INFRASTRUCTURE = "infrastructure"   # LLM error, timeout, adapter down -> silent
    GUARD_VETO = "guard_veto"           # ReplyGuard refused -> silent
    REQUESTED_TOOL = "requested_tool"   # a tool the user asked for failed -> disclosable

    @property
    def disclosable(self) -> bool:
        return self is FailureClass.REQUESTED_TOOL


class NoraError(Exception):
    """Base for Nora's own failures, carrying how the failure should be surfaced.

    Defaults to INFRASTRUCTURE so an uncategorised failure defaults to silence —
    speaking on an unknown failure is the outcome R-5 exists to prevent.
    """

    def __init__(self, message: str, *, failure_class: FailureClass = FailureClass.INFRASTRUCTURE) -> None:
        super().__init__(message)
        self.failure_class = failure_class
```

`nora/domain/__init__.py` re-exports the public surface and states the boundary:

```python
"""Framework-free contracts. Imports only the standard library.

If a type here needs aiohttp, strands, sqlalchemy, pydantic or prometheus_client
to express itself, it does not belong here — the port is wrong. Enforced by
tests/test_boundaries.py and by import-linter.
"""
```

- [ ] **Step 4: Turn `nora/models.py` into a shim**

Every existing importer (`handler.py`, `agent/*`, `platform/*`, `obs/*`, `evals/*`, `tests/*`) currently does `from .models import ...` or `from nora.models import ...`. Rather than editing ~20 call sites in a task about creating the package, make `nora/models.py`:

```python
"""Moved to nora.domain.models. Kept as a re-export so this phase does not
rewrite twenty import sites; later phases migrate callers and delete this."""

from .domain.models import ConversationTarget, NormalizedMessage, PersonRef, TagContext

__all__ = ["ConversationTarget", "NormalizedMessage", "PersonRef", "TagContext"]
```

- [ ] **Step 5: Verify**

```bash
uv run pytest tests/test_domain_errors.py tests/test_models.py -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
```

Expected: full suite at baseline + 5 new tests, ruff clean.

- [ ] **Step 6: Prove domain purity by inspection**

```bash
uv run python -c "
import ast, pathlib
for p in pathlib.Path('nora/domain').rglob('*.py'):
    tree = ast.parse(p.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import): mods |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module: mods.add(n.module.split('.')[0])
    banned = mods & {'aiohttp','strands','sqlalchemy','pydantic','prometheus_client','opentelemetry','yaml','openai'}
    print(p, '->', sorted(mods), 'BANNED:' + str(banned) if banned else 'clean')
"
```

Expected: every file `clean`. Task 5 makes this a permanent gate.

- [ ] **Step 7: Commit**

```bash
git add nora/domain nora/models.py tests/test_domain_errors.py
git commit -m "feat: nora/domain with the platform-neutral contracts and failure taxonomy

Moves the four data contracts verbatim and gives DEC-4's failure classes a typed
home. Today the reasons are ad-hoc strings ('llm_error', 'handler_error', …) — a
de-facto taxonomy with no type, which is why the <1% silent-failure SLO cannot
be computed reliably.

NoraError defaults to INFRASTRUCTURE, so an uncategorised failure defaults to
silence. Speaking on an unknown failure is exactly the outcome R-5 exists to
prevent.

nora/models.py becomes a re-export shim so this commit does not rewrite twenty
import sites; later phases migrate callers and delete it."
```

---

### Task 2: `nora/domain/ports.py` — the Protocols

The seams every later phase plugs into. **These are declarations only** — no implementation moves in this task.

**Files:**
- Create: `nora/domain/ports.py`
- Create: `tests/test_ports.py`

**Interfaces:**
- Produces: `PlatformAdapter`, `AuditStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`, plus the value types `PlatformIdentity`, `SendResult`, `AdapterHealth`, `EngageDecision`, `EngagementRequest`, `ModelTier`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ports.py`:

```python
"""Ports are structural contracts. These tests pin the shape later phases build against."""

import inspect

import pytest

from nora.domain.ports import (
    AdapterHealth,
    AuditStore,
    ContextAssembler,
    EngageDecision,
    EngagePolicy,
    MemoryStore,
    ModelTier,
    PlatformAdapter,
    PlatformIdentity,
    SendResult,
)


def test_platform_identity_holds_aliases_not_one_id():
    """signal-cli emits `source` OR `sourceNumber` and sometimes a UUID; Telegram has
    a numeric id AND a username. R-12 (ignore self-triggers) must test membership,
    not equality, or it silently stops working on the second platform."""
    ident = PlatformIdentity(platform="signal", primary="+85290000100",
                             aliases=frozenset({"+85290000100", "uuid-abc"}))
    assert "uuid-abc" in ident.aliases
    assert ident.primary in ident.aliases


def test_send_result_can_express_a_retryable_failure():
    """Telegram answers 429 with retry_after. Deliverer's latch counts ATTEMPTS, so a
    bare bool would burn the single send on the most common transient failure."""
    ok = SendResult(ok=True)
    assert ok.ok and not ok.retryable
    rate_limited = SendResult(ok=False, retryable=True, retry_after=30.0)
    assert not rate_limited.ok and rate_limited.retryable and rate_limited.retry_after == 30.0


def test_engage_decision_carries_the_rationale_and_threshold():
    """design/02 requires the classifier's reason to reach the prefill, and a bare
    confidence is uninterpretable without the threshold it was compared against."""
    d = EngageDecision(engage=True, reason="trigger_text", tier=ModelTier.FLASH)
    assert d.engage and d.rationale is None and d.confidence is None
    c = EngageDecision(engage=True, reason="classifier", tier=ModelTier.DEEP,
                       rationale="asked a factual question about dinner",
                       confidence=0.82, threshold=0.7, allow_episode=True)
    assert c.confidence > c.threshold and c.allow_episode


def test_ports_are_runtime_checkable_protocols():
    for port in (PlatformAdapter, AuditStore, MemoryStore, EngagePolicy, ContextAssembler):
        assert getattr(port, "_is_protocol", False), f"{port.__name__} is not a Protocol"


def test_port_methods_are_keyword_only_after_self():
    """Convention from spec §4.2: free extensibility forever. A positional parameter
    added to a port later is a breaking change at every call site."""
    offenders = []
    for port in (PlatformAdapter, AuditStore, MemoryStore, EngagePolicy, ContextAssembler):
        for name, member in vars(port).items():
            if name.startswith("_") or not callable(member):
                continue
            params = list(inspect.signature(member).parameters.values())[1:]  # drop self
            for p in params:
                if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
                    offenders.append(f"{port.__name__}.{name}({p.name})")
    assert not offenders, f"port params must be keyword-only: {offenders}"


def test_adapter_health_is_typed_not_a_bare_dict():
    h = AdapterHealth(platform="signal", connected=True, last_event_age_seconds=None)
    assert h.connected is True
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_ports.py -q 2>&1 | tail -3
```

Expected: `ModuleNotFoundError: No module named 'nora.domain.ports'`.

- [ ] **Step 3: Write `nora/domain/ports.py`**

Standard library only — `dataclasses`, `enum`, `typing`, `collections.abc`. Every method **keyword-only after `self`**.

```python
class ModelTier(Enum):
    FLASH = "flash"
    DEEP = "deep"


@dataclass(frozen=True)
class PlatformIdentity:
    platform: str
    primary: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    def __post_init__(self) -> None:  # primary is always an alias of itself
        object.__setattr__(self, "aliases", frozenset(self.aliases) | {self.primary})


@dataclass(frozen=True)
class SendResult:
    ok: bool
    retryable: bool = False
    retry_after: float | None = None


@dataclass(frozen=True)
class AdapterHealth:
    platform: str
    connected: bool
    last_event_age_seconds: float | None = None


@dataclass(frozen=True)
class EngageDecision:
    engage: bool
    reason: str
    tier: ModelTier | None = None
    rationale: str | None = None
    confidence: float | None = None
    threshold: float | None = None
    allow_episode: bool = False


@dataclass(frozen=True)
class EngagementRequest:
    message: NormalizedMessage
    decision: EngageDecision
    tag_id: str
```

Then the five Protocols, all `@runtime_checkable`:

```python
@runtime_checkable
class PlatformAdapter(Protocol):
    name: str
    async def resolve_identity(self) -> PlatformIdentity: ...
    async def listen(self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]) -> None: ...
    async def send(self, *, target: ConversationTarget, text: str) -> SendResult: ...
    async def health(self) -> AdapterHealth: ...


@runtime_checkable
class AuditStore(Protocol):
    async def append(self, *, message: NormalizedMessage, is_nora: bool = False,
                     delivery_status: str = "pending", max_per_conversation: int = 500) -> int: ...
    async def recent(self, *, conversation_id: str, limit: int) -> list[dict[str, Any]]: ...
    async def set_delivery_status(self, *, row_id: int, status: str) -> None: ...
    async def stats(self) -> dict[str, Any]: ...


@runtime_checkable
class MemoryStore(Protocol):
    async def ensure_workspace(self) -> bool: ...
    async def ensure_peer(self, *, peer_id: str) -> None: ...
    async def feed(self, *, peer_id: str, text: str) -> bool: ...
    async def peer_card(self, *, peer_id: str) -> list[str]: ...
    async def health(self) -> dict[str, Any]: ...


@runtime_checkable
class EngagePolicy(Protocol):
    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision: ...


@runtime_checkable
class ContextAssembler(Protocol):
    async def assemble(self, *, request: EngagementRequest) -> TagContext: ...
```

Notes that belong in the module docstring, because each encodes a decision that cost something to reach:
- `PlatformAdapter` has **no `parse()`** — that would force every adapter to pretend its transport is dict-shaped, which Discord's gateway and Telegram's update batches are not. Parsers stay per-adapter modules, tested directly.
- `MemoryStore.ensure_*` is **idempotent under concurrency** — today's `HonchoClient.ensure_session` is check-then-act, and person-centric identity means the same peer id can be hit from two platforms at once. Phase 6 adds the lock; the invariant is declared here.
- `AuditStore` has no `prune` — `db.prune` is dead code today (the real prune is inline in `append`). It is left off the port deliberately rather than carried forward.
- `EngagePolicy` is **async now** even though `TriggerTextPolicy` is synchronous, because `ClassifierPolicy` is I/O and sync→async later is a breaking change through the whole ingest pipeline.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_ports.py -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
```

- [ ] **Step 5: Re-run the domain-purity check from Task 1 Step 6.** `ports.py` must be `clean` — if `Any` pulled in something, or a convenience import crept in, fix it now.

- [ ] **Step 6: Commit**

```bash
git add nora/domain/ports.py tests/test_ports.py
git commit -m "feat: domain ports — the seams every later phase plugs into

Declarations only; no implementation moves here. Four shapes encode decisions
that cost something to reach:

- PlatformIdentity carries aliases, not one id. signal-cli emits `source` OR
  `sourceNumber` and sometimes a UUID; Telegram has a numeric id AND a username.
  R-12 must test membership or it silently stops working on platform two.
- send() returns SendResult, not bool. Telegram answers 429 with retry_after, and
  Deliverer's latch counts attempts — a bool burns the single send on the most
  common transient failure.
- EngageDecision carries rationale and threshold. design/02 requires the
  classifier's reason to reach the prefill, and a confidence without its
  threshold is uninterpretable in a trace.
- No parse() on the adapter port: it would force every adapter to pretend its
  transport is dict-shaped, which Discord's gateway and Telegram's batches
  are not.

All methods keyword-only after self, pinned by a test."
```

---

### Task 3: Verify the existing implementations satisfy the ports

A Protocol nobody checks is a comment. This task proves the *current* classes structurally satisfy the ports they will be injected as — without moving any code.

**Files:**
- Create: `tests/contract/__init__.py`, `tests/contract/test_port_conformance.py`

- [ ] **Step 1: Write the test**

```python
"""Conformance: today's concrete classes must satisfy the ports they'll be injected as.

This is what makes the ports real rather than aspirational. Where a class does NOT
yet conform, the gap is asserted explicitly with the phase that closes it — so the
divergence is tracked, not forgotten.
"""

from nora.domain.ports import MemoryStore, PlatformAdapter
from nora.platform.signal.adapter import SignalAdapter
from nora.store.honcho import HonchoClient


def test_honcho_client_satisfies_memory_store_method_names():
    for method in ("ensure_workspace", "ensure_peer", "feed", "peer_card", "health"):
        assert callable(getattr(HonchoClient, method, None)), f"HonchoClient lacks {method}"


def test_signal_adapter_gap_to_platform_adapter_is_known():
    """SignalAdapter does NOT yet satisfy PlatformAdapter, and that is expected:
    resolve_identity() and the SendResult return type land in the platforms phase.
    Pinning the gap keeps it visible instead of letting it drift."""
    assert hasattr(SignalAdapter, "listen") and hasattr(SignalAdapter, "send")
    assert not hasattr(SignalAdapter, "resolve_identity"), (
        "SignalAdapter grew resolve_identity — update this test and the ports phase note"
    )


def test_runtime_checkable_protocols_only_check_method_names():
    """A guard against false confidence: isinstance() against a runtime_checkable
    Protocol checks names, NOT signatures. Static checking (mypy) is what verifies
    the shapes — which is why DEC-12 adds it."""
    class Fake:
        async def ensure_workspace(self): ...
        async def ensure_peer(self, **kw): ...
        async def feed(self, **kw): ...
        async def peer_card(self, **kw): ...
        async def health(self): ...
    assert isinstance(Fake(), MemoryStore)
```

- [ ] **Step 2: Run, then commit**

```bash
uv run pytest tests/contract -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
git add tests/contract
git commit -m "test: pin port conformance and the known gaps

Asserts today's classes against the ports they will be injected as, and pins
the SignalAdapter gap (no resolve_identity yet) explicitly so it stays visible
rather than drifting.

Includes a guard documenting that isinstance() against a runtime_checkable
Protocol checks method NAMES only, never signatures — which is exactly why
DEC-12 adds static checking rather than relying on runtime checks."
```

---

### Task 4: Composition root skeleton

One place that reads `Settings` and builds the object graph. Nothing is rewired yet — `build_app` in `__main__.py` keeps working — but the seam exists and tests/evals can use it.

**Files:**
- Create: `nora/app/__init__.py`, `nora/app/composition.py`
- Create: `tests/test_composition.py`

- [ ] **Step 1: Write the failing test**

```python
"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

from pathlib import Path

import pytest

from nora.app.composition import Dependencies, build_dependencies
from nora.config import load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    import shutil
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    return load(dst)


def test_build_dependencies_returns_the_wired_graph(cfg):
    deps = build_dependencies(cfg)
    assert isinstance(deps, Dependencies)
    assert deps.memory is not None
    assert deps.persons is not None
    assert deps.metrics is not None


def test_dependencies_do_not_expose_settings(cfg):
    """A stage must never be able to reach the whole config through its deps —
    that is how a god object grows back."""
    deps = build_dependencies(cfg)
    assert not hasattr(deps, "config")
    assert not hasattr(deps, "settings")


def test_only_app_and_main_import_config():
    """Enforced properly by import-linter in the next task; asserted here too so the
    rule has a home in the suite."""
    import ast, pathlib
    offenders = []
    for p in pathlib.Path("nora").rglob("*.py"):
        rel = p.relative_to(pathlib.Path("nora"))
        if rel.parts[0] in ("app", "config.py") or str(rel) in ("__main__.py", "config.py"):
            continue
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and "config" in (n.module or ""):
                offenders.append(str(rel))
    assert not offenders, f"config imported outside app/ and __main__: {offenders}"
```

If that last test fails against today's code, **do not weaken it** — report which modules import config, because that list is the real work item for the pipelines phase. Mark it `xfail` with a reason naming the offending modules if they cannot be fixed in this task.

- [ ] **Step 2: Write `nora/app/composition.py`**

```python
"""The ONE place that reads Settings. Everything else receives dependencies.

A stage never sees Config; it receives an AuditStore, a MemoryStore, a Persons.
That is what makes stages testable in isolation and what stops the god object
from reforming. Enforced by import-linter (DEC-12).
"""
```

`Dependencies` is a frozen dataclass holding the built collaborators — `memory`, `persons`, `metrics` to start, since those exist today. `build_dependencies(cfg) -> Dependencies` constructs them. Deliberately **no** `config` field: a stage that can reach the whole config is a stage that will.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest tests/test_composition.py -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
git add nora/app tests/test_composition.py
git commit -m "feat: composition root skeleton

One place reads Settings; everything else receives dependencies. Dependencies
deliberately has no config field — a stage that can reach the whole config is a
stage that eventually will, which is how the Handler god object grew.

Nothing is rewired yet: __main__.build_app still works. The seam exists so the
pipelines phase has somewhere to land, and so tests and evals stop needing a
back door."
```

---

### Task 5: Mechanical enforcement — mypy and import-linter

DEC-12. Without this the whole design is comments.

**Files:**
- Modify: `pyproject.toml` (`[tool.mypy]`, `[tool.importlinter]`)
- Modify: `.github/workflows/gates.yml`
- Modify: `tests/test_boundaries.py`

- [ ] **Step 1: Configure mypy on the two framework-free packages**

```toml
[tool.mypy]
python_version = "3.12"
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true

[[tool.mypy.overrides]]
module = ["nora.domain.*", "nora.config", "nora.app.*"]
strict = true
```

```bash
uv run mypy nora/domain nora/config.py nora/app
```

Fix what it reports **in the code**, not by adding `# type: ignore`. If a genuine third-party stub gap forces an ignore, it must carry a one-line reason.

- [ ] **Step 2: Configure import-linter**

```toml
[tool.importlinter]
root_package = "nora"

[[tool.importlinter.contracts]]
name = "domain is framework-free"
type = "forbidden"
source_modules = ["nora.domain"]
forbidden_modules = ["aiohttp", "strands", "sqlalchemy", "pydantic", "prometheus_client", "opentelemetry", "yaml", "openai"]

[[tool.importlinter.contracts]]
name = "only app and __main__ read config"
type = "forbidden"
source_modules = ["nora.agent", "nora.obs", "nora.persona", "nora.platform", "nora.store"]
forbidden_modules = ["nora.config"]
```

```bash
uv run lint-imports
```

**If the second contract fails, that is a true finding, not a reason to delete the contract.** Report which modules import config. If they cannot be fixed inside this task, keep the contract and mark it `ignore_imports` with an explicit TODO naming the phase that fixes it — a documented exception is honest; a deleted contract is not.

- [ ] **Step 3: Add both to CI**

In `.github/workflows/gates.yml`, after the ruff step:

```yaml
      - name: mypy
        run: uv run mypy nora/domain nora/config.py nora/app

      - name: import-linter
        run: uv run lint-imports
```

- [ ] **Step 4: Add the domain-purity boundary test**

`tests/test_boundaries.py` already has `_imported_modules` (AST-based, from Phase 0). Add:

```python
DOMAIN_BANNED = {"aiohttp", "strands", "sqlalchemy", "pydantic",
                 "prometheus_client", "opentelemetry", "yaml", "openai"}


def test_domain_imports_no_framework():
    """The rule that keeps the rest honest: if a type in domain/ needs a framework
    to express itself, the port is wrong."""
    offenders = []
    for path in (PKG / "domain").rglob("*.py"):
        banned = _imported_modules(path) & DOMAIN_BANNED
        if banned:
            offenders.append(f"{path.relative_to(ROOT)}: {sorted(banned)}")
    assert not offenders, f"domain/ must import no framework: {offenders}"
```

- [ ] **Step 5: Prove both gates fire**

A gate never seen to fail is not a gate:

```bash
printf '\nimport aiohttp  # probe\n' >> nora/domain/errors.py
uv run lint-imports 2>&1 | tail -5
uv run pytest tests/test_boundaries.py::test_domain_imports_no_framework -q 2>&1 | tail -3
git checkout nora/domain/errors.py
git diff --stat nora/domain/errors.py
```

Expected: import-linter FAILS naming the contract, the boundary test FAILS naming the file, then the probe is reverted and the diff is empty. **If either passes with the probe in place, the gate is misconfigured — fix it before committing.**

- [ ] **Step 6: Verify and commit**

```bash
uv run mypy nora/domain nora/config.py nora/app && echo "MYPY CLEAN"
uv run lint-imports && echo "IMPORTS CLEAN"
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
git add pyproject.toml .github/workflows/gates.yml tests/test_boundaries.py uv.lock
git commit -m "build: enforce the architecture mechanically (DEC-12)

The design is Protocol ports and a framework-free domain layer, and until now
nothing checked either. A Protocol nobody type-checks is a comment.

mypy --strict on domain/, config and app/ — the framework-free packages where
correctness is architectural. import-linter forbids domain/ importing any
framework, and forbids everything except app/ and __main__ importing config,
which is what turns 'a stage never sees Settings' from prose into a gate.

Both gates were observed failing against a deliberate probe before being
committed."
```

---

### Task 6: `ARCHITECTURE.md`, `CLAUDE.md`, and the decision index

Spec §10. A dated spec records a decision; it is not a map of the system, and it goes stale the moment the next phase lands.

**Files:**
- Create: `ARCHITECTURE.md`, `CLAUDE.md`, `design/09-decision-index.md`
- Create: `tests/test_architecture_doc.py`

- [ ] **Step 1: Write the pinning test first**

```python
"""ARCHITECTURE.md is a map, and maps rot. This pins it to reality."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_doc_covers_every_package():
    doc = (ROOT / "ARCHITECTURE.md").read_text()
    packages = sorted(p.name for p in (ROOT / "nora").iterdir()
                      if p.is_dir() and not p.name.startswith(("_", ".")))
    missing = [p for p in packages if f"`{p}/`" not in doc and f"nora/{p}" not in doc]
    assert not missing, f"ARCHITECTURE.md does not mention: {missing}"


def test_claude_md_carries_the_operational_constraints():
    text = (ROOT / "CLAUDE.md").read_text().lower()
    for phrase in ("--network=host", "signal-cli", "uv add", "get /health"):
        assert phrase in text, f"CLAUDE.md must carry: {phrase}"


def test_decision_index_lists_the_amended_decisions():
    text = (ROOT / "design" / "09-decision-index.md").read_text()
    for ident in ("D1", "D4", "D6", "DEC-3", "DEC-9", "R-5", "R-10", "R-12"):
        assert ident in text, f"decision index missing {ident}"
```

- [ ] **Step 2: Write `ARCHITECTURE.md`**

Contents: the two-path diagram (ambient / engagement) from `design/00-overview.md`; a package table with *what lives here* and *what it may import*; the port list with current implementations and known gaps; a file-by-file walk of one ambient message and one tag; and the where-does-new-code-go table from spec §10.

- [ ] **Step 3: Write `CLAUDE.md`**

Gate commands (`uv run pytest`, `ruff check`, `mypy`, `lint-imports`, and later `alembic check`), the reading order (README → ARCHITECTURE.md → decision index → `design/*.md` → `docs/superpowers/specs/`), and the **five operational constraints** from spec §2 verbatim — `--network=host`, Honcho `GET /health`, signal-cli neutrality, the archived legacy path, `uv add` not pip.

- [ ] **Step 4: Write `design/09-decision-index.md`**

One line per decision: identifier, one-sentence statement, defining document, and enforcing test where one exists. Cover `D1`–`D6`, the `A*` amendments, `R-1`…`R-16`, and `DEC-1`…`DEC-12`. Note explicitly which decisions have been **amended** (D4 by DEC-3; design/04's mechanism by DEC-9) and which are **accepted but not implemented** (DEC-3's Episodes).

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_architecture_doc.py tests/test_docs.py -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
git add ARCHITECTURE.md CLAUDE.md design/09-decision-index.md tests/test_architecture_doc.py
git commit -m "docs: ARCHITECTURE.md, CLAUDE.md and the decision index

A dated spec records a decision; it is not a map, and it goes stale the moment
the next phase lands. ARCHITECTURE.md is the map, pinned by a test that walks
nora/*/ and fails if a package is undocumented — the same trick test_docs.py
already uses, and what makes the map un-rottable.

The decision index exists because decisions currently live inside long documents
under eight identifier namespaces with no index, and eighteen production modules
cite section numbers of a superseded spec. Rules are versionless; section
numbers are not.

CLAUDE.md carries the five operational constraints from design/08, which this
spec retires — they would otherwise be lost."
```

---

## Phase 1b exit gate

```bash
uv run pytest -q                                       # green
uv run ruff check nora/ evals/ tests/                  # clean
uv run mypy nora/domain nora/config.py nora/app        # clean
uv run lint-imports                                    # clean
ls nora/domain/{models,ports,errors}.py nora/app/composition.py
ls ARCHITECTURE.md CLAUDE.md design/09-decision-index.md
curl -s http://127.0.0.1:9100/health                   # 200, unchanged
```

The container is **not** rebuilt in this phase — no runtime behaviour changed — but `/health` must still answer, proving nothing broke by accident.

## Self-review notes

- **Spec coverage:** §4.1 layout (Tasks 1, 4), §4.2 ports (Task 2), DEC-4 taxonomy (Task 1), DEC-9 identity aliases and DEC-10's budget concept (Task 2 shapes), DEC-12 enforcement (Task 5), §10 conventions and documentation obligations (Task 6).
- **Deliberately deferred:** `ToolProvider`/`ToolSpec` (they live in `agent/ports.py`, not `domain/`, and belong with the tool registry phase); `EpisodeStore` (DEC-3, accepted-not-implemented); the ingest and engagement pipelines; the adapter registry.
- **Known risk:** Task 4's `test_only_app_and_main_import_config` and Task 5's second import-linter contract may both fail against today's code, because `handler.py`, `obs/health.py` and others take `cfg`. That is a true finding. The plan's instruction is to keep the contract with a documented exception rather than delete it — the pipelines phase is what closes it.
