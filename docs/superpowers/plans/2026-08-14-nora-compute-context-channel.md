# Nora Compute — the context channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make_artifact` gains a `context: list[dict] | None` parameter carrying
Nora's own already-typed tool results (place/route facts) to Smith as a second
file (`context.json`, beside `task.txt`), screened at the boundary so a
person's resolved location can never cross it, with hash-based provenance
persisted alongside the job.

**Architecture:** `ComputeBackend.launch` and `LocalDockerBackend` gain the new
parameter and write it to the job's workspace unmodified. `make_artifact`
screens every string value in `context` with the same identifier/coordinate/
person-name checks `route`/`place_info` already apply to their own arguments,
then hashes `task`/`context` into `compute_jobs` for provenance. Smith reads
`context.json` at startup and gains one new skill, `geocode_address`, so a
place's coordinate is resolved inside Smith's own sandbox from the address
Nora already relayed — never inside Nora's own model context.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy Core + Alembic (Postgres),
Strands Agents SDK (Smith's own separate `pyproject.toml`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-13-nora-compute-context-channel-design.md`
— the plan below implements its §3/§4/§5. Read both; the spec is the argument,
this document is the task breakdown.

## Global Constraints

- `context` is `list[dict] | None = None` on every signature it touches —
  never required, never auto-collected (spec §4).
- `ComputeBackend`'s docstring guarantee "this Protocol takes only a string"
  is deleted, not merely widened around (spec §4's own naming of this).
- `place_info`'s coordinate suppression (`nora/infra/routes/client.py`) is
  **not touched by this plan at all** — `git diff` against that file must be
  empty at the end. The coordinate crosses inside Smith's own container via
  a *new* skill, never by loosening that tool.
- Every string value inside an incoming `context` payload is screened with
  `scan_identifiers`, `_looks_like_coordinate`, and `_names_a_person` —
  the last one checked against **both** the address-book names (`route`'s own
  set) **and** the room's `resolvable_places()` saved-place names. A hit
  anywhere refuses the whole call (`{"status": "refused", ...}`), never a
  silent strip.
- The new behavioural rule is **R-44** (not R-43 — `R-43` was assigned to
  `test_a_job_never_delivered_twice` after the spec was written; this plan
  corrects that stale reference). It needs a `design/09-decision-index.md`
  entry (**DEC-100**, the next free number) and a
  `tests/test_acceptance_rules.py::RULE_TESTS["R-44"]` line, per CLAUDE.md's
  own requirement for a new behavioural rule.
- `task_sha256`/`context_sha256` are computed **once, by `make_artifact`
  itself**, on the slow path only (the fast path never writes a `ComputeJob`
  row — this is a named, out-of-scope gap, not silently narrowed).
- Every gate must pass at the end: `uv run pytest -q`,
  `uv run ruff check nora/ evals/ tests/`,
  `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`,
  `uv run lint-imports`, `uv run python scripts/gen_pg_baseline.py` (schema
  parity), a release fragment.

---

## Task 1: `ComputeBackend.launch` gains `context: list[dict[str, Any]] | None`

**Files:**
- Modify: `nora/domain/ports.py:858-880` (`ComputeBackend` Protocol)
- Modify: `nora/testing/fakes.py:738-770` (`FakeComputeBackend`)
- Modify: `tests/test_compute_backend_port.py`
- Test: the two files above (this task adds no new file)

**Interfaces:**
- Produces: `ComputeBackend.launch(self, *, task: str, artifact_id: str | None = None, context: list[dict[str, Any]] | None = None) -> str` — the signature every later task threads through.
- Consumes: nothing from an earlier task (this is the first task).

**Note on `list[dict[str, Any]]`, not `list[dict]`:** `pyproject.toml` sets
`disallow_any_generics = true` for `nora.domain.*`/`nora.infra.*` (among
others), and there is no bare `dict` annotation anywhere in either package
today — a bare `dict` here would fail `uv run mypy` with `Missing type
parameters for generic type "dict"`. `nora/agent/tools.py` (Task 3) is
outside that strict mypy scope (CLAUDE.md's own gate command only covers
`nora/domain nora/config.py nora/app nora/infra nora/presence`), so
`context: list[dict] | None` is fine there and is used there deliberately —
only the port and `LocalDockerBackend` need the parametrized form.

**Note on the contract suite:** `tests/contract/test_compute_backend_
contract.py::test_port_methods_are_keyword_only_after_self` is parametrized
over BOTH `FakeComputeBackend` and `LocalDockerBackend` — updating its
`params` tuple to include `"context"` in this task would fail the
`LocalDockerBackend` arm with `KeyError: 'context'`, since that class does
not gain the parameter until Task 2. That edit is deferred to Task 2, where
both arms are green together.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_compute_backend_port.py`:

```python
async def test_launch_records_the_context():
    backend = FakeComputeBackend()
    await backend.launch(
        task="chart the rainfall",
        context=[{"kind": "place", "name": "Yoho Mall", "rating": 4.2}],
    )
    assert backend.launched[-1]["context"] == [
        {"kind": "place", "name": "Yoho Mall", "rating": 4.2}
    ]


async def test_launch_records_no_context_as_none_by_default():
    backend = FakeComputeBackend()
    await backend.launch(task="write a haiku")
    assert backend.launched[-1]["context"] is None
```

Update the existing `test_launch_records_the_task_and_artifact_id` in the same
file — it currently asserts an exact dict equality that a new `"context"` key
would break:

```python
async def test_launch_records_the_task_and_artifact_id():
    backend = FakeComputeBackend()
    await backend.launch(task="revise the chart", artifact_id="art_000001")
    assert backend.launched[-1] == {
        "task": "revise the chart", "artifact_id": "art_000001",
        "context": None, "job_id": backend.launched[-1]["job_id"],
    }
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_compute_backend_port.py -v`
Expected: `test_launch_records_the_context` and
`test_launch_records_no_context_as_none_by_default` fail with `TypeError:
launch() got an unexpected keyword argument 'context'`; the updated
`test_launch_records_the_task_and_artifact_id` fails on the dict-equality
assertion (`"context"` key not present in the real dict yet).

- [x] **Step 3: Widen the port and the fake**

In `nora/domain/ports.py`, replace the `ComputeBackend` docstring's guarantee
sentence and the `launch` signature:

```python
    """A compute job's execution environment (Nora Compute spec §2) —
    `LocalDockerBackend` is the only adapter this repo ships. `launch`
    returns immediately with a `job_id`; the job itself runs out-of-band.
    `task` is the caller's OWN distilled brief, never a raw conversation
    transcript (spec §4's "interpret once" boundary). `context` is Nora's
    own already-typed tool results (place/route facts) she chooses to hand
    along, unmodified — never auto-collected, never required (context-channel
    spec §4). Structured facts belong here, not folded into `task`'s prose:
    a second, lossy prose-compression step is exactly the failure the
    context-channel spec exists to remove.

    `artifact_id`, when set, means "this job revises an existing artifact"
    (spec §9) rather than creating a new one — `LocalDockerBackend` uses it
    to mint a narrower-scoped credential (Task 2) than a create job gets.

    NEVER raises. A backend that cannot even start a job (Docker unreachable,
    the image missing) reports that as a `poll` result with
    `status=JobStatus.FAILED`, not an exception out of `launch` — the same
    fail-closed-but-never-crash shape `RosterSource`/`MediaSource` already
    hold elsewhere in this file, so a caller (`make_artifact`, Phase 4) never
    needs a try/except around a call it expects to be load-bearing.
    """

    async def launch(
        self, *, task: str, artifact_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> str: ...
```

`Any` is already imported in `nora/domain/ports.py` (confirm with `grep -n
"^from typing" nora/domain/ports.py` — it is used elsewhere in this same
file, e.g. `WeatherObservation`'s neighbours), so no new import is needed
there.

In `nora/testing/fakes.py`, update `FakeComputeBackend.launch` and its
docstring:

```python
    """In-memory `ComputeBackend`. Every `launch`ed job defaults to immediate
    success (a caller that doesn't care about timing gets a working job with
    no setup) — call `set_result` with `polls_before_done > 0` to arrange a
    job that reports RUNNING some number of times first, the shape Phase 4's
    fast-path/slow-path branching (Nora Compute spec §4) needs to test both
    branches deterministically, without a real timer.

    `launched` records every call's `task`/`artifact_id`/`context`/assigned
    `job_id`, the same "record what was actually asked for" pattern
    `FakeRecordStore.rows`/`FakeArtifactIngest.artifacts` already use
    elsewhere in this file.
    """

    async def launch(
        self, *, task: str, artifact_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        job_id = f"job_{self._next_id:06d}"
        self._next_id += 1
        self.launched.append({
            "task": task, "artifact_id": artifact_id, "context": context,
            "job_id": job_id,
        })
        self._results.setdefault(
            job_id,
            JobResult(
                status=JobStatus.SUCCEEDED,
                url="https://nora.example.net/drop/fake",
                summary="done",
            ),
        )
        self._polls_before_done.setdefault(job_id, 0)
        return job_id
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_compute_backend_port.py -v`
Expected: all PASS, including every pre-existing test in the file.

Run: `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`
Expected: `Success: no issues found` — confirms `list[dict[str, Any]]` (not a
bare `dict`) satisfies `disallow_any_generics`.

- [x] **Step 5: Commit**

```bash
git add nora/domain/ports.py nora/testing/fakes.py tests/test_compute_backend_port.py
git commit -m "feat(compute): ComputeBackend.launch gains an optional context parameter"
```

---

## Task 2: `LocalDockerBackend` writes `context.json` beside `task.txt`

**Files:**
- Modify: `nora/infra/compute/docker_backend.py:146-206` (`launch`, `_run_job`, `_execute_job`)
- Modify: `tests/contract/test_compute_backend_contract.py` (the deferred parametrize edit from Task 1)
- Test: `tests/infra/compute/test_docker_backend.py`

**Interfaces:**
- Consumes: Task 1's `ComputeBackend.launch(..., context: list[dict[str, Any]] | None = None)`.
- Produces: every job's workspace directory contains `context.json` — the
  canonical JSON encoding of `context`, or `[]` when none was given. Smith's
  own `agent.py` (Task 5) reads this file at a fixed path,
  `/workspace/context.json`.

- [x] **Step 1: Write the failing test**

Add to `tests/infra/compute/test_docker_backend.py`, beside
`test_the_bind_source_is_the_host_path_not_this_process_path` (same file,
same `aioresponses`/`_poll_until_done` pattern already used throughout):

```python
async def test_context_is_written_to_the_workspace_as_json(
    backend, docker_client, tmp_path: Path,
):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(
            task="chart the rainfall",
            context=[{"kind": "place", "name": "Yoho Mall", "rating": 4.2}],
        )
        await _poll_until_done(backend, job_id=job_id)
    written = json.loads(
        (backend._workspace_root / job_id / "context.json").read_text()
    )
    assert written == [{"kind": "place", "name": "Yoho Mall", "rating": 4.2}]


async def test_no_context_writes_an_empty_json_list(backend, docker_client):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _poll_until_done(backend, job_id=job_id)
    written = json.loads(
        (backend._workspace_root / job_id / "context.json").read_text()
    )
    assert written == []
```

Add `import json` to this test file's existing import block (it currently has
none — `from __future__ import annotations` then `asyncio`/`threading`).

Also add, in this same step, the parametrize edit deferred from Task 1 — now
both arms (`FakeComputeBackend`, already widened; `LocalDockerBackend`, about
to be) can go green together. In `tests/contract/test_compute_backend_
contract.py`:

```python
@pytest.mark.parametrize(("method", "params"), [
    ("launch", ("task", "artifact_id", "context")),
    ("poll", ("job_id",)),
])
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/infra/compute/test_docker_backend.py -k context -v`
Expected: FAIL with `TypeError: launch() got an unexpected keyword argument
'context'` (the first test) and `FileNotFoundError` (the second, once the
first is fixed and `context` is accepted but nothing writes the file yet —
run them one at a time if the first failure masks the second).

Run: `uv run pytest tests/contract/test_compute_backend_contract.py -k keyword_only -v`
Expected: FAIL on the `LocalDockerBackend` arm with `KeyError: 'context'`.

- [x] **Step 3: Thread `context` through `launch` → `_run_job` → `_execute_job`, write the file**

In `nora/infra/compute/docker_backend.py`, add `import json` to the existing
import block (`asyncio`, `logging`, `uuid`, `Path`, `Any`, `aiohttp`,
`requests.exceptions`), then:

```python
    async def launch(
        self, *, task: str, artifact_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        job_id = f"job_{uuid.uuid4().hex}"
        self._tasks[job_id] = asyncio.ensure_future(
            self._run_job(job_id=job_id, task=task, artifact_id=artifact_id, context=context)
        )
        return job_id
```

```python
    async def _run_job(
        self, *, job_id: str, task: str, artifact_id: str | None,
        context: list[dict[str, Any]] | None,
    ) -> JobResult:
        """The port's "NEVER raises" contract (`ComputeBackend`'s docstring:
        a backend that cannot even start a job "reports that as a `poll`
        result ... not an exception out of `launch`"), enforced in ONE place
        rather than trusted to every statement below it.

        This wrapper exists because the inner body's non-container work is
        just as capable of raising as the container run is, and used not to
        be covered: a review pointed `workspace_root` at a FILE, and
        `workspace.mkdir(...)` raised `NotADirectoryError` straight out of
        `poll` into the caller -- `mkdir`, the `task.txt` write and the final
        `_read_result` all sat outside the only try/except in the method.
        """
        try:
            return await self._execute_job(
                job_id=job_id, task=task, artifact_id=artifact_id, context=context,
            )
        except Exception as exc:  # a raise out of poll breaks the port
            log.exception("compute job %s failed before it could report a result", job_id)
            return JobResult(status=JobStatus.FAILED, error=_unexpected(exc))

    async def _execute_job(
        self, *, job_id: str, task: str, artifact_id: str | None,
        context: list[dict[str, Any]] | None,
    ) -> JobResult:
        async with self._semaphore:
            try:
                token = await self._mint_token(artifact_id=artifact_id)
            except Exception as exc:  # noqa: BLE001 - launch failure is a result, not a raise
                log.warning("compute job %s could not mint a drop token: %s", job_id, exc)
                return JobResult(status=JobStatus.FAILED, error="could not obtain a drop token")

            workspace = self._workspace_root / job_id
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "task.txt").write_text(task)
            # A second file, never folded into `task`'s prose (context-channel
            # spec §4) — `[]` when nothing was given, so Smith's own reader
            # never needs an `exists()` branch for the common "no context" case.
            (workspace / "context.json").write_text(json.dumps(context or []))
```

(The rest of `_execute_job`'s body — `_start_container` call, timeout/
exception handling, `_read_result` — is unchanged; `context` is not needed
past this point, since `_start_container` only bind-mounts the workspace
directory, which now already contains both files.)

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/infra/compute/test_docker_backend.py tests/contract/test_compute_backend_contract.py -v`
Expected: all PASS, including the full pre-existing suite in both files (the
`_run_job`/`_execute_job` signature change must not break any test that calls
them directly, or through `launch`/`poll`; the contract suite's
`LocalDockerBackend` arm now goes green on `test_port_methods_are_keyword_
only_after_self` too).

Run: `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`
Expected: `Success: no issues found`.

- [x] **Step 5: Commit**

```bash
git add nora/infra/compute/docker_backend.py tests/infra/compute/test_docker_backend.py tests/contract/test_compute_backend_contract.py
git commit -m "feat(compute): LocalDockerBackend writes context.json beside task.txt"
```

---

## Task 3: `make_artifact` gains `context`, screened at the boundary (R-44)

**Files:**
- Modify: `nora/agent/tools.py:1024-1052` (screening helper, new function), `:3014-3089` (`make_artifact`, the whole closure — not `:3047`, which is mid-function; the replacement code in Step 3 below runs to the closure's real end)
- Modify: `tests/test_reminder_tools.py`

**Interfaces:**
- Consumes: Task 1/2's `compute_backend.launch(task=..., artifact_id=..., context=...)`.
- Produces: `make_artifact(task: str, context: list[dict] | None = None, artifact_id: str | None = None) -> dict`. A `context` entry that fails screening returns `{"status": "refused", "note": "..."}` before `compute_backend.launch` is ever called. (`context` stays a bare `list[dict]` here, not `list[dict[str, Any]]` — `nora/agent` is outside CLAUDE.md's strict-mypy scope, confirmed in Task 1's note.)

**Note on `tests/unit/test_tool_registry.py`, listed in the spec's own §7
table but NOT touched by this task, deliberately.** `make_artifact` appears
there twice: the tool-name set (`:73`, `test_the_registry_exposes_every_
declared_tool`) and the per-tool budget dict (`:142`, `test_budgets_match_
todays_table`) — neither reads or asserts a parameter list, and
`ToolRegistry.capability()` (`nora/agent/registry.py:325-360`) renders each
tool's `when` text only, never its signature. An optional new parameter
changes none of that. Confirmed by reading the file, not assumed.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_reminder_tools.py`, right after the existing
`# ---- make_artifact ...` section header (before
`test_make_artifact_unavailable_when_compute_backend_not_configured`):

```python
# ---- make_artifact's context channel (context-channel spec §3.2, R-44) -----
#
# The screen runs BEFORE `compute_backend is None` is even checked — same
# ordering `place_info`'s own identifier/person screen uses (it refuses
# before checking `route_source is None` too). The three refusal tests below
# still wire a `FakeComputeBackend` and assert `backend.launched == []`
# (mirroring `test_make_artifact_unavailable_when_slow_and_no_tagger`'s own
# "load-bearing... about ORDER" assertion, line 928) — proving the refusal
# genuinely happens before `launch`, not merely that the call FAILS for some
# other reason.

async def test_a_context_entry_with_an_identifier_is_refused_before_launch():
    """`+85291234567` — `scan_identifiers`'s E.164 pattern requires the
    leading `+` (`nora/agent/egress.py`'s own docstring: a bare local number
    with no `+` is explicitly the NON-matching case, so a fixture without one
    would silently test nothing)."""
    backend = FakeComputeBackend()
    tools = _tools(compute_backend=backend)

    result = await tools["make_artifact"](
        task="chart it", context=[{"note": "call me at +85291234567"}],
    )

    assert result["status"] == "refused"
    assert backend.launched == []


async def test_a_context_entry_with_a_raw_coordinate_is_refused_before_launch():
    backend = FakeComputeBackend()
    tools = _tools(compute_backend=backend)

    result = await tools["make_artifact"](
        task="map it", context=[{"fix": "22.4106,114.1245"}],
    )

    assert result["status"] == "refused"
    assert backend.launched == []


async def test_a_context_entry_naming_a_person_is_refused_before_launch():
    """R-44: the same substring screen `route`'s own `origin`/`destination`
    carry — `persons` here is `_FakePersons(MILES, TYLER)`, `_tools()`'s
    default, so "Tyler Cortez" is a real address-book name."""
    backend = FakeComputeBackend()
    tools = _tools(compute_backend=backend)

    result = await tools["make_artifact"](
        task="report it", context=[{"detail": "picked up from Tyler Cortez's place"}],
    )

    assert result["status"] == "refused"
    assert backend.launched == []


async def test_a_person_name_nested_inside_a_context_entry_is_still_caught():
    """The screen recurses — a person's name three levels deep in a route
    entry's own transit-step list must not slip past a shallow, top-level-
    only check."""
    tools = _tools()

    result = await tools["make_artifact"](
        task="report it",
        context=[{
            "kind": "route", "steps": [{"headsign": "towards Tyler Cortez's place"}],
        }],
    )

    assert result["status"] == "refused"


async def test_a_person_name_hiding_in_a_dict_key_is_still_caught():
    """The screen must walk KEYS as well as values — a model tabulating
    "who go where" naturally enough produces a dict keyed by name, and a
    values-only walk would wave it straight through."""
    tools = _tools()

    result = await tools["make_artifact"](
        task="report it", context=[{"Tyler Cortez": "25 min via MTR"}],
    )

    assert result["status"] == "refused"


async def test_a_context_entry_naming_a_saved_place_is_refused_before_launch(monkeypatch):
    """R-44's second half — a SAVED PLACE name, not just an address-book
    person, must be screened (spec §3.2's own motivating example: "Miles
    Home"). `resolvable_places` already has its own suite
    (`tests/test_whereis.py`); this monkeypatches it rather than standing up
    a real, non-degraded `Room`/`RosterSource` — `_tools()`'s own
    `_FakePersons.resolve` always returns `None` by design (its docstring:
    "the answer the degrade test wants"), so a real roster read here would
    always come back degraded regardless of what this test wires. Patching
    `resolvable_places` exercises exactly the NEW code this task adds — the
    `place_names` frozenset built from its return value, and the screen
    against it — without re-testing `resolvable_places` itself, which is not
    this task's job.
    """
    from nora.domain.presence import SavedPlace

    saved_place = SavedPlace(
        circle_id="c1", place_id="p1", name="Miles Home",
        lat=22.3, lng=114.2, updated_ms=0,
    )

    async def _fake_resolvable_places(**kwargs):
        return (saved_place,)

    monkeypatch.setattr(tools_module, "resolvable_places", _fake_resolvable_places)
    tools = _tools(presence_source=object(), presence_circles=(object(),))

    result = await tools["make_artifact"](
        task="report it", context=[{"detail": "picked up from Miles Home"}],
    )

    assert result["status"] == "refused"


async def test_clean_context_reaches_the_backend_unblocked():
    backend = FakeComputeBackend()
    jobs = FakeComputeJobStore()
    tools = _tools(compute_backend=backend, compute_jobs=jobs)

    result = await tools["make_artifact"](
        task="chart it",
        context=[{"kind": "place", "name": "Yoho Mall", "rating": 4.2}],
    )

    assert result["status"] == "ok"
    assert backend.launched[-1]["context"] == [
        {"kind": "place", "name": "Yoho Mall", "rating": 4.2}
    ]


async def test_no_context_is_the_default_and_is_never_screened():
    backend = FakeComputeBackend()
    jobs = FakeComputeJobStore()
    tools = _tools(compute_backend=backend, compute_jobs=jobs)

    result = await tools["make_artifact"](task="write a haiku")

    assert result["status"] == "ok"
    assert backend.launched[-1]["context"] is None


# ---- _context_leak: the screening helper, tested directly (R-44) -----------
#
# A pure-function unit test, not routed through `make_artifact` — proves the
# saved-place-name half is genuinely CASE-INSENSITIVE and length-guarded
# (`_names_a_person` casefolds only its HAYSTACK argument, `tools.py:1024-
# 1028`; `place_names` must be pre-normalized through `_key` the same way
# `_known_names` pre-normalizes person names, `tools.py:1013-1021`, or a
# place named with any uppercase letter — "Miles Home", spec §3.2's own
# example — silently never matches).

def test_an_uppercase_saved_place_name_is_still_caught_by_the_leak_screen():
    place_names = frozenset({tools_module._key("Miles Home")})
    hit = tools_module._context_leak(
        "picked up from Miles Home", person_names=frozenset(), place_names=place_names,
    )
    assert hit == "person"


def test_a_short_saved_place_name_is_never_included_in_the_screen():
    """The SAME `len(key) >= 2` floor `_known_names` itself applies
    (`tools.py:1013-1021`) — asserted directly on the filtering `make_artifact`
    applies when it builds `place_names` from `resolvable_places()`'s result
    (Step 3, below), not on `_context_leak`: a 1-char needle let through would
    trivially match almost any haystack, so the floor has to stop it from
    ever reaching the set in the first place."""
    names = ["a", "Miles Home"]
    filtered = frozenset(
        tools_module._key(name) for name in names if len(tools_module._key(name)) >= 2
    )
    assert tools_module._key("a") not in filtered
    assert tools_module._key("Miles Home") in filtered
```

`FakeComputeBackend`/`FakeComputeJobStore` are already imported at the top of
this file (line 38); `tools_module` (as `from nora.agent import tools as
tools_module`) is already imported (line 31). No new imports are needed for
these ten tests.

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reminder_tools.py -k "context_entry or context_leak or clean_context or no_context_is_the_default or saved_place or dict_key or place_name" -v`
Expected: FAIL — most with `TypeError: make_artifact() got an unexpected
keyword argument 'context'` (the parameter does not exist yet), the two
`_context_leak` unit tests with `AttributeError: module 'nora.agent.tools'
has no attribute '_context_leak'`, and the saved-place test additionally
with `TypeError: _tools() got an unexpected keyword argument
'presence_source'` until Step 3's `_tools()` extension below lands too — run
them after Step 3 if this mix of failure reasons is confusing to read.

- [x] **Step 3: Add the screening helper and the parameter**

In `nora/agent/tools.py`, add `import hashlib` to the existing import block
(alongside the already-present `asyncio`, `json`, `logging`, `re`,
`urllib.parse`, `uuid` at lines 47-52) — Task 4 needs it too, and adding it
once here keeps both tasks' diffs minimal. Also add `from typing import Any`
(this file has no `typing` import today — `_context_leak`, below, is the
first thing in it that needs `Any`) as its own import line, after the
`collections.abc`/`dataclasses`/`datetime` stdlib imports and before the
blank line that separates them from the `..domain...` imports.

Add the recursive screening helper after BOTH `_names_a_person` (`:1024-
1029`) and `_looks_like_coordinate` (`:1039-1049`) — it calls both, so it
must come after both — right before `_count_known_names`'s own definition
(`:1052`):

```python
def _context_leak(
    value: Any, *, person_names: frozenset[str], place_names: frozenset[str],
) -> str | None:
    """Recurse through one `context` entry's own value tree (context-channel
    spec §3.2, R-44), screening every string LEAF the same way `route`/
    `place_info` screen their own arguments — `context` is schema-light by
    design (spec §4), so a leak can sit at any depth a caller happened to
    nest a route's transit steps or a place's own fields at. Returns the
    kind of hit found, or None. `place_names` is checked through the SAME
    `_names_a_person` substring match `person_names` uses — a saved place's
    own name is R-35/DEC-60 subset-gated data with no subset check on this
    channel, so it is screened exactly like a person's name, not merely
    logged."""
    if isinstance(value, str):
        kind = scan_identifiers(value)
        if kind is not None:
            return kind
        if _looks_like_coordinate(value):
            return "coordinate"
        if _names_a_person(value, person_names) or _names_a_person(value, place_names):
            return "person"
        return None
    if isinstance(value, dict):
        # Keys too, not just values — a model tabulating "who go where" as
        # `{"Tyler Cortez": "25 min via MTR"}` is a plausible shape a values-
        # only walk would wave straight through.
        for k, v in value.items():
            hit = _context_leak(k, person_names=person_names, place_names=place_names)
            if hit is not None:
                return hit
            hit = _context_leak(v, person_names=person_names, place_names=place_names)
            if hit is not None:
                return hit
        return None
    if isinstance(value, list):
        for v in value:
            hit = _context_leak(v, person_names=person_names, place_names=place_names)
            if hit is not None:
                return hit
        return None
    return None
```

Also extend `_tools()` in `tests/test_reminder_tools.py` (needed by Step 1's
saved-place test above) to accept and forward presence collaborators:

```python
def _tools(
    *,
    reminders=None,
    caps: ReminderCaps | None = None,
    clock=lambda: T0,
    fire_turn: bool = False,
    tagger: PersonRef | None = MILES,
    metrics: Metrics | None = None,
    conversation_id: str = CONVERSATION,
    persons: _FakePersons | None = None,
    compute_backend=None,
    compute_jobs=None,
    presence_source=None,
    presence_circles=(),
    location_trusted: bool = False,
) -> dict:
    """..."""  # docstring gains one sentence: "`presence_source`/
    # `presence_circles`/`location_trusted` default the same honest way —
    # unwired, for `make_artifact`'s context-channel screen (R-44)."
    registry = ToolRegistry(
        reminders=reminders, reminder_caps=caps, clock=clock,
        compute_backend=compute_backend, compute_jobs=compute_jobs,
        presence=presence_source, circles=presence_circles,
    )
    specs = registry.specs(
        store=_FakeStore(), conversation_id=conversation_id, conversation_type="group",
        persons=persons if persons is not None else _FakePersons(MILES, TYLER),
        honcho=FakeHoncho(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=_room(tagger=tagger),
        fire_turn=fire_turn, location_trusted=location_trusted,
    )
    return {spec.name: spec.call for spec in specs}
```

(Every existing caller of `_tools()` in this file omits all three new
keyword arguments, so they keep their defaults and are unaffected.)

Replace the `make_artifact` closure (`nora/agent/tools.py:3014-3089` — the
WHOLE closure, ending at its real `return {"status": "ok", "still_working":
True}`, not the `:3047` mid-function line a stale line-count once implied)
with:

```python
    async def make_artifact(
        task: str, context: list[dict] | None = None, artifact_id: str | None = None,
    ) -> dict:
        """Build or research something and hand back a link to it — a report, a
        chart, a document. Use this when someone wants a FILE, not just an
        answer in chat. Takes anywhere from seconds to minutes; you may get the
        result back immediately, or a "still working" status if it's taking
        longer — in that case, tell them you're working on it and the result
        will follow as a separate message once it's ready.

        Args:
            task: what to build, in your own words — a clear, self-contained
                brief. The compute job has no access to this conversation
                beyond `task` and `context`, so include everything it needs
                to know.
            context: already-gathered facts relevant to THIS artifact — the
                typed results of tools you already called this turn (a
                place's rating, a route's duration), as a list of small
                dicts. Copy the fields you already have; do not re-derive or
                summarise them into prose first, and do not include a
                coordinate or anyone's resolved home location — those are
                refused. Leave unset for a task with nothing gathered.
            artifact_id: set this to REVISE an existing artifact (from a
                previous make_artifact result) rather than creating a new one.
                Leave unset to create something new.
        """
        if context:
            place_names: frozenset[str] = frozenset()
            if presence_source is not None and presence_circles:
                saved = await resolvable_places(
                    room=room, circles=presence_circles, presence=presence_source,
                    location_trusted=location_trusted,
                )
                if saved:
                    # `_key`, NOT the raw `.name` — `_names_a_person` casefolds
                    # only its HAYSTACK argument (the text being screened), so
                    # its `names` set must arrive pre-normalized, exactly the
                    # contract `_known_names` already holds for person names
                    # (`_key`, `len >= 2`, both at :1013-1021). A raw "Miles
                    # Home" here would never match a casefolded haystack, and
                    # the whole point of this half of R-44 (spec §3.2's own
                    # example) is that name in particular.
                    place_names = frozenset(
                        _key(p.name) for p in saved if len(_key(p.name)) >= 2
                    )
            for entry in context:
                hit = _context_leak(
                    entry, person_names=_route_person_names, place_names=place_names,
                )
                if hit is not None:
                    _record(metrics, "make_artifact", "refused", reason=hit)
                    return {
                        "status": "refused",
                        "note": "That context carries something that can't leave "
                                "this room — an identifier, a coordinate, or "
                                "somebody's saved place. Describe it in your own "
                                "words instead of pasting the raw data.",
                    }
        if compute_backend is None:
            _record(metrics, "make_artifact", "unavailable")
            return {"status": "unavailable"}
        # BEFORE `launch`, not after it. A job that outruns the sync window
        # needs somewhere to persist and somebody to deliver to; without both,
        # this call can only ever answer `unavailable` — so checking after
        # launching means a container is already running for a result nobody
        # will ever be told about, burning a concurrency slot and a Drop token
        # until it times out. The cost of checking first is that a job which
        # WOULD have finished inside the sync window is refused too, on a turn
        # with no tagger — a version announcement, or a requester who has since
        # left `identity.toml`. That is the right trade: nobody in either case
        # is waiting on a file, and a refusal there is honest rather than lucky.
        if compute_jobs is None or room.tagger is None:
            _record(metrics, "make_artifact", "unavailable", reason="no_delivery_target")
            return {"status": "unavailable"}

        job_id = await compute_backend.launch(task=task, artifact_id=artifact_id, context=context)
        elapsed = 0.0
        while elapsed < _SYNC_POLL_SECONDS:
            result = await compute_backend.poll(job_id=job_id)
            if result.status is not JobStatus.RUNNING:
                break
            await asyncio.sleep(_SYNC_POLL_INTERVAL)
            elapsed += _SYNC_POLL_INTERVAL
        else:
            result = await compute_backend.poll(job_id=job_id)

        if result.status is JobStatus.SUCCEEDED:
            _record(metrics, "make_artifact", "ok")
            return {"status": "ok", "url": result.url, "summary": result.summary}
        if result.status is JobStatus.FAILED:
            # `status="failed"` on the COUNTER, `{"status": "ok", "failed":
            # True}` in the payload, and the two disagree on purpose. The
            # payload is R-27's answer to the MODEL — the tool did run and its
            # answer is trustworthy, it is the job that went wrong — while the
            # counter answers "how did this call go" for whoever is watching
            # the series, and a failed job counted as `ok` is invisible there.
            # `failed=True` only ever reached the log line, so `status=ok` was
            # the whole dimension a dashboard could see. A status outside
            # R-27's three has precedent here — `remind`, `route` and
            # `cancel_reminder` all count `refused` — so this is a fourth
            # value of an existing vocabulary, not a new one.
            _record(metrics, "make_artifact", "failed")
            return {"status": "ok", "failed": True, "error": result.error}

        # Still running past the sync window — fall to the slow path (Nora
        # Compute spec §4). The delivery binding this needs was checked before
        # `launch`, above. Provenance (context-channel spec §5): hashed HERE,
        # not in `LocalDockerBackend`, because this closure already holds
        # both `task` and `context` in hand, while `launch` returns only a
        # bare `job_id` with no path back to a store.
        task_sha256 = hashlib.sha256(task.encode("utf-8")).hexdigest()
        context_sha256 = hashlib.sha256(
            json.dumps(context or [], sort_keys=True).encode("utf-8")
        ).hexdigest()
        job = ComputeJob(
            id=uuid.uuid4(), backend_job_id=job_id, task=task, artifact_id=artifact_id,
            platform=room.platform, conversation_id=bound_conversation_id,
            conversation_type=conversation_type,
            requester_person_id=room.tagger.person_id,
            created_at=now_fn(), state=ComputeJobState.PENDING,
            completed_at=None, result_url=None, result_summary=None, error=None,
            task_sha256=task_sha256, context_sha256=context_sha256,
        )
        await compute_jobs.create(job)
        _record(metrics, "make_artifact", "ok", still_working=True)
        return {"status": "ok", "still_working": True}
```

(The `task_sha256=`/`context_sha256=` keyword arguments to `ComputeJob(...)`
here will not type-check or import successfully until Task 4 adds those two
fields to the dataclass — this is expected; Task 3's own tests below do not
exercise the slow path, so they pass without Task 4. Run
`uv run mypy nora/agent/tools.py` only as part of Task 4's own gate check,
not this task's.)

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reminder_tools.py -k "not slow" -v`
Expected: all PASS — the ten new tests, and every pre-existing test in this
file except the two that reach the slow path
(`test_make_artifact_falls_back_to_still_working_when_slow`,
`test_make_artifact_unavailable_when_slow_and_no_tagger` — both named with
"slow", confirmed the only two that construct a `ComputeJob` via
`make_artifact`). Those two fail at this point with `TypeError: __init__()
missing 2 required keyword-only arguments: 'task_sha256' and
'context_sha256'` (a dataclass rejects unknown/missing kwargs at
construction time, not at import), since `ComputeJob` does not accept the
two new fields until Task 4. That failure is expected and resolved there —
do not treat it as this task's own gate; `-k "not slow"` is.

Do NOT run the whole-file `uv run pytest tests/test_reminder_tools.py -v`
(no `-k`) expecting green — those two tests will fail until Task 4 lands,
by design.

- [x] **Step 5: Commit**

This commit's own `uv run pytest -q` (the FULL suite) is knowingly red on
exactly the two tests named in Step 4, until Task 4's next commit — an
explicit, acknowledged exception to CLAUDE.md's "before every commit" gate,
scoped to this one pair of tests with a known, already-planned fix one
commit away (Task 4), not a general license to commit red.

```bash
git add nora/agent/tools.py tests/test_reminder_tools.py
git commit -m "feat(compute): make_artifact gains a screened context parameter (R-44)"
```

---

## Task 4: `compute_jobs` gains `task_sha256`/`context_sha256` provenance

**Files:**
- Create: `nora/infra/sql/migrations/versions/0009_compute_job_hashes.py`
- Modify: `nora/infra/sql/schema.py:380-404` (`COMPUTE_JOBS`)
- Modify: `nora/domain/compute.py` (`ComputeJob` dataclass)
- Modify: `nora/infra/sql/compute_jobs.py` (`_row_to_job`, `SqlComputeJobStore.create`)
- Modify: `tests/domain/test_compute.py`, `tests/unit/test_compute_poller.py`,
  `tests/contract/test_compute_job_store_contract.py`, `tests/test_composition.py:429`,
  `tests/unit/test_fire_path.py:703`
- Modify: `tests/infra/pg_schema_baseline.sql` (regenerated, not hand-edited)

**Interfaces:**
- Consumes: Task 3's `ComputeJob(..., task_sha256=..., context_sha256=...)` call site (this task makes that call site actually type-check and run).
- Produces: `ComputeJob.task_sha256: str`, `ComputeJob.context_sha256: str` — every construction site in the repo must supply both from this task onward.

- [x] **Step 1: Write the failing tests**

Update every existing `ComputeJob(...)` construction across the repo to
supply the two new fields — this IS the failing-test step, since the
dataclass does not accept them yet. Each is a one-line addition right after
the existing `error=None,`/`error=error,` field, using literal test values
(the actual VALUE never matters to these tests — only that a real hash-shaped
string round-trips):

`tests/domain/test_compute.py` — all three `ComputeJob(...)` calls (lines
26-32, 45-51, 68-74) gain `task_sha256="hash-task", context_sha256="hash-
context",` right after their `error=None,`/`error=None,` line.

`tests/unit/test_compute_poller.py`'s `_pending_job`'s `defaults` dict (line
~41-49) gains `"task_sha256": "hash-task", "context_sha256": "hash-
context",` after `"error": None,`.

`tests/contract/test_compute_job_store_contract.py`'s `_job()`'s `defaults`
dict gains the same two keys after `"error": None,`.

`tests/test_composition.py:429`'s inline `ComputeJob(...)` gains
`task_sha256="hash-task", context_sha256="hash-context",` after `error=None,`.

`tests/unit/test_fire_path.py:703`'s `_compute_job`'s `base` dict gains
`"task_sha256": "hash-task", "context_sha256": "hash-context",` after
`"error": None,`.

Add one new test to `tests/domain/test_compute.py`, asserting the fields
actually round-trip:

```python
def test_a_compute_job_carries_its_own_provenance_hashes():
    job = ComputeJob(
        id=uuid4(), backend_job_id="job_abc123", task="write a haiku",
        artifact_id=None, platform="signal", conversation_id="conv-1",
        conversation_type="group", requester_person_id="person-1",
        created_at=datetime.now(UTC), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="a" * 64, context_sha256="b" * 64,
    )
    assert job.task_sha256 == "a" * 64
    assert job.context_sha256 == "b" * 64
```

Add one new test to `tests/contract/test_compute_job_store_contract.py`,
proving both implementations persist the two columns:

```python
async def test_the_provenance_hashes_round_trip(store):
    job = _job(task_sha256="a" * 64, context_sha256="b" * 64)
    await store.create(job)
    fetched = await store.get(job.id)
    assert fetched is not None
    assert fetched.task_sha256 == "a" * 64
    assert fetched.context_sha256 == "b" * 64
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/domain/test_compute.py -v`
Expected: `test_a_compute_job_carries_its_own_provenance_hashes` fails with
`TypeError: ComputeJob.__init__() got an unexpected keyword argument
'task_sha256'`; the three pre-existing tests in this file now ALSO fail with
the same error, since Step 1 already added the two kwargs to their calls.

- [x] **Step 3: Write the migration**

Create `nora/infra/sql/migrations/versions/0009_compute_job_hashes.py`:

```python
"""compute_jobs gains task_sha256/context_sha256 — provenance for a job's own
inputs (Nora Compute context-channel spec §5).

Revision ID: 0009_compute_job_hashes
Revises: 0008_compute_jobs
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_compute_job_hashes"
down_revision = "0008_compute_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `server_default=""` mirrors 0008's own `state` column (nullable=False,
    # server_default="pending") — it backstops any row that predates this
    # migration, but every row `make_artifact` creates from here on writes a
    # REAL hash explicitly (nora/agent/tools.py), the same way every row
    # already writes a real `state` despite the column having a default.
    op.add_column(
        "compute_jobs",
        sa.Column("task_sha256", sa.Text, nullable=False, server_default=""),
    )
    op.add_column(
        "compute_jobs",
        sa.Column("context_sha256", sa.Text, nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("compute_jobs", "context_sha256")
    op.drop_column("compute_jobs", "task_sha256")
```

- [x] **Step 4: Update the schema, the domain dataclass, and the SQL adapter**

In `nora/infra/sql/schema.py`, add to `COMPUTE_JOBS` (after the existing
`sa.Column("error", sa.Text, nullable=True),` line, before the
`CheckConstraint`):

```python
    sa.Column("task_sha256", sa.Text, nullable=False, server_default=""),
    sa.Column("context_sha256", sa.Text, nullable=False, server_default=""),
```

In `nora/domain/compute.py`, add to the `ComputeJob` dataclass (after
`error: str | None`):

```python
    task_sha256: str
    context_sha256: str
```

And extend the class docstring's final paragraph with one sentence:

```python
    `task_sha256`/`context_sha256` are the SHA-256 of `task`/`context` as
    `make_artifact` actually sent them (context-channel spec §5) — computed
    once, by `make_artifact` itself, never by a backend. A caller can verify
    the workspace file on disk still matches what was launched.
```

In `nora/infra/sql/compute_jobs.py`, update `_row_to_job`:

```python
def _row_to_job(r: Row[Any]) -> ComputeJob:
    return normalise_compute_job(
        ComputeJob(
            id=r.id, backend_job_id=r.backend_job_id, task=r.task,
            artifact_id=r.artifact_id, platform=r.platform,
            conversation_id=r.conversation_id,
            conversation_type=r.conversation_type,
            requester_person_id=r.requester_person_id,
            created_at=r.created_at, state=ComputeJobState(r.state),
            completed_at=r.completed_at, result_url=r.result_url,
            result_summary=r.result_summary, error=r.error,
            task_sha256=r.task_sha256, context_sha256=r.context_sha256,
        )
    )
```

And `SqlComputeJobStore.create`:

```python
    async def create(self, job: ComputeJob) -> None:
        row = normalise_compute_job(job)
        stmt = sa.insert(COMPUTE_JOBS).values(
            id=row.id, backend_job_id=row.backend_job_id, task=row.task,
            artifact_id=row.artifact_id, platform=row.platform,
            conversation_id=row.conversation_id,
            conversation_type=row.conversation_type,
            requester_person_id=row.requester_person_id,
            created_at=row.created_at, state=row.state.value,
            task_sha256=row.task_sha256, context_sha256=row.context_sha256,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
```

- [x] **Step 5: Regenerate the schema-parity baseline**

Run: `uv run python scripts/gen_pg_baseline.py`

This rewrites `tests/infra/pg_schema_baseline.sql` from a live, migrated
Postgres — do not hand-edit that file (CLAUDE.md's own schema-gate note:
`alembic check` is blind to exactly this kind of drift, which is why this
file exists as the oracle instead).

- [x] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/domain/test_compute.py tests/unit/test_compute_poller.py tests/contract/test_compute_job_store_contract.py tests/test_composition.py tests/unit/test_fire_path.py tests/infra/test_schema_parity.py -v`
Expected: all PASS.

Run: `uv run pytest tests/test_reminder_tools.py -v`
Expected: all PASS now, including
`test_make_artifact_falls_back_to_still_working_when_slow` and
`test_make_artifact_unavailable_when_slow_and_no_tagger` (Task 3's slow-path
tests, unblocked now that `ComputeJob` accepts the two new fields).

Run: `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`
Expected: `Success: no issues found`.

- [x] **Step 7: Commit**

```bash
git add nora/infra/sql/migrations/versions/0009_compute_job_hashes.py nora/infra/sql/schema.py nora/domain/compute.py nora/infra/sql/compute_jobs.py tests/domain/test_compute.py tests/unit/test_compute_poller.py tests/contract/test_compute_job_store_contract.py tests/test_composition.py tests/unit/test_fire_path.py tests/infra/pg_schema_baseline.sql
git commit -m "feat(compute): persist task_sha256/context_sha256 provenance on compute_jobs"
```

---

## Task 5: Smith gains `geocode_address`, reads `context.json`

**Files:**
- Create: `nora/compute/smith/skills/geocode/render.py`
- Create: `nora/compute/smith/skills/geocode/SKILL.md`
- Create: `nora/compute/smith/tests/test_geocode_address.py`
- Modify: `nora/compute/smith/agent.py`
- Modify: `nora/compute/smith/doctrine.md`

**Interfaces:**
- Consumes: Task 2's `context.json` file, always present in `/workspace`.
- Produces: a `geocode_address(address: str) -> tuple[float, float]` tool Smith's own model may call before `render_map`.

**No `__init__.py`.** `nora/compute/smith/skills/maps/` (confirmed: `ls`
shows only `SKILL.md`, `render.py`, `__pycache__/`) has none —
`agent.py`'s own `from skills.maps.render import render_map as _render_map`
works via implicit namespace packages. `skills/geocode/` matches the same
shape: no `__init__.py`.

- [x] **Step 1: Write the failing test**

Create `nora/compute/smith/tests/test_geocode_address.py`, mirroring
`test_render_map.py`'s exact shape (same `monkeypatch`/`patch` pattern, same
env var):

```python
from unittest.mock import patch

import pytest

from skills.geocode.render import GeocodeError, geocode_address


def test_a_known_address_returns_its_coordinate(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 22.4106, "lng": 114.1245}}}],
        }
        result = geocode_address(address="1 Larkspur Rd, Tsim Sha Tsui")
    assert result == (22.4106, 114.1245)


def test_the_address_is_sent_as_the_query_param(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 1.0, "lng": 2.0}}}],
        }
        geocode_address(address="Yoho Mall, Yuen Long")
    params = mock_get.call_args.kwargs["params"]
    assert params["address"] == "Yoho Mall, Yuen Long"


def test_an_address_google_cannot_resolve_raises_geocode_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"status": "ZERO_RESULTS", "results": []}
        with pytest.raises(GeocodeError):
            geocode_address(address="nowhere at all")


def test_raise_for_status_is_called_before_the_json_is_parsed(monkeypatch):
    """Revert-sensitive on the actual line, not on `requests`' own well-tested
    behaviour: delete `response.raise_for_status()` from the skill and this
    goes red, because nothing else in the function calls it. (A prior draft
    of this test set `status_code = 403` and asserted `pytest.raises
    (Exception)` — but `GeocodeError` (raised on a non-OK JSON `status`) IS
    an `Exception`, so that assertion stayed green even with the
    `raise_for_status()` call deleted; it also never read `status_code` at
    all. `assert_called_once()` on the mock is the only assertion that
    actually pins the call happening.)"""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 1.0, "lng": 2.0}}}],
        }
        geocode_address(address="1 Larkspur Rd")
    mock_get.return_value.raise_for_status.assert_called_once()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd nora/compute/smith && uv run pytest tests/test_geocode_address.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skills.geocode'`.

- [x] **Step 3: Write the skill**

Create `nora/compute/smith/skills/geocode/render.py`:

```python
"""`geocode_address` — the fourth deterministic skill (context-channel spec
§3.1). A place's own ADDRESS in, its coordinate out — resolved here, inside
Smith's own sandbox, from data Nora already relayed freely (a business's
address is public; this module never receives a person's name or a raw
coordinate to begin with). The coordinate this returns is model-facing
INSIDE Smith only; it is never sent back to Nora, and Nora's own
`place_info`/`route` never return a coordinate at all (DEC-60/63/64,
untouched by this document).
"""

from __future__ import annotations

import os

import requests

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GeocodeError(Exception):
    """Google could not resolve the given address to a place."""


def geocode_address(*, address: str) -> tuple[float, float]:
    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    response = requests.get(
        GEOCODE_URL, params={"address": address, "key": api_key}, timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "OK" or not data.get("results"):
        raise GeocodeError(f"could not geocode {address!r}: {data.get('status')}")
    location = data["results"][0]["geometry"]["location"]
    return (location["lat"], location["lng"])
```

Create `nora/compute/smith/skills/geocode/SKILL.md`:

```markdown
---
name: geocode-address
description: Resolve a place's own address to a (lat, lng) coordinate, for feeding render_map.
---

# geocode_address

Call `geocode_address(address=...)`. Returns `(lat, lng)`. Use this when
`context.json` gives you a place's NAME and ADDRESS but `render_map` needs
coordinates — geocode the address first, then pass the result to
`render_map`. Raises `GeocodeError` if Google cannot resolve the address;
catch it and either drop that one marker or fall back to prose naming the
place instead of failing the whole artifact.
```

- [x] **Step 4: Run the test to verify it passes**

Run: `cd nora/compute/smith && uv run pytest tests/test_geocode_address.py -v`
Expected: all 4 PASS.

- [x] **Step 5: Wire the skill into `agent.py`, read `context.json`**

In `nora/compute/smith/agent.py`, add to the existing skill imports —
**alphabetically FIRST**, before `skills.html_report` (`geocode` < `html_
report`; Smith's own `pyproject.toml` selects ruff rule `"I"`, import-sort,
so this ordering is enforced, not cosmetic):

```python
from skills.geocode.render import geocode_address as _geocode_address
from skills.html_report.render import render_html_report as _render_html_report
from skills.maps.render import render_map as _render_map
from skills.markdown.render import render_markdown as _render_markdown
from skills.mermaid.render import render_mermaid as _render_mermaid
```

(Only the new `geocode` line is added; the other four are shown to make the
correct final order unambiguous — do not reorder the existing four. Not
importing `GeocodeError` here: the wrapper below never references it by
name — see the no-`try`/`except` note — so importing it would be an unused
import, `ruff`'s `F401`.)

Add the tool wrapper (after `render_map`'s own `@tool @guarded(_budget)`
block, before `render_html_report`'s) — no `try`/`except` around the call,
matching `render_mermaid`'s own wrapper (`agent.py:120-137`), which lets its
skill's exception propagate for Strands to turn into an ordinary
`status: "error"` ToolResult rather than catching and re-raising it for no
behavioural difference:

```python
@tool
@guarded(_budget)
def geocode_address(address: str) -> tuple[float, float]:
    """Resolve a place's own address to a (lat, lng) coordinate — call this
    before render_map when context.json gives you a name/address but no
    coordinate. Raises GeocodeError if the address cannot be resolved; catch
    it and drop that marker or describe the place in prose instead."""
    return _geocode_address(address=address)
```

Add it to `main()`'s tool list:

```python
        tools=[
            run_python, register_artifact, render_mermaid, render_markdown, render_map,
            render_html_report, geocode_address,
        ],
```

Read `context.json` and fold it into the prompt, in `main()`, right after
`task = task_file.read_text()`:

```python
    task = task_file.read_text()
    # Nora's already-typed tool results (context-channel spec §4), delivered
    # as a SEPARATE file so a model copying JSON fields into its own JSON
    # array is a literal task, not the lossy prose-compression step that
    # motivated this channel in the first place. `[]` (no context given) adds
    # nothing to the prompt — an empty block would just be noise the model
    # has to notice is empty.
    context_path = WORKSPACE / "context.json"
    context_json = context_path.read_text() if context_path.exists() else "[]"
    if context_json.strip() not in ("", "[]"):
        task = (
            f"{task}\n\n---\nSupporting data already gathered (context.json) — "
            f"use it directly, never re-derive or guess a fact it already "
            f"gives you:\n{context_json}"
        )
    doctrine = Path(__file__).parent.joinpath("doctrine.md").read_text()
```

(`result = agent(task, limits=...)` below already uses the local `task`
variable — no further change needed there; the context block is now part of
what it reads.)

- [x] **Step 6: Update the doctrine**

In `nora/compute/smith/doctrine.md`, add a new bullet to "Your tools" (after
the "Locations" bullet, before "Putting it together"):

```markdown
- **Places needing a map, from an address rather than a coordinate** —
  `geocode_address(address=...)` first, then pass the result into
  `render_map`. `context.json` (below) is where a place's name/address
  usually comes from; you resolve its coordinate yourself — Nora never sends
  you one.
```

Add a new "Your input" section, right after the "Your tools" section (before
"## Color"):

```markdown
## Your input

`/workspace/task.txt` is the brief. If Nora gathered facts relevant to this
task — a place's rating, a route's duration — they are appended to the same
prompt as `context.json`'s own JSON, clearly marked. Treat it as VERIFIED
ground truth: build from it directly, never re-derive or guess at a fact it
already gives you. Empty (`[]`) means nothing was gathered; work from
`task.txt` alone.
```

- [x] **Step 7: Run the full Smith test suite**

Run: `cd nora/compute/smith && uv run pytest -v`
Expected: all PASS, including every pre-existing test file
(`test_render_markdown.py`, `test_page_shell.py`, `test_render_html_report.py`,
`test_budget.py`, `test_render_mermaid.py`, `test_render_map.py`,
`test_geocode_address.py`).

Run: `cd nora/compute/smith && uv run ruff check .`
Expected: clean (this sub-project has its own `pyproject.toml`/ruff config,
separate from the repo root's).

- [x] **Step 8: Commit**

```bash
git add nora/compute/smith/skills/geocode/ nora/compute/smith/tests/test_geocode_address.py nora/compute/smith/agent.py nora/compute/smith/doctrine.md
git commit -m "feat(smith): add geocode_address skill, read context.json at startup"
```

---

## Task 6: Decision index — DEC-100, R-44, and the R-28/`place_info` citation fix

**Files:**
- Modify: `design/09-decision-index.md`
- Modify: `tests/test_acceptance_rules.py`

**Interfaces:**
- Consumes: Task 3's `test_a_context_entry_naming_a_person_is_refused_before_launch` (the R-44 rule test), Task 5's `test_geocode_address.py` (cited alongside it).
- Produces: `RULE_TESTS["R-44"]`, a `DEC-100` row.

- [x] **Step 1: Confirm the structural gate this task must satisfy**

`tests/test_acceptance_rules.py:189` — `test_every_enforced_rule_has_a_named_
test` — walks `RULE_TESTS` and asserts every named test was actually
collected. It is not itself edited by this task; it is the gate that proves
`RULE_TESTS["R-44"]` (Step 2, below) points at a real, collected test —
`test_a_context_entry_naming_a_person_is_refused_before_launch`, added by
Task 3. This task has no test of its own to write first: adding the dict
entry, correctly, IS what the existing gate checks.

Run: `uv run pytest tests/test_acceptance_rules.py -v`
Expected: all PASS (before this task's own edit below — confirms the
starting point is clean, so any red after Step 2 is this task's own doing).

- [x] **Step 2: Add the `RULE_TESTS` entry**

In `tests/test_acceptance_rules.py`, add after the `"R-43"` entry:

```python
    "R-44": "test_a_context_entry_naming_a_person_is_refused_before_launch",
```

- [x] **Step 3: Add the DEC-100 row and fix R-28's stale citation**

In `design/09-decision-index.md`, append after the last existing row (the
`DEC-99` row):

```markdown
| DEC-100 | **The context channel (context-channel spec, 2026-08-13): `make_artifact` gains `context: list[dict] \| None`, Nora's own already-typed tool results (place/route facts), delivered to Smith as `context.json` beside `task.txt` — never folded into prose. Two trust rules, not one. (1) A PLACE's name/address/rating/price crosses freely — already public, already what `place_info` returns unchanged. Its COORDINATE is resolved SMITH-SIDE, via a new `geocode_address` skill using the address Nora already relayed, never inside Nora's own model context — DEC-60/63/64/69(2)'s server-side-coordinate rule is untouched (`place_info`'s own suppression is not modified by this decision), because R-28 governs what reaches the MODEL/GROUP and Smith's own render pipeline, which never speaks to the group directly, is not that surface. (2) A PERSON's resolved location never crosses at all: every string value inside an incoming `context` payload is screened at `make_artifact`'s own boundary (R-44) with the same `scan_identifiers`/`_looks_like_coordinate`/`_names_a_person` checks `route`/`place_info` already apply to their own arguments, `_names_a_person` additionally checked against the room's own `resolvable_places()` saved-place names — a match anywhere refuses the whole call. `ComputeBackend.launch` gains the same `context` parameter; `task_sha256`/`context_sha256` ride on `compute_jobs` for provenance (slow path only — the fast path writes no row, a named limitation, not silently narrowed).** | `test_a_context_entry_naming_a_person_is_refused_before_launch`, `test_a_person_name_nested_inside_a_context_entry_is_still_caught`, `test_clean_context_reaches_the_backend_unblocked` (`tests/test_reminder_tools.py`), `test_a_known_address_returns_its_coordinate` (`nora/compute/smith/tests/test_geocode_address.py`) |
```

Fix the pre-existing R-28 row's citation gap (§8's own flagged item — the
row currently names only `nora.agent.tools.route`, not `place_info`'s
identical screen, shipped under DEC-63): find the R-28 row (search for
`| R-28 |`) and append one sentence to its prose, right before the final
`See DEC-46.`:

```markdown
 `place_info` carries the identical screen (`nora.agent.tools.place_info`, shipped under DEC-63) — this row's own citation gap, corrected 2026-08-14 alongside DEC-100.
```

And extend that row's test cell to also name `place_info`'s own test:
`` `test_a_route_naming_a_person_is_refused_before_egress`, `test_place_info_tool_refuses_a_person` ``.

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_acceptance_rules.py -v`
Expected: all PASS.

- [x] **Step 5: Commit**

```bash
git add design/09-decision-index.md tests/test_acceptance_rules.py
git commit -m "docs(decisions): add DEC-100 (the context channel), R-44, fix R-28's place_info citation gap"
```

---

## Task 7: Release fragment

**Files:**
- Create: `release-fragments/unreleased/compute-context-channel.json`

**Interfaces:**
- Consumes: nothing (documentation-only task).
- Produces: a fragment `check_release_fragments.py` accepts.

- [x] **Step 1: Write the fragment**

Create `release-fragments/unreleased/compute-context-channel.json`:

```json
{
  "type": "feature",
  "summary": "When Nora already looked something up before building you a file, she can hand that along directly instead of re-typing it into a brief.",
  "details": [
    "A place's rating, price, or a route's travel time can ride alongside the request as its own data, so the sandboxed builder gets the real numbers rather than a paraphrase.",
    "A map needing a coordinate for an address resolves it inside the sandbox itself, from the address already given — never by asking Nora for a raw position.",
    "Anything that would identify a person or their saved location is refused before the job ever starts, the same way a route request already refuses to reveal someone's home."
  ],
  "limitations": [
    "What Nora hands along is her own choice each time — nothing is gathered automatically on her behalf."
  ]
}
```

- [x] **Step 2: Run the gate**

Run: `uv run python scripts/check_release_fragments.py --base origin/master`
Expected: passes (no longer reports "changed branch has no release fragment").

- [x] **Step 3: Commit**

```bash
git add release-fragments/unreleased/compute-context-channel.json
git commit -m "docs(release): add fragment for the context channel"
```

---

## Task 8: Final whole-branch verification

**Files:** none (verification only).

**Interfaces:** none.

- [x] **Step 1: Full test suite**

Run: `uv run pytest -q`
Expected: all PASS, 0 failures.

- [x] **Step 2: Smith's own suite**

Run: `cd nora/compute/smith && uv run pytest -q`
Expected: all PASS.

- [x] **Step 3: Lint**

Run: `uv run ruff check nora/ evals/ tests/`
Expected: clean.

Run: `cd nora/compute/smith && uv run ruff check .`
Expected: clean.

- [x] **Step 4: Types**

Run: `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`
Expected: `Success: no issues found`.

- [x] **Step 5: Import boundaries**

Run: `uv run lint-imports`
Expected: clean.

- [x] **Step 6: Schema parity**

Run: `uv run pytest tests/infra/test_schema_parity.py -v`
Expected: PASS (confirms Task 4's regenerated baseline still matches a fresh
migration run).

- [x] **Step 7: Release fragment gate**

Run: `uv run python scripts/check_release_fragments.py --base origin/master`
Expected: passes.

- [x] **Step 8: Confirm the untouched-file constraint**

Run: `git diff origin/master -- nora/infra/routes/client.py`
Expected: empty output — `place_info`'s coordinate suppression was never
touched by this plan (Global Constraints, and spec §3.1's own explicit
promise).

- [x] **Step 9: Confirm no `docs/superpowers/plans/2026-08-14-nora-compute-context-channel.md` checkbox is unticked**

Re-read this file; every `- [ ]` above should now be `- [x]`.
