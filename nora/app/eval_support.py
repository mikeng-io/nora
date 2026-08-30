"""The facade `evals/` reads config, agent, doctrine, and tag pieces through.

`evals/` may import `nora.app` and `nora.testing` — nothing else from `nora/`
(a boundary test enforces this: `tests/test_boundaries.py`). `evals/run.py`
and `evals/evaluators.py` used to import `nora.agent.factory`,
`nora.agent.guards`, `nora.config`, `nora.doctrine.loader`, `nora.persons`, and
`nora.tag` directly — none of those are `nora.app`, so all of it funnels
through here instead. Every name below is a straight re-export or a thin
pass-through wrapper; nothing here changes behavior, it only relocates which
module gets to import the lower-level pieces. `nora.app` already imports all
of these directly in `composition.py`, so none of this widens what `nora.app`
itself is allowed to reach.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from nora.agent import factory
from nora.agent.factory import create_client, create_model_provider
from nora.agent.guards import LeakGuard
from nora.agent.registry import ToolRegistry
from nora.agent.whereis import Circle
from nora.config import Config, ConfigError
from nora.config import load as load_config
from nora.doctrine.loader import Doctrine, DoctrineStore
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.infra.honcho import HonchoClient
from nora.infra.sql.engine import create_sql_engine
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
from nora.tag import is_tagged
from nora.testing import (
    BACKED_TOOLS,
    EVAL_CIRCLE_ID,
    CannedPresenceReader,
    CannedRouteSource,
    CannedSearchProvider,
    CannedTransitSource,
    CannedWeatherProvider,
)

from .agent_runner import LiveAgentRunner

#: The single person the eval harness identity (`_IDENTITY_TOML`) maps — the
#: harness's `+852111` sender resolves to this person_id. The eval `Circle`'s
#: roster is exactly this, so the harness room (also just this person) is a
#: verified subset and the R-31 disclosure gate PERMITS `where_is`/`saved_places`.
_EVAL_PERSON_ID = "miles"

#: The SHIPPED table, deliberately WITHOUT `[llm.budgets]`. An eval measures the
#: agent as the repo defines it, so runs stay comparable across deployments and
#: across time — folding in a per-deployment delta would make a trajectory's
#: verdict depend on whichever config.toml happened to be on the machine.
#:
#: The cost is stated rather than hidden (external review, 2026-08-11): once an
#: operator raises a cap, an eval judges `ToolBudget` against a ceiling
#: production no longer enforces, so a trajectory that is legal in production
#: can fail here. That is the right way round — evals get stricter, never
#: laxer, and the divergence shows up as a failing eval rather than as a
#: passing one that proved nothing.
REPLY_GUARD_BUDGETS = ToolRegistry().budgets()
# What the doctrine loader's tool-catalogue gate checks backticked prose
# against, so an eval run loads the shipped doctrine under the same rule
# production does.
TOOL_NAMES = ToolRegistry().tool_names()

__all__ = [
    "BACKED_TOOLS",
    "REPLY_GUARD_BUDGETS",
    "TOOL_NAMES",
    "Config",
    "ConfigError",
    "Doctrine",
    "DoctrineStore",
    "LeakGuard",
    "Metrics",
    "Persons",
    "ProductionSeed",
    "build_eval_tool_registry",
    "build_live_runner",
    "build_production_seed",
    "create_client",
    "create_model_provider",
    "is_tagged",
    "load_config",
    "load_persons",
]


def load_eval_config(candidates: tuple[Path, ...]) -> Config:
    """Try each candidate path in order; raise the last `ConfigError` if none load.

    `config.toml` (tracked in git, per-deployment) is preferred; `config.example.toml`
    (committed) is the fallback for a local `config.toml` predating the schema.
    """
    last_error: ConfigError | None = None
    for candidate in candidates:
        try:
            return load_config(candidate)
        except ConfigError as exc:
            last_error = exc
    if last_error is None:
        raise ValueError("no config candidates provided")
    raise last_error


def build_live_runner(
    cfg: Config, *, effort: str | None = None,
    skills_dir: str | Path | None = None,
) -> LiveAgentRunner:
    """Build the REAL, networked `LiveAgentRunner` — Nora's actual model — for
    the eval harness's live path (`evals.harness.run_case(..., live=True)`).

    This is the one place `evals/` reaches a live model, and it exists HERE, in
    `nora.app`, rather than in `evals/` for the boundary reason the whole module
    exists (`tests/test_boundaries.py`): only `nora.app` may import
    `nora.agent.factory` and `nora.app.agent_runner`. It mirrors
    `nora.app.composition.build_dependencies`'s runner tail EXACTLY — same
    `cfg.llm` tier, same `ModelSource` over one client, same retry policy — so a
    live eval drives the SAME model configuration production does. The one
    deliberate difference is `skills_dir=None`: the eval/offline runners leave the
    on-demand skill layer INERT (`nora.app.agent_runner.LiveAgentRunner.__init__`
    documents this convention), so a live eval measures the doctrine+tools loop
    without the DEC-50 skill layer. A production-shape run passes the real
    `cfg.doctrine.dir.parent / "skills"` to close that gap; exercising skills
    live is future work when the shipped skill set stabilises.

    `effort` overrides `cfg.llm.reasoning_effort` for THIS run only — the
    drift-checking use case ("does the current harness agree with the config
    value, or with the ADR that picked it?") must be able to compare effort
    levels WITHOUT rewriting `config.toml` between runs (and without the
    config-drift trap of shipping a config; the operator changed the config to
    `low` on 2026-08-21 while the ADR still says `none`). None = use the shipped
    config value, which is what a plain gate run wants.

    The model under test is `cfg.llm` (Nora's production model), NOT
    `cfg.evals.judge_model` — the judge is a separate, cheaper grader built
    elsewhere. Raises if the LLM key env (`cfg.llm.api_key_env`) is unset, so a
    live run fails loudly at construction rather than at the first token.
    """
    api_key = os.environ.get(cfg.llm.api_key_env, "")
    if not api_key:
        raise ValueError(
            f"live eval runner needs the LLM key: environment variable "
            f"{cfg.llm.api_key_env} is not set"
        )
    tier = cfg.llm
    if effort is not None:
        # `cfg.llm` is a frozen pydantic model — model_copy, not assignment.
        tier = tier.model_copy(update={"reasoning_effort": effort})
    client = create_client(base_url=tier.base_url, api_key=api_key)
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=tier.model, max_tokens=tier.max_tokens, temperature=tier.temperature,
            reasoning_effort=tier.reasoning_effort,
        ),
    )
    return LiveAgentRunner(
        models=models,
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        # The runner's `skills_dir` is typed `Path | None`; accept str for the
        # caller's convenience and normalize here.
        skills_dir=Path(skills_dir) if skills_dir is not None else None,
        agent_name=cfg.agent.name,
    )


def build_eval_tool_registry() -> ToolRegistry:
    """A `ToolRegistry` wired with the canned eval backends (F4): `weather`,
    `web_search`, `place_info`, `route`, `transit`, `where_is` and `saved_places`
    answer `ok` instead of `unavailable`, so the live routing gate no longer
    confounds a real leak with a fallback-on-failure. `BACKED_TOOLS` names exactly
    what this backs. The presence tools clear the R-31 subset gate because the
    eval `Circle`'s roster is the one eval person and the harness room is that same
    person (a verified subset). Lives here, not in `evals/`, for the boundary
    reason `build_live_runner` does: only `nora.app` may import
    `nora.agent.registry`, `nora.agent.whereis` and `nora.testing`'s doubles.

    `route`/`transit` are `cast` because `ToolRegistry` types them as the concrete
    `RouteClient`/`TransitClient`, not a port — the canned doubles match the one
    method each tool calls (`place_info`/`route`, `arrivals`) but not the class.
    """
    return ToolRegistry(
        weather=CannedWeatherProvider(),
        search=CannedSearchProvider(),
        route=cast(Any, CannedRouteSource()),
        transit=cast(Any, CannedTransitSource()),
        presence=CannedPresenceReader(),
        circles=(Circle(circle_id=EVAL_CIRCLE_ID, person_ids=frozenset({_EVAL_PERSON_ID})),),
    )


@dataclass(frozen=True)
class ProductionSeed:
    """Read-only production-context snapshot an eval can start from.

    `evals.harness.run_case(..., live=True, seed=...)` injects REAL production
    context into the fake stores so the model sees a prompt SHAPE close to what
    production assembles — real transcript rows and real Honcho cards — while
    every write the run makes still lands in the fakes (zero contamination of
    the live store, and reproducible across runs because the seed is a fixed
    snapshot, not a live read).

    Built by `build_production_seed` (the only place allowed to touch the real
    stores from the eval path) or hand-built in a test. `recents` are
    `TranscriptRow`s remapped onto the eval room's conversation id; `cards`
    keyed by person id.

    These are the read surfaces the eval path exercises (`FakeRecordStore` only
    serves `recent(...)` and `FakeMemoryStore` serves `peer_card` from `cards`),
    so seeding exactly those is what changes the model-visible prompt without
    pretending the eval is something it is not.
    """

    recents: tuple[TranscriptRow, ...] = ()
    cards: dict[str, list[str]] = field(default_factory=dict)


async def build_production_seed(
    cfg: Config, *, recents_limit: int = 12,
) -> ProductionSeed:
    """Read a production-shaped context snapshot from the REAL stores
    (Postgres transcript + Honcho peer card), read-only, for `ProductionSeed`.

    This is the one place the eval path touches the live stores, and it lives
    HERE in `nora.app` for the boundary reason `build_live_runner` does: only
    `nora.app` may import the infra modules (`evals/` is limited to `nora.app`
    + `nora.testing`). It reads exactly what `TranscriptInjection` would
    assemble in production and returns it as a fixed snapshot — the caller
    (e.g. `evals/run.py --seed-production`) hands it to `run_case`, which
    injects it into the FAKE stores, so no eval run ever writes to the live
    database or Honcho.

    `recents_limit` rows are taken from the transcript, newest-first across
    EVERY conversation (a production room's id is not the eval room's, and the
    point is a realistic prompt shape, not one exact thread), remapped onto the
    eval room. If the deployment env is missing (`cfg.store.database_url_env`)
    this raises rather than silently seeding an empty snapshot — a
    `--seed-production` run without the deployment env would measure nothing.
    """

    database_url = os.environ.get(cfg.store.database_url_env, "")
    if not database_url:
        raise ValueError(
            f"build_production_seed needs the deployment database: environment "
            f"variable {cfg.store.database_url_env} is not set"
        )
    engine = create_sql_engine(database_url)
    recents: list[TranscriptRow] = []
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT id, sender_id, sender_name, ts, body, is_nora, "
                    "delivery_status, raw_json, quoted_ts, media_json, person_id "
                    "FROM messages ORDER BY ts DESC LIMIT :n"
                ),
                {"n": recents_limit},
            )
            rows = result.all()
    finally:
        await engine.dispose()
    for row in rows:
        recents.append(
            TranscriptRow(
                id=row.id, sender_id=row.sender_id, sender_name=row.sender_name,
                ts=row.ts, body=row.body, is_nora=bool(row.is_nora),
                # `DeliveryStatus(...)` from the raw column string — the same
                # decode `SqlRecordStore._row` uses, so the seed row matches a
                # production `recent()` row exactly (`str(enum)` would be
                # `"DeliveryStatus.PENDING"`, a value the DB never holds).
                delivery_status=DeliveryStatus(row.delivery_status),
                raw_json=row.raw_json, quoted_ts=row.quoted_ts,
                media_json=row.media_json, person_id=row.person_id,
            )
        )
    # Honcho peer card for the eval person, read-only. Keyed under the eval
    # person id (`_EVAL_PERSON_ID`) because `FakeMemoryStore` is asked for that
    # peer in the harness. A dead/missing card is a normal `[]`.
    cards: dict[str, list[str]] = {}
    honcho = HonchoClient(
        cfg.memory.base_url, cfg.memory.workspace_id, ai_peer=cfg.memory.ai_peer,
    )
    try:
        card = await asyncio.wait_for(
            honcho.peer_card(peer_id=_EVAL_PERSON_ID), timeout=5.0,
        )
        if card:
            cards[_EVAL_PERSON_ID] = card
    finally:
        await honcho.aclose()
    return ProductionSeed(recents=tuple(recents), cards=cards)
