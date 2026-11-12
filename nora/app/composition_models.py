from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from openai import AsyncOpenAI

from nora.agent import factory, tool_choice
from nora.app.agent_runner import LiveAgentRunner
from nora.app.composition_helpers import _reasoning_effort

"""Model-lane wiring steps: clients, runners and limits."""


@dataclass(frozen=True)
class LaneWiring:
    client: Any
    extra_model_clients: Any
    model_client_for: Any
    route_model: Any
    tier: Any


def lane(cfg: Any)-> LaneWiring:


    tier = cfg.llm
    url, api_key, _ = cfg.endpoint("tag")
    client = factory.create_client(base_url=url, api_key=api_key)
    routed_clients: dict[str, AsyncOpenAI] = {}
    extra_model_clients: list[AsyncOpenAI] = []

    def model_client_for(purpose: str)-> AsyncOpenAI:

        url, key, _ = cfg.endpoint(purpose)
        if (url, key) == cfg.endpoint("tag")[:2]:
            return client
        route = cfg.model_routes.get(purpose)
        name = route.provider if route and route.provider else cfg.llm.provider
        if name not in routed_clients:
            routed_clients[name] = factory.create_client(base_url=url, api_key=key)
            extra_model_clients.append(routed_clients[name])
        return routed_clients[name]

    def route_model(purpose: str, model: str = "") -> str:

        return cast(str, cfg.endpoint(purpose, model)[2])

    return LaneWiring(
        client=client,
        extra_model_clients=extra_model_clients,
        model_client_for=model_client_for,
        route_model=route_model,
        tier=tier,
    )


@dataclass(frozen=True)
class SourceWiring:
    models: Any


def source(client: Any, route_model: Any, tier: Any)-> SourceWiring:

    # One CLIENT (DEC-13), MANY MODELS — one per tag, built by this source. The model
    # carries `tool_choice`, which `ReplyGuard` flips mid-turn from `required` to `auto`, so
    # it is per-turn mutable state and two concurrent conversations must not share it.
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=route_model("tag", tier.model),
            max_tokens=tier.max_tokens,
            temperature=tier.temperature,
            reasoning_effort=tier.reasoning_effort,
        ),
    )
    return SourceWiring(models=models)


@dataclass(frozen=True)
class RunnerWiring:
    runner: Any


def build_runner(cfg: Any, metrics: Any, models: Any, tier: Any)-> RunnerWiring:

    runner = LiveAgentRunner(
        models=models,
        # Derived from the same `timeout_seconds` the invoke stage enforces, so
        # a throttle can never be misreported as a timeout (see
        # `factory.build_retry_policy`).
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        # The on-demand skill layer, ON (DEC-50, activating DEC-48). Skills sit beside doctrine
        # under `nora/` — `cfg.doctrine.dir` is the resolved absolute doctrine path, so its
        # sibling `skills/` is the operator skill directory (`nora/skills`). It is baked into…
        skills_dir=cfg.doctrine.dir.parent / "skills",
        agent_name=cfg.agent.name,
        # The process-wide registry, not a private one — `nora_tagtrace_
        # truncations_total` has to land where `/metrics` reads it.
        metrics=metrics,
    )
    return RunnerWiring(runner=runner)


@dataclass(frozen=True)
class RunnersWiring:
    limits: Any
    participation_runner: Any
    proactive_runner: Any
    summons_runner: Any


def runners(
    cfg: Any, metrics: Any, model_client_for: Any, route_model: Any,
    summons_wired: Any, tier: Any,
) -> RunnersWiring:


    def _auto_runner(
        effort: str, source: str, max_tokens: int | None = None,
    ) -> LiveAgentRunner:
        return LiveAgentRunner(
            models=factory.ModelSource(
                client=model_client_for(source.split("]")[0].lstrip("[")),
                spec=factory.ModelSpec(
                    model=route_model(source.split("]")[0].lstrip("["), tier.model),
                    max_tokens=max_tokens or tier.max_tokens,
                    temperature=tier.temperature,
                    reasoning_effort=str(_reasoning_effort(effort, source=source)),
                    tool_choice=tool_choice.AFTER_SPEAKING,
                ),
            ),
            retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
            skills_dir=cfg.doctrine.dir.parent / "skills",
            agent_name=cfg.agent.name,
            metrics=metrics,
        )

    # `[participation]`'s own effort, never the tag lane's — see the field's
    # note: silence is this occasion's default, and more reasoning
    # produces more of it.
    participation_runner: LiveAgentRunner | None = None
    if cfg.participation.enabled:
        participation_runner = _auto_runner(
            cfg.participation.reasoning_effort,
            "[participation] reasoning_effort",
        )
    # The proactive act turn's runner: the same `auto` shape, its OWN effort
    # (`ProactiveConfig`). It is not shared with the participation runner because the two
    # fail in opposite directions: at `none` a proactive turn spoke on every case where…
    proactive_runner = _auto_runner(
        cfg.proactive.reasoning_effort,
        "[proactive] reasoning_effort",
        max_tokens=cfg.proactive.max_tokens,
    )
    # The public Instagram summons' runner (`nora.app.summons`): the SAME `AFTER_SPEAKING`
    # shape as the participation runner above, for the same reason — a summons may end in
    # silence, which the tag runner's `FIRST_CALL` pin makes inexpressible. Its own, not…
    summons_runner: LiveAgentRunner | None = None
    if summons_wired:
        summons_runner = LiveAgentRunner(
            models=factory.ModelSource(
                client=model_client_for("summons"),
                spec=factory.ModelSpec(
                    model=route_model("summons", tier.model),
                    max_tokens=tier.max_tokens,
                    temperature=tier.temperature,
                    reasoning_effort=tier.reasoning_effort,
                    tool_choice=tool_choice.AFTER_SPEAKING,
                ),
            ),
            retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
            skills_dir=None,
            agent_name=cfg.agent.name,
            metrics=metrics,
        )
    # `output_tokens` comes from `[llm.limits]`, NOT from `tier.max_tokens`: the two are
    # separate values, and tying them would make the whole loop's output budget equal to one
    # call's ceiling — see `factory.build_limits`.
    limits = factory.build_limits(
        output_tokens=tier.limits.output_tokens,
        turns=tier.limits.turns,
        total_tokens=tier.limits.total_tokens,
    )
    return RunnersWiring(
        limits=limits,
        participation_runner=participation_runner,
        proactive_runner=proactive_runner,
        summons_runner=summons_runner,
    )
