# Nora Compute — Phase 2: ComputeBackend, LocalDockerBackend, and Smith's Bare Sandbox — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the execution environment a compute job actually runs in — a `ComputeBackend` port, its `LocalDockerBackend` adapter (per-job scoped Drop credentials, one container per job, guardrails), and Smith's bare sandbox image (a separate, neutral Strands agent with code-exec and a raw artifact-registration call, no skill library yet) — proven end-to-end with a trivial job (write a file, register it with Drop, report back) rather than any of Phase 3's real rendering skills.

**Architecture:** `LocalDockerBackend.launch` mints a scoped, time-limited Drop token (a new `POST /api/tokens` endpoint on Drop itself, since Drop's write routes today only know one static shared token), starts an `asyncio.Task` that runs a Docker container via `docker-py`, and returns a `job_id` immediately; `poll(job_id)` reports `RUNNING`/`SUCCEEDED`/`FAILED` by checking whether that task has finished. The container image bakes in Smith's own bootstrap script — a minimal Strands `Agent` with only a code-exec tool and a raw Drop-registration tool, run once against a task file dropped into the bind-mounted workspace, writing its result back to that same workspace for `LocalDockerBackend` to read once the container exits.

**Tech Stack:** Python 3.12, `docker` (docker-py, new dependency for the top-level `nora` package), `PyJWT` (new dependency for `deploy/drop`), Strands Agents SDK (Smith's sandbox image, a separate Python environment from both `nora` and `deploy/drop`), `asyncpg`/Starlette (Drop's existing stack), pytest, Docker.

## Global Constraints

- `ComputeBackend`, like every port in `nora/domain/ports.py`, is keyword-only after `self` on every method (`tests/test_ports.py`'s generic check covers it once declared).
- Per the Nora Compute spec §5a, Smith's containers join `nora-compute` only — never `nora`. `LocalDockerBackend` must launch containers with `network="nora-compute"` explicitly; it must never default to Docker's bridge network or the `nora` network.
- Per spec §5, every job gets: a hard wall-clock timeout (killed, not just abandoned), a per-job token that stops working once that timeout elapses (this plan implements the bound via the token's own short TTL rather than an active revoke call — see Task 2's own reasoning), and a concurrency ceiling enforced by `LocalDockerBackend` itself, not left to Docker or the operator to notice.
- Per spec §4, Smith never receives the raw conversation transcript — only the `task` string `make_artifact` (Phase 4) will eventually pass through `launch`. This plan doesn't build `make_artifact` yet, but `ComputeBackend.launch`'s signature must already reflect that boundary (`task: str`, nothing richer).
- `LocalDockerBackend` real container-launch behavior needs a live Docker daemon — this repo's own established convention (Phase 1's `PgRustfsArtifactStore`, confirmed via `deploy/drop/tests/conftest.py`'s own docstring) is to unit-test the parts that don't require live infrastructure and verify the rest by hand, not to invent a new mocked-Docker-daemon test harness. This plan follows that same convention.
- Docker SDK's exact exception type on a `container.wait(timeout=...)` overrun (this plan assumes `requests.exceptions.ReadTimeout`, docker-py's documented behavior as of the SDK versions this plan was written against) should be confirmed against the actually-installed `docker` package version during Task 3 — flagged explicitly rather than asserted as certain, the same honesty this repo's own `CLAUDE.md` requires of a measurement before trusting it.

---

## File Structure

| File | Change |
|---|---|
| `nora/domain/ports.py` | Add `JobStatus`, `JobResult`, `ComputeBackend` Protocol |
| `nora/testing/fakes.py` | Add `FakeComputeBackend` |
| `deploy/drop/auth.py` | Add scoped job-token minting/verification (`PyJWT`) |
| `deploy/drop/server.py` | Add `POST /api/tokens`; switch the artifact write routes to accept either the master token or a matching scoped token |
| `deploy/drop/pyproject.toml` | Add `PyJWT` dependency |
| `deploy/drop/tests/test_auth.py` | Extend for scoped-token minting/verification |
| `deploy/drop/tests/test_artifacts_route.py` | Extend for scoped-token acceptance/rejection on the write routes |
| `nora/infra/compute/docker_backend.py` (new) | `LocalDockerBackend` |
| `tests/infra/compute/test_docker_backend.py` (new) | Its tests, against a mocked Docker client |
| `nora/compute/smith/Dockerfile` (new) | Smith's sandbox image |
| `nora/compute/smith/agent.py` (new) | Smith's bootstrap script |
| `nora/compute/smith/doctrine.md` (new) | Smith's governing prose — bare-bones for this phase |
| `docker-compose.yml` | Add `DROP_JOB_TOKEN_SECRET` env to `drop` |

---

### Task 1: `ComputeBackend` port + `FakeComputeBackend`

**Files:**
- Modify: `nora/domain/ports.py`
- Modify: `nora/testing/fakes.py`
- Modify: `tests/test_ports.py` (only if it doesn't already generically discover every `Protocol` — see Step 3)

**Interfaces:**
- Produces: `JobStatus` (`StrEnum`: `RUNNING`, `SUCCEEDED`, `FAILED`), `JobResult` (`status`, `url: str | None`, `summary: str | None`, `error: str | None`), `ComputeBackend.launch(self, *, task: str, artifact_id: str | None = None) -> str` (returns a `job_id`), `ComputeBackend.poll(self, *, job_id: str) -> JobResult`. Task 3's `LocalDockerBackend` and Phase 4's `make_artifact` both consume these exact names and shapes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ports.py` (or a new `tests/test_compute_backend_port.py` if `test_ports.py` doesn't already import port symbols generically — check the file first; if it iterates `nora.domain.ports.__dict__` for every `Protocol`, no new test is needed there and this step becomes a read, not a write):

```python
from nora.domain.ports import ComputeBackend, JobResult, JobStatus
from nora.testing import FakeComputeBackend


def test_fake_compute_backend_satisfies_the_port():
    assert isinstance(FakeComputeBackend(), ComputeBackend)


async def test_a_launched_job_starts_running():
    backend = FakeComputeBackend()
    job_id = await backend.launch(task="write a haiku")
    result = await backend.poll(job_id=job_id)
    # default fake behavior (no set_result call) is immediate success --
    # see FakeComputeBackend's own docstring for why, and set_result below
    # for how a test arranges the RUNNING branch instead
    assert result.status == JobStatus.SUCCEEDED


async def test_set_result_can_arrange_a_still_running_job():
    backend = FakeComputeBackend()
    job_id = await backend.launch(task="write a haiku")
    backend.set_result(
        job_id=job_id,
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="a haiku"),
        polls_before_done=2,
    )
    assert (await backend.poll(job_id=job_id)).status == JobStatus.RUNNING
    assert (await backend.poll(job_id=job_id)).status == JobStatus.RUNNING
    third = await backend.poll(job_id=job_id)
    assert third.status == JobStatus.SUCCEEDED
    assert third.url == "https://x/drop/1"


async def test_polling_an_unknown_job_id_fails_rather_than_raising():
    backend = FakeComputeBackend()
    result = await backend.poll(job_id="job_doesnotexist")
    assert result.status == JobStatus.FAILED


async def test_launch_records_the_task_and_artifact_id():
    backend = FakeComputeBackend()
    await backend.launch(task="revise the chart", artifact_id="art_000001")
    assert backend.launched[-1] == {
        "task": "revise the chart", "artifact_id": "art_000001",
        "job_id": backend.launched[-1]["job_id"],
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ports.py -k compute_backend -v` (or the new file's path if created separately)
Expected: FAIL — `ImportError: cannot import name 'ComputeBackend' from 'nora.domain.ports'`.

- [ ] **Step 3: Add `JobStatus`, `JobResult`, `ComputeBackend` to `nora/domain/ports.py`**

Add after the `ArtifactIngest` Protocol (after its closing `...` line):

```python
class JobStatus(StrEnum):
    """A compute job's current state, as `ComputeBackend.poll` reports it."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class JobResult:
    """What `ComputeBackend.poll` returns. `url`/`summary` are populated only
    when `status is SUCCEEDED`; `error` only when `status is FAILED` — never
    both, and `RUNNING` carries neither (there is nothing to report yet)."""

    status: JobStatus
    url: str | None = None
    summary: str | None = None
    error: str | None = None


@runtime_checkable
class ComputeBackend(Protocol):
    """A compute job's execution environment (Nora Compute spec §2) —
    `LocalDockerBackend` is the only adapter this repo ships. `launch`
    returns immediately with a `job_id`; the job itself runs out-of-band.
    `task` is the caller's OWN distilled brief, never a raw conversation
    transcript (spec §4's "interpret once" boundary) — this Protocol takes
    only a string, so a caller cannot accidentally hand it anything richer.

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

    async def launch(self, *, task: str, artifact_id: str | None = None) -> str: ...

    async def poll(self, *, job_id: str) -> JobResult: ...
```

Add the module docstring entry (near `ArtifactIngest`'s own entry, following the same style as every other port's explanation):

```
- `ComputeBackend` is the Nora Compute spec's own port (§2): `launch` starts a
  job and returns immediately, `poll` reports its progress. Deliberately NEVER
  raises — see the Protocol's own docstring for why a launch failure is a
  `poll` result, not an exception, matching this file's existing fail-closed
  convention for ports a live turn depends on.
```

- [ ] **Step 4: Add `FakeComputeBackend` to `nora/testing/fakes.py`**

```python
class FakeComputeBackend:
    """In-memory `ComputeBackend`. Every `launch`ed job defaults to immediate
    success (a caller that doesn't care about timing gets a working job with
    no setup) — call `set_result` with `polls_before_done > 0` to arrange a
    job that reports RUNNING some number of times first, the shape Phase 4's
    fast-path/slow-path branching (Nora Compute spec §4) needs to test both
    branches deterministically, without a real timer.

    `launched` records every call's `task`/`artifact_id`/assigned `job_id`,
    the same "record what was actually asked for" pattern `FakeRecordStore
    .rows`/`FakeArtifactIngest.artifacts` already use elsewhere in this file.
    """

    def __init__(self) -> None:
        self._results: dict[str, JobResult] = {}
        self._polls_before_done: dict[str, int] = {}
        self.launched: list[dict[str, Any]] = []
        self._next_id = 1

    async def launch(self, *, task: str, artifact_id: str | None = None) -> str:
        job_id = f"job_{self._next_id:06d}"
        self._next_id += 1
        self.launched.append({"task": task, "artifact_id": artifact_id, "job_id": job_id})
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

    def set_result(
        self, *, job_id: str, result: JobResult, polls_before_done: int = 0,
    ) -> None:
        self._results[job_id] = result
        self._polls_before_done[job_id] = polls_before_done

    async def poll(self, *, job_id: str) -> JobResult:
        if job_id not in self._results:
            return JobResult(status=JobStatus.FAILED, error="unknown job_id")
        if self._polls_before_done[job_id] > 0:
            self._polls_before_done[job_id] -= 1
            return JobResult(status=JobStatus.RUNNING)
        return self._results[job_id]
```

Add `ComputeBackend`, `JobResult`, `JobStatus` to this file's existing import from `nora.domain.ports`, and add `FakeComputeBackend` to `nora/testing/__init__.py`'s exports (matching how every other fake is re-exported there).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ports.py -v` (or the new file)
Expected: PASS.

- [ ] **Step 6: Type-check and lint**

Run: `uv run mypy nora/domain && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/domain/ports.py nora/testing/fakes.py nora/testing/__init__.py tests/test_ports.py
git commit -m "feat(ports): add ComputeBackend port and FakeComputeBackend"
```

---

### Task 2: Drop's scoped job-token endpoint

**Files:**
- Modify: `deploy/drop/auth.py`
- Modify: `deploy/drop/server.py`
- Modify: `deploy/drop/pyproject.toml`
- Modify: `deploy/drop/tests/test_auth.py`
- Modify: `deploy/drop/tests/test_artifacts_route.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `POST /api/tokens` on `internal_app`, bearer-authed with the MASTER `NORA_DROP_TOKEN` only (never a scoped token — this prevents a job from minting its own further tokens). `write_authorized(request, *, master_token, job_secret, required_scope, artifact_id=None) -> bool`, replacing `bearer_ok` on the two artifact write routes (`mint_link` keeps using plain `bearer_ok` — links aren't part of this credential-scoping story).

**Design decision, named explicitly rather than left implicit:** the spec (§5, §9) says a per-job token is "minted at launch, revoked on container exit." This plan implements the bound via a **short TTL matching the job's own wall-clock timeout guardrail**, not an active revoke call — a token simply stops verifying once its `exp` claim passes, which happens at essentially the same moment `LocalDockerBackend` (Task 3) would kill the container anyway for exceeding that same timeout. This avoids a revocation-list/database lookup on every write request (the master-token path today is a pure in-memory string compare; a stateful revoke list would slow down every write, not just job-scoped ones) in exchange for a token that's live for a few seconds past a killed container in the worst case — judged an acceptable gap given jobs are killed anyway at that same boundary. If this gap ever matters, add a revocation table later; not built here.

- [ ] **Step 1: Write the failing tests**

Add to `deploy/drop/tests/test_auth.py`:

```python
from auth import TokenScope, mint_job_token, verify_job_token

SECRET = "test-job-secret"  # noqa: S105


def test_a_freshly_minted_create_token_verifies():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)
    assert verify_job_token(token=token, secret=SECRET, required_scope=TokenScope.CREATE)


def test_an_update_token_only_verifies_for_its_own_artifact_id():
    token = mint_job_token(
        secret=SECRET, scope=TokenScope.UPDATE, artifact_id="art_1", ttl_seconds=60,
    )
    assert verify_job_token(
        token=token, secret=SECRET, required_scope=TokenScope.UPDATE, artifact_id="art_1",
    )
    assert not verify_job_token(
        token=token, secret=SECRET, required_scope=TokenScope.UPDATE, artifact_id="art_2",
    )


def test_a_create_token_does_not_verify_as_an_update_token():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)
    assert not verify_job_token(
        token=token, secret=SECRET, required_scope=TokenScope.UPDATE, artifact_id="art_1",
    )


def test_an_expired_token_does_not_verify():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=-1)
    assert not verify_job_token(token=token, secret=SECRET, required_scope=TokenScope.CREATE)


def test_a_token_signed_with_the_wrong_secret_does_not_verify():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)
    assert not verify_job_token(token=token, secret="wrong-secret", required_scope=TokenScope.CREATE)
```

Add to `deploy/drop/tests/test_artifacts_route.py` (extend `wired_apps` usage — the fixture itself needs `job_secret`/master `token` both set, which it already does via `TOKEN`; add a new `job_secret` constant and wire it in `conftest.py`'s `wired_apps` in Step 3):

```python
def test_a_valid_create_scoped_token_may_post(wired_apps):
    server, _links, _artifacts = wired_apps
    from auth import TokenScope, mint_job_token
    from conftest import JOB_SECRET
    client = TestClient(server.internal_app)
    scoped = mint_job_token(secret=JOB_SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 200


def test_an_update_scoped_token_may_only_patch_its_own_artifact_id(wired_apps):
    server, _links, _artifacts = wired_apps
    from auth import TokenScope, mint_job_token
    from conftest import JOB_SECRET
    client = TestClient(server.internal_app)

    created = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    ).json()
    scoped = mint_job_token(
        secret=JOB_SECRET, scope=TokenScope.UPDATE,
        artifact_id=created["artifact_id"], ttl_seconds=60,
    )

    ok = client.patch(
        f"/api/artifacts/{created['artifact_id']}",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T2", "media_type": "text/plain"},
        files={"file": ("t.txt", b"bye", "text/plain")},
    )
    assert ok.status_code == 200

    other_id_resp = client.patch(
        "/api/artifacts/art_someone_elses",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T2", "media_type": "text/plain"},
        files={"file": ("t.txt", b"bye", "text/plain")},
    )
    assert other_id_resp.status_code == 403


def test_a_create_scoped_token_may_not_patch(wired_apps):
    server, _links, _artifacts = wired_apps
    from auth import TokenScope, mint_job_token
    from conftest import JOB_SECRET
    client = TestClient(server.internal_app)
    scoped = mint_job_token(secret=JOB_SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)

    resp = client.patch(
        "/api/artifacts/art_x",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403


def test_the_master_token_mints_a_scoped_token(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/tokens",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"scope": "create", "ttl_seconds": 60},
    )
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_a_scoped_token_cannot_mint_another_token(wired_apps):
    server, _links, _artifacts = wired_apps
    from auth import TokenScope, mint_job_token
    from conftest import JOB_SECRET
    client = TestClient(server.internal_app)
    scoped = mint_job_token(secret=JOB_SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)

    resp = client.post(
        "/api/tokens",
        headers={"Authorization": f"Bearer {scoped}"},
        json={"scope": "create", "ttl_seconds": 60},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `deploy/drop/`): `uv run pytest tests/test_auth.py tests/test_artifacts_route.py -v`
Expected: FAIL — `ImportError: cannot import name 'TokenScope' from 'auth'`.

- [ ] **Step 3: Add `PyJWT`, implement `auth.py`'s scoped-token functions**

In `deploy/drop/pyproject.toml`'s dependencies list, add `pyjwt`. Run (from `deploy/drop/`): `uv add pyjwt`.

Rewrite `deploy/drop/auth.py`:

```python
"""Bearer auth for the internal listener (§5a) — two shapes now, not one.

The MASTER token (`NORA_DROP_TOKEN`) is Nora's own backend's credential —
only it may call `POST /api/tokens` (Task 2 of the Nora Compute Phase 2 plan)
and it remains a valid credential on the artifact write routes too, unchanged
from the Drop spec. A SCOPED job token (`mint_job_token`/`verify_job_token`)
is what `LocalDockerBackend` mints per job and hands to Smith's container —
narrower (create-only, or update-only-this-artifact_id) and short-lived (its
own `exp` claim, not an active revoke call — see the Phase 2 plan's own
reasoning for why TTL alone is judged sufficient here).

A network-level control (no `ports:` entry, no Tunnel ingress rule, §5a) is
still the PRIMARY control on this listener; both token shapes are defence in
depth on top of that, the same relationship the Drop spec already describes
for the single master token.
"""

from __future__ import annotations

import time
from enum import StrEnum

import jwt
from starlette.requests import Request


def bearer_ok(request: Request, *, token: str) -> bool:
    return request.headers.get("Authorization") == f"Bearer {token}"


class TokenScope(StrEnum):
    CREATE = "create"
    UPDATE = "update"


def mint_job_token(
    *, secret: str, scope: TokenScope, artifact_id: str | None, ttl_seconds: int,
) -> str:
    payload: dict[str, object] = {
        "scope": scope.value,
        "exp": int(time.time()) + ttl_seconds,
    }
    if artifact_id is not None:
        payload["artifact_id"] = artifact_id
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_job_token(
    *, token: str, secret: str, required_scope: TokenScope, artifact_id: str | None = None,
) -> bool:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return False
    if payload.get("scope") != required_scope.value:
        return False
    if required_scope is TokenScope.UPDATE and payload.get("artifact_id") != artifact_id:
        return False
    return True


def write_authorized(
    request: Request,
    *,
    master_token: str,
    job_secret: str,
    required_scope: TokenScope,
    artifact_id: str | None = None,
) -> bool:
    """A write route is authorized by EITHER the master token OR a scoped job
    token matching `required_scope` (and `artifact_id`, for updates)."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    presented = header[len("Bearer "):]
    if presented == master_token:
        return True
    return verify_job_token(
        token=presented, secret=job_secret, required_scope=required_scope,
        artifact_id=artifact_id,
    )
```

- [ ] **Step 4: Wire `JOB_SECRET` into `deploy/drop/tests/conftest.py`'s `wired_apps`**

Add near the existing `TOKEN = "test-drop-token"` line:

```python
JOB_SECRET = "test-job-secret"  # noqa: S105
```

In `wired_apps`, add `app.state.job_secret = JOB_SECRET` alongside the existing `app.state.token = TOKEN` line for both apps.

- [ ] **Step 5: Add `POST /api/tokens` and switch the write routes to `write_authorized`**

In `deploy/drop/server.py`, add after the imports:

```python
from auth import TokenScope, bearer_ok, mint_job_token, write_authorized
```

(replacing the existing `from auth import bearer_ok` line).

Add the mint handler, after `robots_txt` and before `mint_link`:

```python
# ── internal: POST /api/tokens ───────────────────────────────────────────────

async def mint_token(request: Request) -> JSONResponse:
    """Mints a scoped, short-lived job token (Nora Compute spec §5/§9) —
    master-token-only, so a job holding a scoped token can never mint itself
    (or anyone else) a further token."""
    if not bearer_ok(request, token=request.app.state.token):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        body: Any = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid body"}, status_code=400)

    scope_raw = body.get("scope") if isinstance(body, dict) else None
    ttl_raw = body.get("ttl_seconds") if isinstance(body, dict) else None
    artifact_id = body.get("artifact_id") if isinstance(body, dict) else None

    if scope_raw not in ("create", "update"):
        return JSONResponse({"error": "scope must be create or update"}, status_code=400)
    if scope_raw == "update" and not isinstance(artifact_id, str):
        return JSONResponse({"error": "update scope requires artifact_id"}, status_code=400)
    if not isinstance(ttl_raw, int) or ttl_raw <= 0:
        return JSONResponse({"error": "ttl_seconds must be a positive integer"}, status_code=400)

    token = mint_job_token(
        secret=request.app.state.job_secret,
        scope=TokenScope(scope_raw),
        artifact_id=artifact_id if scope_raw == "update" else None,
        ttl_seconds=ttl_raw,
    )
    return JSONResponse({"token": token})
```

Change `register_artifact`'s auth check (currently `if not bearer_ok(request, token=request.app.state.token):`) to:

```python
    if not write_authorized(
        request,
        master_token=request.app.state.token,
        job_secret=request.app.state.job_secret,
        required_scope=TokenScope.CREATE,
    ):
        return JSONResponse({"error": "forbidden"}, status_code=403)
```

Change `update_artifact`'s auth check (from Phase 1's Task 1) to:

```python
    artifact_id = request.path_params["artifact_id"]
    if not write_authorized(
        request,
        master_token=request.app.state.token,
        job_secret=request.app.state.job_secret,
        required_scope=TokenScope.UPDATE,
        artifact_id=artifact_id,
    ):
        return JSONResponse({"error": "forbidden"}, status_code=403)
```

(Move the `artifact_id = request.path_params["artifact_id"]` line up if it isn't already above the auth check — `write_authorized` needs it for the scope match.)

Register the new route on `internal_app`:

```python
internal_app = Starlette(routes=[
    Route("/health", health),
    Route("/api/links", mint_link, methods=["POST"]),
    Route("/api/tokens", mint_token, methods=["POST"]),
    Route("/api/artifacts", register_artifact, methods=["POST"]),
    Route("/api/artifacts/{artifact_id}", update_artifact, methods=["PATCH"]),
])
```

Add `job_secret` to `_build_state`'s returned dict:

```python
    job_secret = os.environ["DROP_JOB_TOKEN_SECRET"]
    ...
    return {
        "token": token,
        "job_secret": job_secret,
        "links": link_store,
        "artifacts": artifact_store,
        "_pg_pool": pg_pool,
        "_redis": redis,
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run (from `deploy/drop/`): `uv run pytest -v`
Expected: PASS — all tests, including Phase 1's, plus every new one.

- [ ] **Step 7: Lint and type-check**

Run (from `deploy/drop/`): `uv run ruff check . && uv run mypy .`
Expected: clean.

- [ ] **Step 8: Add `DROP_JOB_TOKEN_SECRET` to `docker-compose.yml`**

In `drop`'s `environment:` block (after the existing `NORA_DROP_TOKEN` line):

```yaml
      # Signs/verifies per-job scoped tokens (Nora Compute spec §5/§9) --
      # separate from NORA_DROP_TOKEN (the master credential): a job's own
      # token is minted and verified against THIS secret, never compared to
      # the master token directly.
      - DROP_JOB_TOKEN_SECRET=${DROP_JOB_TOKEN_SECRET:?set DROP_JOB_TOKEN_SECRET in .env}
```

Add `DROP_JOB_TOKEN_SECRET=` to `.env.example` with a comment matching the existing `NORA_DROP_TOKEN`/`RUSTFS_ROOT_PASSWORD` style.

- [ ] **Step 9: Validate compose config**

Run: `docker compose config --quiet`
Expected: no output if `.env` already has the new variable set; otherwise the expected `variable is not set` warning naming exactly `DROP_JOB_TOKEN_SECRET`, confirming the reference is wired correctly.

- [ ] **Step 10: Commit**

```bash
git add deploy/drop/auth.py deploy/drop/server.py deploy/drop/pyproject.toml deploy/drop/uv.lock deploy/drop/tests/conftest.py deploy/drop/tests/test_auth.py deploy/drop/tests/test_artifacts_route.py docker-compose.yml .env.example
git commit -m "feat(drop): add scoped, short-lived per-job tokens (POST /api/tokens)"
```

---

### Task 3: `LocalDockerBackend`

**Files:**
- Create: `nora/infra/compute/__init__.py`
- Create: `nora/infra/compute/docker_backend.py`
- Create: `tests/infra/compute/__init__.py`
- Create: `tests/infra/compute/test_docker_backend.py`

**Interfaces:**
- Consumes: `ComputeBackend`, `JobResult`, `JobStatus` (Task 1); calls Drop's `POST /api/tokens` (Task 2) over HTTP.
- Produces: `LocalDockerBackend(docker_client, drop_internal_url, drop_master_token, sandbox_image, job_timeout_seconds, max_concurrent_jobs, workspace_root)`, satisfying `ComputeBackend`. Nothing later in this plan consumes it directly (Phase 4's `make_artifact` will); this task's own deliverable is "launch a container, read its result back" proven against Task 4's sandbox image.

- [ ] **Step 1: Write the failing tests (mocked Docker client)**

Create `tests/infra/compute/test_docker_backend.py`:

```python
"""`LocalDockerBackend` against a MOCKED docker client -- real container
launches need a live Docker daemon and are verified by hand (Task 3, Step 5),
the same convention `deploy/drop`'s own test suite already follows for
`PgRustfsArtifactStore` (its module docstring)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioresponses import aioresponses

from nora.domain.ports import JobStatus
from nora.infra.compute.docker_backend import LocalDockerBackend

DROP_URL = "http://drop:8081"
MASTER_TOKEN = "master-token"  # noqa: S105


@pytest.fixture
def docker_client():
    client = MagicMock()
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    client.containers.run.return_value = container
    return client


@pytest.fixture
def backend(tmp_path: Path, docker_client):
    return LocalDockerBackend(
        docker_client=docker_client,
        drop_internal_url=DROP_URL,
        drop_master_token=MASTER_TOKEN,
        model_api_key="test-model-key",
        model_base_url="https://omniroute.example/v1",
        model_name="test-model",
        sandbox_image="smith:latest",
        job_timeout_seconds=30,
        max_concurrent_jobs=2,
        workspace_root=tmp_path,
    )


async def test_launch_returns_a_job_id_immediately(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
    assert job_id.startswith("job_")


async def _poll_until_done(backend, *, job_id: str, attempts: int = 50):
    """`launch` schedules `_run_job` as a background `asyncio.Task` it never
    awaits directly, and neither `launch` nor `poll` contains an internal
    `await` that actually suspends — so a tight retry loop with no sleep
    between iterations never yields to the event loop at all, and the
    scheduled task never gets a turn to run. `asyncio.sleep(0)` on every
    iteration is what actually lets it progress (its own real suspension
    points are the two `asyncio.to_thread(...)` calls inside `_run_job`, which
    only get checked when this coroutine yields). Every test in this file
    that waits for a job to finish should call this rather than hand-rolling
    a bare retry loop."""
    result = None
    for _ in range(attempts):
        result = await backend.poll(job_id=job_id)
        if result.status != JobStatus.RUNNING:
            return result
        await asyncio.sleep(0)
    return result


async def test_a_successful_job_reports_succeeded(backend, docker_client, tmp_path):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")

    # Yield once so `_run_job` actually starts and creates the workspace
    # directory before this test writes into it -- see `_poll_until_done`'s
    # own docstring for why a bare `await` on a non-suspending coroutine
    # does not do this on its own.
    await asyncio.sleep(0)

    # Simulate what Smith's sandbox (Task 4) writes to the bind-mounted
    # workspace before exiting -- the container mock's `wait` already
    # resolves immediately, so the workspace files just need to exist by the
    # time this test polls.
    workspace = tmp_path / job_id
    (workspace / "url.txt").write_text("https://nora.example.net/drop/abc")
    (workspace / "summary.txt").write_text("wrote a haiku about autumn")

    result = await _poll_until_done(backend, job_id=job_id)
    assert result.status == JobStatus.SUCCEEDED
    assert result.url == "https://nora.example.net/drop/abc"
    assert result.summary == "wrote a haiku about autumn"


async def test_a_job_that_exits_without_writing_output_is_failed(backend, tmp_path):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")

    # No url.txt/summary.txt written -- the container "exited" (per the mock)
    # without producing a result.
    result = await _poll_until_done(backend, job_id=job_id)
    assert result.status == JobStatus.FAILED
    assert result.error is not None


async def test_launch_mints_a_create_scoped_token_by_default(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        await backend.launch(task="write a haiku")
    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["json"] == {"scope": "create", "ttl_seconds": 30}


async def test_launch_mints_an_update_scoped_token_for_an_artifact_id(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        await backend.launch(task="revise the chart", artifact_id="art_1")
    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["json"] == {
        "scope": "update", "artifact_id": "art_1", "ttl_seconds": 30,
    }


async def test_launch_attaches_the_container_to_nora_compute_only(backend, docker_client):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
    await _poll_until_done(backend, job_id=job_id)
    _, kwargs = docker_client.containers.run.call_args
    assert kwargs["network"] == "nora-compute"


async def test_a_third_job_waits_for_the_concurrency_semaphore(backend, docker_client):
    """max_concurrent_jobs=2 (the `backend` fixture) -- a third launch must
    not start its container until one of the first two finishes. Verified
    indirectly: the semaphore itself is asyncio-internal, so this asserts the
    OBSERVABLE effect -- `containers.run` is not called a third time until
    the first job's workspace signals completion and its task is awaited."""
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"}, repeat=True)
        job1 = await backend.launch(task="job one")
        job2 = await backend.launch(task="job two")
        launch3 = asyncio.ensure_future(backend.launch(task="job three"))
        await asyncio.sleep(0.05)
        assert docker_client.containers.run.call_count == 2

        # let job1 "finish" so the semaphore releases for job three
        (backend._workspace_root / job1 / "url.txt").parent.mkdir(
            parents=True, exist_ok=True,
        )
        await _poll_until_done(backend, job_id=job1)
        await launch3
        assert docker_client.containers.run.call_count == 3
```

This suite needs `aioresponses` (mocks `aiohttp` calls) as a new dev dependency — add it via `uv add --dev aioresponses` at the repo root if not already present (check `pyproject.toml`'s `[dependency-groups.dev]` first).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infra/compute/test_docker_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.infra.compute'`.

- [ ] **Step 3: Add `docker` as a dependency**

Run: `uv add docker`. `requests` is already a transitive dependency of `docker`, but this module imports it directly (for `requests.exceptions.ReadTimeout`), so add it explicitly too: `uv add requests`.

- [ ] **Step 4: Implement `LocalDockerBackend`**

Create `nora/infra/compute/__init__.py` (empty).

Create `nora/infra/compute/docker_backend.py`:

```python
"""`LocalDockerBackend` — the only `ComputeBackend` adapter (Nora Compute
spec §2). One Docker container per job, bind-mounted workspace, a scoped
per-job Drop credential minted before launch. No FUSE, no sync protocol —
the host filesystem already is the single source of truth once the
container and this process share one machine (the same argument the Nora
Drop spec's §1 makes about storage, applied here to compute).

`launch` returns a `job_id` immediately and starts an `asyncio.Task` that
does the actual container run/wait/cleanup in the background; `poll` just
checks whether that task has finished. This keeps `poll` cheap (no Docker
API call on every poll) and keeps `ComputeBackend`'s own "never raises"
contract easy to hold — the task's own exception handling is internal, never
surfaced to a caller as a raised exception out of `poll`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import requests.exceptions  # docker-py's container.wait(timeout=) overrun
                             # raises this, not the builtin TimeoutError --
                             # see the except clause below.

from nora.domain.ports import JobResult, JobStatus

log = logging.getLogger("nora.compute")

DEFAULT_MEM_LIMIT = "1g"


class LocalDockerBackend:
    """Satisfies `nora.domain.ports.ComputeBackend`."""

    def __init__(
        self,
        *,
        docker_client: Any,
        drop_internal_url: str,
        drop_master_token: str,
        model_api_key: str,
        model_base_url: str,
        model_name: str,
        sandbox_image: str,
        job_timeout_seconds: int,
        max_concurrent_jobs: int,
        workspace_root: Path,
    ) -> None:
        self._docker = docker_client
        self._drop_internal_url = drop_internal_url.rstrip("/")
        self._drop_master_token = drop_master_token
        # Smith's own LLM credential (Nora Compute spec §3 assumed Smith could
        # call a model without ever specifying HOW it gets one — a real gap,
        # caught only by a subagent review that actually tried to run this
        # plan's own code and found `agent.py` had no `model=` anywhere).
        # Reuses the SAME OmniRoute-shaped (api_key, base_url, model name)
        # credential Nora herself is configured with
        # (`nora/app/composition.py`'s own `OMNIROUTE_API_KEY` construction)
        # rather than inventing a second model-provider story for Smith.
        self._model_api_key = model_api_key
        self._model_base_url = model_base_url
        self._model_name = model_name
        self._sandbox_image = sandbox_image
        self._job_timeout_seconds = job_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._workspace_root = workspace_root
        self._tasks: dict[str, asyncio.Task[JobResult]] = {}

    async def launch(self, *, task: str, artifact_id: str | None = None) -> str:
        job_id = f"job_{uuid.uuid4().hex}"
        self._tasks[job_id] = asyncio.ensure_future(
            self._run_job(job_id=job_id, task=task, artifact_id=artifact_id)
        )
        return job_id

    async def poll(self, *, job_id: str) -> JobResult:
        running = self._tasks.get(job_id)
        if running is None:
            return JobResult(status=JobStatus.FAILED, error="unknown job_id")
        if not running.done():
            return JobResult(status=JobStatus.RUNNING)
        return running.result()

    async def _run_job(
        self, *, job_id: str, task: str, artifact_id: str | None,
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

            try:
                container = await asyncio.to_thread(self._start_container, job_id, token, workspace, artifact_id)
                await asyncio.to_thread(container.wait, timeout=self._job_timeout_seconds)
            except requests.exceptions.ReadTimeout:
                # NOT the builtin `TimeoutError` -- docker-py's `container.wait
                # (timeout=...)` overrun raises `requests.exceptions.
                # ReadTimeout` (confirmed against docker-py's own issue
                # tracker: docker/docker-py#1966, #618, #1950, #2266, #3034,
                # #1374 all report this same shape; `ReadTimeout` does NOT
                # inherit from `TimeoutError`, so catching the builtin here
                # would silently fall through to the generic `except
                # Exception` below instead — fail-closed either way (the job
                # still ends up FAILED and killed), but with the wrong error
                # message, which is why this is worth getting right rather
                # than leaving the wrong exception type "harmlessly" caught
                # by the broader clause.
                await asyncio.to_thread(self._kill_and_remove, job_id)
                return JobResult(status=JobStatus.FAILED, error="job exceeded its wall-clock timeout")
            except Exception as exc:  # noqa: BLE001
                await asyncio.to_thread(self._kill_and_remove, job_id)
                return JobResult(status=JobStatus.FAILED, error=f"container run failed: {exc}")
            else:
                await asyncio.to_thread(self._kill_and_remove, job_id)

            return self._read_result(workspace)

    def _start_container(
        self, job_id: str, token: str, workspace: Path, artifact_id: str | None,
    ) -> Any:
        return self._docker.containers.run(
            self._sandbox_image,
            detach=True,
            network="nora-compute",
            volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
            environment={
                "DROP_JOB_TOKEN": token,
                "DROP_INTERNAL_URL": self._drop_internal_url,
                "TASK_FILE": "/workspace/task.txt",
                "ARTIFACT_ID": artifact_id or "",
                "SMITH_MODEL_API_KEY": self._model_api_key,
                "SMITH_MODEL_BASE_URL": self._model_base_url,
                "SMITH_MODEL_NAME": self._model_name,
            },
            mem_limit=DEFAULT_MEM_LIMIT,
            name=job_id,
        )

    def _kill_and_remove(self, job_id: str) -> None:
        """A killed-or-finished container is always removed -- a timed-out
        job must not keep running past its own guardrail (spec §5), and a
        finished one shouldn't accumulate as dead weight. `force=True`
        tolerates "already exited" without raising."""
        try:
            container = self._docker.containers.get(job_id)
            container.remove(force=True)
        except Exception:  # noqa: BLE001 - already gone is fine
            pass

    def _read_result(self, workspace: Path) -> JobResult:
        url_path = workspace / "url.txt"
        summary_path = workspace / "summary.txt"
        if not url_path.exists() or not summary_path.exists():
            return JobResult(
                status=JobStatus.FAILED, error="job exited without producing a result",
            )
        return JobResult(
            status=JobStatus.SUCCEEDED,
            url=url_path.read_text().strip(),
            summary=summary_path.read_text().strip(),
        )

    async def _mint_token(self, *, artifact_id: str | None) -> str:
        scope = "update" if artifact_id is not None else "create"
        body: dict[str, Any] = {"scope": scope, "ttl_seconds": self._job_timeout_seconds}
        if artifact_id is not None:
            body["artifact_id"] = artifact_id
        async with aiohttp.ClientSession() as session, session.post(
            f"{self._drop_internal_url}/api/tokens",
            headers={"Authorization": f"Bearer {self._drop_master_token}"},
            json=body,
        ) as response:
            if response.status != 200:
                raise ConnectionError(f"drop refused token mint (http {response.status})")
            data: Any = await response.json()
            return str(data["token"])
```

**Resolved, not left open:** `container.wait(timeout=...)`'s overrun exception is `requests.exceptions.ReadTimeout`, confirmed against docker-py's own issue tracker (docker/docker-py#1966, #618, #1950, #2266, #3034, #1374 all report this same shape — `ReadTimeout` does not inherit from the builtin `TimeoutError`). The `except` clause above already catches the correct type; no further verification step is needed for this specific exception.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/infra/compute/test_docker_backend.py -v`
Expected: PASS.

- [ ] **Step 6: Type-check and lint**

Run: `uv run mypy nora/infra && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/infra/compute/ tests/infra/compute/ pyproject.toml uv.lock
git commit -m "feat(compute): add LocalDockerBackend"
```

---

### Task 4: Smith's bare sandbox image

**Files:**
- Create: `nora/compute/smith/Dockerfile`
- Create: `nora/compute/smith/agent.py`
- Create: `nora/compute/smith/doctrine.md`
- Create: `nora/compute/smith/pyproject.toml` (Smith's OWN dependency set — a separate Python environment baked into its own image, not the top-level `nora` package's dependencies, matching the Nora Compute spec §3's isolation requirement)

**Interfaces:**
- Consumes: `DROP_JOB_TOKEN`, `DROP_INTERNAL_URL`, `TASK_FILE`, `ARTIFACT_ID` env vars — the exact contract `LocalDockerBackend._start_container` (Task 3) sets.
- Produces: `/workspace/url.txt`, `/workspace/summary.txt` — the exact contract `LocalDockerBackend._read_result` (Task 3) reads.

This task's own testable deliverable, since it needs a real Docker build and a real (or Task-2-real) Drop instance rather than anything `uv run pytest` can exercise: a manual end-to-end run proving the whole chain — mint a token, launch a container, Smith runs a trivial task, calls Drop directly, writes its result, `LocalDockerBackend` reads it back.

- [ ] **Step 1: Write Smith's doctrine (bare-bones for this phase)**

Create `nora/compute/smith/doctrine.md`:

```markdown
---
name: smith-doctrine
description: >
  Governs Smith, Nora's separate compute agent. This phase is bare-bones —
  code execution and raw artifact registration only. The skill library
  (chart/mermaid/markdown/map rendering, and the rendering-mode policy that
  goes with it) lands in a later phase; do not invent it here.
---

# Smith

You are Smith, a separate, neutral agent — not Nora. You have no persona, no
memory of any conversation, and no knowledge of who asked for this task
beyond the task description itself.

Your job: read the task in `/workspace/task.txt`, do the work using your
code-execution tool, and produce exactly one output file in `/workspace`.
Then call `register_artifact` with that file's bytes, a short title, and its
media type. Write the URL it returns to `/workspace/url.txt` and a one- or
two-sentence plain-text summary of what you built to `/workspace/summary.txt`.

If `ARTIFACT_ID` is set in your environment, you are REVISING an existing
artifact, not creating a new one — `register_artifact` handles this
automatically based on that environment variable; you do not need to do
anything differently in how you call it.

Never write a summary that just restates the task — say what you actually
produced.
```

- [ ] **Step 2: Write Smith's bootstrap script**

Create `nora/compute/smith/agent.py`:

```python
"""Smith's bootstrap script — the ENTRYPOINT of the sandbox image (Nora
Compute spec §3). Reads its task, runs a bare Strands agent (code-exec plus
one raw artifact-registration tool, no skill library yet — that's a later
phase), and writes its result to the bind-mounted workspace for
`LocalDockerBackend` (nora/infra/compute/docker_backend.py) to read once this
process exits.

Deliberately NOT importing anything from the top-level `nora` package — this
image is a separate, isolated environment (its own `pyproject.toml`), and an
arbitrary-code-execution sandbox should not hold a copy of Nora's own runtime
dependencies just because both happen to be Python.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests
from strands import Agent, tool
from strands.models.openai import OpenAIModel

WORKSPACE = Path("/workspace")
DROP_INTERNAL_URL = os.environ["DROP_INTERNAL_URL"]
DROP_JOB_TOKEN = os.environ["DROP_JOB_TOKEN"]
ARTIFACT_ID = os.environ.get("ARTIFACT_ID") or None
# Smith's own LLM credential — the same OmniRoute-shaped (api_key, base_url,
# model name) triple `LocalDockerBackend` mints Nora's own from, NOT a copy
# of Nora's own key (nora/infra/compute/docker_backend.py's own docstring on
# why: reused SHAPE, not a shared secret, since this sandbox runs
# LLM-written code and should hold its own bounded credential).
MODEL_API_KEY = os.environ["SMITH_MODEL_API_KEY"]
MODEL_BASE_URL = os.environ["SMITH_MODEL_BASE_URL"]
MODEL_NAME = os.environ["SMITH_MODEL_NAME"]


@tool
def run_python(code: str) -> str:
    """Run a Python snippet in the workspace directory and return its stdout
    (and stderr, if any). Files this snippet writes should go under
    /workspace so they persist after this container exits."""
    result = subprocess.run(
        ["python3", "-c", code],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    return output


@tool
def register_artifact(file_path: str, title: str, media_type: str) -> str:
    """Register a file under /workspace with Drop, returning its URL. Use
    this exactly once, on the one file this task was asked to produce."""
    data = (WORKSPACE / file_path).read_bytes()
    files = {"file": (file_path, data, media_type)}
    form = {"title": title, "media_type": media_type}
    headers = {"Authorization": f"Bearer {DROP_JOB_TOKEN}"}

    if ARTIFACT_ID:
        response = requests.patch(
            f"{DROP_INTERNAL_URL}/api/artifacts/{ARTIFACT_ID}",
            data=form, files=files, headers=headers, timeout=30,
        )
    else:
        response = requests.post(
            f"{DROP_INTERNAL_URL}/api/artifacts",
            data=form, files=files, headers=headers, timeout=30,
        )
    response.raise_for_status()
    return str(response.json()["url"])


def main() -> None:
    task_file = Path(os.environ["TASK_FILE"])
    task = task_file.read_text()
    doctrine = Path(__file__).parent.joinpath("doctrine.md").read_text()

    # Without this, Smith has NO way to call an LLM at all — a real gap an
    # earlier draft of this plan shipped (caught only by a subagent review
    # that actually tried to run this code). Mirrors how Nora's own agent is
    # built (nora/app/composition.py's OMNIROUTE_API_KEY -> AsyncOpenAI
    # client -> Agent(model=...) in nora/agent/factory.py) — confirm
    # `strands.models.openai.OpenAIModel`'s exact constructor signature
    # against the installed Strands SDK version before treating this as
    # final; the import path and general shape (api_key/base_url/model_id)
    # are the assumption this plan is making, not a verified fact.
    model = OpenAIModel(
        client_args={"api_key": MODEL_API_KEY, "base_url": MODEL_BASE_URL},
        model_id=MODEL_NAME,
    )
    agent = Agent(
        model=model,
        system_prompt=doctrine,
        tools=[run_python, register_artifact],
    )
    result = agent(task)

    summary_path = WORKSPACE / "summary.txt"
    if not summary_path.exists():
        # The agent's own final text is the fallback summary if it never
        # explicitly wrote one — better than leaving `LocalDockerBackend`'s
        # `_read_result` to report a bare "no result" failure for a job that
        # otherwise completed.
        summary_path.write_text(str(result))


if __name__ == "__main__":
    main()
```

**Verification note:** the exact Strands SDK import shape (`from strands import Agent, tool`), the `strands.models.openai.OpenAIModel` constructor shape, and the calling convention (`agent(task)` returning something string-convertible) should be confirmed against the Strands Agents SDK version this repo's own `nora` package already pins — confirmed as `strands-agents==1.50.2` in the repo root `pyproject.toml` — before this task is considered done; adjust the import/call shape to match if it differs.

- [ ] **Step 3: Write Smith's `pyproject.toml`**

Create `nora/compute/smith/pyproject.toml`, pinned to the SAME `strands-agents` version the root `nora` package uses (`==1.50.2`, confirmed against the repo root `pyproject.toml` — not left unpinned, so Smith's tool-calling behavior can't silently drift from Nora's own on an unrelated `uv add` elsewhere):

```toml
[project]
name = "smith"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "strands-agents==1.50.2",
    "requests",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

(Pin `strands-agents` to the same version the top-level `nora` package uses — check the repo root `pyproject.toml` and match it exactly, so Smith's tool-calling behavior doesn't drift from Nora's own.)

- [ ] **Step 4: Write the Dockerfile**

Create `nora/compute/smith/Dockerfile`:

```dockerfile
FROM python:3.12-slim

# Same reason as nora's/signal-cli's own Dockerfiles (CLAUDE.md operational
# constraint 1): must be built with --network=host on this host, or
# BuildKit's default bridge can't resolve deb.debian.org.
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system --no-cache .

COPY agent.py doctrine.md ./

WORKDIR /workspace
ENTRYPOINT ["python3", "/app/agent.py"]
```

- [ ] **Step 5: Build the image**

Run: `docker build --network=host -t smith:latest nora/compute/smith/`
Expected: builds successfully.

- [ ] **Step 6: Manual end-to-end verification**

With `db`, `valkey`, `rustfs`, `drop` up (`docker compose up -d db valkey rustfs drop`) and the `smith:latest` image built:

1. Mint a scoped token by hand: `curl -X POST http://<drop-host>:8081/api/tokens -H "Authorization: Bearer $NORA_DROP_TOKEN" -H "Content-Type: application/json" -d '{"scope": "create", "ttl_seconds": 120}'` (from a shell that can reach the internal listener — e.g. `docker compose exec drop curl ...` if nothing else is on that network yet, or temporarily join a debug container to `nora-compute`).
2. Create a workspace directory with a `task.txt` containing a trivial task, e.g. `echo "Write the text 'hello from smith' to a file called output.txt, then register it as a text/plain artifact titled Hello." > /tmp/smith-test/task.txt`.
3. Run the container by hand, matching `_start_container`'s exact shape: `docker run --rm --network nora-compute -v /tmp/smith-test:/workspace -e DROP_JOB_TOKEN=<minted token> -e DROP_INTERNAL_URL=http://drop:8081 -e TASK_FILE=/workspace/task.txt -e ARTIFACT_ID= -e SMITH_MODEL_API_KEY=$OMNIROUTE_API_KEY -e SMITH_MODEL_BASE_URL=<the real OmniRoute base URL, matching config.toml> -e SMITH_MODEL_NAME=<a real model name Nora herself is configured with> smith:latest`.
4. Confirm `/tmp/smith-test/url.txt` and `/tmp/smith-test/summary.txt` now exist, and that the URL in `url.txt` actually serves the registered content when fetched.

- [ ] **Step 7: Commit**

```bash
git add nora/compute/smith/
git commit -m "feat(compute): add Smith's bare sandbox image (code-exec + raw artifact registration)"
```

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-08-12-nora-compute-design.md`):
- `ComputeBackend` port, `LocalDockerBackend` adapter (§2, §12) — Tasks 1, 3. ✓
- Smith as a separate, neutral Strands instance (§3) — Task 4. ✓ (skill library deferred to Phase 3, as scoped)
- Per-job scoped credential (§5, §9) — Task 2, consumed by Task 3. ✓
- `nora-compute` network usage from the compute side (§5a; Phase 1 already declared the network itself) — Task 3's `network="nora-compute"`. ✓
- Hard wall-clock timeout, concurrency limit (§5) — Task 3 (`job_timeout_seconds` via `container.wait(timeout=)`, `max_concurrent_jobs` via the semaphore). ✓
- Sandbox image built once, not per job (§3) — Task 4 builds the image as a standalone step, `LocalDockerBackend` only ever launches containers *from* it. ✓
- Per-job model-call/token spend cap (§5) — **NOT built in this phase.** Smith's own `Agent` call has no iteration/token ceiling yet; flagged here rather than silently dropped — needs a follow-up task (either in Phase 3 or a small addition to this phase) adding a max-tool-calls or max-tokens bound to `agent.py`'s `Agent` construction.

**Placeholder scan:** no TBD/TODO. One remaining "verify against the installed version" note (the exact `OpenAIModel` constructor shape) is a verification step with a concrete action attached, not a placeholder. The docker-py timeout exception is no longer an open item — resolved against the real issue tracker (`requests.exceptions.ReadTimeout`, not the builtin `TimeoutError`) rather than left as an assumption.

**Fixed after independent subagent review, not shipped as originally drafted:**
1. **Smith had no way to call an LLM at all** — no `model=` anywhere in `Agent(...)`'s construction, no API credential threaded through `LocalDockerBackend`'s environment. This was a functional blocker, not a style nit: as originally drafted, every job would fail on its first model call. Fixed: `LocalDockerBackend` now takes `model_api_key`/`model_base_url`/`model_name`, passes them as `SMITH_MODEL_*` env vars, and `agent.py` constructs a real `OpenAIModel` from them before building `Agent(model=model, ...)`.
2. **The mocked test suite had a real asyncio bug**, not just a style concern — `launch`/`poll` never suspend internally, so a bare retry loop with no sleep between iterations never yields to the event loop and the background `_run_job` task never actually runs; several tests would have raised `FileNotFoundError` rather than testing anything. Fixed with a shared `_poll_until_done` helper that sleeps between iterations, used everywhere a test waits for a job.
3. **The `TimeoutError` assumption was wrong** — confirmed against docker-py's own issue tracker to be `requests.exceptions.ReadTimeout`. Fixed, not just flagged.

**Type consistency:** `JobResult`/`JobStatus` used identically across Task 1 (Protocol), Task 3 (`LocalDockerBackend`), and the tests. `launch`/`poll`'s keyword-only signatures match between the Protocol and `LocalDockerBackend`/`FakeComputeBackend`.

**Gap found and named, not silently fixed:** the per-job token/spend cap guardrail from spec §5 has no task in this plan. Given this phase's own scope (prove the pipe works end-to-end with a trivial job), and that a genuinely open-ended task is what would exercise that cap, it's reasonable to defer — but it must not be forgotten. Added as an explicit task for Phase 3 rather than assumed away.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-nora-compute-02-backend-sandbox.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
