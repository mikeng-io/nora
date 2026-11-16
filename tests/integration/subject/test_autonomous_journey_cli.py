"""The official autonomous-journey CLI rehearsal through the declared gateway.

One generated case, one real child process: ``sys.executable -m evals
autonomous_journey`` bound to the pinned private cohort, a services manifest
declaring only a loopback finite model gateway, the frozen grading contract
and explicit run bounds. The same finite ``CausalProtocol``/``GeneratedJudge``
the generated world serves case-owned here answers through the declared
gateway's real HTTP upstream, so ``--measure behavioral`` reaches the same
accounting branch a paid run would — still a fixture, so product quality is
UNMEASURED and generated PASS labels are not behavioral acceptance of Nora.

Cold checks bind every declared input — cohort, services, contract, budget,
measurement, generated config — without composing the App or dispatching a
model. The whole-path test runs the child once and reads the retained
events, receipts and reports back; a refusal is a finding, not a retry.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import tomllib
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import tomli_w

REPO_ROOT = Path(__file__).resolve().parents[3]

_PRIVATE_ROOT = Path(
    "/Users/mike/.local/share/nora/eval-private/cli-controls/"
    "generated-later-observation-v1-20261116"
)
_DEPLOYMENT_SRC = Path(
    "/Users/mike/.local/share/nora/eval-private/baseline-preflight/"
    "20261116/deployment-config-inspection.toml"
)
_DEPLOYMENT_SRC_SHA256 = "c3483c9d73d3311ccd42d7a7cca11fe5a6374a17d1934ade82ee7908f5699dc3"
_COHORT = _PRIVATE_ROOT / "cohort.json"
_COHORT_SHA256 = "0e0e30104ab796bff3f0ee57438f96a2c50c7211c3f2a53d687d6f76b03fde1f"
_EARLIER_SERVICES = _PRIVATE_ROOT / "services.json"

_CORPUS = "autonomous_journey"
_CASE = "generated-later-recall-restraint-v1"
_JUDGES = ["generated-a", "generated-b"]
_MODELS = ["nora", "nora-dreamer", *_JUDGES]
_RUN_BUDGET = "60,,40000,30"
_RUN_INPUT_BYTES = 4_000_000
_OTLP_PORT = 14318
_OTLP_ENDPOINT = f"http://127.0.0.1:{_OTLP_PORT}/v1/traces"
_CHILD_DEADLINE_S = 600

#: The declared world binds signal, honcho, redis and the model upstream
#: only; these integrations refuse composition without deployment
#: credentials, run daemons/pollers, or dispatch outside the gateway's
#: declared models — effects the declared world cannot bind, so the case's
#: services plan declares them off.
_WORLD_OVERRIDES = {
    "computer.enabled": False,
    "otp.enabled": False,
    "platforms.discord.enabled": False,
    "memory.index.embed": False,
    "memory.graph.enabled": False,
    "social.enabled": False,
    "social.poll.enabled": False,
    "social.instagram.enabled": False,
    "social.instagram.summons.enabled": False,
    "social.linkedin.enabled": False,
    "presence.enabled": False,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _private_run_dir() -> Path:
    root = _PRIVATE_ROOT / "runs" / uuid.uuid4().hex[:12]
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    return root


def _stage_deployment(target: Path, *, upstream: str) -> Path:
    """The stated deployment with declared endpoints bound to owned fixtures.

    Functional values and model aliases stay verbatim; only the model
    upstream and the eval's own OTLP visibility sink bind to the loopback
    listeners this test owns.
    """
    data = tomllib.loads(_DEPLOYMENT_SRC.read_text(encoding="utf-8"))
    data["providers"]["primary"]["base_url"] = upstream
    tracing = data.setdefault("obs", {}).setdefault("tracing", {})
    tracing["enabled"] = True
    tracing["exporter"] = "otlp_http"
    tracing["endpoint"] = _OTLP_ENDPOINT
    target.write_bytes(tomli_w.dumps(data).encode())
    return target


def _world_policy() -> str:
    earlier = json.loads(_EARLIER_SERVICES.read_text(encoding="utf-8"))
    return earlier["corpora"][_CORPUS][_CASE]["policy"]


def _write_services(path: Path, *, upstream: str) -> Path:
    """The earlier generated-world input plus the declared gateway plan."""
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "corpora": {
                    _CORPUS: {
                        _CASE: {
                            "kind": "journey_world",
                            "policy": _world_policy(),
                            "room": ["signal", "g1"],
                            "judges": list(_JUDGES),
                            "overrides": dict(_WORLD_OVERRIDES),
                            "model_gateway": {
                                "upstream": upstream,
                                "models": list(_MODELS),
                            },
                        }
                    }
                },
            },
            indent=1,
        )
    )
    return path


def _write_contract(path_dir: Path, *, cohort_sha: str) -> tuple[Path, str]:
    """The frozen grading reference for exactly this selection, via the
    shared writer every CLI contract test uses."""
    from evals.grading_contract import case_scenario, digest
    from evals.journey_cohort import load_cohort
    from tests.integration.test_grading_contract import write_contract

    cohort = load_cohort(_COHORT, corpus=_CORPUS)
    cases = {
        f"{_CORPUS}:{case.name}": {
            "input_sha256": digest(case_scenario(case)),
            "expected_output": "",
            "units": [],
        }
        for case in cohort.cases
    }
    selection = {
        "corpora": [_CORPUS],
        "only": [_CASE],
        "sample": 1,
        "judge_samples": 3,
        "journey_cohort": cohort_sha,
        "measurement": "behavioral-evaluation",
    }
    return write_contract(
        path_dir,
        entrypoint="eval",
        judges=tuple(_JUDGES),
        cases=cases,
        selection=selection,
    )


def _protocol_for(deployment_toml: Path, workdir: Path) -> Any:
    """`CausalProtocol` bound to the generated config of the staged deployment —
    the same `cfg` the child's world computes for the same declared inputs."""
    from evals.subject_config import build
    from nora.subject.obtain import SubjectSpec, build_config
    from nora.testing.causal_protocol import CausalProtocol

    generated = build(
        (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"),
        deployment_toml.read_text(encoding="utf-8"),
    )
    generated_path = workdir / "config.evaluation.toml"
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_text(generated, encoding="utf-8")
    cfg = build_config(
        SubjectSpec(
            database_url="sqlite://",
            workdir=workdir / "upstream-cfg",
            policy=_world_policy(),
            overrides={**_WORLD_OVERRIDES, "obs.metrics.port": 0},
            config=generated_path,
        )
    )
    return CausalProtocol(cfg)


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        merged = {"content-length": str(len(body)), **headers}
        for name, value in merged.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class _UpstreamHandler(_QuietHandler):
    """One POST route — the finite protocol/judges behind the declared gateway."""

    def do_POST(self) -> None:
        server: _ModelUpstream = self.server  # type: ignore[assignment]
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        if self.path != "/v1/chat/completions":
            self._send(404, b'{"error":"route"}', {})
            return
        try:
            body = json.loads(raw)
        except ValueError:
            server.errors.append(("malformed_request", "json"))
            self._send(400, b'{"error":"malformed journey request"}', {})
            return
        server.requests.append(body)
        model = body.get("model")
        if model in server.judge_models:
            try:
                answer = server.judge.answer(body)
            except (AssertionError, KeyError, TypeError, ValueError) as exc:
                server.errors.append((model, type(exc).__name__, str(exc)))
                self._send(422, b'{"error":"unmatched generated judge request"}', {})
                return
            payload = {
                "id": "generated-judge",
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(answer),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            }
            self._send(
                200,
                json.dumps(payload).encode(),
                {
                    "content-type": "application/json",
                    "x-omniroute-model": f"{model}-served",
                    "x-omniroute-provider": f"generated-{model}",
                },
            )
            return
        status, response = server.protocol.dispatch(body)
        if status != 200:
            self._send(
                status,
                json.dumps(response).encode(),
                {"content-type": "application/json"},
            )
            return
        headers = dict(response["headers"])
        if "json" in response:
            self._send(
                200,
                json.dumps(response["json"]).encode(),
                {**headers, "content-type": "application/json"},
            )
            return
        body_out = (
            b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in response["chunks"])
            + b"data: [DONE]\n\n"
        )
        self._send(200, body_out, headers)


class _ModelUpstream(ThreadingHTTPServer):
    """The declared gateway's real loopback upstream: the same finite
    responders the generated world serves case-owned, behind a real socket
    the gateway admits. Every received body lands in `requests` — the
    fixture request census the rehearsal receipt reconciles."""

    daemon_threads = True

    def __init__(self, *, protocol: Any, judge_models: tuple[str, ...]) -> None:
        from nora.subject.journey_world import GeneratedJudge

        self.protocol = protocol
        self.judge = GeneratedJudge()
        self.judge_models = frozenset(judge_models)
        self.requests: list[dict[str, Any]] = []
        self.errors: list[tuple] = []
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)
        self.url = f"http://127.0.0.1:{self.server_address[1]}/v1"


class _OtlpHandler(_QuietHandler):
    def do_POST(self) -> None:
        server: _OtlpSink = self.server  # type: ignore[assignment]
        length = int(self.headers.get("content-length") or 0)
        server.payloads.append(self.rfile.read(length))
        self._send(200, b"{}", {"content-type": "application/json"})


class _OtlpSink(ThreadingHTTPServer):
    """The owner-local OTLP/HTTP endpoint `obs.tracing.endpoint` authorizes
    (`127.0.0.1:14318` is one of telemetry's declared private shapes). It is
    real so the export preflight and span exports are measured, not stubbed."""

    daemon_threads = True

    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        super().__init__(("127.0.0.1", _OTLP_PORT), _OtlpHandler)


@pytest.fixture
def rehearsal(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The staged private inputs, OTLP sink and finite model upstream —
    everything the child CLI binds, owned by this test and retained under
    the cohort's private root."""
    run_dir = _private_run_dir()
    workdir = tmp_path_factory.mktemp("journey-cli")

    sink = _OtlpSink()
    threading.Thread(target=sink.serve_forever, daemon=True).start()

    # The upstream must exist before its bound port is staged into the
    # deployment and declared to the gateway.
    probe = _ModelUpstream(protocol=None, judge_models=tuple(_JUDGES))  # type: ignore[arg-type]
    upstream_url = probe.url
    probe.protocol = _protocol_for(
        _stage_deployment(workdir / "deployment.toml", upstream=upstream_url),
        workdir / "proto-cfg",
    )
    threading.Thread(target=probe.serve_forever, daemon=True).start()

    deployment = run_dir / "deployment.toml"
    deployment.write_bytes(
        _stage_deployment(workdir / "deployment-staged.toml", upstream=upstream_url).read_bytes()
    )
    services = _write_services(run_dir / "services.json", upstream=upstream_url)
    contract_dir = run_dir / "contract"
    contract_dir.mkdir()
    contract_path, contract_sha = _write_contract(contract_dir, cohort_sha=_COHORT_SHA256)
    evidence_root = run_dir / "evidence-root"
    evidence_root.mkdir(mode=0o700)

    ctx = {
        "run_dir": run_dir,
        "workdir": workdir,
        "deployment": deployment,
        "services": services,
        "contract": contract_path,
        "contract_sha": contract_sha,
        "upstream": probe,
        "sink": sink,
        "evidence_root": evidence_root,
        "upstream_url": upstream_url,
    }
    yield ctx
    sink.shutdown()
    probe.shutdown()


def _child_env(ctx: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    env["NORA_EVAL_EXCHANGES"] = str(ctx["evidence_root"])
    env["NORA_EVAL_SOURCE_URL"] = ""
    env.pop("NORA_CONFIG", None)
    return env


def _cli_args(ctx: dict[str, Any], *, source_url: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "evals",
        _CORPUS,
        "--journey-cohort",
        str(_COHORT),
        "--services",
        str(ctx["services"]),
        "--case",
        _CASE,
        "--measure",
        "behavioral",
        "--deployment-config",
        str(ctx["deployment"]),
        "--source-url",
        source_url,
        "--run-budget",
        _RUN_BUDGET,
        "--run-input-bytes",
        str(_RUN_INPUT_BYTES),
        "--grading-contract",
        str(ctx["contract"]),
        "--grading-contract-sha256",
        ctx["contract_sha"],
    ]


def _events(evidence_root: Path) -> tuple[Path, list[dict[str, Any]]]:
    from nora.subject.eval_evidence import read_events

    runs = sorted(evidence_root.glob("*-*/events.jsonl"))
    assert runs, f"no retained run under {evidence_root}"
    path = runs[-1].parent
    return path, read_events(path)


def _kinds(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("kind")) for e in events]


def test_autonomous_journey_cli_inputs_bind() -> None:
    """Every pinned input binds through the same production loaders the
    child CLI calls — without a subprocess, an App, or a model dispatch."""
    from evals.journey_cohort import bound_journey_services, load_cohort
    from evals.measurement import for_measure
    from evals.services import load_services
    from evals.subject_config import build, divergences
    from nora.subject.journey_world import JourneyServices

    assert _COHORT.is_file() and _EARLIER_SERVICES.is_file()
    assert _DEPLOYMENT_SRC.is_file()
    assert _sha256(_COHORT) == _COHORT_SHA256
    assert _sha256(_DEPLOYMENT_SRC) == _DEPLOYMENT_SRC_SHA256

    cohort = load_cohort(_COHORT, corpus=_CORPUS)
    assert cohort.sha256 == _COHORT_SHA256
    assert cohort.judges == tuple(_JUDGES)
    assert [c.name for c in cohort.cases] == [_CASE]
    plan = cohort.cases[0].metadata["journey"]
    assert plan["version"] == 2
    assert plan["expected_wakes"] == 2
    assert [e["kind"] for e in plan["expectations"]] == ["behavior", "behavior"]

    measurement = for_measure("behavioral")
    assert measurement.behavioral

    earlier = load_services(_EARLIER_SERVICES)
    earlier_plan = earlier.corpora[_CORPUS][_CASE]
    assert isinstance(earlier_plan, JourneyServices)
    assert earlier_plan.model_gateway is None
    with pytest.raises(ValueError, match="declares no model gateway"):
        bound_journey_services(
            _CORPUS,
            cohort.cases,
            {_CASE: earlier_plan},
            judges=cohort.judges,
            measurement=measurement,
        )

    generated = build(
        (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"),
        _DEPLOYMENT_SRC.read_text(encoding="utf-8"),
    )
    assert divergences(generated, _DEPLOYMENT_SRC.read_text(encoding="utf-8")) == {}


def test_autonomous_journey_cli_bindings_bind(
    rehearsal: dict[str, Any],
) -> None:
    """The staged inputs — gateway services plan, staged deployment,
    contract — bind through the same production loaders, still without the
    App or a model dispatch."""
    from evals.grading_contract import load_contract
    from evals.journey_cohort import bound_journey_services, load_cohort
    from evals.measurement import for_measure
    from evals.services import load_services
    from evals.subject_config import build, divergences
    from nora.subject.eval_evidence import evaluation_export_for

    ctx = rehearsal
    cohort = load_cohort(_COHORT, corpus=_CORPUS)
    services = load_services(ctx["services"])
    plan = services.corpora[_CORPUS][_CASE]
    assert plan.model_gateway is not None
    assert set(cohort.judges) <= set(plan.model_gateway.models)
    bound_journey_services(
        _CORPUS,
        cohort.cases,
        {_CASE: plan},
        judges=cohort.judges,
        measurement=for_measure("behavioral"),
    )

    deployment_text = ctx["deployment"].read_text(encoding="utf-8")
    generated = build(
        (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"),
        deployment_text,
    )
    assert divergences(generated, deployment_text) == {}
    assert evaluation_export_for(Path(ctx["deployment"])) is not None

    contract = load_contract(
        ctx["contract"],
        ctx["contract_sha"],
        entrypoint="eval",
        judges=tuple(_JUDGES),
        selection={
            "corpora": [_CORPUS],
            "only": [_CASE],
            "sample": 1,
            "judge_samples": 3,
            "journey_cohort": _COHORT_SHA256,
            "measurement": "behavioral-evaluation",
        },
    )
    contract.verify_cases({f"{_CORPUS}:{_CASE}": cohort.cases[0]})


def test_autonomous_journey_cli_launcher_refuses_at_source(
    rehearsal: dict[str, Any],
) -> None:
    """The real child CLI binds every declared input — cohort, services,
    contract, budget, measurement, export — and refuses where it is aimed:
    a `--source-url` naming a database that is not a declared clone. Nothing
    composes and no model is dispatched."""
    ctx = rehearsal
    proc = subprocess.run(  # noqa: S603 — fixed argv, generated inputs only
        _cli_args(
            ctx,
            source_url="postgresql+asyncpg://u:p@127.0.0.1:9/notaclone",
        ),
        cwd=REPO_ROOT,
        env=_child_env(ctx),
        capture_output=True,
        text=True,
        timeout=120,
    )
    (ctx["run_dir"] / "cold.stdout.txt").write_text(proc.stdout)
    (ctx["run_dir"] / "cold.stderr.txt").write_text(proc.stderr)
    assert proc.returncode == 2, (proc.stdout[-2000:], proc.stderr[-2000:])
    assert "not a clone" in proc.stderr
    _evidence_dir, events = _events(ctx["evidence_root"])
    kinds = _kinds(events)
    assert "journey_cohort" in kinds
    assert "external_service_inputs" in kinds
    assert "grading_contract" in kinds
    assert "visibility" in kinds
    assert "run_started" in kinds
    finished = [e for e in events if e.get("kind") == "run_finished"]
    assert finished and finished[-1].get("exit_code") == 2
    assert "case_started" not in kinds
    assert ctx["sink"].payloads, "export preflight reached no sink"


def test_autonomous_journey_cli_whole_path(
    rehearsal: dict[str, Any], pg_admin: Any, pg_template: str
) -> None:
    """One real `python -m evals autonomous_journey` run, once: the declared
    cohort, gateway and contract through the SDK, `obtain`, `App.run`,
    ingress, autonomous scheduling, delivery, the correction, the tagged
    follow-up and both judge adapters — then the retained evidence read
    back. Any inherited boundary surfaces as the child's own refusal,
    preserved with its evidence."""
    import psycopg

    ctx = rehearsal
    source_db = "generated_eval"
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        for name in (source_db, f"{source_db}_blank"):
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{source_db}" TEMPLATE "{pg_template}"')
    source_url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{source_db}"
    )

    config_target = REPO_ROOT / "config.evaluation.toml"
    config_backup = config_target.read_bytes() if config_target.is_file() else None
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, generated inputs only
            _cli_args(ctx, source_url=source_url),
            cwd=REPO_ROOT,
            env=_child_env(ctx),
            capture_output=True,
            text=True,
            timeout=_CHILD_DEADLINE_S,
        )
    finally:
        if config_backup is not None:
            config_target.write_bytes(config_backup)
        elif config_target.exists():
            config_target.unlink()
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            for name in (source_db, f"{source_db}_blank"):
                conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (name,),
                )
                conn.execute(f'DROP DATABASE IF EXISTS "{name}"')

    (ctx["run_dir"] / "whole.stdout.txt").write_text(proc.stdout)
    (ctx["run_dir"] / "whole.stderr.txt").write_text(proc.stderr)

    evidence_dir, events = _events(ctx["evidence_root"])
    kinds = _kinds(events)
    receipt: dict[str, Any] = {
        "rehearsal": "autonomous_journey_cli_whole_path",
        "product_quality": "UNMEASURED — a generated fixture run of the "
        "instrument, never behavioral acceptance of Nora",
        "cohort": {"path": str(_COHORT), "sha256": _COHORT_SHA256},
        "services": {"path": str(ctx["services"]), "sha256": _sha256(ctx["services"])},
        "contract": {"path": str(ctx["contract"]), "sha256": ctx["contract_sha"]},
        "deployment_source": {
            "path": str(_DEPLOYMENT_SRC),
            "sha256": _DEPLOYMENT_SRC_SHA256,
            "staged": str(ctx["deployment"]),
        },
        "command": _cli_args(ctx, source_url=source_url),
        "child_returncode": proc.returncode,
        "evidence_dir": str(evidence_dir),
        "event_kinds": kinds,
        "fixture_request_census": {
            "received": len(ctx["upstream"].requests),
            "judge_requests": sum(
                1
                for r in ctx["upstream"].requests
                if r.get("model") in ctx["upstream"].judge_models
            ),
            "subject_requests": sum(
                1
                for r in ctx["upstream"].requests
                if r.get("model") not in ctx["upstream"].judge_models
            ),
            "errors": ctx["upstream"].errors,
        },
        "otlp_payloads_received": len(ctx["sink"].payloads),
        "inference": "none — the only model upstream is the declared "
        "loopback fixture serving CausalProtocol/GeneratedJudge",
        "paid_request": "none — bytes are requests, never tokens; no USD cost is inferable",
    }
    (ctx["run_dir"] / "receipt.json").write_text(json.dumps(receipt, indent=1))

    if proc.returncode != 0:
        terminal = [e for e in events if e.get("kind") in ("run_finished", "run_failed")]
        boundary = [
            e for e in events if "external_dependency_not_isolated" in json.dumps(e, default=str)
        ]
        pytest.fail(
            "whole-path rehearsal unavailable — the child refused at an "
            f"inherited boundary (rc={proc.returncode}).\n"
            f"terminal: {terminal[-1] if terminal else None}\n"
            f"isolation records: {boundary[:3]}\n"
            f"evidence: {evidence_dir}\n"
            f"receipt: {ctx['run_dir'] / 'receipt.json'}\n"
            f"stderr tail: {proc.stderr[-1500:]}"
        )

    # Exit 0 — reconcile the retained evidence against the acceptance.
    finished = [e for e in events if e.get("kind") == "run_finished"]
    assert finished and finished[-1].get("exit_code") == 0
    started = [e for e in events if e.get("kind") == "case_started"]
    assert len(started) == 1 and _CASE in json.dumps(started[0])

    report_dir = evidence_dir / f"journey-{_CASE}"
    report = json.loads((report_dir / "report.json").read_text())
    assert (report_dir / "report.html").is_file()

    produced = report["run"]["wake_census"]["observed"]
    assert len(produced) == 2, produced
    expected = report["expected_cases"][0]
    assert expected["wakes"] == 2
    assert expected["turns"] == 3
    assert [e["kind"] for e in expected["expectations"]] == [
        "behavior",
        "behavior",
    ]
    assert {e["turn"] for e in expected["expectations"]} == {2}
    assert report["run"]["judges"] == _JUDGES
    census = report["run"]["request_census"]
    assert census["status"] == "observed"
    assert report["run"]["judge_requests"] == 4
    judged = [e for e in events if e.get("kind") == "judge_answer"]
    assert len(judged) == 4
    assert {str(j.get("model")) for j in judged} == set(_JUDGES)

    receipt["event_reconciliation"] = {
        "case_started": len(started),
        "produced_wakes": produced,
        "judge_requests": report["run"]["judge_requests"],
        "model_requests": report["run"]["model_requests"],
        "judge_answer_events": len(judged),
        "otlp_payloads": len(ctx["sink"].payloads),
        "fixture_requests": receipt["fixture_request_census"],
        "report_status": report["status"],
    }
    (ctx["run_dir"] / "receipt.json").write_text(json.dumps(receipt, indent=1))
