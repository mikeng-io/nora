# Nora Compute — Phase 1: Drop's `update_artifact` Extension — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Drop's already-shipped artifact-ingest surface an `update` path — `PATCH /api/artifacts/{artifact_id}` overwrites an existing artifact's bytes/metadata at its existing URL, instead of the only current option (`POST /api/artifacts`, always mints a new id/URL) — plus the `nora-compute` Docker network Smith's sandbox (Phase 2) will need to reach Drop without joining the wider `nora` network.

**Architecture:** One new HTTP verb on Drop's existing internal listener (`deploy/drop/server.py`), a new `ArtifactStore.update`/`PgRustfsArtifactStore.update` method (`deploy/drop/artifacts.py`) that does a targeted Postgres `UPDATE` + RustFS overwrite at the same object key (never a second `INSERT`), and a matching `ArtifactIngest.update` method on Nora's own `nora/domain/ports.py` Protocol plus its `DropArtifactIngest`/`FakeArtifactIngest` implementations — kept in sync per this repo's own port-conformance discipline, even though (per the Nora Compute spec §9) the actual caller in production is Smith's own HTTP client against Drop directly, not this Python port. `docker-compose.yml` gains a `nora-compute` network with `drop` attached to it alongside its existing `nora` membership.

**Tech Stack:** Python 3.12, Starlette/uvicorn (`deploy/drop`, its own venv/`pyproject.toml`), `asyncpg`, `boto3` (RustFS/S3), `aiohttp` (`nora/infra/drop.py`'s HTTP client), pytest, Docker Compose.

## Global Constraints

- Every port method on `nora.domain.ports.ArtifactIngest` (and every other port in that file) must be keyword-only after `self` — enforced generically by `tests/test_ports.py::test_port_methods_are_keyword_only_after_self`, which iterates every `Protocol` in the module; no per-method test needs adding for this, just conformance.
- CLAUDE.md: "a port never gains a second implementation — including a test fake — without a shared contract suite both pass." `ArtifactIngest` already has two implementations (`DropArtifactIngest`, `FakeArtifactIngest`) bound by `tests/contract/test_artifact_ingest_contract.py` — adding `update` means extending all three together, in the same commit, not just the Protocol.
- `expires_at: int | None = None` on `update` means "leave the artifact's existing expiry unchanged" — the update-specific reading of the same convention `register`'s own docstring gives `None` ("apply whatever default exists"). This is NOT "set expiry to null."
- Wire format for both `POST /api/artifacts` and `PATCH /api/artifacts/{artifact_id}` is `multipart/form-data` (`file`, `title`, `media_type`, optional `expires_at`) — never JSON/base64. `deploy/drop/server.py`'s `register_artifact` docstring gives the reasoning (streams the file part; base64 inflates payload ~33% and forces whole-body buffering); `update_artifact` must match this exactly, not invent a second wire shape for the same port.
- `deploy/drop` is a separate Python package with its own venv (`deploy/drop/pyproject.toml`, its own `uv run pytest` from inside that directory) — distinct from the top-level `nora` package's own `uv run pytest`. Tasks in this plan that touch `deploy/drop/` run tests from inside that directory; tasks touching `nora/` run from the repo root.
- `PgRustfsArtifactStore` has no dedicated automated unit test in this repo (confirmed: only `FakeArtifactStore`-backed route tests exist under `deploy/drop/tests/`) — real-backend behavior is verified "by hand" against live Postgres/RustFS, per `deploy/drop/tests/conftest.py`'s own module docstring. This plan follows that existing convention for `PgRustfsArtifactStore.update` rather than inventing a new testing pattern the rest of the file doesn't have.

---

## File Structure

| File | Change |
|---|---|
| `deploy/drop/artifacts.py` | Add `update` to the `ArtifactStore` Protocol; implement `PgRustfsArtifactStore.update` |
| `deploy/drop/tests/conftest.py` | Add `FakeArtifactStore.update` |
| `deploy/drop/tests/test_artifacts_route.py` | Add route tests for `PATCH /api/artifacts/{artifact_id}` |
| `deploy/drop/server.py` | Add `update_artifact` handler; register the `PATCH` route on `internal_app` |
| `nora/domain/ports.py` | Add `update` to the `ArtifactIngest` Protocol |
| `nora/infra/drop.py` | Add `DropArtifactIngest.update` |
| `nora/testing/fakes.py` | Add `FakeArtifactIngest.update` |
| `tests/contract/test_artifact_ingest_contract.py` | Extend the shared contract suite to cover `update` on both implementations |
| `docker-compose.yml` | Add `nora-compute` network; attach `drop` to it |

---

### Task 1: Drop's `update` route, backed by the fake store

**Files:**
- Modify: `deploy/drop/tests/conftest.py` (`FakeArtifactStore`, currently lines 40–70)
- Modify: `deploy/drop/tests/test_artifacts_route.py`
- Modify: `deploy/drop/server.py` (add handler + route, near `register_artifact` at lines 167–225 and the `internal_app` route list at lines 279–283)

**Interfaces:**
- Consumes: `ArtifactRow`, `ArtifactStoreUnavailableError`, `Registration` from `deploy/drop/artifacts.py` (already exist); `bearer_ok` from `deploy/drop/auth.py` (already exists, already imported in `server.py`).
- Produces: `FakeArtifactStore.update(self, *, artifact_id: str, data: bytes, title: str, media_type: str, expires_at: int | None = None) -> Registration | None` — later tasks' route tests and Task 2's real implementation must match this exact signature and `None`-means-not-found convention.

- [ ] **Step 1: Write the failing route tests**

Add to `deploy/drop/tests/test_artifacts_route.py` (after the existing `test_a_successful_register_returns_artifact_id_and_url` test, using the same `wired_apps` fixture):

```python
def test_a_successful_update_overwrites_title_and_media_type(wired_apps):
    server, _links, artifacts = wired_apps
    client = TestClient(server.internal_app)

    minted = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Original", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v1", "text/plain")},
    ).json()
    artifact_id = minted["artifact_id"]

    resp = client.patch(
        f"/api/artifacts/{artifact_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Revised", "media_type": "text/markdown"},
        files={"file": ("t.md", b"v2", "text/markdown")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == artifact_id
    assert body["url"] == minted["url"]  # same slug, same URL -- the whole point


def test_an_update_actually_overwrites_the_bytes(wired_apps):
    server, _links, artifacts = wired_apps
    client = TestClient(server.internal_app)

    minted = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Original", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v1", "text/plain")},
    ).json()
    artifact_id = minted["artifact_id"]

    client.patch(
        f"/api/artifacts/{artifact_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Revised", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v2", "text/plain")},
    )

    slug = minted["url"].rsplit("/", 1)[-1]
    assert artifacts._bytes[artifact_id] == b"v2"
    assert artifacts._rows[slug].title == "Revised"


def test_an_unknown_artifact_id_update_is_404(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_doesnotexist",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 404


def test_an_update_missing_the_token_is_403(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_000001",
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403


def test_an_update_missing_the_file_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_000001",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
    )
    assert resp.status_code == 400


def test_an_unavailable_store_on_update_is_503(wired_apps):
    server, _links, _artifacts = wired_apps
    server.internal_app.state.artifacts = FakeArtifactStore(fail=True)
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_000001",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 503
```

Add `FakeArtifactStore` to this test file's imports (it's already imported by `conftest.py`'s own fixtures, but the `fail=True` construction in the last test needs the class directly):

```python
from conftest import TOKEN, FakeArtifactStore
```

(This import line already exists at the top of `test_artifacts_route.py` — confirm it's there; if not, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `deploy/drop/`): `uv run pytest tests/test_artifacts_route.py -k update -v`
Expected: FAIL — `AttributeError: 'FakeArtifactStore' object has no attribute 'update'` (or a 404/405 from the missing route, depending on which fails first).

- [ ] **Step 3: Implement `FakeArtifactStore.update`**

In `deploy/drop/tests/conftest.py`, add to `FakeArtifactStore` (after its existing `fetch_bytes` method, before the `wired_apps` fixture):

```python
    async def update(
        self, *, artifact_id: str, data: bytes, title: str, media_type: str,
        expires_at: int | None = None,
    ) -> Registration | None:
        if self._fail:
            raise ArtifactStoreUnavailableError("rustfs/postgres down")
        slug = None
        for existing_slug, row in self._rows.items():
            if row.id == artifact_id:
                slug = existing_slug
                break
        if slug is None:
            return None
        new_expires_at = expires_at if expires_at is not None else self._rows[slug].expires_at
        self._rows[slug] = ArtifactRow(
            id=artifact_id, slug=slug, title=title, media_type=media_type,
            size_bytes=len(data), expires_at=new_expires_at,
        )
        self._bytes[artifact_id] = data
        return Registration(artifact_id=artifact_id, slug=slug)
```

Add `Registration` to this file's existing import from `artifacts` (it currently imports `ArtifactRow, ArtifactStoreUnavailableError, Registration` — verify `Registration` is already there; it is, per the existing `register` method's return type).

- [ ] **Step 4: Implement the `update_artifact` route handler**

In `deploy/drop/server.py`, add after `register_artifact` (after line 225):

```python
async def update_artifact(request: Request) -> JSONResponse:
    """`PATCH /api/artifacts/{artifact_id}` — the HTTP implementation of
    `ArtifactIngest.update`'s contract (`nora/domain/ports.py`, Nora Compute
    spec §9): overwrites an existing artifact's bytes/metadata AT ITS EXISTING
    slug/URL, never mints a new one. Same multipart wire shape as
    `register_artifact` (this module's own docstring on why multipart, not
    JSON) plus the `artifact_id` path parameter naming which row to overwrite.
    """
    if not bearer_ok(request, token=request.app.state.token):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    artifact_id = request.path_params["artifact_id"]

    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - any multipart parse failure is a 400
        return JSONResponse(
            {"error": "body must be multipart/form-data with file/title/media_type"},
            status_code=400,
        )

    upload = form.get("file")
    title = form.get("title")
    media_type = form.get("media_type")
    expires_raw = form.get("expires_at")

    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "missing file"}, status_code=400)
    if not isinstance(title, str) or not title:
        return JSONResponse({"error": "missing title"}, status_code=400)
    if not isinstance(media_type, str) or not media_type:
        return JSONResponse({"error": "missing media_type"}, status_code=400)

    expires_at: int | None = None
    if expires_raw not in (None, ""):
        try:
            expires_at = int(expires_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return JSONResponse({"error": "expires_at must be epoch ms"}, status_code=400)

    data = await upload.read()

    try:
        registration = await request.app.state.artifacts.update(
            artifact_id=artifact_id, data=data, title=title, media_type=media_type,
            expires_at=expires_at,
        )
    except ArtifactStoreUnavailableError:
        return JSONResponse({"error": "unavailable"}, status_code=503)

    if registration is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    url = f"{PUBLIC_BASE_URL.rstrip('/')}/drop/{registration.slug}"
    return JSONResponse({"artifact_id": registration.artifact_id, "url": url})
```

Then register the route in `internal_app`'s route list (currently lines 279–283):

```python
internal_app = Starlette(routes=[
    Route("/health", health),
    Route("/api/links", mint_link, methods=["POST"]),
    Route("/api/artifacts", register_artifact, methods=["POST"]),
    Route("/api/artifacts/{artifact_id}", update_artifact, methods=["PATCH"]),
])
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `deploy/drop/`): `uv run pytest tests/test_artifacts_route.py -v`
Expected: PASS (all tests, including the new ones and every pre-existing one).

- [ ] **Step 6: Lint and type-check**

Run (from `deploy/drop/`): `uv run ruff check . && uv run mypy .`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add deploy/drop/artifacts.py deploy/drop/server.py deploy/drop/tests/conftest.py deploy/drop/tests/test_artifacts_route.py
git commit -m "feat(drop): add PATCH /api/artifacts/{artifact_id} update route"
```

(Note: `deploy/drop/artifacts.py` is listed here even though this task didn't modify it — it will have been modified by the time Step 7 runs if Task 2 is done first; if running Task 1 alone, drop that file from the `git add`.)

---

### Task 2: `PgRustfsArtifactStore.update` — the real backend

**Files:**
- Modify: `deploy/drop/artifacts.py`

**Interfaces:**
- Consumes: `self._pool` (`asyncpg.Pool`), `self._s3`, `self._bucket`, `self._put_object` (all already exist on `PgRustfsArtifactStore`, used identically to `register`).
- Produces: `PgRustfsArtifactStore.update(self, *, artifact_id: str, data: bytes, title: str, media_type: str, expires_at: int | None = None) -> Registration | None` — must satisfy the `ArtifactStore` Protocol extended in this task, and match `FakeArtifactStore.update`'s contract from Task 1 exactly (same `None`-means-not-found convention) since `server.py`'s route code calls whichever is wired into `app.state.artifacts` without knowing which.

- [ ] **Step 1: Add `update` to the `ArtifactStore` Protocol**

In `deploy/drop/artifacts.py`, extend the `ArtifactStore` Protocol (currently lines 65–76):

```python
class ArtifactStore(Protocol):
    """The shape both `PgRustfsArtifactStore` and the tests' in-memory fake
    share — see `links.py::LinkStore`'s docstring for why this is a Protocol
    even though this app does not depend on `nora.domain.ports`."""

    async def register(
        self, *, data: bytes, title: str, media_type: str, expires_at: int | None,
    ) -> Registration: ...

    async def update(
        self, *, artifact_id: str, data: bytes, title: str, media_type: str,
        expires_at: int | None = None,
    ) -> Registration | None: ...

    async def get_by_slug(self, *, slug: str) -> ArtifactRow | None: ...

    async def fetch_bytes(self, *, artifact_id: str) -> bytes | None: ...
```

- [ ] **Step 2: Implement `PgRustfsArtifactStore.update`**

Add to `PgRustfsArtifactStore` (after the existing `register` method, before `_put_object`, i.e. after line 135):

```python
    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> Registration | None:
        """Overwrite an existing artifact's bytes/metadata AT ITS EXISTING id
        and slug — a targeted UPDATE, never a second INSERT (`register` mints
        a fresh id/slug pair every call; this reuses the one already assigned).

        Deliberately the REVERSE of `register`'s write order: Postgres first,
        RustFS second. Checking Postgres first means an update for an unknown
        `artifact_id` never touches RustFS at all — no orphaned object for a
        write that was going to 404 anyway. `expires_at=None` leaves the
        existing value alone (`COALESCE`), matching the same "None means no
        change" convention `ArtifactIngest.update`'s own docstring documents.

        Residual risk, named rather than solved (same posture Nora Drop spec
        §4 already takes toward RustFS's own maturity): if the Postgres UPDATE
        succeeds but the following RustFS put then fails, the row's metadata
        briefly describes bytes that haven't actually been overwritten yet.
        Not compensated here — artifacts are regenerable outputs, not a
        system of record, and a compensating transaction is more machinery
        than that failure mode's actual cost justifies.
        """
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "UPDATE artifacts SET title = $2, media_type = $3, "
                    "size_bytes = $4, expires_at = COALESCE($5, expires_at) "
                    "WHERE id = $1 RETURNING slug",
                    artifact_id, title, media_type, len(data), expires_at,
                )
            except Exception as exc:
                raise ArtifactStoreUnavailableError(f"postgres update failed: {exc}") from exc
        if row is None:
            return None

        try:
            await asyncio.to_thread(self._put_object, artifact_id, data, media_type)
        except Exception as exc:
            raise ArtifactStoreUnavailableError(f"rustfs put failed: {exc}") from exc
        return Registration(artifact_id=artifact_id, slug=row["slug"])
```

- [ ] **Step 3: Confirm the fake and the real implementation stay in sync**

Run (from `deploy/drop/`): `uv run mypy .`
Expected: clean — `mypy` structurally checks `PgRustfsArtifactStore` against the `ArtifactStore` Protocol; a signature mismatch with Task 1's `FakeArtifactStore.update` or the Protocol itself fails here.

Run (from `deploy/drop/`): `uv run pytest -v`
Expected: PASS — the full existing suite (Task 1's new tests included) still passes; `PgRustfsArtifactStore` itself has no direct automated test, matching this file's existing convention (module docstring on `deploy/drop/tests/conftest.py`).

- [ ] **Step 4: Manual verification against real Postgres + RustFS**

Following the same by-hand verification `deploy/drop`'s own docstrings describe for `register` (not an automated pytest step): bring up `docker compose up -d db valkey rustfs drop`, mint an artifact via `curl -X POST` against `drop`'s internal port with a bearer token, then `curl -X PATCH` the same `artifact_id` with new content, and confirm `GET /drop/<slug>` on the public listener now serves the new bytes at the same URL.

- [ ] **Step 5: Commit**

```bash
git add deploy/drop/artifacts.py
git commit -m "feat(drop): implement PgRustfsArtifactStore.update"
```

---

### Task 3: `ArtifactIngest.update` on Nora's own Python port

**Files:**
- Modify: `nora/domain/ports.py` — two separate locations, not one (an earlier draft of this plan conflated them): the `ArtifactIngest` class's OWN docstring, inside the class body at lines 743–796, and the file's separate top-of-file MODULE docstring's own `ArtifactIngest` bullet, currently lines 145–157 — a shorter, independent summary that also currently says "declared here with ZERO implementations" and needs its own update once `update` exists and Task 4 gives the port a second real implementation of it.

**Interfaces:**
- Consumes: `ArtifactRegistration` (already defined in this file, lines 724–738).
- Produces: `ArtifactIngest.update(self, *, artifact_id: str, data: bytes, title: str, media_type: str, expires_at: int | None = None) -> ArtifactRegistration` — Task 4's `DropArtifactIngest`/`FakeArtifactIngest` must both implement this exact signature.

- [ ] **Step 1: Write the failing conformance check**

Run: `uv run pytest tests/test_ports.py -k "artifact_ingest or keyword_only" -v`
Expected: at this point `update` doesn't exist on the Protocol yet, so this just confirms the generic keyword-only test currently passes against `register` alone — there's nothing to fail yet. This step is a baseline read, not a red step; the real red/green cycle for this task is Task 4's contract test (a Protocol addition with no implementation isn't independently testable — Python's `Protocol` doesn't enforce anything at declaration time).

- [ ] **Step 2: Add `update` to the `ArtifactIngest` Protocol**

In `nora/domain/ports.py`, extend `ArtifactIngest` (currently ending at line 796 with the `register` method):

```python
    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration: ...
```

Add a paragraph to the `ArtifactIngest` CLASS's own docstring (the one attached to the class body, ending "...that wiring is Compute's, not this port's." — inside the 743–796 line range, not the separate module-level one):

```
    `update` (Nora Compute spec §9) overwrites an existing artifact at its
    existing `artifact_id`/URL rather than minting a new one — `drop`'s own
    `PATCH /api/artifacts/{artifact_id}` (`deploy/drop/server.py`) is its
    concrete implementation. Note the caller asymmetry this port carries
    deliberately: Nora Compute's actual production caller for BOTH `register`
    and `update` is Smith's own HTTP client against `drop` directly (its own
    per-job credential, its own container — see the Nora Compute spec §2/§3
    on why it never imports this Python module), not `DropArtifactIngest`.
    This Protocol and its two implementations exist so the port stays
    conformant and testable on Nora's own side per this repo's own
    discipline, the same reason `register` already had `DropArtifactIngest`
    as a real second implementation before Compute had a caller for it.
```

**Separately**, update the file's actual top-of-file MODULE docstring — a different, shorter block at lines 145–157 that also has its own `ArtifactIngest` bullet and currently says "declared here with ZERO implementations," now stale once this task and Task 4 exist. Find the bullet starting `- `ArtifactIngest` is the Nora Drop spec's §7a seam...` and add a trailing clause noting it has since gained `update` (spec §9) and that `DropArtifactIngest`/`FakeArtifactIngest` are both real implementations bound by their contract suite — a one- or two-sentence addition, not a rewrite of the existing bullet.

- [ ] **Step 3: Verify keyword-only conformance**

Run: `uv run pytest tests/test_ports.py -v`
Expected: PASS — `test_port_methods_are_keyword_only_after_self` iterates every `Protocol` method generically and now includes `update`.

- [ ] **Step 4: Type-check**

Run: `uv run mypy nora/domain`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add nora/domain/ports.py
git commit -m "feat(ports): add ArtifactIngest.update"
```

---

### Task 4: `DropArtifactIngest.update`, `FakeArtifactIngest.update`, and the shared contract suite

**Files:**
- Modify: `nora/infra/drop.py`
- Modify: `nora/testing/fakes.py` (`FakeArtifactIngest`, currently lines 568–632)
- Modify: `tests/contract/test_artifact_ingest_contract.py`

**Interfaces:**
- Consumes: `ArtifactIngest.update`'s signature from Task 3; `ArtifactRegistration` from `nora.domain.ports`.
- Produces: both `DropArtifactIngest.update` and `FakeArtifactIngest.update`, held to one shared contract test — nothing later in this plan consumes these directly (Phase 4 will, as `make_artifact`'s eventual `artifact_id`-set path, but that's out of this plan's scope).

- [ ] **Step 1: Write the failing contract tests**

In `tests/contract/test_artifact_ingest_contract.py`, extend `_WorkingDrop` (currently lines 41–57) with an update handler:

```python
class _WorkingDrop:
    """A minimal double for `drop`'s internal `POST /api/artifacts` and
    `PATCH /api/artifacts/{artifact_id}`: bearer-checked, mints/overwrites a
    fresh id/url per call (never the fake that answers every request the same
    way — CLAUDE.md's own warning is exactly about a double like this one)."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._known: set[str] = set()

    async def register(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "forbidden"}, status=403)
        await request.multipart()  # drain the body like a real handler would
        n = next(self._ids)
        artifact_id = f"art_c{n:06d}"
        self._known.add(artifact_id)
        return web.json_response(
            {"artifact_id": artifact_id, "url": f"https://drop.nora.example.net/d/c{n}"}
        )

    async def update(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "forbidden"}, status=403)
        artifact_id = request.match_info["artifact_id"]
        await request.multipart()
        if artifact_id not in self._known:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {"artifact_id": artifact_id, "url": f"https://drop.nora.example.net/d/{artifact_id}"}
        )
```

Extend the `ingest` fixture (currently lines 64–78) to register the update route:

```python
@pytest.fixture(params=["drop", "fake"])
async def ingest(request):
    """A working `ArtifactIngest` of each kind."""
    if request.param == "fake":
        yield FakeArtifactIngest()
        return
    drop = _WorkingDrop()
    app = web.Application()
    app.router.add_post("/api/artifacts", drop.register)
    app.router.add_patch("/api/artifacts/{artifact_id}", drop.update)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    try:
        yield DropArtifactIngest(base_url=base, token=TOKEN, timeout_seconds=5.0)
    finally:
        await server.close()
```

Add new tests (after the existing `test_a_backend_failure_raises_rather_than_degrading`):

```python
def test_update_is_keyword_only_after_self():
    """Same convention `tests/test_ports.py` pins for every port method."""
    params = inspect.signature(DropArtifactIngest.update).parameters
    for name in ("artifact_id", "data", "title", "media_type", "expires_at"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY


async def test_a_successful_update_returns_a_populated_registration(ingest):
    created = await ingest.register(data=b"v1", title="A Title", media_type="text/plain")
    updated = await ingest.update(
        artifact_id=created.artifact_id, data=b"v2", title="New Title",
        media_type="text/markdown",
    )
    assert isinstance(updated, ArtifactRegistration)
    assert updated.artifact_id == created.artifact_id


async def test_updating_an_unknown_artifact_id_raises(ingest):
    with pytest.raises(ConnectionError):
        await ingest.update(
            artifact_id="art_doesnotexist", data=b"x", title="T", media_type="text/plain",
        )


async def test_an_update_backend_failure_raises_rather_than_degrading(failing_ingest):
    with pytest.raises(ConnectionError):
        await failing_ingest.update(
            artifact_id="art_x", data=b"x", title="X", media_type="text/plain",
        )
```

**Also extend the pre-existing `failing_ingest` fixture** (already in this file, registering only `app.router.add_post("/api/artifacts", _refusing_drop)`) with a matching PATCH route, or the test above doesn't exercise what it claims to. Without this, `failing_ingest.update(...)`'s PATCH request hits no registered route at all — aiohttp's own unmatched-route 404, not `_refusing_drop`'s intended 403 — and the test still passes only because *any* non-200 status raises `ConnectionError` in `DropArtifactIngest`. That makes it accidentally identical to `test_updating_an_unknown_artifact_id_raises` above rather than genuinely testing "the backend refuses/is unreachable." Add:

```python
app.router.add_patch("/api/artifacts/{artifact_id}", _refusing_drop)
```

to `failing_ingest`'s own `app.router.add_post(...)` line, so both routes point at the same refusing handler.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_artifact_ingest_contract.py -k update -v`
Expected: FAIL — `AttributeError: 'DropArtifactIngest' object has no attribute 'update'` / `'FakeArtifactIngest' object has no attribute 'update'`.

- [ ] **Step 3: Implement `DropArtifactIngest.update`**

In `nora/infra/drop.py`, add after `register` (after line 116, before the closing of the class):

```python
    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        """Overwrite an existing artifact at `artifact_id`. Same raise-on-any-
        failure contract as `register` (this class's own docstring) — a 404
        for an unknown `artifact_id` is not distinguished from a 503 or a
        network failure; every non-200 raises `ConnectionError`, matching
        `register`'s own simplicity rather than inventing a richer error
        taxonomy this port doesn't otherwise have."""
        form = aiohttp.FormData()
        form.add_field("title", title)
        form.add_field("media_type", media_type)
        if expires_at is not None:
            form.add_field("expires_at", str(expires_at))
        form.add_field(
            "file", data, filename=title or "artifact", content_type=media_type,
        )
        update_url = self._register_url + "/" + artifact_id
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.patch(
                    update_url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    data=form,
                ) as response,
            ):
                if response.status != 200:
                    body_text = (await response.text())[:200]
                    raise ConnectionError(
                        f"drop refused update (http {response.status}): {body_text}"
                    )
                body: Any = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise  # shutdown is not an update failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(
                f"drop unreachable ({type(exc).__name__})"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConnectionError(
                f"drop returned a non-JSON body ({type(exc).__name__})"
            ) from exc

        returned_id = body.get("artifact_id") if isinstance(body, dict) else None
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(returned_id, str) or not returned_id or not isinstance(
            url, str,
        ) or not url:
            raise ConnectionError(f"drop returned a malformed registration: {body!r}")
        return ArtifactRegistration(artifact_id=returned_id, url=url)
```

- [ ] **Step 4: Implement `FakeArtifactIngest.update`**

In `nora/testing/fakes.py`, add to `FakeArtifactIngest` (after the existing `register` method, before the class ends):

```python
    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        if self._fail:
            raise ConnectionError("drop unreachable")
        if artifact_id not in self.artifacts:
            raise ConnectionError(f"drop refused update (http 404): not found")
        existing_expires_at = self.artifacts[artifact_id]["expires_at"]
        self.artifacts[artifact_id] = {
            "data": data,
            "title": title,
            "media_type": media_type,
            "expires_at": expires_at if expires_at is not None else existing_expires_at,
        }
        return ArtifactRegistration(
            artifact_id=artifact_id,
            url=f"https://nora.example.net/drop/{artifact_id}",
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_artifact_ingest_contract.py -v`
Expected: PASS — all tests, including the pre-existing `register`-focused ones.

- [ ] **Step 6: Full suite, lint, types**

Run: `uv run pytest -q && uv run ruff check nora/ evals/ tests/ && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/infra/drop.py nora/testing/fakes.py tests/contract/test_artifact_ingest_contract.py
git commit -m "feat(drop-client): add DropArtifactIngest.update and FakeArtifactIngest.update"
```

---

### Task 5: `nora-compute` Docker network

**Files:**
- Modify: `docker-compose.yml` (the `drop` service block, currently lines 343–420; the top-level `networks:` block, currently lines 446–450)

**Interfaces:**
- Consumes: nothing code-level — this is infra config only.
- Produces: a `nora-compute` network that Phase 2's `LocalDockerBackend` will attach Smith's per-job containers to, with `drop` reachable from it. Nothing on `nora` (Postgres, `signal-cli`, Valkey, RustFS) is reachable from `nora-compute` — that's the point (Nora Compute spec §5a).

- [ ] **Step 1: Add the network to `drop`'s service block**

In `docker-compose.yml`, change `drop`'s `networks:` line (currently line 354, `networks: [nora]`) to:

```yaml
    # Two networks, deliberately (Nora Compute spec §5a): `nora` keeps drop
    # reachable by Nora and able to reach Postgres/Valkey/RustFS, unchanged
    # from the Drop spec. `nora-compute` is new and narrower -- only drop and
    # Smith's per-job sandbox containers (Phase 2) ever join it, so Smith
    # never gets network-level reachability to anything else on `nora`
    # (Postgres, signal-cli, Valkey, RustFS) just by being able to reach drop.
    networks: [nora, nora-compute]
```

- [ ] **Step 2: Declare the network**

In `docker-compose.yml`'s top-level `networks:` block (currently lines 446–450):

```yaml
networks:
  nora:
  # SearXNG + Firecrawl network joins REMOVED (DEC-73, 2026-08-08): web_search is
  # Tavily (a public API reached over the default network), web_fetch is gone, so
  # Nora no longer calls the co-located SearXNG/Firecrawl containers.
  nora-compute:
  # Nora Compute spec §5a: only `drop` and Smith's per-job sandbox containers
  # (Phase 2, LocalDockerBackend) ever join this network. Smith needs to reach
  # `drop:8081` directly (its own per-job credential, Nora Compute spec §2/§3)
  # without inheriting reachability to Postgres/signal-cli/Valkey/RustFS the
  # way it would if it joined `nora` itself.
```

- [ ] **Step 3: Validate the compose file**

Run: `docker compose config --quiet`
Expected: no output, exit code 0 — confirms the YAML parses and the network reference resolves.

- [ ] **Step 4: Bring `drop` up and confirm it still passes health**

Run: `docker compose up -d db valkey rustfs drop && sleep 5 && docker compose ps drop`
Expected: `drop`'s status shows `healthy` (or `starting` still converging within its `start_period: 20s`) — confirms the added network attachment didn't break `drop`'s existing connectivity to `db`/`valkey`/`rustfs` on `nora`.

Run: `docker network inspect nora-compute --format '{{range .Containers}}{{.Name}} {{end}}'`
Expected: includes `drop` (Smith isn't built yet — Phase 2 — so it's the only member for now).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(drop): add nora-compute network for Smith's future sandbox containers"
```

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-08-12-nora-compute-design.md` §9 and the network portion of §5a):
- `ArtifactIngest.update` port method — Task 3. ✓
- Drop's own `update` HTTP verb — Task 1 (route) + Task 2 (real backend). ✓
- `expires_at=None` means "leave unchanged" — implemented via `COALESCE` in Task 2, and in both fakes (Task 1, Task 4). ✓
- `DropArtifactIngest`/`FakeArtifactIngest` kept in sync via the shared contract suite (CLAUDE.md's own port discipline, flagged by the subagent review as missing from the original spec text) — Task 4. ✓
- `nora-compute` network (§5a) — Task 5. ✓
- Everything else in the Nora Compute spec (Smith, the skill library, `make_artifact`, guardrails, the outbox/poller) is explicitly out of this plan's scope — Phases 2–4.

**Placeholder scan:** no TBD/TODO; every step has real code, not a description of code.

**Type consistency:** `update`'s signature — `artifact_id: str, data: bytes, title: str, media_type: str, expires_at: int | None = None`, returning `Registration | None` at the `ArtifactStore`/fake-store layer and `ArtifactRegistration` (never `None`, raises instead) at the `ArtifactIngest`/`DropArtifactIngest`/`FakeArtifactIngest` layer — is identical across Tasks 1, 2, 3, and 4. The `None`-vs-raise split at the HTTP boundary (`server.py` turns `None` into a 404; `DropArtifactIngest` turns a non-200 into a raise) is deliberate, not an inconsistency — it mirrors the exact same split `register`/`ArtifactStoreUnavailableError` already has between "backend down" (raise) and "no such row" (a value, not a raise) at the store layer, while the *port* layer stays raise-only for both, matching `register`'s existing simplicity.

**Fixed after independent subagent review, not shipped as originally drafted:**
1. **Task 4's backend-failure update test didn't test what it claimed.** The pre-existing `failing_ingest` fixture only registered a POST route for its refusing handler — a PATCH request hit no route at all (a bare 404) rather than the intended refusal, so the test passed for the wrong reason and was functionally a duplicate of the not-found test beside it. Fixed by extending the fixture with a matching PATCH route.
2. **Task 3's file citation conflated two different docstrings.** `nora/domain/ports.py` has both a top-of-file MODULE docstring (with its own short `ArtifactIngest` bullet, at lines 145–157) and the `ArtifactIngest` class's own docstring (inside the class body, 743–796) — an earlier draft cited the class docstring's location but labeled it "module docstring," and never revisited the actual module-level bullet, which still says "declared here with ZERO implementations" and would have stayed stale. Fixed: both locations are now named correctly, and Step 2 updates both.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-nora-compute-01-drop-update.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
