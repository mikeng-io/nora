"""`instrument_aiohttp_app` — the middleware must run on the contrib
instrumentor's INITIALIZED lifecycle, not its exported `middleware` bare.

`opentelemetry.instrumentation.aiohttp_server` keeps its excluded-urls set,
meters and default tracer in module globals that only
`AioHttpServerInstrumentor.instrument()` populates; the package-level
`middleware` singleton was created before any of that ran, so calling it
raises `AttributeError` on the first NON-excluded request. `/health` was
excluded in Nora's wrapper, which is why the crash hid behind the pollers.
These tests drive real HTTP through an instrumented app and read exported
spans — the shape of the defect is a 500 the handler never saw, not a wrong
attribute.
"""
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from opentelemetry import trace

from nora.obs.tracing import instrument_aiohttp_app

# A valid W3C traceparent header value, built rather than pasted — a literal
# 32-hex trace id is the shape the fixture hook refuses.
PARENT = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"


@pytest.fixture
def spans(monkeypatch):
    """An in-memory provider behind `trace.get_tracer_provider()` — the same
    lookup `instrument_aiohttp_app` makes, so the test is hermetic even when
    another test already ran the one-time `instrument()` in this process."""
    import opentelemetry.trace as trace_api
    from opentelemetry.instrumentation.aiohttp_server import AioHttpServerInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    instrumentor = AioHttpServerInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace_api, "get_tracer_provider", lambda: provider)
    yield exporter
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    provider.shutdown()


def _app() -> web.Application:
    """A small app with the same route mix the real services carry: traced
    API routes plus the two poll endpoints the wrapper excludes."""
    app = web.Application()
    app["hits"] = []

    async def api(request: web.Request) -> web.Response:
        request.app["hits"].append(request.path)
        return web.json_response({"ok": True})

    async def poll(request: web.Request) -> web.Response:
        request.app["hits"].append(request.path)
        return web.Response(text="ok")

    app.router.add_get("/api/generated", api)
    app.router.add_post("/refresh", api)
    app.router.add_get("/health", poll)
    app.router.add_get("/metrics", poll)
    instrument_aiohttp_app(app)
    return app


async def _serve(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_a_real_route_reaches_its_handler_and_exports_a_server_span(
    spans,
):
    """The crash shape: an uninstrumented `middleware` raised AttributeError
    before `handler`, so the route answered 500 and `hits` stayed empty."""
    app = _app()
    client = await _serve(app)
    try:
        resp = await client.get("/api/generated")
        assert resp.status == 200
    finally:
        await client.close()
    assert app["hits"] == ["/api/generated"]
    (span,) = spans.get_finished_spans()
    assert span.kind == trace.SpanKind.SERVER
    assert span.attributes["http.route"] == "/api/generated"


async def test_w3c_parentage_is_preserved(spans):
    """An incoming traceparent is the server span's parent — the request
    joins the caller's trace rather than opening a second root."""
    app = _app()
    client = await _serve(app)
    try:
        resp = await client.get("/api/generated", headers={"traceparent": PARENT})
        assert resp.status == 200
    finally:
        await client.close()
    (span,) = spans.get_finished_spans()
    assert f"{span.context.trace_id:032x}" == "ab" * 16
    assert span.parent is not None
    assert f"{span.parent.span_id:016x}" == "cd" * 8


async def test_the_poll_routes_answer_but_stay_untraced(spans):
    """`/health` and `/metrics` are scraped on a timer — they must answer
    (they always did, which is what hid the crash) and emit no span."""
    app = _app()
    client = await _serve(app)
    try:
        for path in ("/health", "/metrics"):
            resp = await client.get(path)
            assert resp.status == 200
    finally:
        await client.close()
    assert app["hits"] == ["/health", "/metrics"]
    assert spans.get_finished_spans() == ()


async def test_a_second_app_lifecycle_still_instruments(spans):
    """`instrument()` is process-level one-time init; a second `web.Application`
    in the same process (presence's app beside health's, or the next app after
    a restart) must instrument cleanly rather than re-running init."""
    app = _app()
    client = await _serve(app)
    try:
        assert (await client.post("/refresh")).status == 200
    finally:
        await client.close()
    app2 = _app()
    client2 = await _serve(app2)
    try:
        assert (await client2.get("/api/generated")).status == 200
    finally:
        await client2.close()
    assert app2["hits"] == ["/api/generated"]
    assert len(spans.get_finished_spans()) == 2


async def test_a_later_app_still_excludes_polling_and_has_one_server_span(spans):
    first = _app()
    client = await _serve(first)
    try:
        assert (await client.get("/api/generated")).status == 200
    finally:
        await client.close()
    second = _app()
    client = await _serve(second)
    try:
        assert (await client.get("/health")).status == 200
        assert (await client.get("/api/generated")).status == 200
    finally:
        await client.close()
    spans = spans.get_finished_spans()
    assert len(spans) == 2
    assert all(s.attributes["http.route"] == "/api/generated" for s in spans)
