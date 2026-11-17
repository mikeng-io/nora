"""A `journey_world` plan's declared external recording serves the real
isolated boundary: historical memory/roster responses cross the real
dependency seam, generated responders are never installed to fill missing
history, and the case-owned action executor and daemon keep their real
routes ahead of the recording's catch-all. A missing, mismatched or unused
response fails closed — never a generated-empty fill, never a recorded
model dispatch or a recorded action receipt standing in for candidate
execution.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import aiohttp
import pytest
import sqlalchemy as sa

from evals.services import load_services
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import MESSAGES
from nora.subject.eval_evidence import EvidenceError
from nora.subject.journey_world import JourneyServices
from nora.subject.obtain import build_config, default_policy
from nora.subject.recorded_services import RecordedServices


def _manifest(tmp_path, recording):
    """The parsed `journey_world` plan with its embedded version-1 recording."""
    manifest = {"version": 1, "corpora": {"autonomous_journey": {"the_outing": {
        "kind": "journey_world",
        "policy": default_policy(("signal", "g1"), unit="generated-unit"),
        "judges": ["alpha", "beta"],
        "external": recording}}}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(manifest))
    return load_services(path).corpora["autonomous_journey"]["the_outing"]


RECORDING = {
    "version": 1,
    "bindings": {"honcho.base_url": "memory",
                 "platforms.signal.base_url": "bridge"},
    "services": {
        "memory": [{"method": "GET",
                    "path": "/v3/workspaces/generated-ws/peers/miles_novak/card",
                    "response": {"status": 200, "json": {
                        "peer_card": [{"content": "generated history"}]}}}],
        "bridge": [{"method": "GET", "path": "/v1/self",
                    "response": {"status": 200, "json": {
                        "primary": "+85290000001", "aliases": []}}},
                   {"method": "GET", "path": "/v1/groups",
                    "response": {"status": 200, "json": [
                        {"id": "g1", "members": [["+852111"], ["+852222"]]}]}}],
    },
}


async def _pending_row(store: SqlBridgeStore) -> int:
    """A committed pending outbound audit row — `live_invocation` authority."""
    async with store._engine.begin() as conn:
        rid = (await conn.execute(sa.insert(MESSAGES).values(
            platform="signal", conversation_id="g1",
            conversation_type="group", sender_id="+85290000001", ts=1,
            body="pending body", is_nora=1, delivery_status="pending",
            raw_json=json.dumps({"tag_id": "generated-tag"}))
            .returning(MESSAGES.c.id))).scalar_one()
    return int(rid)


def _wire_actions(plan, env, database_url: str):
    """The real case-owned executor: a production store beside the case DB."""
    engine = create_sql_engine(database_url)
    store = SqlBridgeStore(engine)
    runtime = SimpleNamespace(
        store=store, readers=[SimpleNamespace(_account_key="default")])
    plan.wire_actions(runtime, env)
    return engine, store


def _send(action_id: str, rid: int, text: str = "generated reply") -> dict:
    return {"action_id": action_id, "op": "send",
            "owner": {"kind": "live_invocation", "id": str(rid)},
            "request": {"target": {"id": "g1", "kind": "group"}, "text": text}}


async def test_the_declared_recording_serves_history_and_the_real_action_seam(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """The manifest parses, the isolated boundary opens, and the configured
    dependency receives its declared nonempty memory/roster responses while
    candidate actions still cross the real `DurableActionService` and daemon."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    plan = _manifest(tmp_path, RECORDING)
    async with plan.open(clean_pg_url) as env:
        assert env.guarded
        bound = env.bind_config(build_config(plan.subject_spec(
            clean_pg_url, env, workdir=tmp_path / "subject")))
        engine, store = _wire_actions(plan, env, clean_pg_url)
        try:
            rid = await _pending_row(store)
            async with aiohttp.ClientSession() as client:
                async with client.get(
                        bound.honcho.base_url
                        + "/v3/workspaces/generated-ws/peers/miles_novak/card") as r:
                    assert r.status == 200
                    assert (await r.json())["peer_card"] == [
                        {"content": "generated history"}]
                async with client.get(
                        bound.platforms.signal.base_url + "/v1/self") as r:
                    assert r.status == 200
                    assert (await r.json())["primary"] == "+85290000001"
                async with client.get(
                        bound.platforms.signal.base_url + "/v1/groups") as r:
                    assert r.status == 200
                    assert (await r.json()) == [
                        {"id": "g1", "members": [["+852111"], ["+852222"]]}]
                async with client.post(
                        bound.platforms.signal.base_url + "/v1/actions",
                        json=_send("generated-action-1", rid)) as r:
                    reply = await r.json()
        finally:
            await engine.dispose()
        assert reply["action_id"] == "generated-action-1"
        assert reply["state"] == "confirmed"
        assert plan.daemon is not None and plan.daemon.rpc_calls == ["send"]
        env.check()


async def test_a_missing_response_fails_closed_never_a_generated_fill(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """With the signal binding unbound the generated identity/roster reads
    are gone: the writes-only bridge answers 404 and the denial is a named
    error, even though the caller caught the HTTP failure."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    recording = {"version": 1, "bindings": {"honcho.base_url": "memory"},
                 "services": {"memory": RECORDING["services"]["memory"]}}
    plan = _manifest(tmp_path, recording)
    with pytest.raises(EvidenceError, match="external_response_not_recorded"):
        async with plan.open(clean_pg_url) as env:
            signal = env.urls.get("platforms.signal.base_url")
            assert signal is not None, "the candidate action boundary is missing"
            engine, store = _wire_actions(plan, env, clean_pg_url)
            try:
                rid = await _pending_row(store)
                async with aiohttp.ClientSession() as client:
                    async with client.get(
                            env.urls["honcho.base_url"]
                            + "/v3/workspaces/generated-ws/peers/miles_novak/card") as r:
                        assert r.status == 200
                    async with client.post(
                            signal + "/v1/actions",
                            json=_send("generated-action-3", rid)) as r:
                        reply = await r.json()
                        assert reply["state"] == "confirmed"
                    assert plan.daemon is not None and plan.daemon.rpc_calls == ["send"]
                    async with client.get(signal + "/v1/self") as r:
                        assert r.status == 404
                        assert "generated-self" not in await r.text()
                env.check()
            finally:
                await engine.dispose()


async def test_a_mismatched_request_is_a_named_error_after_the_caught_500(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A request the recording did not declare answers 500 and stays denied —
    consuming the declared row still fails closed at the boundary check."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    recording = {"version": 1, "bindings": {"platforms.signal.base_url": "bridge"},
                 "services": {"bridge": [RECORDING["services"]["bridge"][0]]}}
    plan = _manifest(tmp_path, recording)
    with pytest.raises(EvidenceError, match="external_response_not_recorded"):
        async with plan.open(clean_pg_url) as env:
            async with aiohttp.ClientSession() as client:
                signal = env.urls["platforms.signal.base_url"]
                async with client.get(signal + "/v1/groups") as r:
                    assert r.status == 500
                async with client.get(signal + "/v1/self") as r:
                    assert r.status == 200
                    assert (await r.json())["primary"] == "+85290000001"
            env.check()


async def test_a_recorded_action_receipt_cannot_stand_in_for_execution(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A row declaring `POST /v1/actions` is unreachable: the real route is
    claimed ahead of the recording, the real executor answers, and the
    unused recorded receipt is a named error at close."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    recording = {"version": 1,
                 "bindings": {"platforms.signal.base_url": "bridge"},
                 "services": {"bridge": [
                     {"method": "POST", "path": "/v1/actions",
                      "response": {"status": 200, "json": {
                          "action_id": "forged", "state": "confirmed"}}}]}}
    plan = _manifest(tmp_path, recording)
    with pytest.raises(EvidenceError, match="external_response_not_consumed"):
        async with plan.open(clean_pg_url) as env:
            engine, store = _wire_actions(plan, env, clean_pg_url)
            try:
                rid = await _pending_row(store)
                async with aiohttp.ClientSession() as client, client.post(
                        env.urls["platforms.signal.base_url"] + "/v1/actions",
                        json=_send("generated-action-2", rid)) as r:
                    reply = await r.json()
            finally:
                await engine.dispose()
            # The production executor answered — the recorded receipt did not.
            assert reply["action_id"] == "generated-action-2"
            assert reply["state"] == "confirmed"
            assert plan.daemon is not None and plan.daemon.rpc_calls == ["send"]


async def test_each_open_gets_independent_consumption_state(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A second open replays the same declared responses fresh — positions
    reset per opening, so an unconsumed remainder is still a named error."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    recording = {"version": 1,
                 "bindings": {"platforms.signal.base_url": "bridge"},
                 "services": {"bridge": RECORDING["services"]["bridge"]}}
    plan = _manifest(tmp_path, recording)
    async with plan.open(clean_pg_url) as env:
        async with aiohttp.ClientSession() as client:
            signal = env.urls["platforms.signal.base_url"]
            for path in ("/v1/self", "/v1/groups"):
                async with client.get(signal + path) as r:
                    assert r.status == 200
        env.check()
    with pytest.raises(EvidenceError, match="external_response_not_consumed"):
        async with plan.open(clean_pg_url) as env:
            async with aiohttp.ClientSession() as client:
                async with client.get(
                        env.urls["platforms.signal.base_url"] + "/v1/self") as r:
                    assert r.status == 200


def test_a_recording_cannot_claim_the_model_boundary():
    """Constructed directly or via the manifest, model dispatch bindings are
    refused: subject and judge aliases never become recorded playback."""
    for binding in ("llm.base_url", "providers.judge.base_url"):
        recording = RecordedServices({"version": 1,
                                      "bindings": {binding: "model"},
                                      "services": {"model": []}})
        with pytest.raises(ValueError, match="model dispatch"):
            JourneyServices(policy="[rooms]\nallow = []", external=recording)


async def test_explicit_generated_bindings_survive_additional_recorded_services(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    manifest = {
        "version": 1,
        "corpora": {
            "autonomous_journey": {
                "outing": {
                    "kind": "journey_world",
                    "policy": default_policy(("signal", "g1")),
                    "judges": ["alpha", "beta"],
                    "generated_bindings": ["honcho.base_url", "platforms.signal.base_url"],
                    "external": {
                        "version": 1,
                        "bindings": {"platforms.discord.base_url": "discord"},
                        "services": {
                            "discord": [
                                {
                                    "method": "GET",
                                    "path": "/v1/self",
                                    "response": {
                                        "status": 200,
                                        "json": {"primary": "generated-discord", "aliases": []},
                                    },
                                }
                            ]
                        },
                    },
                }
            }
        },
    }
    path = tmp_path / "services.json"
    path.write_text(json.dumps(manifest))
    plan = load_services(path).corpora["autonomous_journey"]["outing"]
    with pytest.raises(EvidenceError, match="external_response_not_recorded"):
        async with plan.open(clean_pg_url) as env:
            assert "honcho.base_url" in env.urls
            async with aiohttp.ClientSession() as client:
                async with client.post(
                    env.urls["honcho.base_url"] + "/v3/workspaces",
                    json={"id": "generated-workspace"},
                ) as response:
                    assert (await response.json())["id"] == "generated-workspace"
                async with client.get(
                    env.urls["platforms.signal.base_url"] + "/v1/self"
                ) as response:
                    assert (await response.json())["primary"] == "generated-self"
                async with client.get(
                    env.urls["platforms.discord.base_url"] + "/v1/self"
                ) as response:
                    assert (await response.json())["primary"] == "generated-discord"
                async with client.get(
                    env.urls["platforms.discord.base_url"] + "/undeclared"
                ) as response:
                    assert response.status == 500
            with pytest.raises(EvidenceError, match="external_response_not_recorded"):
                env.check()


@pytest.mark.parametrize("bindings", [
    ("llm.base_url",), ("honcho.base_url", "honcho.base_url"),
    ("platforms.discord.base_url",), ("platforms.signal.base_url",),
])
def test_generated_bindings_cannot_claim_models_unknown_services_or_recordings(bindings):
    recording = RecordedServices({
        "version": 1, "bindings": {"platforms.signal.base_url": "bridge"},
        "services": {"bridge": []}})
    with pytest.raises(ValueError, match="generated bindings"):
        JourneyServices(policy=default_policy(("signal", "g1")),
                        external=recording, generated_bindings=bindings)


async def test_recorded_discovery_resolves_its_owned_token_endpoint(example_config):
    recording = RecordedServices({
        "version": 1,
        "bindings": {"image_gen.xai.issuer_url": "issuer", "llm.base_url": "issuer"},
        "services": {"issuer": [
            {"method": "GET", "path": "/.well-known/openid-configuration",
             "response": {"status": 200,
                          "json": {"token_endpoint": "${service_origin}/oauth2/token"}}},
            {"method": "POST", "path": "/oauth2/token",
             "response": {"status": 200, "json": {"access_token": "generated-access"}}},
        ]},
    })
    async with recording.open("sqlite://") as env:
        cfg = env.bind_config(example_config)
        async with aiohttp.ClientSession() as client:
            async with client.get(cfg.image_gen.xai.issuer_url +
                                  "/.well-known/openid-configuration") as response:
                endpoint = (await response.json())["token_endpoint"]
            assert endpoint == env.urls["image_gen.xai.issuer_url"] + "/oauth2/token"
            async with client.post(endpoint) as response:
                assert (await response.json())["access_token"] == "generated-access"  # noqa: S105


@pytest.mark.parametrize("sink_fails", [False, True])
async def test_journey_spans_export_after_isolation_without_opening_the_sink(
    tmp_path, sink_fails,
):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tempfile import TemporaryFile

    from opentelemetry import trace
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from evals.backbone import _prefill_capture
    from nora.agent.telemetry import evaluation_export
    from nora.subject.environment import IsolatedServices
    from nora.subject.eval_evidence import RunEvidence

    payloads = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/traces"
            payloads.append(ExportTraceServiceRequest.FromString(
                self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(400 if sink_fails and len(payloads) > 1 else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 14318), Collector)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    _prefill_capture()
    provider = trace.get_tracer_provider()
    export = evaluation_export({"enabled": True, "exporter": "otlp_http",
                                "endpoint": "http://127.0.0.1:14318/v1/traces"})
    try:
        with TemporaryFile() as lock:
            evidence = RunEvidence(run_id="generated-isolated-export", metadata={},
                                   lock_fd=lock.fileno(), private_root=tmp_path)
            from contextlib import nullcontext

            with (pytest.raises(EvidenceError, match="export unavailable")
                  if sink_fails else nullcontext(), evidence.capture(provider, export=export)):
                assert len(payloads) == 1, "the preflight must reach the real sink"
                async with IsolatedServices(database_url="sqlite://", applications={}) as env:
                    with provider.get_tracer(__name__).start_as_current_span("journey.stage"):
                        pass
                    assert not env.denied, "export attempted egress during isolated execution"
                    assert len(payloads) == 1, "stage export must wait for guard teardown"
                    assert not env.owns("http://127.0.0.1:14318/v1/traces")
                    env.check()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    spans = [s for p in payloads for r in p.resource_spans
             for scope in r.scope_spans for s in scope.spans]
    root, = [s for s in spans if s.name == "eval.run"]
    stage, = [s for s in spans if s.name == "journey.stage"]
    assert stage.parent_span_id == root.span_id
    assert stage.trace_id == root.trace_id
