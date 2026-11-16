"""The generated world behind one autonomous-journey case's isolated services.

`JourneyServices` is the `Spec.services` plan an autonomous-journey case
declares: opening it binds every external the composition will reach —
the model (finite request-aware `CausalProtocol` for subject calls, a
payload-inspecting `GeneratedJudge` for the two judge aliases), the
memory service, and the signal bridge's action/daemon routes — to
listeners this process owns, with a Redis for the real bridge queue.
The world is generated rehearsal input, never Nora's control loop.
"""

from __future__ import annotations

import html as html_mod
import importlib.util
import json
import re
import socket
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiohttp import web

from nora.platform.bridge.service import DurableActionService, action_http_status, action_response
from nora.platform.signal.react import SignalReactor
from nora.platform.signal.send import SignalSender
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import current
from nora.subject.obtain import SubjectSpec, build_config
from nora.testing.causal_protocol import CausalProtocol, texts
from nora.testing.signal_daemon import FakeSignalDaemon


def _record_request(protocol: CausalProtocol, body: dict) -> None:
    """`fixture_model_request`: each request the finite world received."""
    if evidence := current():
        evidence.record("fixture_model_request", phase=protocol.phase, body=body)


def finite_boundary(protocol: CausalProtocol) -> web.Application:
    """The served model endpoint of a finite protocol.

    The subject, not the protocol, owns the served boundary: each
    received request is recorded as case evidence before the protocol
    names and answers it.
    """
    application = web.Application()

    async def respond(request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except ValueError:
            pass  # `protocol.respond` names the malformed case itself.
        else:
            _record_request(protocol, body)
        return await protocol.respond(request)

    application.router.add_post("/v1/chat/completions", respond)
    return application


def _wrapper() -> Any:
    """The deployed signal-bridge wrapper, loaded the way the bridge tests load it."""
    if module := sys.modules.get("signal_bridge_wrapper"):
        return module
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "signal_bridge_wrapper", root / "deploy" / "signal-bridge" / "wrapper.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("deploy/signal-bridge/wrapper.py unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules["signal_bridge_wrapper"] = module
    spec.loader.exec_module(module)
    return module


def memory_service(feeds: list) -> web.Application:
    application = web.Application()

    async def create(request):
        body = await request.json()
        if not isinstance(body.get("id"), str) or not body["id"]:
            return web.json_response({"error": "missing identity"}, status=422)
        return web.json_response(body)

    async def configure(request):
        body = await request.json()
        if not body or any(not isinstance(value, dict) for value in body.values()):
            return web.json_response({"error": "missing peer configuration"}, status=422)
        return web.json_response(body)

    async def members(request):
        return web.json_response({"items": [], "total": 0})

    async def join(request):
        body = await request.json()
        if not body.get("session_ids"):
            return web.json_response({"error": "missing sessions"}, status=422)
        return web.Response(status=204)

    async def feed(request):
        body = await request.json()
        if not body.get("messages") or any(
            not row.get("content") or not row.get("peer_id") for row in body["messages"]
        ):
            return web.json_response({"error": "missing message or author"}, status=422)
        feeds.extend(body["messages"])
        return web.json_response({"messages": body["messages"]})

    async def card(request):
        if request.match_info["peer"] not in {"miles_novak", "nora"}:
            return web.json_response({"error": "unrecorded peer"}, status=422)
        return web.json_response({"peer_card": []})

    async def representation(request):
        body = await request.json()
        if request.match_info["peer"] != "nora" or body.get("target") != "miles_novak":
            return web.json_response({"error": "unrecorded peer model"}, status=422)
        return web.json_response({"representation": ""})

    async def workspaces(request):
        # `inspect_workspace`'s read: empty means every bound workspace is
        # absent, so ownership is claimed by create, marked with Nora's key.
        return web.json_response({"items": [], "total": 0})

    application.router.add_post("/v3/workspaces", create)
    application.router.add_post("/v3/workspaces/list", workspaces)
    for resource in ("peers", "sessions", "scopes"):
        application.router.add_post(f"/v3/workspaces/{{workspace}}/{resource}", create)
    application.router.add_post("/v3/workspaces/{workspace}/sessions/{session}/peers", configure)
    application.router.add_post("/v3/workspaces/{workspace}/sessions/{session}/messages", feed)
    application.router.add_post("/v3/workspaces/{workspace}/scopes/{scope}/sessions/list", members)
    application.router.add_post("/v3/workspaces/{workspace}/scopes/{scope}/sessions", join)
    application.router.add_get("/v3/workspaces/{workspace}/peers/{peer}/card", card)
    application.router.add_post(
        "/v3/workspaces/{workspace}/peers/{peer}/representation", representation)
    return application


def action_service(runtime: Any, environment: IsolatedServices) -> DurableActionService:
    """Signal's durable action executor against the case-owned daemon address."""
    address = environment.urls["platforms.signal.base_url"]
    executor = _wrapper()._SignalExecutor(SignalSender(address), SignalReactor(address))
    return DurableActionService(
        store=runtime.store, platform="signal",
        account_key=runtime.readers[0]._account_key, execute=executor,
        receipt_intents=frozenset)


class GeneratedJudge:
    """Answers each judge request from ITS received payload, never a fixed string.

    Finite and inspecting: it verifies the digest's required evidence axes and
    answers against the expectation's own kind in the shown `state_after`,
    citing the ids it actually read — so judge citations resolve to the
    evidence the judge was shown, not to a scripted verdict.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.errors: list[tuple] = []

    def answer(self, body: dict) -> dict:
        text = "\n".join(texts(body.get("messages", [])))
        block = re.search(r"<cited_sources\b[^>]*>\n(.*?)\n</cited_sources>",
                          html_mod.unescape(text), re.DOTALL)
        if block is None:
            raise AssertionError("the judge request carried no cited_sources payload")
        payload = json.loads(block.group(1))
        expectation = payload["expectation"]
        if payload.get("measure") == "behavior":
            return self._behavior(payload, expectation)
        kind = expectation["kind"]
        problems = [name for name in ("effects", "settlement", "final_response")
                    if (payload.get(name) or {}).get("status") != "observed"]
        instructions = payload.get("subject_instructions") or {}
        if instructions.get("status") not in ("observed", None):
            problems.append("subject_instructions")
        if problems:
            return {"verdict": "cannot_tell",
                    "reason": f"generated inspection: evidence unavailable {problems}",
                    "entry_ids": []}
        ids = [f"{kind}:{key}" for key in (payload.get("state_after") or {})]
        if expectation.get("polarity") == "must":
            verdict, cited = ("met", ids[:1]) if ids else ("not_met", [])
        else:
            verdict, cited = ("not_met", ids[:1]) if ids else ("met", [])
        return {"verdict": verdict,
                "reason": f"generated inspection: {expectation.get('polarity')} {kind} "
                          f"holds entries {ids or 'none'} in the shown state",
                "entry_ids": cited}

    def _behavior(self, payload: dict, expectation: dict) -> dict:
        """The generated rehearsal's behavior binding: the claim anchors a
        turn by its declared input and outcome, and `check` is the declared
        token the turn's observed output must carry or lack — the token
        the fixture itself made observable, never a scripted verdict."""
        outcome = (payload.get("turn_outcome") or {}).get("status")
        effects = (payload.get("effects") or {}).get("status")
        declared = (payload.get("declared_input") or {}).get("text")
        if (outcome not in ("delivered", "intentional_non_delivery",
                            "recorded")
                or effects != "observed" or not declared):
            return {"verdict": "cannot_tell",
                    "reason": "generated inspection: turn evidence "
                              "incomplete — outcome, effects or declared "
                              "input missing",
                    "entry_ids": []}
        said = " ".join(str(v) for v in (
            (payload.get("turn_outcome") or {}).get("said") or []))
        said = f"{said} {payload.get('final_output') or ''}"
        # What the turn SAID binds only on its delivered output and its
        # acted calls — an observation call's arguments, or a call that
        # failed or was refused, never proves the turn recalled or said
        # the token.
        said += " " + " ".join(
            json.dumps(call.get("arguments"), ensure_ascii=False)
            for call in payload.get("tool_calls") or []
            if call.get("tool") in ("send_reply", "remind")
            and (call.get("result") or {}).get("status") == "ok"
            and not call.get("refusal")
            and call.get("arguments") is not None)
        check = expectation.get("check")
        if check is None:
            return {"verdict": "cannot_tell",
                    "reason": "generated inspection: the claim declares no "
                              "check token for the rehearsal to bind",
                    "entry_ids": []}
        found = check in said
        met = found if expectation.get("polarity") == "must" else not found
        effects = payload.get("effects") or {}
        if found:
            # The cite is the evidence the token was found in: the tool
            # outcome that carries it, else a delivery receipt.
            cited = [
                row["ref"] for row in effects.get("tools") or []
                if row.get("ref") and check in json.dumps(
                    row, ensure_ascii=False)][:1]
            if not cited:
                cited = [
                    row["ref"] for row in effects.get("items") or []
                    if row.get("kind") == "delivery" and row.get("ref")][:1]
        else:
            # An absence rests on everything the turn produced: cite the
            # inspected output surface — its deliveries and actions —
            # never state provenance, never an invented row.
            cited = [
                row["ref"]
                for row in effects.get("items") or []
                if row.get("kind") == "delivery" and row.get("ref")]
            cited += [row["ref"] for row in effects.get("tools") or []
                      if row.get("ref")]
            cited = cited[:3]
        return {"verdict": "met" if met else "not_met",
                "reason": f"generated inspection: check {check!r} "
                          f"{'present' if found else 'absent'} in the "
                          f"turn's observed output ({outcome})",
                "entry_ids": cited}

    async def respond(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        try:
            if body.get("stream") is True or body.get("tools"):
                raise AssertionError("a judge request is an unstreamed, tool-less completion")
            answer = self.answer(body)
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            self.errors.append((body.get("model"), type(exc).__name__, str(exc)))
            return web.json_response({"error": "unmatched generated judge request"}, status=422)
        return web.json_response(
            {"id": "generated-judge", "object": "chat.completion", "created": 1,
             "model": body["model"],
             "choices": [{"index": 0, "finish_reason": "stop",
                          "message": {"role": "assistant", "content": json.dumps(answer)}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
            headers={"x-omniroute-model": f"{body['model']}-served",
                     "x-omniroute-provider": f"generated-{body['model']}"})


class JourneyServices:
    """A `Spec.services` plan: one case's generated world, opened as
    `IsolatedServices` beside the case's own database.

    Carries the case's composition inputs (`policy`, `identity`, `room`,
    `overrides`) as well as the world's state — the `subject_spec` the
    drive composes comes from the same values, so the model the finite
    protocol answers and the subject the case composes cannot drift.
    """

    def __init__(self, *, policy: str, room: tuple[str, str] = ("signal", "g1"),
                 identity: str = "", overrides: dict[str, Any] | None = None,
                 judge_models: tuple[str, str] = ("generated-a", "generated-b")) -> None:
        self.policy, self.room, self.identity = policy, room, identity
        self._overrides = dict(overrides or {})
        self.judge_models = tuple(judge_models)
        self.requests: list[dict] = []
        self.errors: list[tuple] = []
        self.feeds: list[dict] = []
        self.owners: dict[str, Any] = {}
        self.daemon: FakeSignalDaemon | None = None
        self.protocol: CausalProtocol | None = None
        self.judge: GeneratedJudge | None = None
        self._metrics_port = self._free_port()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def overrides(self) -> dict[str, Any]:
        return {**self._overrides, "obs.metrics.port": self._metrics_port}

    def subject_spec(self, database_url: str, environment: IsolatedServices,
                     *, workdir: Path) -> SubjectSpec:
        """The case's own `SubjectSpec`, from the same inputs the world read."""
        return SubjectSpec(
            database_url=database_url, workdir=workdir, room=self.room,
            policy=self.policy, identity=self.identity, overrides=self.overrides(),
            environment=environment)

    def wire_actions(self, runtime: Any, environment: IsolatedServices) -> None:
        """Bind the real `DurableActionService` once the composed runtime exists."""
        self.owners["actions"] = action_service(runtime, environment)

    async def _actions(self, request: web.Request) -> web.Response:
        body = await request.json()
        if self.daemon is not None:
            self.daemon.sent_at_ms = int(time.time() * 1000)
        actions = self.owners.get("actions")
        if actions is None:
            return web.json_response({"error": "actions not wired"}, status=422)
        result = await actions.run(body)
        return web.json_response(action_response(body["action_id"], result),
                                 status=action_http_status(result))

    def _bridge_app(self) -> web.Application:
        application = web.Application()

        async def read(request):
            return web.json_response({
                "/v1/self": {"primary": "generated-self", "aliases": []},
                "/v1/groups": [{"id": "g1", "members": [["+852111"]]}],
                "/v1/health": {"ok": True},
            }[request.path])

        for path in ("/v1/self", "/v1/groups", "/v1/health"):
            application.router.add_get(path, read)
        application.router.add_post("/v1/actions", self._actions)
        if self.daemon is None:
            raise RuntimeError("journey world bridge is unbound")
        application.router.add_post("/api/v1/rpc", self.daemon.rpc)
        return application

    def _declared_config(self, database_url: str, workdir: Path) -> Any:
        return build_config(SubjectSpec(
            database_url=database_url, workdir=workdir,
            policy=self.policy, identity=self.identity, overrides=self.overrides()))

    def _destination(self, body: dict) -> Any:
        """The responder a request belongs to, by its declared model.

        The judge aliases answer the eval's own calls — a judge request that
        arrives streamed or with tools is `GeneratedJudge`'s named error, not
        a subject call. Every other request is the subject's to dispatch:
        `stream` status alone never picks a responder, so a raw non-judge
        call (the relevance gate, a malformed request, an unknown lane)
        reaches `CausalProtocol`, which names it into `errors` — it never
        lands on the judge by accident."""
        if body.get("model") in self.judge_models:
            if self.judge is None:
                raise RuntimeError("journey world judge is unbound")
            return self.judge.respond
        if self.protocol is None:
            raise RuntimeError("journey world protocol is unbound")
        return self.protocol.respond

    @asynccontextmanager
    async def open(self, database_url: str, redis: bool = False) -> Any:
        """`Spec.services`' contract: the case-owned world beside this database."""
        declared = Path(tempfile.mkdtemp(prefix="nora-journey-declared-"))
        cfg = self._declared_config(database_url, declared)
        self.protocol = CausalProtocol(cfg)
        self.judge = GeneratedJudge()
        self.daemon = FakeSignalDaemon(sent_at_ms=int(time.time() * 1000))
        model = web.Application()

        async def respond(request: web.Request) -> web.StreamResponse:
            try:
                body = await request.json()
            except ValueError as exc:
                self.errors.append(("malformed_request", type(exc).__name__))
                return web.json_response(
                    {"error": "malformed journey request"}, status=400)
            self.requests.append(body)
            if self.protocol is None or self.judge is None:
                return web.json_response({"error": "world unbound"}, status=503)
            destination = self._destination(body)
            if destination == self.protocol.respond:
                _record_request(self.protocol, body)
            return await destination(request)

        model.router.add_post("/v1/chat/completions", respond)
        apps = {
            "llm.base_url": model,
            "honcho.base_url": memory_service(self.feeds),
            "platforms.signal.base_url": self._bridge_app(),
        }
        apps.update({f"providers.{name}.base_url": model for name in cfg.providers})
        environment = IsolatedServices(
            database_url=database_url, applications=apps, redis=redis)
        async with environment as env:
            env.world = self
            yield env
