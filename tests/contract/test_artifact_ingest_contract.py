"""One suite, both implementations of `ArtifactIngest` (Nora Drop spec §7a).

CLAUDE.md: "A port never gains a second implementation — including a test fake
— without a shared contract suite both pass." `nora.testing.FakeArtifactIngest`
was that port's sole implementation until `nora.infra.drop.DropArtifactIngest`
(this task) — `nora.testing`'s own module docstring named this suite as the
thing to add "once `drop`'s own ingest adapter exists," and this is it.

`DropArtifactIngest` is an HTTP client, so "the real implementation" here is
pointed at a small in-process aiohttp double standing in for `drop`'s internal
listener (the same choice `tests/test_shortener.py` and
`tests/test_drop_artifact_ingest.py` make) — never a live `drop` container.
That keeps this suite exercising `DropArtifactIngest`'s own request/response
handling, not `deploy/drop`'s server code (which has its own, separate test
suite under `deploy/drop/tests/`, in its own venv, run against fakes for
valkey/rustfs/postgres — see that package's docstrings).

The property both implementations must hold: **a successful `register` always
returns a populated `ArtifactRegistration`, two calls never collide, and every
failure raises rather than degrading** — the inversion from
`LinkPreviewSource`'s fail-soft `None` that `ArtifactIngest.register`'s own
docstring calls out (this mirrors `BlobStore.put`, not a fail-soft lookup).
"""

from __future__ import annotations

import inspect
import itertools

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import ArtifactIngest, ArtifactRegistration
from nora.infra.drop import DropArtifactIngest
from nora.testing import FakeArtifactIngest

TOKEN = "contract-drop-token"  # noqa: S105


class _WorkingDrop:
    """A minimal double for `drop`'s internal `POST /api/artifacts` and
    `PATCH /api/artifacts/{artifact_id}`: bearer-checked, mints a fresh id/url
    per `register` call but — matching the real server
    (`deploy/drop/artifacts.py`'s `PgRustfsArtifactStore.update`, and
    `deploy/drop/tests/test_artifacts_route.py`'s own assertion that update
    never re-mints) — `update` returns the SAME url `register` already
    minted for that `artifact_id`, never a freshly-built one. Minting a
    different url on update would be exactly the kind of double that "answers
    every request the same way [as if the underlying property didn't matter]"
    CLAUDE.md warns against: it would let this suite pass while silently
    disagreeing with the server it stands in for."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._urls: dict[str, str] = {}

    async def register(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "forbidden"}, status=403)
        await request.multipart()  # drain the body like a real handler would
        n = next(self._ids)
        artifact_id = f"art_c{n:06d}"
        url = f"https://drop.nora.example.net/d/c{n}"
        self._urls[artifact_id] = url
        return web.json_response({"artifact_id": artifact_id, "url": url})

    async def update(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "forbidden"}, status=403)
        artifact_id = request.match_info["artifact_id"]
        await request.multipart()
        url = self._urls.get(artifact_id)
        if url is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"artifact_id": artifact_id, "url": url})


async def _refusing_drop(request: web.Request) -> web.Response:
    return web.json_response({"error": "forbidden"}, status=403)


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


@pytest.fixture(params=["drop", "fake"])
async def failing_ingest(request):
    """An `ArtifactIngest` of each kind whose backend is unreachable/refuses."""
    if request.param == "fake":
        yield FakeArtifactIngest(fail=True)
        return
    app = web.Application()
    app.router.add_post("/api/artifacts", _refusing_drop)
    app.router.add_patch("/api/artifacts/{artifact_id}", _refusing_drop)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    try:
        yield DropArtifactIngest(base_url=base, token=TOKEN, timeout_seconds=5.0)
    finally:
        await server.close()


def test_register_is_keyword_only_after_self():
    """Same convention `tests/test_ports.py` pins for every port method."""
    params = inspect.signature(DropArtifactIngest.register).parameters
    for name in ("data", "title", "media_type", "expires_at"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    "impl", [lambda: DropArtifactIngest(base_url="http://x", token="t"),  # noqa: S106
             FakeArtifactIngest],
)
def test_both_implementations_are_artifact_ingests(impl):
    assert isinstance(impl(), ArtifactIngest)


async def test_a_successful_register_returns_a_populated_registration(ingest):
    reg = await ingest.register(data=b"hello", title="A Title", media_type="text/plain")
    assert isinstance(reg, ArtifactRegistration)
    assert reg.artifact_id
    assert reg.url


async def test_two_registrations_never_collide(ingest):
    first = await ingest.register(data=b"one", title="One", media_type="text/plain")
    second = await ingest.register(data=b"two", title="Two", media_type="text/plain")
    assert (first.artifact_id, first.url) != (second.artifact_id, second.url)


async def test_a_backend_failure_raises_rather_than_degrading(failing_ingest):
    """The load-bearing inversion from `LinkPreviewSource`'s contract: THIS
    port's failure mode is a raise, never a sentinel — a caller must not be
    able to mistake a failed write for a successful one with an empty id."""
    with pytest.raises(ConnectionError):
        await failing_ingest.register(data=b"x", title="X", media_type="text/plain")


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
    # Same slug, no re-mint — the whole point of `update` over registering a
    # second artifact (`ArtifactIngest.update`'s own docstring, and this is
    # the property `_WorkingDrop`'s register/update previously disagreed on).
    assert updated.url == created.url


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
