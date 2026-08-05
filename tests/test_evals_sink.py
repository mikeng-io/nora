"""Tests for `evals/sink.py` (Phase 7 Task 1, DEC-6).

`LangfuseEvalSink`'s HTTP scenarios below are offline: a local
`aiohttp.test_utils.TestServer` stands in for Langfuse (mirrors
`tests/test_honcho.py`'s pattern for `HonchoClient`) — no live network is
required, and this repo must never send anything to the real Langfuse instance
from a test.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from evals.sink import EvalSink, LangfuseEvalSink, NullEvalSink, resolve_headers_from_env


@pytest.fixture
async def server_factory():
    servers: list[TestServer] = []

    async def _make(handler) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("/")).rstrip("/")

    yield _make

    for server in servers:
        await server.close()


# --- resolve_headers_from_env: the credential-trap decode ------------------


def test_resolve_headers_decodes_url_encoded_basic_auth(monkeypatch):
    """The exact shape `LANGFUSE_OTLP_HEADERS` carries in .env: `key=value`,
    URL-encoded — NOT a valid `Authorization` header value on its own until
    decoded. This is the credential trap the plan calls out."""
    monkeypatch.setenv(
        "LANGFUSE_OTLP_HEADERS", "Authorization=Basic%20cGstbGY6c2stbGY%3D"
    )
    headers = resolve_headers_from_env("LANGFUSE_OTLP_HEADERS")
    # parse_env_headers lowercases header names (OTel spec: HTTP header names are
    # case-insensitive) — aiohttp sends this correctly regardless of case.
    assert headers == {"authorization": "Basic cGstbGY6c2stbGY="}


def test_resolve_headers_is_empty_for_an_unnamed_env_var():
    assert resolve_headers_from_env("") == {}


def test_resolve_headers_is_empty_when_the_named_var_is_unset(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_LANGFUSE_VAR", raising=False)
    assert resolve_headers_from_env("SOME_UNSET_LANGFUSE_VAR") == {}


# --- NullEvalSink ------------------------------------------------------------


async def test_null_eval_sink_upsert_dataset_is_a_genuine_noop():
    assert await NullEvalSink().upsert_dataset(name="nora-trigger") is None


async def test_null_eval_sink_upsert_item_is_a_genuine_noop():
    sink = NullEvalSink()
    assert (
        await sink.upsert_item(
            dataset_name="nora-trigger", item_id="trigger/x", input="hi", expected_output=None
        )
        is None
    )


async def test_null_eval_sink_start_run_returns_the_run_name():
    sink = NullEvalSink()
    assert await sink.start_run(dataset_name="nora-trigger", run_name="run-1") == "run-1"


async def test_null_eval_sink_record_score_is_a_genuine_noop():
    sink = NullEvalSink()
    assert (
        await sink.record_score(
            run_id="run-1", item_id="trigger/x", name="exactly_once_send_reply",
            value=1.0, data_type="BOOLEAN",
        )
        is None
    )


# --- LangfuseEvalSink over a local fake server -------------------------------


async def test_upsert_dataset_posts_to_v2_datasets(server_factory):
    calls: list[tuple[str, dict]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.path, await request.json()))
        return web.json_response({"name": "nora-trigger"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    await sink.upsert_dataset(name="nora-trigger")

    assert calls == [("/api/public/v2/datasets", {"name": "nora-trigger"})]


async def test_upsert_item_posts_id_dataset_name_input_and_expected_output(server_factory):
    calls: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append(await request.json())
        return web.json_response({"id": "trigger/x"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    await sink.upsert_item(
        dataset_name="nora-trigger", item_id="trigger/x", input="hi", expected_output="bye"
    )

    assert calls == [
        {
            "id": "trigger/x",
            "datasetName": "nora-trigger",
            "input": "hi",
            "expectedOutput": "bye",
        }
    ]


async def test_record_score_links_the_item_then_posts_the_score(server_factory):
    calls: list[tuple[str, dict]] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append((request.path, body))
        if request.path == "/api/public/dataset-run-items":
            return web.json_response({"datasetRunId": "run-uuid-1"})
        return web.json_response({"id": "score-1"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )

    assert calls[0][0] == "/api/public/dataset-run-items"
    assert calls[0][1]["runName"] == "gate-run"
    assert calls[0][1]["datasetItemId"] == "trigger/x"
    assert calls[1][0] == "/api/public/scores"
    assert calls[1][1]["datasetRunId"] == "run-uuid-1"
    assert calls[1][1]["name"] == "exactly_once_send_reply"
    assert calls[1][1]["value"] == 1.0
    assert calls[1][1]["dataType"] == "BOOLEAN"


# --- PROBE (Phase 7 fix round): the live 404 this suite failed to catch ------
#
# `LangfuseEvalSink._link_item` used to POST `/api/public/dataset-run-items`
# with only `runName`/`datasetItemId`/`observationId` — no `traceId`. Every
# fake server above accepted that payload unconditionally (an `id`/
# `datasetRunId` echoed straight back), so the whole suite stayed green while
# the real endpoint 404'd on every single call: Langfuse requires `traceId`
# (or `observationId`, but that alone was NOT sufficient live — see
# `LangfuseEvalSink`'s docstring) on `CreateDatasetRunItemRequest`. A full
# `--suite all --export-langfuse` run against live Langfuse produced zero
# dataset runs despite the gate reporting "exported" — the bug this test
# exists to make impossible again: it fails the moment `traceId` (or the
# other required fields) go missing from the request body, without needing a
# live Langfuse instance at all.


async def test_link_item_request_has_trace_id_and_the_other_required_fields(server_factory):
    calls: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        return web.json_response({"datasetRunId": "run-uuid-1"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )

    body = calls[0]
    assert body["runName"] == "gate-run"
    assert body["datasetItemId"] == "trigger/x"
    assert isinstance(body.get("traceId"), str) and body["traceId"], (
        "dataset-run-items request is missing traceId — Langfuse 404s this "
        "endpoint without one, live-verified"
    )


async def test_link_item_trace_id_is_deterministic_across_repeat_calls(server_factory):
    """Same (run, item) -> same `traceId` on every call, so re-running the same
    gate invocation updates one Langfuse row instead of accumulating a fresh
    row per re-run (live-verified: a repeat POST with the same `traceId`
    updates the existing dataset-run-item in place)."""
    trace_ids: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        trace_ids.append(body["traceId"])
        return web.json_response({"datasetRunId": "run-uuid-1"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")

    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )
    # A second sink instance, same run/item — mirrors a fresh gate re-run.
    sink2 = LangfuseEvalSink(host=base_url, headers={})
    run_id2 = await sink2.start_run(dataset_name="nora-trigger", run_name="gate-run")
    await sink2.record_score(
        run_id=run_id2, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )

    assert trace_ids[0] == trace_ids[1]


async def test_record_score_posts_to_legacy_scores_endpoint_not_v2(server_factory):
    """`/api/public/v2/scores` has no POST route live (405, deprecated GET
    only) — the score-creating endpoint is the plain `/api/public/scores`
    (`legacyCreateScoreRequest`), confirmed against the live OpenAPI spec and
    a real POST. This pins the corrected path so it can't regress back."""
    paths: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        paths.append(request.path)
        if request.path == "/api/public/dataset-run-items":
            return web.json_response({"datasetRunId": "run-uuid-1"})
        return web.json_response({"id": "score-1"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")

    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )

    assert "/api/public/scores" in paths
    assert "/api/public/v2/scores" not in paths


async def test_record_score_id_is_deterministic_across_repeat_calls(server_factory):
    """Same (run, item, score name) -> same score `id`, so a re-run updates
    that score's value in place instead of creating a duplicate — live-
    verified: a repeat POST /api/public/scores with the same `id` updates the
    existing score's `value`/`updatedAt`."""
    score_ids: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if request.path == "/api/public/dataset-run-items":
            return web.json_response({"datasetRunId": "run-uuid-1"})
        score_ids.append(body["id"])
        return web.json_response({"id": body["id"]})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")

    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=0.0, data_type="BOOLEAN",
    )

    assert score_ids[0] == score_ids[1]


async def test_boolean_score_value_is_a_json_number_not_a_string(server_factory):
    """Langfuse requires a JSON number for BOOLEAN/NUMERIC scores — the string
    "1.0" is invalid. This pins that `record_score` never stringifies."""
    scores: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if request.path == "/api/public/dataset-run-items":
            return web.json_response({"datasetRunId": "run-uuid-1"})
        scores.append(body)
        return web.json_response({"id": "score-1"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")

    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="a8_silent_failure",
        value=0.0, data_type="BOOLEAN",
    )

    assert isinstance(scores[0]["value"], float)


async def test_categorical_score_value_is_a_string(server_factory):
    scores: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if request.path == "/api/public/dataset-run-items":
            return web.json_response({"datasetRunId": "run-uuid-1"})
        scores.append(body)
        return web.json_response({"id": "score-1"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})
    run_id = await sink.start_run(dataset_name="nora-adversarial", run_name="gate-run")

    await sink.record_score(
        run_id=run_id, item_id="injections/injection_dan", name="gate_verdict",
        value="PASS", data_type="CATEGORICAL",
    )

    assert isinstance(scores[0]["value"], str)


async def test_sends_the_resolved_authorization_header(server_factory):
    seen_headers: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen_headers.update(request.headers)
        return web.json_response({"name": "nora-trigger"})

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={"Authorization": "Basic abc123"})

    await sink.upsert_dataset(name="nora-trigger")

    assert seen_headers.get("Authorization") == "Basic abc123"


async def test_never_raises_when_langfuse_is_unreachable():
    """Never load-bearing: no server at all, all four methods swallow the failure."""
    sink = LangfuseEvalSink(host="http://127.0.0.1:1", headers={}, timeout=0.3)

    await sink.upsert_dataset(name="nora-trigger")
    await sink.upsert_item(dataset_name="nora-trigger", item_id="trigger/x", input="hi")
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )


async def test_never_raises_on_a_server_error(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": "boom"}, status=500)

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    await sink.upsert_dataset(name="nora-trigger")
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="gate-run")
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )


async def test_failed_post_logs_the_response_body(server_factory, caplog):
    """A bare `-> 404` was a five-minute diagnosis away from a hunt: the real
    bug (this fix round) was a missing `traceId`, and Langfuse's error body
    names the offending field. The warning must include that body (truncated)
    so the next field-name drift is diagnosable from the log alone."""
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(
            {"message": "Invalid request data",
             "error": [{"path": ["traceId"], "message": "traceId is required"}]},
            status=404,
        )

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    with caplog.at_level("WARNING", logger="nora.evals.sink"):
        await sink.upsert_dataset(name="nora-trigger")

    assert any("404" in r.message for r in caplog.records)
    assert any("traceId is required" in r.message for r in caplog.records)


async def test_failed_post_truncates_a_huge_response_body(server_factory, caplog):
    """The truncation guard: a runaway/HTML error body must not get logged in
    full (e.g. a misconfigured host returning a whole error page)."""
    huge_body = "x" * 5000

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=huge_body, status=502)

    base_url = await server_factory(handler)
    sink = LangfuseEvalSink(host=base_url, headers={})

    with caplog.at_level("WARNING", logger="nora.evals.sink"):
        await sink.upsert_dataset(name="nora-trigger")

    logged = next(r.message for r in caplog.records if "502" in r.message)
    assert len(logged) < len(huge_body)


def test_both_implementations_satisfy_the_port():
    """Mirrors tests/contract/test_audit_store_contract.py's
    `test_both_implementations_satisfy_the_port` — `isinstance` against a
    `runtime_checkable` Protocol checks method names only, not signatures."""
    assert isinstance(NullEvalSink(), EvalSink)
    assert isinstance(LangfuseEvalSink(host="http://x", headers={}), EvalSink)


# --- PROBE (Task 1): NullEvalSink must be the genuinely exercised default ---
#
# Verbatim from the session: `NullEvalSink.upsert_dataset` was temporarily
# changed to `raise RuntimeError("probe")` instead of `return None`. Running
#   uv run pytest tests/test_evals_sink.py -q
# failed with:
#   RuntimeError: probe
#   at test_null_eval_sink_upsert_dataset_is_a_genuine_noop
#   (and at test_default_sink_construction_actually_exercises_every_method,
#   below, and at the Task 3 gate PROBE in tests/test_evals_gate.py, which
#   constructs its "no export" baseline the same way `evals.run.main` does)
# confirming the default path is genuinely exercised by the suite, not skipped
# over. The change was reverted and the suite re-run green before committing —
# verbatim before/after output is in notes.md.


async def test_default_sink_construction_actually_exercises_every_method():
    """What `evals.run.main` builds when `--export-langfuse` is absent (Task 3
    wires the real construction call; this test exercises the same class Task
    3 wires in, without depending on evals/run.py's contents so Task 1 stands
    alone). If any `NullEvalSink` method ever stops being a genuine no-op, this
    — not just a mock — is what catches it.
    """
    sink: EvalSink = NullEvalSink()
    await sink.upsert_dataset(name="nora-trigger")
    await sink.upsert_item(dataset_name="nora-trigger", item_id="trigger/x", input="hi")
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="probe-run")
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )
