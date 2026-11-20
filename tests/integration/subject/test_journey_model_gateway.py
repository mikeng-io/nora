"""A parsed `journey_world` manifest binds its declared model gateway.

`load_services` builds the `JourneyServices` the live-clock drive opens;
with `model_gateway` declared the world installs NO generated subject or
judge responders — the declared local upstream answers through the
environment's admitted egress, each dispatch counted once through the
installed `RunBudget`. An undeclared model, an unbudgeted client or an
endpoint outside the declared boundary refuses rather than rewriting.
"""

import ast
import json
from typing import Any

import httpx
import pytest
from aiohttp import web
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from evals.services import load_services
from nora.agent import factory
from nora.agent.speech_format import render_input
from nora.instructions.dreamer import DreamerInstructions
from nora.obs.metrics import Metrics
from nora.subject.eval_evidence import EvidenceError
from nora.subject.journey_world import make_causal_protocol
from nora.subject.obtain import SubjectSpec, build_config
from tests.architecture.test_every_model_call_is_a_declared_occasion import (
    _modules,
    declares_blocks,
)
from tests.integration.subject.test_model_gateway import _body, _budget, _completion
from tests.unit.test_causal_protocol import _call, _content, _drive, _req


def _foreground_declarations(cfg, request, tool_spec):
    return {
        "nora/agent/reaction.py": [
            (
                request(
                    "reaction",
                    cfg.reaction.model,
                    '<recent_chat scope="room"/><nora_reply key="turn">Generated</nora_reply>',
                ),
                "reaction",
                {"mode": "none", "query": ""},
            ),
            (
                request(
                    "reaction",
                    cfg.reaction.model,
                    '<recent_chat scope="room"/><nora_reply key="turn"/>'
                    '<gif_candidates query="generated"/>',
                ),
                "reaction",
                {"id": ""},
            ),
        ],
        "nora/agent/speech_format.py": [
            (
                request(
                    "speech_format",
                    cfg.speech.tts.format.model,
                    render_input("Generated & spoken."),
                ),
                "speech_format",
                "Generated & spoken.",
            ),
        ],
        "nora/app/proactive_fire.py": [
            (
                request(
                    "proactive",
                    cfg.llm.model,
                    "<due_intention>Generated intention</due_intention>"
                    "<why_now>intention_due at 2099-01-01T12:00:00+00:00</why_now>",
                    tools=(tool_spec("send_reply", "text", "string"),),
                ),
                "proactive",
                "",
            ),
        ],
        "nora/domain/summons.py": [
            (
                request(
                    "summons",
                    cfg.llm.model,
                    '<social_post source="the instagram post">Generated</social_post>',
                ),
                "summons",
                "",
            ),
        ],
        "nora/engagement/debrief_render.py": [
            (
                request("standing", cfg.standing.model, "<standing_prior/>"),
                "fold_room",
                {"standing": "Generated protocol acknowledgement.", "working_set": []},
            ),
            (
                request(
                    "standing",
                    cfg.standing.model,
                    "<standing_prior/>",
                    system="Scope of this call: UNIT",
                ),
                "fold_unit",
                {"items": []},
            ),
        ],
        "nora/engagement/stages/graph_debrief.py": [
            (
                request("standing", cfg.standing.model, '<debrief_items occasion="called"/>'),
                "debrief",
                {"operations": []},
            ),
        ],
        "nora/ingest/relevance_gate.py": [
            (
                request(
                    "relevance_gate",
                    cfg.relevance_gate.model,
                    '<transcript window="1"/>\n{"verdict": "go" | "no_go", "relevance_score": 0.0}',
                    raw=True,
                ),
                "relevance_gate",
                {"verdict": "no_go", "relevance_score": 0},
            ),
        ],
        "nora/language/browse_render.py": [
            (
                request(
                    "browse",
                    cfg.computer.browse.model,
                    '<browse_task scope="turn"/>',
                    tools=(tool_spec("browse_done", "answer", "string"),),
                ),
                "browse",
                ("browse_done", {"answer": "No result in the generated browser.", "found": False}),
            ),
        ],
    }


def _language_declarations(cfg, request, background, tool_spec):
    profiles = tool_spec("profile_no_change", "why", "string")
    return {
        "nora/language/character.py": [
            (background(name), name, "Generated protocol retains the previous account.")
            for name in ("character-nora", "character-person", "character-unit")
        ],
        "nora/language/culture.py": [
            (
                background(
                    "culture", tools=(tool_spec("character_no_change", "entity", "string"),)
                ),
                "culture",
                (
                    "character_no_change",
                    {
                        "entity": "room",
                        "why": "Generated protocol retains the previous account.",
                    },
                ),
            ),
        ],
        "nora/language/doctrine_check.py": [
            (
                request(
                    "lesson_check",
                    cfg.lessons.check_model,
                    '<law_rules key="turn"/><lesson_to_check key="turn"/>',
                    raw=True,
                ),
                "doctrine_check",
                {"verdict": "consistent"},
            ),
        ],
        "nora/language/profile.py": [
            (
                background("profile-person", tools=(profiles,)),
                "profile-person",
                ("profile_no_change", {"why": "Generated protocol retains the previous account."}),
            ),
        ],
        "nora/language/profile_collective.py": [
            (
                background(name, tools=(profiles,)),
                name,
                ("profile_no_change", {"why": "Generated protocol retains the previous account."}),
            )
            for name in ("profile-nora", "profile-room", "profile-unit")
        ],
        "nora/language/profile_verdict.py": [
            (
                background(
                    "profile-verdict",
                    '<profile_verdict_docket><guess id="H1"/></profile_verdict_docket>',
                    tools=(tool_spec("profile_verdict", "verdicts", "array"),),
                ),
                "profile-verdict",
                ("profile_verdict", {"verdicts": [{"line": "H1", "verdict": "unknown"}]}),
            ),
        ],
        "nora/language/subagent_render.py": [
            (
                request("subagent", cfg.llm.model, '<subagent_brief key="turn"/>'),
                "subagent",
                "Generated task completed.",
            ),
        ],
    }


def test_generated_subject_answers_every_production_declaration(tmp_path, evaluation_config):
    cfg = build_config(
        SubjectSpec(
            database_url="sqlite://",
            workdir=tmp_path,
            config=evaluation_config,
            overrides={"dreamer.model": "generated-dreamer"},
        )
    )
    protocol = make_causal_protocol(cfg)

    def request(purpose, fallback, user, *, system="Generated instruction", tools=(), raw=False):
        model = cfg.endpoint(purpose, fallback)[2]
        body = _req(
            model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=tools,
            stream=not raw,
        )
        if raw:
            body["response_format"] = {"type": "json_object"}
        return body

    def background(name, user="", *, tools=()):
        return request(
            "dreamer",
            cfg.dreamer_model(),
            user,
            tools=tools,
            system=f'<dreamer_objective name="{name}">Generated</dreamer_objective>',
        )

    def tool_spec(name, arg, arg_type):
        return name, {"type": "object", "required": [arg], "properties": {arg: {"type": arg_type}}}

    wake_instructions = DreamerInstructions(cfg.dreamer.dir, metrics=Metrics())
    cases = {
        **_foreground_declarations(cfg, request, tool_spec),
        **_language_declarations(cfg, request, background, tool_spec),
        "nora/app/wake_pass.py": [
            (
                request(
                    "dreamer", cfg.dreamer_model(), "<agenda/>\n<lesson_evidence/>",
                    system=wake_instructions.for_objective(objective).text,
                    tools=(tool_spec("graph_propose", "operations", "array"),),
                ),
                objective,
                "Generated protocol retains the previous account.",
            )
            for objective in ("wake", "wake-history")
        ],
    }
    # These three builders share the scripted turn, cognition and fold journey.
    scripted = {
        "nora/agent/prefill.py",
        "nora/language/cognition_render.py",
        "nora/language/comprehend_render.py",
    }
    # These declarations are reached by an offline diagnostic or an eval arm.
    offline = {
        "nora/app/profile_spike.py",
        "nora/language/confirmation_render.py",
        "nora/subject/context_selector_render.py",
        "scripts/memory_graph_spike.py",
    }
    modules = _modules()
    declared = {path for path, tree in modules.items() if declares_blocks(tree)}
    assert set(cases) | scripted | offline == declared
    for path in cases:
        objectives = {
            node.value.value for node in modules[path].body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and any(isinstance(target, ast.Name) and target.id.endswith("_ADAPTOR_NAME")
                    for target in node.targets)
        }
        if objectives:
            assert objectives == {occasion for _, occasion, _ in cases[path]}
    for path, requests in cases.items():
        for body, occasion, expected in requests:
            status, response = protocol.dispatch(body)
            assert status == 200, (path, occasion, response)
            assert protocol.occasions[occasion] >= 1 and protocol.phase == 0
            if body["stream"]:
                chunks = [ChatCompletionChunk.model_validate(chunk) for chunk in response["chunks"]]
                usage = chunks[-1].usage
                found = _call(response) if isinstance(expected, tuple) else _content(response)
            else:
                reply = ChatCompletion.model_validate(response["json"])
                usage, found = reply.usage, reply.choices[0].message.content
            assert usage and usage.total_tokens == usage.prompt_tokens + usage.completion_tokens > 0
            assert (json.loads(found) if isinstance(expected, dict) else found) == expected
            if isinstance(expected, tuple) and occasion in {
                "profile-person", "profile-room", "profile-unit", "profile-nora", "profile-verdict",
            }:
                continuation = dict(body, messages=[
                    *body["messages"], {"role": "tool", "content": '{"status":"ok"}'},
                ])
                status, answer = protocol.dispatch(continuation)
                assert status == 200 and _content(answer) == "Generated pass settled."
    _drive(protocol)
    status, response = protocol.dispatch(background("generated-unregistered"))
    assert status == 422 and "unsupported_objective" in response["error"]
    assert "generated-unregistered" not in protocol.occasions


@pytest.fixture
async def upstream():
    """The sole generated upstream — a real listener recording every arrival."""
    seen: dict[str, Any] = {"requests": [], "headers": []}
    app = web.Application()

    async def respond(request: web.Request) -> web.StreamResponse:
        body = json.loads(await request.read())
        seen["requests"].append(body)
        seen["headers"].append(dict(request.headers))
        return web.json_response(
            _completion(body["model"]),
            headers={"x-served-model": body["model"],
                     "x-served-provider": "generated"})

    app.router.add_post("/v1/chat/completions", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0, shutdown_timeout=0.5).start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}/v1", seen
    await runner.cleanup()


def _load_plan(tmp_path, upstream_url, *, models=("nora", "alpha", "beta")):
    """The parsed `journey_world` plan the live-clock selection binds."""
    manifest = {"version": 1, "corpora": {"autonomous_journey": {"the_outing": {
        "kind": "journey_world",
        "policy": "[rooms]\nallow = []",
        "judges": ["alpha", "beta"],
        "overrides": {"llm.base_url": upstream_url},
        "model_gateway": {"upstream": upstream_url,
                          "models": list(models)}}}}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(manifest))
    return load_services(path).corpora["autonomous_journey"]["the_outing"]


async def test_the_declared_world_admits_subject_and_judges_through_the_budget(
        upstream, tmp_path):
    url, seen = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        # The declared boundary replaces the world's generated responders:
        # no finite protocol, no generated judge, no owned model listener.
        assert plan.protocol is None and plan.judge is None
        assert "llm.base_url" not in services.urls
        with _budget(max_requests=8) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                responses = [
                    await client.chat.completions.with_raw_response.create(
                        **_body(model))
                    for model in ("nora", "alpha", "beta")
                ]
            assert all(r.http_response.status_code == 200 for r in responses)
            # one admitted subject request and the two distinct judge
            # requests, each counted exactly once
            assert budget.requests == 3
        # the upstream saw each final request — model alias and payload intact
        assert [r["model"] for r in seen["requests"]] == ["nora", "alpha", "beta"]
        assert all(r["messages"][0]["content"] == "generated"
                   for r in seen["requests"])
        services.check()


async def test_an_undeclared_model_is_refused_before_the_upstream(
        upstream, tmp_path):
    url, seen = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        with _budget(max_requests=8) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                await client.chat.completions.create(**_body("nora"))
                assert budget.requests == 1
                with pytest.raises(PermissionError, match="not isolated"):
                    await client.chat.completions.create(**_body("nora-undeclared"))
        assert [r["model"] for r in seen["requests"]] == ["nora"]
        with pytest.raises(EvidenceError, match="model_identity_undeclared"):
            services.check()


async def test_an_unbudgeted_dispatch_is_refused_before_the_upstream(
        upstream, tmp_path):
    url, seen = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        with _budget(max_requests=8) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                await client.chat.completions.create(**_body("nora"))
            assert budget.requests == 1
            # a budget is installed, but this client's transport is not bound to it
            async with httpx.AsyncClient() as unbudgeted:
                with pytest.raises(PermissionError, match="not isolated"):
                    await unbudgeted.post(
                        url + "/chat/completions", json=_body("nora"))
        assert len(seen["requests"]) == 1
        with pytest.raises(EvidenceError, match="model_dispatch_unbudgeted"):
            services.check()


async def test_an_endpoint_outside_the_declared_boundary_is_refused(
        upstream, tmp_path):
    url, _ = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        # The declared upstream IS the admitted endpoint — never rewritten,
        # only bound or refused.
        spec = SubjectSpec(database_url="sqlite://", workdir=tmp_path / "subject",
                           overrides={"llm.base_url": url})
        bound = services.bind_config(build_config(spec))
        assert bound.llm.base_url == url
        other = build_config(SubjectSpec(
            database_url="sqlite://", workdir=tmp_path / "other",
            overrides={"llm.base_url": "http://undeclared.invalid:9/v1"}))
        with pytest.raises(ValueError, match="model dispatch"):
            services.bind_config(other)
        services.check()
