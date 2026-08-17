# Nora Web Portal v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v1 of Nora's friend-group web portal — Discord-OAuth-gated
access to a status module and a memory showcase, built as an installable PWA,
with a module-registry shell that the (later, separately built) game hub and
future modules plug into without touching v1's code.

**Architecture:** A new `nora/web/` Python package adds two read-only routes
(`GET /app/v1/status`, `GET /app/v1/memory`) to the aiohttp app Nora already
runs on `:9100` — no new port, no new backend container. A new `app/`
top-level Next.js 15 app is the only thing friends' browsers reach: Auth.js
(Discord provider) handles login, middleware resolves the authenticated
Discord snowflake against `identity.toml`'s existing roster and blocks
everyone else, and two portal pages poll Nora's new routes through
same-origin Next.js API routes (the browser never calls Nora directly).

**Tech Stack:** Backend — Python, aiohttp (existing), pytest + pytest-aiohttp
(existing). Frontend — Next.js 15 (App Router), React 19, TypeScript,
Tailwind 4, Auth.js v5 (`next-auth@beta`), `zod`, `smol-toml`, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-17-nora-web-design.md`

## Global Constraints

- Route prefix is `/app/v1/*`; the frontend directory is `app/`; the Python
  package stays `nora/web/` (not `nora/app/` — that name is already the
  composition-root package, per spec §3's naming note).
- No WebSocket in v1 — every module is REST + client-side polling (spec §2).
- No OpenAPI/Swagger — hand-written `zod` schemas at the fetch boundary,
  mirroring Pulse's own `pulse-schemas.ts` precedent (spec §3.3).
- Memory showcase shows every whitelisted person's full representation to
  every other whitelisted person — no filtering, no per-person opt-out. This
  is deliberate (spec §2, §5.2), not a gap to fill in this plan.
- UI copy never says "Honcho" (spec §5.2) — "what Nora remembers" / "her
  read on the group" instead.
- `identity.toml` is the whitelist source, loaded fresh per request on the
  frontend side (no hot-reload story needed — the file is tiny; see Task 7).
- PWA: installable (`manifest.json`, `display: standalone`) and a minimal
  service worker for asset caching only — no offline data story (spec §3.2).
- Auth is app-level (Auth.js), not Cloudflare Access (spec §4). Public
  exposure is Cloudflare Tunnel at `nora.67.zone` (spec §6) — out of this
  plan's scope (operator-side DNS/tunnel setup, not application code).
- Game hub, the Life360 map, and interactive chat are **not** built in this
  plan (spec §2, §5.3–§5.5).

---

## File Structure

**Backend (`nora/web/`, new package):**
- `nora/web/__init__.py` — package marker.
- `nora/web/status.py` — `build_status_payload(...)`, the pure data-assembly
  function behind `GET /app/v1/status`.
- `nora/web/memory.py` — `build_memory_payload(...)`, the pure data-assembly
  function behind `GET /app/v1/memory`.
- `nora/web/api.py` — `register_web_routes(app, ...)`, wires both into an
  existing `aiohttp.web.Application`.

**Backend (modified):**
- `nora/app/composition.py` — `Dependencies` gains a `reminders` field.
- `nora/__main__.py` — calls `register_web_routes` on the health app.

**Backend tests:**
- `tests/web/__init__.py`
- `tests/web/test_status.py`
- `tests/web/test_memory.py`
- `tests/test_composition.py` — one new test for the `reminders` field.

**Frontend (`app/`, new top-level directory):**
- `app/package.json`, `app/tsconfig.json`, `app/next.config.ts`,
  `app/postcss.config.mjs`, `app/vitest.config.ts` — scaffold.
- `app/src/app/globals.css` — Tailwind 4 entry (`@import "tailwindcss";`).
- `app/src/app/layout.tsx` — root layout, PWA manifest link.
- `app/src/lib/allowlist.ts` + `app/src/lib/allowlist.test.ts` — the
  `identity.toml` → Discord-snowflake resolver (pure function, unit tested).
- `app/src/auth.ts` — Auth.js config (Discord provider, JWT/session
  callbacks carrying the Discord snowflake).
- `app/src/app/api/auth/[...nextauth]/route.ts` — Auth.js route handlers.
- `app/src/types/next-auth.d.ts` — session type augmentation.
- `app/src/middleware.ts` — session check + allowlist gate.
- `app/src/app/login/page.tsx` — the one public route.
- `app/src/lib/modules.ts` — the module registry (spec §3.1).
- `app/src/lib/status-schema.ts` + `.test.ts` — zod schema + fetch.
- `app/src/lib/memory-schema.ts` + `.test.ts` — zod schema + fetch.
- `app/src/app/api/status/route.ts` — same-origin proxy to Nora's status
  route (keeps `NORA_API_BASE` server-only).
- `app/src/app/api/memory/route.ts` — same-origin proxy to Nora's memory
  route.
- `app/src/app/(portal)/layout.tsx` — portal shell nav, reads `modules.ts`.
- `app/src/app/(portal)/status/page.tsx` — status module page.
- `app/src/app/(portal)/memory/page.tsx` — memory module page.
- `app/public/manifest.json`, `app/public/sw.js` — PWA.

**Deployment (modified):**
- `docker-compose.yml` — new `app` service.

---

### Task 1: Expose `reminders` on `Dependencies`

The reminders store already exists inside `build_dependencies` (a local
`SqlReminderStore`, `nora/app/composition.py:416`) but the frozen
`Dependencies` dataclass has no field for it, so nothing outside
`composition.py` can read it. The status module needs it (Task 3).

**Files:**
- Modify: `nora/app/composition.py` — add a field to `Dependencies`
  (around line 343, after `client: AsyncOpenAI`) and pass it in the final
  `return Dependencies(...)` (around line 1315).
- Test: `tests/test_composition.py`

**Interfaces:**
- Produces: `Dependencies.reminders: ReminderStore` — later tasks read
  `deps.reminders`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_composition.py` (it already imports `Dependencies`,
`build_dependencies`, and has the `cfg` fixture at the top of the file):

```python
async def test_dependencies_exposes_the_reminders_store(cfg):
    """The web API's status route (nora/web/status.py) needs to read pending
    reminders, and the only reminders store this process builds lives inside
    build_dependencies() as a local — this pins it onto Dependencies so
    anything outside composition.py can reach it."""
    from nora.infra.sql.reminders import SqlReminderStore

    deps = await build_dependencies(cfg)
    assert isinstance(deps.reminders, SqlReminderStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py::test_dependencies_exposes_the_reminders_store -v`
Expected: FAIL with `AttributeError: 'Dependencies' object has no attribute 'reminders'` (or a `TypeError` from `Dependencies(...)` if you jump ahead — either way, a failure)

- [ ] **Step 3: Add the field**

In `nora/app/composition.py`, in the `Dependencies` dataclass, add after
`client: AsyncOpenAI`:

```python
    # The reminders store `build_dependencies` already constructs (see
    # `reminders = SqlReminderStore(engine)` below) — exposed here so
    # `nora/web/status.py` can read pending rows without a second store.
    reminders: ReminderStore
```

`ReminderStore` is already imported at the top of the file
(`from nora.domain.reminders import ReminderCaps, ReminderStore`, line 52) —
no new import needed.

In the final `return Dependencies(...)` (around line 1315), add
`reminders=reminders,` to the keyword arguments — e.g. right after
`dispatcher=dispatcher, adapter=adapter, registry=registry,`:

```python
    return Dependencies(
        audit=audit, memory=memory, persons=persons, metrics=metrics,
        policy=policy, assembler=assembler, ingest=ingest, engagement=engagement,
        dispatcher=dispatcher, adapter=adapter, registry=registry,
        account=account, doctrine=doctrine,
        doctrine_store=doctrine_store, debounce=debounce, background=background,
        llm_state=llm_state, client=client, reminders=reminders,
        language_store=language_store, language_consolidators=language_consolidators,
        language_scopes=language_scopes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition.py::test_dependencies_exposes_the_reminders_store -v`
Expected: PASS

- [ ] **Step 5: Run the full composition test file to check nothing else broke**

Run: `uv run pytest tests/test_composition.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add nora/app/composition.py tests/test_composition.py
git commit -m "feat(web): expose reminders store on Dependencies"
```

---

### Task 2: `nora/web/memory.py` — the memory showcase payload

Builds the array `/app/v1/memory` returns: every `identity.toml` person's
current Honcho representation, respecting R-27's fail-closed
`reachable`/`conclusions` split (`nora/domain/ports.py:512` — `Recollection`)
rather than treating an unreachable Honcho the same as "nothing known."

**Files:**
- Create: `nora/web/__init__.py` (empty)
- Create: `nora/web/memory.py`
- Test: `tests/web/__init__.py` (empty), `tests/web/test_memory.py`

**Interfaces:**
- Consumes: `nora.domain.ports.MemoryStore.representation(*, peer_id, target,
  search_query, max_conclusions) -> Recollection` (already exists,
  `nora/domain/ports.py:749`); `nora.persons.Persons.by_person: dict[str,
  PersonRef]` (already exists); `nora.domain.models.PersonRef.name` (already
  exists).
- Produces: `async def build_memory_payload(*, honcho: MemoryStore, persons:
  Persons, ai_peer: str) -> list[dict]` — each entry
  `{"person_id": str, "display_name": str, "reachable": bool, "conclusions":
  list[str]}`. Task 4 (`api.py`) calls this directly.

- [ ] **Step 1: Write the failing test**

Create `tests/web/__init__.py` (empty file).

Create `tests/web/test_memory.py`:

```python
import pytest

from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.memory import build_memory_payload
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


async def test_returns_one_entry_per_whitelisted_person():
    honcho = FakeHoncho(conclusions={
        "miles": ["likes climbing", "works in fintech"],
        "ada": ["plays guitar"],
    })
    persons = _persons(MILES, ADA)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    by_id = {entry["person_id"]: entry for entry in payload}
    assert set(by_id) == {"miles", "ada"}
    assert by_id["miles"]["display_name"] == "Miles"
    assert by_id["miles"]["reachable"] is True
    assert by_id["miles"]["conclusions"] == ["likes climbing", "works in fintech"]
    assert by_id["ada"]["conclusions"] == ["plays guitar"]


async def test_a_person_with_no_conclusions_is_reachable_but_empty():
    honcho = FakeHoncho(conclusions={})
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload == [
        {"person_id": "miles", "display_name": "Miles", "reachable": True, "conclusions": []},
    ]


async def test_an_unreachable_honcho_is_reported_unavailable_not_empty():
    """R-27: an unreachable Honcho must never look identical to 'nothing
    known' — FakeHoncho(fail=True) mirrors the live client's unreachable
    shape (tests/fakes.py's Recollection(reachable=False))."""
    honcho = FakeHoncho(fail=True)
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload == [
        {"person_id": "miles", "display_name": "Miles", "reachable": False, "conclusions": []},
    ]


async def test_queries_honcho_as_the_ai_peer_observing_each_person():
    """Nora's MODEL OF the person, not the person's self-model — the same
    (observer=ai_peer, observed=target) shape nora/engagement/context.py's
    _recall_self/_recall_tagger_card already use."""
    honcho = FakeHoncho(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert honcho.queries == [("nora", "miles", "")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_memory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nora.web'`

- [ ] **Step 3: Write the implementation**

Create `nora/web/__init__.py`:

```python
"""The nora/web/ package registers /app/v1/* onto the aiohttp app Nora
already runs on :9100 (nora/obs/health.py::build_health_app). See
docs/superpowers/specs/2026-08-17-nora-web-design.md."""
```

Create `nora/web/memory.py`:

```python
"""GET /app/v1/memory — the memory showcase (design spec §5.2).

Every identity.toml person's current Honcho representation, shown to every
OTHER whitelisted person with no filtering beyond what Honcho itself already
produced — a deliberate choice (spec §2), not a gap. UI copy built on top of
this must never say "Honcho" (spec §5.2); this module doesn't render copy, so
that rule lives in the frontend, not here.
"""

from __future__ import annotations

from nora.domain.ports import MemoryStore
from nora.persons import Persons

# Generous relative to the tagger/self cards' small caps (nora/engagement/
# context.py uses 3-5) because this is the ONE place a person's whole
# showcased history renders at once, not a per-turn prefill budget.
_MAX_CONCLUSIONS = 20


async def build_memory_payload(
    *, honcho: MemoryStore, persons: Persons, ai_peer: str,
) -> list[dict]:
    """One entry per identity.toml person, each Nora's own model of them
    (observer=ai_peer, observed=that person) — the same `representation`
    verb and (observer, observed) shape as nora/engagement/context.py's
    `_recall_self`/`_recall_tagger_card`, just with no message to scope
    `search_query` to (empty string: the general, not turn-relevant, view)."""
    payload: list[dict] = []
    for person_id, ref in persons.by_person.items():
        recall = await honcho.representation(
            peer_id=ai_peer, target=person_id, search_query="",
            max_conclusions=_MAX_CONCLUSIONS,
        )
        payload.append({
            "person_id": person_id,
            "display_name": ref.name,
            "reachable": recall.reachable,
            "conclusions": list(recall.conclusions),
        })
    return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_memory.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add nora/web/__init__.py nora/web/memory.py tests/web/__init__.py tests/web/test_memory.py
git commit -m "feat(web): memory showcase payload builder"
```

---

### Task 3: `nora/web/status.py` — the status payload

Builds the object `/app/v1/status` returns: uptime, store/Honcho
reachability (re-projected from the same signals `/health` already computes),
and a timestamp-only reminders summary. (A cross-room "recent activity"
field was considered and dropped — `RecordStore.recent()` is scoped to one
`conversation_id`, `nora/domain/ports.py:656`, and no port method spans
every room; adding one is a real port change, not this task's job.)

**Files:**
- Create: `nora/web/status.py`
- Test: `tests/web/test_status.py`

**Interfaces:**
- Consumes: `nora.domain.ports.RecordStore.stats() -> dict` (already exists,
  used by `nora/obs/health.py`); `nora.domain.ports.MemoryStore.health() ->
  MemoryHealth` (already exists); `nora.domain.reminders.ReminderStore
  .all_pending(*, limit: int) -> Sequence[Reminder]` (already exists,
  `nora/domain/reminders.py:503`) — `Reminder.due_at: datetime` is the only
  field read (never `.text`, per the timestamps-not-content rule, spec §5.1).
- Produces: `async def build_status_payload(*, store, honcho, reminders,
  started_at: float) -> dict` — `{"uptime_seconds": float, "store":
  {"reachable": bool}, "honcho": {"reachable": bool}, "reminders": {"count":
  int, "next_due_at": str | None}}`. Task 4 calls this directly.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_status.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.reminders import Reminder, ReminderState
from nora.testing import FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.status import build_status_payload
from tests.fakes import FakeHoncho


def _reminder(due_at: datetime) -> Reminder:
    return Reminder(
        id=uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="g1", conversation_type="group",
        text="a private sentence that must never reach the web API",
        created_at=due_at, due_at=due_at, state=ReminderState.PENDING,
        fired_at=None, outcome=None,
    )


async def test_reports_uptime_from_started_at(monkeypatch):
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["uptime_seconds"] > 0


async def test_reports_store_and_honcho_reachability_when_healthy():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["store"] == {"reachable": True}
    assert payload["honcho"] == {"reachable": True}


async def test_reports_honcho_unreachable_without_raising():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(fail=True), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["honcho"] == {"reachable": False}


async def test_reminders_summary_has_count_and_earliest_due_at_only():
    store = FakeReminderStore()
    earlier = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    later = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
    await store.create(_reminder(later))
    await store.create(_reminder(earlier))

    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=store, started_at=0.0,
    )

    assert payload["reminders"] == {"count": 2, "next_due_at": "2026-08-18T09:00:00+00:00"}
    assert "text" not in str(payload)
    assert "private sentence" not in str(payload)


async def test_reminders_summary_when_none_pending():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["reminders"] == {"count": 0, "next_due_at": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nora.web.status'`

- [ ] **Step 3: Write the implementation**

Create `nora/web/status.py`:

```python
"""GET /app/v1/status — Nora's public face (design spec §5.1).

A thin, filtered re-projection of what nora/obs/health.py::build_health_app
already computes, plus a reminders summary. Deliberately NOT the full
/health body: this is a friend-facing page, not an operator dashboard, and
carries none of /health's SSE/platform/doctrine-sha internals.
"""

from __future__ import annotations

import time

from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore

# All-pending, not due(): the operator's-eye-view nora/app/cli.py's
# `nora reminders list` already uses (nora/domain/reminders.py:503's
# docstring) — every conversation's pending rows, not just overdue ones.
_REMINDERS_LIMIT = 50


async def build_status_payload(
    *, store: RecordStore, honcho: MemoryStore, reminders: ReminderStore,
    started_at: float,
) -> dict:
    try:
        await store.stats()
        store_reachable = True
    except Exception:                          # noqa: BLE001 — never raise from a status read
        store_reachable = False

    try:
        honcho_reachable = (await honcho.health()).reachable
    except Exception:                          # noqa: BLE001
        honcho_reachable = False

    rows = await reminders.all_pending(limit=_REMINDERS_LIMIT)
    # Timestamps only, never `.text` — a reminder's sentence is the
    # requester's own words and stays off this friend-facing surface.
    next_due_at = min((r.due_at for r in rows), default=None)

    return {
        "uptime_seconds": time.time() - started_at,
        "store": {"reachable": store_reachable},
        "honcho": {"reachable": honcho_reachable},
        "reminders": {
            "count": len(rows),
            "next_due_at": next_due_at.isoformat() if next_due_at else None,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_status.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add nora/web/status.py tests/web/test_status.py
git commit -m "feat(web): status payload builder with reminders summary"
```

---

### Task 4: `nora/web/api.py` — register the routes

Wires Task 2 and Task 3's payload builders onto an `aiohttp.web.Application`
— same DI shape as `nora/obs/health.py::build_health_app`.

**Files:**
- Create: `nora/web/api.py`
- Test: `tests/web/test_api.py`

**Interfaces:**
- Consumes: `build_status_payload` (Task 3), `build_memory_payload`
  (Task 2).
- Produces: `def register_web_routes(app: web.Application, *, store,
  honcho, reminders, persons, ai_peer: str, started_at: float) -> None` —
  Task 5 calls this on the `health_app` `build_health_app` already returns.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_api.py`:

```python
import pytest
from aiohttp import web

from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.testing import FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")


@pytest.fixture
async def client(aiohttp_client):
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeHoncho(conclusions={"miles": ["x"]}),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES}, index={}),
        ai_peer="nora", started_at=0.0,
    )
    return await aiohttp_client(app)


async def test_status_route_returns_200_with_the_expected_shape(client):
    resp = await client.get("/app/v1/status")
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"uptime_seconds", "store", "honcho", "reminders"}


async def test_memory_route_returns_200_with_every_person(client):
    resp = await client.get("/app/v1/memory")
    assert resp.status == 200
    body = await resp.json()
    assert body == [
        {"person_id": "miles", "display_name": "Miles", "reachable": True, "conclusions": ["x"]},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nora.web.api'`

- [ ] **Step 3: Write the implementation**

Create `nora/web/api.py`:

```python
"""Registers GET /app/v1/status and GET /app/v1/memory onto an existing
aiohttp Application — same process/container as Nora (design spec §3).

Deliberately a `register_*` function taking an existing `web.Application`,
not a `build_*_app` returning a new one: these routes join the health app
nora/obs/health.py::build_health_app already builds and nora/__main__.py
already serves on :9100 — a second app would mean a second port, which the
spec's deployment section (§6) explicitly rules out.
"""

from __future__ import annotations

from aiohttp import web

from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore
from nora.persons import Persons
from nora.web.memory import build_memory_payload
from nora.web.status import build_status_payload


def register_web_routes(
    app: web.Application, *, store: RecordStore, honcho: MemoryStore,
    reminders: ReminderStore, persons: Persons, ai_peer: str, started_at: float,
) -> None:
    app["web_store"] = store
    app["web_honcho"] = honcho
    app["web_reminders"] = reminders
    app["web_persons"] = persons
    app["web_ai_peer"] = ai_peer
    app["web_started_at"] = started_at

    async def status(request: web.Request) -> web.Response:
        a = request.app
        body = await build_status_payload(
            store=a["web_store"], honcho=a["web_honcho"], reminders=a["web_reminders"],
            started_at=a["web_started_at"],
        )
        return web.json_response(body)

    async def memory(request: web.Request) -> web.Response:
        a = request.app
        body = await build_memory_payload(
            honcho=a["web_honcho"], persons=a["web_persons"], ai_peer=a["web_ai_peer"],
        )
        return web.json_response(body)

    app.router.add_get("/app/v1/status", status)
    app.router.add_get("/app/v1/memory", memory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nora/web/api.py tests/web/test_api.py
git commit -m "feat(web): register /app/v1/status and /app/v1/memory routes"
```

---

### Task 5: Wire the routes into `nora/__main__.py`

Calls `register_web_routes` on the same `health_app` object
`build_health_app` returns, before `serve()` starts listening — so
`:9100` serves `/health`, `/metrics`, `/app/v1/status`, and `/app/v1/memory`
from one process, one port, matching spec §6.

**Files:**
- Modify: `nora/__main__.py:95-104`

**Interfaces:**
- Consumes: `register_web_routes` (Task 4), `deps.reminders` (Task 1),
  `deps.persons` (already exists), `self.cfg.memory.ai_peer` (already
  exists, confirmed at `nora/app/composition.py:364`).

- [ ] **Step 1: Add the import**

In `nora/__main__.py`, add beside the existing `from .obs.health import
build_health_app, serve` (line 25):

```python
from .web.api import register_web_routes
```

- [ ] **Step 2: Hoist `started_at` and call `register_web_routes`**

Replace `App.run`'s existing block (`nora/__main__.py:95-104`):

```python
    async def run(self) -> None:
        deps = self.deps
        health_app = build_health_app(
            adapter=deps.adapter, adapters=deps.registry.adapters,
            store=deps.audit, honcho=deps.memory,
            doctrine_store=deps.doctrine_store, metrics=deps.metrics,
            started_at=time.time(), llm_state=deps.llm_state,
            identity_file=self.cfg.identity_file, policy_file=self.cfg.policy_file,
        )
        self._health_runner = await serve(health_app, self.cfg.obs.metrics.port)
```

with:

```python
    async def run(self) -> None:
        deps = self.deps
        # Shared with register_web_routes below so /health's uptime and
        # /app/v1/status's uptime never disagree about when this process
        # started.
        started_at = time.time()
        health_app = build_health_app(
            adapter=deps.adapter, adapters=deps.registry.adapters,
            store=deps.audit, honcho=deps.memory,
            doctrine_store=deps.doctrine_store, metrics=deps.metrics,
            started_at=started_at, llm_state=deps.llm_state,
            identity_file=self.cfg.identity_file, policy_file=self.cfg.policy_file,
        )
        register_web_routes(
            health_app, store=deps.audit, honcho=deps.memory, reminders=deps.reminders,
            persons=deps.persons, ai_peer=self.cfg.memory.ai_peer, started_at=started_at,
        )
        self._health_runner = await serve(health_app, self.cfg.obs.metrics.port)
```

- [ ] **Step 3: Run the full test suite to check nothing broke**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 4: Run lint and types**

Run: `uv run ruff check nora/ evals/ tests/`
Expected: clean

Run: `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`
Expected: clean (note: `nora/web/` and `nora/__main__.py` are not in this
scoped mypy target list, so a type error introduced only there won't be
caught here — read the diff carefully instead)

- [ ] **Step 5: Manual smoke check**

Run: `uv run python -c "from nora.__main__ import App; print('imports clean')"`
Expected: prints `imports clean` with no `ImportError`

- [ ] **Step 6: Commit**

```bash
git add nora/__main__.py
git commit -m "feat(web): serve /app/v1/* from the same process as /health"
```

---

### Task 6: Scaffold `app/` — Next.js 15, Tailwind 4, Vitest

The first frontend task. Produces a running (if empty) Next.js app with
TypeScript, Tailwind 4, and Vitest wired — the foundation every later
frontend task builds on.

**Files:**
- Create: `app/package.json`
- Create: `app/tsconfig.json`
- Create: `app/next.config.ts`
- Create: `app/postcss.config.mjs`
- Create: `app/vitest.config.ts`
- Create: `app/src/app/globals.css`
- Create: `app/src/app/layout.tsx`
- Create: `app/src/app/page.tsx`
- Create: `app/.gitignore`

**Interfaces:**
- Produces: a `npm run build`-able, `npm test`-able app for every later task
  to add files into.

- [ ] **Step 1: Write `package.json`**

```json
{
  "name": "nora-app",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "^15.5.22",
    "next-auth": "5.0.0-beta.25",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "smol-toml": "^1.3.1",
    "zod": "^3.25.6"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "4.0.9",
    "@types/node": "^22.15.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "tailwindcss": "4.0.9",
    "typescript": "^5.8.3",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Step 2: Write `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: Write `next.config.ts`, `postcss.config.mjs`, `.gitignore`**

`app/next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {};

export default nextConfig;
```

`app/postcss.config.mjs`:

```js
export default {
  plugins: { "@tailwindcss/postcss": {} },
};
```

`app/.gitignore`:

```
node_modules
.next
dist
*.tsbuildinfo
.env*.local
```

- [ ] **Step 4: Write `vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
```

- [ ] **Step 5: Write the minimal app shell**

`app/src/app/globals.css`:

```css
@import "tailwindcss";
```

`app/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Nora",
  description: "Nora's public face",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

`app/src/app/page.tsx`:

```tsx
export default function Home() {
  return <main className="p-8">Nora</main>;
}
```

- [ ] **Step 6: Install and verify the build**

Run: `cd app && npm install`
Expected: installs cleanly

Run: `cd app && npm run build`
Expected: build succeeds

Run: `cd app && npm test`
Expected: passes (no test files yet — Vitest reports 0 tests, exit 0)

- [ ] **Step 7: Commit**

```bash
git add app/
git commit -m "feat(app): scaffold Next.js 15 + Tailwind 4 + Vitest"
```

---

### Task 7: `app/src/lib/allowlist.ts` — the `identity.toml` resolver

The pure function behind the auth gate: given `identity.toml`'s raw text and
a Discord snowflake, returns the matching person or `null`. Deliberately
re-parses the file per call rather than caching — it's tiny (one friend
group's worth of entries) and this sidesteps a stale-cache story nobody
asked for (contrast with the Python side's load-once-at-startup, which
exists because Nora's process is long-lived in a way a single parse call
isn't).

**Files:**
- Create: `app/src/lib/allowlist.ts`
- Test: `app/src/lib/allowlist.test.ts`

**Interfaces:**
- Produces: `function resolveDiscordPerson(identityToml: string, discordUserId:
  string): { personId: string; displayName: string } | null` — Task 9
  (middleware) is the consumer.

- [ ] **Step 1: Write the failing test**

Create `app/src/lib/allowlist.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { resolveDiscordPerson } from "./allowlist";

const IDENTITY_TOML = `
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "+85212345678"

[[persons.miles.discord]]
user_id = "100000000000000003"

[persons.ada]
name = "Ada"

[[persons.ada.discord]]
user_id = "988260089562885951"
`;

describe("resolveDiscordPerson", () => {
  it("resolves a known Discord snowflake to its person", () => {
    const result = resolveDiscordPerson(IDENTITY_TOML, "100000000000000003");
    expect(result).toEqual({ personId: "miles", displayName: "Miles" });
  });

  it("resolves a second person's snowflake independently", () => {
    const result = resolveDiscordPerson(IDENTITY_TOML, "988260089562885951");
    expect(result).toEqual({ personId: "ada", displayName: "Ada" });
  });

  it("returns null for an unknown snowflake", () => {
    expect(resolveDiscordPerson(IDENTITY_TOML, "000000000000000000")).toBeNull();
  });

  it("returns null for a person with no discord block at all", () => {
    const toml = `
[persons.signalonly]
name = "Signal Only"

[[persons.signalonly.signal]]
phone = "+85200000000"
`;
    expect(resolveDiscordPerson(toml, "100000000000000003")).toBeNull();
  });

  it("never matches on username, only user_id", () => {
    const toml = `
[persons.miles]
name = "Miles"

[[persons.miles.discord]]
user_id = "100000000000000003"
username = "pinegrove"
`;
    expect(resolveDiscordPerson(toml, "pinegrove")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — `Cannot find module './allowlist'`

- [ ] **Step 3: Write the implementation**

Run: `cd app && npm install smol-toml` (already in `package.json` from Task 6,
this is just confirming the lockfile — skip if already installed)

Create `app/src/lib/allowlist.ts`:

```ts
import { parse } from "smol-toml";

/**
 * identity.toml's shape (design/10-identity-and-policy-schema.md):
 *
 *   [persons.miles]
 *   name = "Miles"
 *   [[persons.miles.discord]]
 *   user_id = "100000000000000003"
 *
 * TOML parses this as { persons: { miles: { name, discord: [{ user_id }] } } }.
 * `user_id` is the ONLY discord field ever matched on — `username` is
 * accepted in the schema for readability but never indexed, because it's
 * renameable (spec: design/10 §3.1's "username is accepted and never
 * matched" rule). This mirrors that on the frontend rather than trusting
 * every caller to remember it.
 */
interface IdentityToml {
  persons?: Record<
    string,
    {
      name?: string;
      discord?: Array<{ user_id?: string }>;
    }
  >;
}

export function resolveDiscordPerson(
  identityToml: string,
  discordUserId: string,
): { personId: string; displayName: string } | null {
  const parsed = parse(identityToml) as IdentityToml;
  const persons = parsed.persons ?? {};

  for (const [personId, person] of Object.entries(persons)) {
    const discordBlocks = person.discord ?? [];
    const matches = discordBlocks.some((block) => block.user_id === discordUserId);
    if (matches) {
      return { personId, displayName: person.name ?? personId };
    }
  }
  return null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm test`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/allowlist.ts app/src/lib/allowlist.test.ts app/package.json app/package-lock.json
git commit -m "feat(app): identity.toml Discord-snowflake resolver"
```

---

### Task 8: Auth.js — Discord provider

Wires Auth.js v5 with the Discord OAuth provider, carrying the Discord
snowflake from the OAuth account into the session (Auth.js's default session
shape doesn't include it — needs explicit `jwt`/`session` callbacks).

**Files:**
- Create: `app/src/auth.ts`
- Create: `app/src/app/api/auth/[...nextauth]/route.ts`
- Create: `app/src/types/next-auth.d.ts`

**Interfaces:**
- Produces: `auth()` (session reader), `handlers` (route handlers),
  `signIn`/`signOut` — all exported from `@/auth`. Task 9 (middleware)
  consumes `auth`; `session.user.discordId: string` is the field it reads.

- [ ] **Step 1: Write `app/src/auth.ts`**

No test here — this is thin config wrapping a third-party library
(`next-auth`), not logic this repo owns; `allowlist.ts` (Task 7, already
tested) is where the actual decision logic lives.

```ts
import NextAuth from "next-auth";
import Discord from "next-auth/providers/discord";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Discord],
  callbacks: {
    // Discord's OAuth `providerAccountId` IS the snowflake
    // (design/10-identity-and-policy-schema.md's "the snowflake, via
    // Developer Mode") — carried onto the JWT on first sign-in only, since
    // `account` is undefined on every subsequent call.
    async jwt({ token, account }) {
      if (account?.provider === "discord") {
        token.discordId = account.providerAccountId;
      }
      return token;
    },
    async session({ session, token }) {
      if (typeof token.discordId === "string") {
        session.user.discordId = token.discordId;
      }
      return session;
    },
  },
});
```

- [ ] **Step 2: Write the route handler**

Create `app/src/app/api/auth/[...nextauth]/route.ts`:

```ts
import { handlers } from "@/auth";

export const { GET, POST } = handlers;
```

- [ ] **Step 3: Write the session type augmentation**

Create `app/src/types/next-auth.d.ts`:

```ts
import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      discordId?: string;
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    discordId?: string;
  }
}
```

- [ ] **Step 4: Verify the build still succeeds**

Run: `cd app && npm run build`
Expected: fails with a clear error about missing `AUTH_DISCORD_ID`/
`AUTH_DISCORD_SECRET`/`AUTH_SECRET` env vars ONLY if Next.js eagerly
validates them at build time — if so, add placeholder values for the build
check:

Run: `cd app && AUTH_DISCORD_ID=x AUTH_DISCORD_SECRET=x AUTH_SECRET=x npm run build`
Expected: build succeeds

- [ ] **Step 5: Commit**

```bash
git add app/src/auth.ts app/src/app/api/auth app/src/types
git commit -m "feat(app): Auth.js Discord provider with snowflake on session"
```

---

### Task 9: `app/src/middleware.ts` — the auth + allowlist gate

The enforcement point: every portal route requires a valid session AND an
`identity.toml` match; `/login` and the Auth.js routes are the only
exceptions.

**Files:**
- Create: `app/src/middleware.ts`
- Create: `app/src/app/login/page.tsx`

**Interfaces:**
- Consumes: `auth` (Task 8), `resolveDiscordPerson` (Task 7, already unit
  tested — middleware itself is thin glue over it, matching Task 8's "thin
  config, no new test" reasoning).
- Produces: every downstream page (Tasks 11, 12) can assume it only ever
  runs for an allowlisted, authenticated request.

- [ ] **Step 1: Write `app/src/middleware.ts`**

```ts
import { NextResponse } from "next/server";
import fs from "node:fs";
import { auth } from "@/auth";
import { resolveDiscordPerson } from "@/lib/allowlist";

// identity.toml is bind-mounted read-only into this container at deploy
// time (docker-compose.yml's `app` service, Task 14) — the SAME file Nora's
// own process reads, so the web portal's whitelist can never drift from
// Nora's roster.
const IDENTITY_TOML_PATH = process.env.IDENTITY_TOML_PATH ?? "/app/identity.toml";

const PUBLIC_PATHS = ["/login", "/api/auth"];

export default auth((req) => {
  const { pathname } = req.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  const discordId = req.auth?.user?.discordId;
  if (!discordId) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  const identityToml = fs.readFileSync(IDENTITY_TOML_PATH, "utf-8");
  const person = resolveDiscordPerson(identityToml, discordId);
  if (!person) {
    return NextResponse.redirect(new URL("/login?denied=1", req.url));
  }

  const res = NextResponse.next();
  res.headers.set("x-nora-person-id", person.personId);
  res.headers.set("x-nora-display-name", person.displayName);
  return res;
});

export const config = {
  // Excludes Next's internal assets, the PWA manifest/service worker (must
  // stay reachable unauthenticated for the browser to install the app), and
  // static files.
  matcher: ["/((?!_next|favicon.ico|manifest.json|sw.js|.*\\..*).*)"],
  // LOAD-BEARING: middleware runs on the Edge runtime by default, where
  // Node's `fs` module doesn't exist. `resolveDiscordPerson` needs to read
  // identity.toml off disk, so this opts into the Node.js middleware
  // runtime (stable since Next.js 15.2) — dropping this line makes every
  // request 500 with "Module not found: Can't resolve 'node:fs'".
  runtime: "nodejs",
};
```

- [ ] **Step 2: Write `app/src/app/login/page.tsx`**

```tsx
import { signIn } from "@/auth";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ denied?: string }>;
}) {
  const { denied } = await searchParams;
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-2xl font-semibold">Nora</h1>
      {denied && (
        <p className="text-sm text-red-600">
          That Discord account isn&apos;t on the list. Ask Miles to add it.
        </p>
      )}
      <form
        action={async () => {
          "use server";
          await signIn("discord");
        }}
      >
        <button
          type="submit"
          className="rounded-md bg-[#5865F2] px-4 py-2 text-white hover:bg-[#4752C4]"
        >
          Sign in with Discord
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 3: Verify the build succeeds**

Run: `cd app && AUTH_DISCORD_ID=x AUTH_DISCORD_SECRET=x AUTH_SECRET=x npm run build`
Expected: build succeeds

- [ ] **Step 4: Commit**

```bash
git add app/src/middleware.ts app/src/app/login
git commit -m "feat(app): auth + identity.toml allowlist gate"
```

---

### Task 10: `app/src/lib/modules.ts` — the module registry

The single place a nav entry gets declared (spec §3.1) — status and memory
now, game hub/future modules later add one entry each here and nowhere else
in the shell.

**Files:**
- Create: `app/src/lib/modules.ts`
- Test: `app/src/lib/modules.test.ts`

**Interfaces:**
- Produces: `MODULES: Module[]`, `type Module = { id: string; label: string;
  href: string; status: "live" | "designed" | "future" }`. Task 12's portal
  layout consumes this to render the nav.

- [ ] **Step 1: Write the failing test**

Create `app/src/lib/modules.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { MODULES } from "./modules";

describe("MODULES", () => {
  it("lists status and memory as live, with unique ids", () => {
    const ids = MODULES.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
    const live = MODULES.filter((m) => m.status === "live").map((m) => m.id);
    expect(live).toEqual(["status", "memory"]);
  });

  it("every module has a leading-slash href", () => {
    for (const m of MODULES) {
      expect(m.href.startsWith("/")).toBe(true);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — `Cannot find module './modules'`

- [ ] **Step 3: Write the implementation**

Create `app/src/lib/modules.ts`:

```ts
/**
 * The module registry (design spec §3.1) — the ONE place a nav entry gets
 * declared. Game hub and future modules (Life360 map, 1-on-1 chat) each add
 * one entry here when they're built; nothing else in the portal shell
 * changes.
 */
export interface Module {
  id: string;
  label: string;
  href: string;
  status: "live" | "designed" | "future";
}

export const MODULES: Module[] = [
  { id: "status", label: "Status", href: "/status", status: "live" },
  { id: "memory", label: "Memory", href: "/memory", status: "live" },
];
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/modules.ts app/src/lib/modules.test.ts
git commit -m "feat(app): module registry"
```

---

### Task 11: Status module — schema, proxy route, page

**Files:**
- Create: `app/src/lib/status-schema.ts`
- Test: `app/src/lib/status-schema.test.ts`
- Create: `app/src/app/api/status/route.ts`
- Create: `app/src/app/(portal)/layout.tsx`
- Create: `app/src/app/(portal)/status/page.tsx`

**Interfaces:**
- Consumes: `MODULES` (Task 10); Nora's `GET /app/v1/status` (Task 5) —
  matched exactly by this schema.
- Produces: `StatusSchema` (zod), `fetchStatus(): Promise<Status>` — a
  browser-side helper the status page's client component polls.

- [ ] **Step 1: Write the failing test**

Create `app/src/lib/status-schema.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { StatusSchema } from "./status-schema";

describe("StatusSchema", () => {
  it("parses a real /app/v1/status response shape", () => {
    const body = {
      uptime_seconds: 1234.5,
      store: { reachable: true },
      honcho: { reachable: true },
      reminders: { count: 2, next_due_at: "2026-08-18T09:00:00+00:00" },
    };
    expect(() => StatusSchema.parse(body)).not.toThrow();
  });

  it("parses next_due_at: null", () => {
    const body = {
      uptime_seconds: 0,
      store: { reachable: false },
      honcho: { reachable: false },
      reminders: { count: 0, next_due_at: null },
    };
    expect(() => StatusSchema.parse(body)).not.toThrow();
  });

  it("rejects a response missing a required field", () => {
    expect(() => StatusSchema.parse({ uptime_seconds: 0 })).toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — `Cannot find module './status-schema'`

- [ ] **Step 3: Write `status-schema.ts`**

```ts
import { z } from "zod";

// Mirrors nora/web/status.py::build_status_payload's return shape exactly
// (design spec §3.3: hand-written zod instead of OpenAPI/codegen).
export const StatusSchema = z.object({
  uptime_seconds: z.number(),
  store: z.object({ reachable: z.boolean() }),
  honcho: z.object({ reachable: z.boolean() }),
  reminders: z.object({
    count: z.number(),
    next_due_at: z.string().nullable(),
  }),
});

export type Status = z.infer<typeof StatusSchema>;

export async function fetchStatus(): Promise<Status> {
  const res = await fetch("/api/status", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`status fetch failed: ${res.status}`);
  }
  return StatusSchema.parse(await res.json());
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm test`
Expected: PASS

- [ ] **Step 5: Write the same-origin proxy route**

Create `app/src/app/api/status/route.ts`:

```ts
import { NextResponse } from "next/server";

// Server-only: NORA_API_BASE is never sent to the browser. The browser
// calls same-origin /api/status; this route is the only thing that ever
// reaches Nora's :9100 directly (design spec §6's "browser never talks to
// Nora's API directly — only to Next.js").
const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET() {
  const res = await fetch(`${NORA_API_BASE}/app/v1/status`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
```

- [ ] **Step 6: Write the portal layout and status page**

Create `app/src/app/(portal)/layout.tsx`:

```tsx
import Link from "next/link";
import { MODULES } from "@/lib/modules";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <nav className="flex gap-4 border-b border-neutral-200 px-6 py-4">
        {MODULES.map((m) => (
          <Link key={m.id} href={m.href} className="text-sm font-medium hover:underline">
            {m.label}
          </Link>
        ))}
      </nav>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
```

Create `app/src/app/(portal)/status/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchStatus, type Status } from "@/lib/status-schema";

const POLL_INTERVAL_MS = 20_000;

export default function StatusPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchStatus()
        .then((s) => {
          if (!cancelled) {
            setStatus(s);
            setError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (error) return <p className="text-red-600">Couldn&apos;t reach Nora.</p>;
  if (!status) return <p>Loading…</p>;

  return (
    <div className="space-y-2">
      <h1 className="text-xl font-semibold">Status</h1>
      <p>Uptime: {Math.floor(status.uptime_seconds / 3600)}h</p>
      <p>Store: {status.store.reachable ? "reachable" : "unreachable"}</p>
      <p>Memory: {status.honcho.reachable ? "reachable" : "unreachable"}</p>
      <p>
        Reminders pending: {status.reminders.count}
        {status.reminders.next_due_at && ` (next ${status.reminders.next_due_at})`}
      </p>
    </div>
  );
}
```

- [ ] **Step 7: Verify the build succeeds**

Run: `cd app && AUTH_DISCORD_ID=x AUTH_DISCORD_SECRET=x AUTH_SECRET=x npm run build`
Expected: build succeeds

- [ ] **Step 8: Commit**

```bash
git add app/src/lib/status-schema.ts app/src/lib/status-schema.test.ts app/src/app/api/status app/src/app/\(portal\)
git commit -m "feat(app): status module — schema, proxy route, page"
```

---

### Task 12: Memory module — schema, proxy route, page

**Files:**
- Create: `app/src/lib/memory-schema.ts`
- Test: `app/src/lib/memory-schema.test.ts`
- Create: `app/src/app/api/memory/route.ts`
- Create: `app/src/app/(portal)/memory/page.tsx`

**Interfaces:**
- Consumes: Nora's `GET /app/v1/memory` (Task 5).
- Produces: `MemorySchema` (zod), `fetchMemory(): Promise<MemoryEntry[]>`.

- [ ] **Step 1: Write the failing test**

Create `app/src/lib/memory-schema.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { MemorySchema } from "./memory-schema";

describe("MemorySchema", () => {
  it("parses a real /app/v1/memory response shape", () => {
    const body = [
      { person_id: "miles", display_name: "Miles", reachable: true, conclusions: ["likes climbing"] },
      { person_id: "ada", display_name: "Ada", reachable: false, conclusions: [] },
    ];
    expect(() => MemorySchema.parse(body)).not.toThrow();
  });

  it("rejects an entry missing display_name", () => {
    expect(() =>
      MemorySchema.parse([{ person_id: "miles", reachable: true, conclusions: [] }]),
    ).toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm test`
Expected: FAIL — `Cannot find module './memory-schema'`

- [ ] **Step 3: Write `memory-schema.ts`**

```ts
import { z } from "zod";

// Mirrors nora/web/memory.py::build_memory_payload's return shape exactly.
export const MemoryEntrySchema = z.object({
  person_id: z.string(),
  display_name: z.string(),
  reachable: z.boolean(),
  conclusions: z.array(z.string()),
});

export const MemorySchema = z.array(MemoryEntrySchema);

export type MemoryEntry = z.infer<typeof MemoryEntrySchema>;

export async function fetchMemory(): Promise<MemoryEntry[]> {
  const res = await fetch("/api/memory", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`memory fetch failed: ${res.status}`);
  }
  return MemorySchema.parse(await res.json());
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm test`
Expected: PASS

- [ ] **Step 5: Write the proxy route**

Create `app/src/app/api/memory/route.ts`:

```ts
import { NextResponse } from "next/server";

const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

export async function GET() {
  const res = await fetch(`${NORA_API_BASE}/app/v1/memory`, { cache: "no-store" });
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
```

- [ ] **Step 6: Write the memory page**

Create `app/src/app/(portal)/memory/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchMemory, type MemoryEntry } from "@/lib/memory-schema";

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchMemory()
      .then(setEntries)
      .catch(() => setError(true));
  }, []);

  if (error) return <p className="text-red-600">Couldn&apos;t reach Nora.</p>;
  if (!entries) return <p>Loading…</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">What Nora remembers</h1>
      {entries.map((entry) => (
        <section key={entry.person_id} className="rounded-lg border p-4">
          <h2 className="font-medium">{entry.display_name}</h2>
          {!entry.reachable && (
            <p className="text-sm text-neutral-500">unavailable right now</p>
          )}
          {entry.reachable && entry.conclusions.length === 0 && (
            <p className="text-sm text-neutral-500">nothing yet</p>
          )}
          {entry.reachable && entry.conclusions.length > 0 && (
            <ul className="list-disc pl-5 text-sm">
              {entry.conclusions.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Verify the build succeeds**

Run: `cd app && AUTH_DISCORD_ID=x AUTH_DISCORD_SECRET=x AUTH_SECRET=x npm run build`
Expected: build succeeds

- [ ] **Step 8: Commit**

```bash
git add app/src/lib/memory-schema.ts app/src/lib/memory-schema.test.ts app/src/app/api/memory "app/src/app/(portal)/memory"
git commit -m "feat(app): memory showcase module — schema, proxy route, page"
```

---

### Task 13: PWA — manifest and service worker

**Files:**
- Create: `app/public/manifest.json`
- Create: `app/public/sw.js`
- Create: `app/src/app/register-sw.tsx`
- Modify: `app/src/app/layout.tsx`

**Interfaces:**
- Produces: an installable app (`display: standalone`) with asset caching
  via a minimal service worker — no offline data story (spec §3.2).

- [ ] **Step 1: Write `app/public/manifest.json`**

```json
{
  "name": "Nora",
  "short_name": "Nora",
  "start_url": "/status",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#111111",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

Note: `icon-192.png`/`icon-512.png` are real image assets this plan cannot
generate — placeholder PNGs (any 192x192 / 512x512 image) must be dropped
into `app/public/` before this manifest is spec-valid; browsers accept a
manifest that references a missing icon (they just don't show one), so this
doesn't block the build.

- [ ] **Step 2: Write `app/public/sw.js`**

```js
// Asset caching only — no offline data story (design spec §3.2: every
// module is a live fetch against Nora/Pulse, and there's nothing meaningful
// to show offline). This exists to make the app installable and fast on
// repeat loads, not to work without a network.
const CACHE_NAME = "nora-static-v1";
const STATIC_ASSETS = ["/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  // Never intercept API calls — /api/status and /api/memory must always
  // hit the network; a cached status page would silently go stale.
  if (request.url.includes("/api/")) return;

  event.respondWith(
    caches.match(request).then((cached) => cached ?? fetch(request)),
  );
});
```

- [ ] **Step 3: Write the registration component**

Create `app/src/app/register-sw.tsx`:

```tsx
"use client";

import { useEffect } from "react";

export function RegisterServiceWorker() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // Best-effort: a failed registration degrades to "not installable,"
        // never to a broken app.
      });
    }
  }, []);
  return null;
}
```

- [ ] **Step 4: Wire the manifest link and registration into the root layout**

Modify `app/src/app/layout.tsx` — add the manifest to `metadata` and render
`RegisterServiceWorker`:

```tsx
import type { Metadata } from "next";
import "./globals.css";
import { RegisterServiceWorker } from "./register-sw";

export const metadata: Metadata = {
  title: "Nora",
  description: "Nora's public face",
  manifest: "/manifest.json",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <RegisterServiceWorker />
      </body>
    </html>
  );
}
```

- [ ] **Step 5: Verify the build succeeds**

Run: `cd app && AUTH_DISCORD_ID=x AUTH_DISCORD_SECRET=x AUTH_SECRET=x npm run build`
Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add app/public/manifest.json app/public/sw.js app/src/app/register-sw.tsx app/src/app/layout.tsx
git commit -m "feat(app): PWA manifest and asset-caching service worker"
```

---

### Task 14: Deployment — the `app` service

Adds the `app` service to `docker-compose.yml`, on the existing `nora`
network, bound to loopback only (spec §6). Public exposure via Cloudflare
Tunnel is an operator-side step (DNS + `cloudflared` config), out of this
plan's scope — this task only makes the container reachable on
`127.0.0.1` for `cloudflared` (or a developer) to point at.

**Files:**
- Modify: `docker-compose.yml`
- Create: `app/Dockerfile`
- Create: `app/.dockerignore`

**Interfaces:**
- Consumes: the `nora` service already listening on `:9100` inside the
  `nora` network (existing); `identity.toml` (existing, already
  bind-mounted into other services the same way).

- [ ] **Step 1: Write `app/Dockerfile`**

```dockerfile
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/package.json /app/package-lock.json ./
RUN npm ci --omit=dev
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.ts ./
EXPOSE 3000
CMD ["npm", "start"]
```

- [ ] **Step 2: Write `app/.dockerignore`**

```
node_modules
.next
.git
*.test.ts
```

- [ ] **Step 3: Add the `app` service to `docker-compose.yml`**

Add a new service after the existing `nora` service block (find it with
`grep -n "^  nora:" docker-compose.yml` — insert after that block ends,
before `# polls each circle derived from identity.toml` around line 255,
i.e. before the `presence` service):

```yaml
  # The Next.js portal (design spec: docs/superpowers/specs/2026-08-17-
  # nora-web-design.md). Loopback-only, same pattern as `nora` itself
  # (spec §6) — public reachability is a separate, operator-side
  # Cloudflare Tunnel step, not this compose file's job.
  app:
    build:
      context: ./app
      network: host
    image: nora-app:latest
    container_name: nora-app
    restart: unless-stopped
    networks: [nora]
    ports: ["127.0.0.1:3000:3000"]
    depends_on:
      nora:
        condition: service_healthy
    environment:
      - NORA_API_BASE=http://nora:9100
      - AUTH_DISCORD_ID=${AUTH_DISCORD_ID:?set AUTH_DISCORD_ID in the host environment first}
      - AUTH_DISCORD_SECRET=${AUTH_DISCORD_SECRET:?set AUTH_DISCORD_SECRET in the host environment first}
      - AUTH_SECRET=${AUTH_SECRET:?set AUTH_SECRET in the host environment first}
    volumes:
      # Same file, same read-only mount as the `nora` and `presence`
      # services above — the web portal's whitelist can never drift from
      # Nora's own roster because it reads the identical bytes.
      - ./identity.toml:/app/identity.toml:ro
```

- [ ] **Step 4: Validate the compose file**

Run: `docker compose config --quiet`
Expected: no errors (this validates YAML syntax and variable interpolation
without starting anything)

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml app/Dockerfile app/.dockerignore
git commit -m "feat(deploy): add the app service to docker-compose.yml"
```

---

## Self-Review Notes

- **Spec coverage:** §1 (portal shell/framing) — Tasks 6, 10. §2 (non-goals)
  — respected throughout (no WebSocket, no game hub, no per-person opt-out).
  §3 (architecture, module registry, PWA, type safety) — Tasks 4–14. §4
  (auth) — Tasks 7–9. §5.1 (status) — Tasks 3, 5, 11. §5.2 (memory) — Tasks
  2, 5, 12. §5.3–§5.5 (game hub, future modules) — deliberately out of this
  plan. §6 (deployment) — Task 14 (application-side only; Cloudflare Tunnel
  itself is an operator step, not code this plan produces). §7 (testing) —
  every task's own steps. §8 (open questions) — not blocking, untouched.
- **Correction from the spec's original wording:** the spec's §5.2 said
  "the current Honcho representation via `HonchoClient`" without naming a
  method. Investigation while writing this plan (Task 2) found the correct
  call is `MemoryStore.representation(peer_id=ai_peer, target=person_id,
  ...)` — Nora's OWN model of each person (the same verb and (observer,
  observed) shape `nora/engagement/context.py`'s `_recall_self` already
  uses) — not `peer_card`, a different, narrower verb. `representation`
  also returns a `Recollection(reachable, conclusions)`, not a bare string,
  which is why Task 2 threads R-27's fail-closed `reachable` flag through
  to the API response instead of collapsing "unreachable" and "nothing
  known" into the same empty list.
- **Type consistency:** `build_status_payload`'s return dict keys
  (`uptime_seconds`, `store`, `honcho`, `reminders`) match `StatusSchema`
  (Task 11) field-for-field; `build_memory_payload`'s entry keys
  (`person_id`, `display_name`, `reachable`, `conclusions`) match
  `MemoryEntrySchema` (Task 12) field-for-field. `register_web_routes`'s
  keyword arguments (Task 4) match what Task 5 passes from `deps`/`cfg`
  exactly.
- **No placeholders:** every step above contains real, runnable code. The
  one deliberate exception is Task 13's PWA icon PNGs, which are binary
  image assets this plan cannot generate — called out explicitly in that
  task rather than silently glossed over, and confirmed not to block the
  build.
