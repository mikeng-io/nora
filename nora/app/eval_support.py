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
import logging
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from pydantic import BaseModel, Field

from nora.agent import factory
from nora.agent import tool_choice as tool_choice_module
from nora.agent.dreamer import (
    AdaptorBudget,
    AdaptorGrant,
    AdaptorSchedule,
    DreamerRuntime,
    HomeRoom,
    RegistryToolSource,
    proactive_grant,
)
from nora.agent.factory import create_client, create_model_provider
from nora.agent.guards import LeakGuard
from nora.agent.registry import ToolRegistry
from nora.agent.structured import ask_structured, build_structured_model
from nora.agent.whereis import Circle
from nora.config import Config, ConfigError
from nora.config import load as load_config
from nora.doctrine.loader import Doctrine, DoctrineStore
from nora.domain.models import DeliveryStatus, NormalizedMessage, PersonRef, TranscriptRow
from nora.domain.notes import Note, NoteRevision
from nora.domain.reminders import Reminder, ReminderCaps, ReminderState
from nora.domain.standing import Standing
from nora.engagement.stages.deliver import BUDGET_STOP_REASONS as _BUDGET_STOP_REASONS
from nora.infra.honcho import HonchoClient
from nora.infra.sql.engine import create_sql_engine
from nora.ingest.relevance_gate import GATE_AUDIENCE, RelevanceGate
from nora.ingest.relevance_gate import Completion as GateCompletion
from nora.instructions.dreamer import DreamerInstructions
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.proactive import ProactiveAdaptor, WorkspaceRoom
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
from nora.policy import permissive_policy
from nora.tag import is_tagged
from nora.testing import (
    BACKED_TOOLS,
    EVAL_CIRCLE_ID,
    CannedParkingProvider,
    CannedPresenceReader,
    CannedRouteSource,
    CannedSearchProvider,
    CannedStreetParkingProvider,
    CannedTransitSource,
    CannedWeatherProvider,
    FakeAdapter,
    FakeRosterSource,
    InMemoryNoteStore,
    InMemoryStandingStore,
    transcript_rows,
)
from nora.testing.fakes import FakeMemoryStore, FakeRecordStore
from nora.testing.reminders import FakeReminderStore

from .agent_runner import LiveAgentRunner
from .fire import FirePath, proactive_occasion
from .proactive_screen import ProactiveScreen

#: The single person the eval harness identity (`_IDENTITY_TOML`) maps — the
#: harness's `+852111` sender resolves to this person_id (kept as the REAL
#: production person id, e.g. "miles_novak", so a `--seed-production` run reads the
#: actual Honcho card/conclusions instead of a synthetic "miles" that has none —
#: measured 2026-08-31: "miles" returned an empty card while "miles_novak" has a
#: 12-line card + 9 conclusions). The eval `Circle`'s roster is exactly this,
#: so the harness room (also just this person) is a verified subset and the
#: R-31 disclosure gate PERMITS `where_is`/`saved_places`.
_EVAL_PERSON_ID = "miles_novak"

#: The workspace `nora.policy.permissive_policy()` resolves EVERY conversation
#: to (`_PermissivePolicy.conversation`'s hardcoded default) — the note and
#: fact tiers are keyed on `Conversation.workspace` (DEC-105), so a seed read
#: under any other workspace string would seed rows the eval room's assembler
#: can never reach, silently discarding the seed rather than injecting it.
_EVAL_WORKSPACE = "nora"

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
    "build_live_participation_runner",
    "build_live_runner",
    "build_proactive_runtime",
    "build_production_seed",
    "create_client",
    "create_model_provider",
    "is_tagged",
    "load_config",
    "load_persons",
    "run_note_candidates_case",
    "run_proactive_act_case",
    "run_proactive_decide_case",
    "seed_from_dict",
]


#: The stop reasons that mean a BUDGET cap ended the turn rather than the
#: model. Re-exported, never re-typed: `evals` may not import
#: `nora.engagement`, and the copy that lived in `evals/evaluators.py` was
#: hand-written and WRONG — five of its six strings never appear in the field
#: it guarded, so the guard was dead for the life of the participation suite.
#: One name the code increments against, which is this repo's own rule for a
#: label vocabulary.
BUDGET_STOP_REASONS = _BUDGET_STOP_REASONS


#: OmniRoute serves cached completions (`x-omniroute-cache: HIT`, ~12ms, zero
#: cost) and honours neither `Cache-Control: no-cache` nor its own
#: `x-omniroute-cache: bypass` — measured 2026-09-09. A suite reading the cache
#: is replaying old answers: every `sample: n` returns one verdict n times by
#: construction, so the determinism control reports what the cache remembers
#: rather than whether the model is stable.
NO_CACHE_HEADERS = {"x-omniroute-no-cache": "true"}


log = logging.getLogger("nora.app.eval_support")


def eval_base_url(configured: str) -> str:
    """The LLM base url a live eval should use.

    `[llm] base_url` is CONTAINER-side (`host.docker.internal`), which does not
    resolve on the host an eval is usually run from — so every live suite here
    fails with a bare `APIConnectionError` unless it is run inside the
    container. `NORA_EVAL_BASE_URL` overrides it.

    One function rather than the same `os.environ.get` at four call sites,
    because the failure it prevents is not a crash: a suite whose model calls
    all fail still produces verdicts (the gate fails closed, the agent says
    nothing), and "the room heard nothing" is the EXPECTED answer for most
    participation cases. The participation suite reported 10/15 correct and
    "false-speaks: none" with not one call having succeeded. An override that
    only some suites honour is how that happens twice.
    """
    return os.environ.get("NORA_EVAL_BASE_URL") or configured


def load_eval_config(candidates: tuple[Path, ...]) -> Config:
    """Try each candidate path in order; raise the last `ConfigError` if none load.

    `config.toml` (tracked in git, per-deployment) is preferred; `config.example.toml`
    (committed) is the fallback for a local `config.toml` predating the schema.
    """
    last_error: ConfigError | None = None
    for candidate in candidates:
        try:
            cfg = load_config(candidate)
        except ConfigError as exc:
            last_error = exc
            continue
        # SAY WHICH ONE. The fallback is silent, and a worktree has no
        # `config.toml` — so a suite run from one measured the TEMPLATE while
        # reporting numbers as if they were the deployment's. Measured
        # divergence on eval-relevant keys alone: `judge_samples` 1 vs 3,
        # `journey_samples` 3 vs 9, a different judge model
        # (`docs/eval-sop.md`: "a run without it measures a different model
        # than production and its verdict is about nothing anyone deploys").
        if candidate.name != "config.toml":
            log.warning(
                "eval config: %s has no config.toml; measuring %s instead. "
                "Its model and sample counts are the TEMPLATE's, not the "
                "deployment's — see docs/eval-sop.md.",
                candidate.parent, candidate.name,
            )
        return cfg
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
    client = create_client(base_url=eval_base_url(tier.base_url), api_key=api_key)
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


def build_live_participation_runner(
    cfg: Config, *, effort: str | None = None,
    skills_dir: str | Path | None = None,
) -> LiveAgentRunner:
    """The participation occasion's live runner — `build_live_runner`'s mirror
    of `composition.build_dependencies`'s `participation_runner` tail.

    The one difference that matters: `tool_choice=AFTER_SPEAKING` ("auto"),
    because a determination must be able to end in SILENCE and the tag tier's
    `FIRST_CALL` ("required") pin makes that inexpressible — under it the
    model must call something on the first pass and the only terminal call is
    `send_reply`. An eval that graded participation on the tag runner would be
    measuring a model forbidden from the outcome the suite exists to grade.
    """
    api_key = os.environ.get(cfg.llm.api_key_env, "")
    if not api_key:
        raise ValueError(
            f"live eval runner needs the LLM key: environment variable "
            f"{cfg.llm.api_key_env} is not set"
        )
    tier = cfg.llm
    if effort is not None:
        tier = tier.model_copy(update={"reasoning_effort": effort})
    client = create_client(base_url=eval_base_url(tier.base_url), api_key=api_key)
    return LiveAgentRunner(
        models=factory.ModelSource(
            client=client,
            spec=factory.ModelSpec(
                model=tier.model, max_tokens=tier.max_tokens,
                temperature=tier.temperature,
                reasoning_effort=tier.reasoning_effort,
                tool_choice=tool_choice_module.AFTER_SPEAKING,
            ),
        ),
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        skills_dir=Path(skills_dir) if skills_dir is not None else None,
        agent_name=cfg.agent.name,
    )


def build_eval_tool_registry(
    *, notes: Any | None = None, note_workspace: str = _EVAL_WORKSPACE,
    reminders: Any | None = None, clock: Any | None = None,
) -> ToolRegistry:
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

    `notes` is passed by the harness and was NOT before, which made two of the
    participation suite's three knowledge-cut twins measure nothing. The four
    note tools were registered and their store was `None`, so `note_read` and
    `note_search` answered `unavailable` — and the `<notes>` block renders a
    title and a description only, never the body. A `-known` case therefore
    differed from its `-unknown` twin by one index line containing no answer,
    backed by a tool that said it could not look, and the model reasoned
    exactly that: «the Raft question is already tracked in the note». A pair
    that cannot flip measures nothing, which is why the fact-lookup and
    correction pairs were deleted; these two were left in the same state
    (audit, 2026-09-08).

    `note_workspace` is a constant here rather than a resolver: every seeded
    row lands in the eval workspace by construction (`seed_from_dict`), and a
    row keyed anywhere else silently never renders.

    `reminders`/`clock` are the proactive suites' pair (design/dreamer-agent-
    loop/39 §5): without a store `remind` answers `unavailable` and every
    decide case is ERROR, and the clock must be the CASE's `now`, not the
    wall clock, or `remind`'s past/too-soon checks measure the day the suite
    happened to run. `None` keeps every other caller exactly as it was.
    """
    extra: dict[str, Any] = {}
    if clock is not None:
        extra["clock"] = clock
    return ToolRegistry(
        weather=CannedWeatherProvider(),
        parking=CannedParkingProvider(),
        street_parking=CannedStreetParkingProvider(),
        search=CannedSearchProvider(),
        route=cast(Any, CannedRouteSource()),
        transit=cast(Any, CannedTransitSource()),
        presence=CannedPresenceReader(),
        circles=(Circle(circle_id=EVAL_CIRCLE_ID, person_ids=frozenset({_EVAL_PERSON_ID})),),
        notes=notes,
        note_workspace=(lambda _platform, _conv: note_workspace) if notes else None,
        reminders=reminders,
        reminder_caps=ReminderCaps() if reminders is not None else None,
        **extra,
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
    serves `recent(...)`, `FakeMemoryStore` serves `peer_card` from `cards`,
    and `InMemoryStandingStore`/`InMemoryNoteStore` serve
    `standing`/`notes`), so seeding exactly those is what changes the
    model-visible prompt without pretending the eval is something it is not.

    `standing` is the single latest row across every conversation, remapped
    onto the eval room by the caller (`evals.harness.run_case`) — there is no
    "the eval room's own position" in production, so the closest production
    shape is whatever position Nora most recently held anywhere. `notes` pairs
    a `Note` with its HEAD `NoteRevision` (what `InMemoryNoteStore.standing`
    reads), read under `_EVAL_WORKSPACE` so they land where the eval's
    `permissive_policy()` resolves every room to (see that constant's
    docstring).
    """

    recents: tuple[TranscriptRow, ...] = ()
    cards: dict[str, list[str]] = field(default_factory=dict)
    conclusions: dict[str, list[str]] = field(default_factory=dict)
    standing: Standing | None = None
    notes: tuple[tuple[Note, NoteRevision], ...] = ()


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
    standing: Standing | None = None
    notes: list[tuple[Note, NoteRevision]] = []
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

            # Standing: the single latest position across every conversation —
            # there is no "the eval room's own position" in production, so the
            # closest shape is whatever Nora most recently held anywhere. The
            # caller remaps `platform`/`conversation_id` onto the eval room
            # (S4's key), never this function — the eval room's ids are an
            # `evals/` concern this module has no business naming.
            standing_row = (await conn.execute(sa.text(
                "SELECT platform, conversation_id, body, fold_count, updated_at, "
                "consumed_verdict_tag, last_folded_row_id FROM standing "
                "ORDER BY updated_at DESC LIMIT 1"
            ))).one_or_none()
            if standing_row is not None:
                standing = Standing(**dict(standing_row._mapping))

            # Notes: every live (unretired) note in the eval workspace, paired
            # with its HEAD revision — the exact shape `InMemoryNoteStore.
            # standing` reads back (module docstring), so seeding `.notes`/
            # `.revisions` from these pairs reproduces what `SqlNoteStore.
            # standing` would have returned in production.
            note_rows = (await conn.execute(
                sa.text(
                    "SELECT n.id, n.workspace, n.title, n.description, "
                    "n.uncertainty, n.closing_condition, n.anchor_date, "
                    "n.anchor_entity_ids, n.established_in, n.created_at, "
                    "n.head_revision_id, n.retired_at, n.replaced_by, "
                    "n.retire_reason, n.revisit_at, n.strikes, "
                    "n.last_wake_revision_id, n.event_id, n.reminder_id, "
                    "r.id AS rev_id, r.note_id AS rev_note_id, "
                    "r.body AS rev_body, r.parent_revision_id, r.written_at, "
                    "r.established_by, r.instruction_sha256, r.model "
                    "FROM note n JOIN note_revision r "
                    "ON r.id = n.head_revision_id "
                    "WHERE n.workspace = :ws AND n.retired_at IS NULL "
                    "ORDER BY n.created_at"
                ),
                {"ws": _EVAL_WORKSPACE},
            )).all()
            for nrow in note_rows:
                note = Note(
                    id=nrow.id, workspace=nrow.workspace, title=nrow.title,
                    description=nrow.description, uncertainty=nrow.uncertainty,
                    closing_condition=nrow.closing_condition,
                    anchor_date=nrow.anchor_date,
                    anchor_entity_ids=tuple(nrow.anchor_entity_ids),
                    established_in=nrow.established_in, created_at=nrow.created_at,
                    head_revision_id=nrow.head_revision_id,
                    retired_at=nrow.retired_at, replaced_by=nrow.replaced_by,
                    retire_reason=nrow.retire_reason, revisit_at=nrow.revisit_at,
                    strikes=nrow.strikes,
                    last_wake_revision_id=nrow.last_wake_revision_id,
                    event_id=nrow.event_id, reminder_id=nrow.reminder_id,
                )
                revision = NoteRevision(
                    id=nrow.rev_id, note_id=nrow.rev_note_id, body=nrow.rev_body,
                    parent_revision_id=nrow.parent_revision_id,
                    written_at=nrow.written_at, established_by=nrow.established_by,
                    instruction_sha256=nrow.instruction_sha256, model=nrow.model,
                )
                notes.append((note, revision))

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
    # Honcho conclusions for the eval person — the READ surface `about` serves
    # in production. Seeding these is what makes the recall journeys
    # (`about_recall`) testable at all: without them `about` answers "nothing
    # known" and the model's reasonable fallback (search_memory/search_knowledge)
    # is counted as a sibling leak + budget overrun — a harness artifact, not a
    # routing failure (measured 2026-08-31; injecting one conclusion flipped
    # about_recall from 0/9 to routing-pass). Same read-only constraint: this is
    # the same endpoint the production `about` tool calls, and nothing writes.
    #
    # PERSON-ID NOTE: `_EVAL_PERSON_ID` ("miles") is the SYNTHETIC harness person
    # (`+852111` → miles in `evals/harness.py`'s identity), not the production
    # person (e.g. "miles_novak"). Honcho keys conclusions under the PRODUCTION id —
    # reading "miles" returns an empty card/conclusions even though the person
    # exists (measured 2026-08-31: `miles` empty, `miles_novak` 12-card/9-conclusion).
    # `build_production_seed` reads the REAL id from the transcript and keys the
    # seed under it; `FakeMemoryStore` is a plain dict keyed however we choose,
    # so the harness can serve it under the production id while the R-31 gate
    # still resolves the synthetic room through `_IDENTITY_TOML`.
    conclusions: dict[str, list[str]] = {}
    honcho = HonchoClient(
        cfg.memory.base_url, cfg.memory.workspace_id, ai_peer=cfg.memory.ai_peer,
    )
    try:
        card = await asyncio.wait_for(
            honcho.peer_card(peer_id=_EVAL_PERSON_ID), timeout=5.0,
        )
        if card:
            cards[_EVAL_PERSON_ID] = card
        rec = await asyncio.wait_for(
            honcho.representation(
                peer_id=cfg.memory.ai_peer,
                search_query="personal facts about this person",
                # `max_conclusions` deliberately NOT passed — same contract as
                # the production `about` tool (Honcho splits it three ways).
                target=_EVAL_PERSON_ID,
            ),
            timeout=5.0,
        )
        if rec.reachable and rec.conclusions:
            conclusions[_EVAL_PERSON_ID] = list(rec.conclusions)
    finally:
        await honcho.aclose()
    return ProductionSeed(recents=tuple(recents), cards=cards,
                          conclusions=conclusions, standing=standing,
                          notes=tuple(notes))


#: Timestamp base for case-file seeds — 2026-08-31 08:00 HKT, matching
#: `evals.run._BODY_TS_BASE` so a per-case transcript ticks forward the same
#: way a journey's does.
_SEED_TS_BASE = 1_788_480_000_000


def _seed_id(n: int, kind: str) -> str:
    """UUID-SHAPED, like the real stores' ids — the same rule
    `InMemoryNoteStore._shaped_id` states: the shape is what R-18's
    identifier screen reads, so a case seed's ids must look like the thing
    they stand in for. Deterministic, so a failure reproduces."""
    return f"{n:08x}-0000-4000-8000-{ord(kind[0]):012x}"


def seed_from_dict(raw: dict[str, Any]) -> ProductionSeed:
    """Build a `ProductionSeed` from a case file's `seed:` mapping
    (`evals/cases/participation/`) — the per-case seed `Case.from_dict`
    gained for the participation suite (design/dreamer-agent-loop/35 §9
    gap 2).

    EVERYTHING is keyed to `_EVAL_WORKSPACE` ("nora"). That is the footgun
    the design audit named: the note tier is workspace-keyed
    (DEC-105) and the eval room resolves to this workspace through
    `permissive_policy()`, so a seed row keyed anywhere else silently
    vanishes — the case would grade a prompt missing the very block it is
    about, and the knowledge-cut twin would "pass" for the wrong reason.

    Shape (all keys optional):

        seed:
          transcript:             # prior room messages, oldest first
            - "plain body"                       # the eval member speaks
            - {name: "Grant", body: "..."}     # another (unmapped) member
          standing: "where the conversation had got to"
          notes:
            - {title, description, body, uncertainty?, closing_condition?}
    """
    recents: list[TranscriptRow] = []
    speakers: dict[str, tuple[str, str]] = {}   # name -> (sender_id, person-ish id)
    for i, entry in enumerate(raw.get("transcript") or []):
        if isinstance(entry, str):
            name, body = "Miles", entry
        else:
            name, body = entry["name"], entry["body"]
        if name not in speakers:
            if name == "Miles":
                speakers[name] = ("+852111", _EVAL_PERSON_ID)
            else:
                # An unmapped member: resolved to NO person, like a sender
                # identity.toml does not know.
                speakers[name] = (f"+8529{len(speakers):03d}", "")
        sender_id, person_id = speakers[name]
        recents.extend(transcript_rows(
            [body], sender_id=sender_id, sender_name=name,
            person_id=person_id or None,
            ts_base=_SEED_TS_BASE + i * 60_000, id_start=1000 + i,
        ))

    standing = None
    if raw.get("standing"):
        # platform/conversation_id deliberately blank: `run_case` remaps the
        # position onto the eval room (S4's key), exactly as it does for a
        # `--seed-production` snapshot.
        standing = Standing(
            platform="", conversation_id="", body=raw["standing"],
            fold_count=1, updated_at=_SEED_TS_BASE,
        )

    notes: list[tuple[Note, NoteRevision]] = []
    for n, entry in enumerate(raw.get("notes") or []):
        note_id = _seed_id(0x1000 + n, "note")
        revision_id = _seed_id(0x2000 + n, "rev")
        note = Note(
            id=note_id, workspace=_EVAL_WORKSPACE,
            title=entry["title"], description=entry["description"],
            uncertainty=entry.get("uncertainty", "open"),
            closing_condition=entry.get("closing_condition", "the room settles it"),
            established_in="g1", created_at=_SEED_TS_BASE + n,
            head_revision_id=revision_id,
            anchor_date=entry.get("anchor_date", 20260901),
        )
        revision = NoteRevision(
            id=revision_id, note_id=note_id, body=entry["body"],
            parent_revision_id=None, written_at=_SEED_TS_BASE + n,
            established_by="case-seed",
        )
        notes.append((note, revision))

    return ProductionSeed(
        recents=tuple(recents), standing=standing, notes=tuple(notes),
    )


# --- the Relevance Gate's eval seam (design/dreamer-agent-loop/36) ---------
#
# `evals/` may import `nora.app` and `nora.testing` and nothing else, so the
# gate's construction lives HERE rather than in `evals/relevance_gate.py`:
# building it there meant reaching into `nora.ingest`, `nora.obs`,
# `nora.doctrine`, `nora.domain` and `nora.agent` directly, which is six
# violations of the one boundary this module exists to hold. The suite keeps
# what is genuinely its own — the cases, the four measurements, the report.


@dataclass(frozen=True)
class GateProbeResult:
    """One gate call, as the suite needs to read it."""

    verdict: str
    score: float | None
    source: str
    error_kind: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


#: The conversation the gate probe's rows are written under AND read back
#: under. One constant, because two spellings is what went wrong: the store
#: was filled under one id and `decide` was called with another, so `recent`
#: matched nothing and every case silently judged an EMPTY ROOM. The suite
#: still reported verdicts — the gate is fail-closed and an empty room is a
#: legitimate no_go — so it read as three dead capabilities rather than as a
#: broken harness. The tell was in the cost line: identical token totals for
#: a one-line and a seven-line window.
GATE_PROBE_CONVERSATION = "gate-eval"


async def _gate_records(bodies: tuple[str, ...]) -> FakeRecordStore:
    """`nora.testing.fakes.FakeRecordStore`, filled — NOT a hand-rolled store.

    That fake PASSES `tests/contract/test_audit_store_contract.py`, which pins
    the two properties this suite depends on: `recent` returns rows
    OLDEST-first and its `limit` takes the NEWEST. A convenient local fake got
    the first one wrong once already, and the gate rendered every conversation
    backwards while the test written to guard the ordering passed — because it
    was fed rows in the order the bug expected. A store that a contract suite
    has already judged is the only kind worth measuring against.
    """
    store = FakeRecordStore()
    names = ("Kit", "Sam", "Aaron")
    for i, body in enumerate(bodies, 1):
        await store.append(
            message=NormalizedMessage(
                platform="discord", conversation_id=GATE_PROBE_CONVERSATION,
                conversation_type="group", sender_id=f"p{i % 3}",
                sender_name=names[i % 3], timestamp=i, body=body, raw={},
            ),
        )
    return store


class _GateStanding:
    """Satisfies `RelevanceGate`'s `StandingReader` — `read` and nothing else.

    It carried a `write` that raised, purely to satisfy the full
    `StandingStore` protocol the gate used to declare. That method had no
    production counterpart and made the name ambiguous to
    `tests/architecture/test_store_methods_have_production_callers.py`,
    which is the gate that notices a store method losing its last real
    caller. Narrowing the gate's own type was the fix; this is the fake
    getting smaller as a result.
    """

    def __init__(self, body: str | None) -> None:
        self._body = body

    async def read(self, *, platform: str, conversation_id: str) -> Standing | None:
        if self._body is None:
            return None
        return Standing(
            platform=platform, conversation_id=conversation_id,
            body=self._body, fold_count=1, updated_at=1,
        )


class _MeasuringGateClient:
    """The gate's real call, plus the token usage the suite must report.

    NOT a wrapper around the composition root's client: that one returns the
    gate's `Completion`, which carries `content` and `finish_reason` and
    deliberately nothing else — usage is the provider's fact and the gate has
    no use for it, so adding a field there to serve an eval would be the
    measurement changing the thing it measures.
    """

    def __init__(self, *, client: Any, gate_cfg: Any, reasoning_effort: Any) -> None:
        self._client = client
        self._cfg = gate_cfg
        self._reasoning_effort = reasoning_effort
        self.usage: tuple[int, int, int] | None = None

    @property
    def model(self) -> str:
        return str(self._cfg.model)

    async def complete(self, *, system: str, prompt: str) -> GateCompletion:
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._cfg.max_tokens,
                temperature=self._cfg.temperature,
                reasoning_effort=self._reasoning_effort,
            ),
            timeout=self._cfg.timeout_seconds,
        )
        usage = response.usage
        if usage is not None:
            self.usage = (
                usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
            )
        choice = response.choices[0]
        return GateCompletion(
            content=(choice.message.content or "").strip(),
            finish_reason=choice.finish_reason or "",
        )


class GateVerdictSchema(BaseModel):
    """The gate's verdict, as a schema the SDK can enforce.

    The SAME two fields `participation.md` asks for in prose and
    `parse_verdict` reads back, declared once so the comparison below is about
    the CALL and not about two different questions. `relevance_score` is
    optional exactly as it is today — the gate's own parser treats a missing
    score as absent rather than as zero.
    """

    verdict: Literal["go", "no_go"] = Field(
        description="whether this window is worth a participation turn",
    )
    relevance_score: float = Field(
        default=-1.0,
        description="0.0-1.0 confidence that the room wants Nora; omit if unsure",
    )


class _StrandsGateClient:
    """The gate's model call through the Agent SDK instead of a raw completion.

    Satisfies the SAME `nora.ingest.relevance_gate.ModelClient` and returns a
    `Completion` whose `content` is the verdict JSON, so the gate's own
    parsing, fail-closed rules and metrics are byte-for-byte the path they are
    today. The only variable is how the bytes were obtained — a comparison
    that also changed the parser would measure two things and attribute the
    difference to one.

    The SDK call itself lives in `nora.agent.structured`, because `strands`
    may only be imported from `nora/agent/` (`tests/test_boundaries.py`) —
    a rule this class tripped on its first cut, and correctly: the SDK belongs
    behind the agent boundary whoever is calling it.
    """

    def __init__(self, *, base_url: str, api_key: str, gate_cfg: Any) -> None:
        self._cfg = gate_cfg
        self._model = build_structured_model(
            base_url=base_url, api_key=api_key, model_id=str(gate_cfg.model),
            reasoning_effort=str(gate_cfg.reasoning_effort),
            default_headers=NO_CACHE_HEADERS,
            max_tokens=gate_cfg.max_tokens, temperature=gate_cfg.temperature,
        )
        self.usage: tuple[int, int, int] | None = None

    @property
    def model(self) -> str:
        return str(self._cfg.model)

    async def complete(self, *, system: str, prompt: str) -> GateCompletion:
        got = await ask_structured(
            model=self._model, prompt=prompt, schema=GateVerdictSchema,
            timeout_seconds=self._cfg.timeout_seconds, system_prompt=system,
        )
        self.usage = got.usage
        return GateCompletion(
            content=got.content, finish_reason=got.finish_reason,
        )


@dataclass(frozen=True)
class GateProbe:
    """A built gate, plus the instruction provenance a run is read against."""

    _cfg: Any
    _client: Any
    _doctrine: Any
    _effort: Any
    base_url: str
    instruction_version: int | None
    instruction_sha256: str
    #: Which client the probe drives. False is production's raw completion;
    #: True is the same gate through the Agent SDK's structured output.
    via_sdk: bool = False
    _api_key_env: str = ""
    #: `ToolRegistry.capability_digest()`, or empty. The suite could not see
    #: this at all until 2026-09-10, so a run measured the INSTRUCTION while
    #: production measured the instruction plus a digest — two different
    #: prompts wearing one verdict. Empty is the historical baseline and stays
    #: the default, so an existing run's numbers keep meaning what they meant.
    capabilities: str = ""

    async def run(
        self, *, transcript: tuple[str, ...], standing: str | None, label: str,
    ) -> GateProbeResult:
        model: Any = (
            _StrandsGateClient(
                base_url=self.base_url,
                api_key=os.environ.get(self._api_key_env, ""),
                gate_cfg=self._cfg,
            )
            if self.via_sdk else
            _MeasuringGateClient(
                client=self._client, gate_cfg=self._cfg, reasoning_effort=self._effort,
            )
        )
        gate = RelevanceGate(
            client=model, prompts=self._doctrine,
            records=await _gate_records(transcript), metrics=Metrics(),
            standing=_GateStanding(standing),
            transcript_limit=self._cfg.transcript_rows,
            capabilities=self.capabilities,
        )
        got = await gate.decide(
            platform="discord", conversation_id=GATE_PROBE_CONVERSATION,
        )
        p, c, t = model.usage if model.usage else (None, None, None)
        return GateProbeResult(
            verdict=got.verdict, score=got.score, source=got.source,
            error_kind=got.error_kind,
            prompt_tokens=p, completion_tokens=c, total_tokens=t,
        )


def build_gate_probe(
    cfg: Config, *, via_sdk: bool = False, capabilities: bool = False,
) -> GateProbe:
    """The real `RelevanceGate`, on the real `participation.md`, no turn.

    Reports the base url it used (see `eval_base_url`) so a misconfigured run
    is legible rather than a bare connection error.
    """
    base_url = eval_base_url(cfg.llm.base_url)
    client = create_client(
        base_url=base_url, api_key=os.environ.get(cfg.llm.api_key_env, ""),
    ).with_options(default_headers=NO_CACHE_HEADERS)
    doctrine = DoctrineStore(
        cfg.doctrine.dir, strict=cfg.doctrine.strict, reload="never",
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, metrics=Metrics(),
    )
    assembled = doctrine.get(applies_to=GATE_AUDIENCE)
    return GateProbe(
        _cfg=cfg.relevance_gate, _client=client, _doctrine=doctrine,
        _effort=cast(Any, cfg.relevance_gate.reasoning_effort),
        base_url=base_url,
        instruction_version=(
            assembled.sections[0].version if assembled.sections else None
        ),
        instruction_sha256=assembled.sha256,
        via_sdk=via_sdk,
        _api_key_env=cfg.llm.api_key_env,
        # Built HERE and not by the caller: `evals` may only reach `nora.app`
        # and `nora.testing` (the import contract), so the registry has to be
        # read on this side of that line.
        capabilities=ToolRegistry().capability_digest() if capabilities else "",
    )


# ---------------------------------------------------------------------------
# The note-candidates scenario suite's drive (design/dreamer-agent-loop/37 §7,
# issue #118): the REAL DreamerRuntime and the REAL NoteCandidateAdaptor over
# an in-memory notebook, with only the model live. Everything the adaptor
# does in production — render the batch and the notes index, screen the
# envelope, stage, gate, materialise — runs here unchanged; what differs is
# the store (`InMemoryNoteStore`, the note tier's contract-suite double) and
# the absence of a database, Honcho, or any tool (the adaptor is tool-free).
# ---------------------------------------------------------------------------


class _NoDreamTools:
    """`DreamToolSource` for an adaptor that declares no tools. `read_only`
    is the ceiling the runtime checks the adaptor's (empty) subset against;
    `build` is never asked for a name because there are none to ask for."""

    def read_only(self) -> frozenset[str]:
        return frozenset()

    def build(
        self, *, key: str, names: frozenset[str], workspace: str | None = None,
        adaptor: str = "", grant: Any = None,
    ) -> list[Any]:
        return []


@dataclass(frozen=True)
class NoteCandidatesDrive:
    """What one live run left behind, in plain tuples so `evals/` can grade it
    without importing the note tier's types: the runtime's outcome, the
    notebook before and after as `(id, title, anchor_date, head_revision_id)`,
    and every staged candidate as `(title, anchor_date, message_ids, status)`."""

    status: str
    stop_reason: str | None
    instruction_sha256: str
    before: tuple[tuple[str, str, int | None, str], ...]
    after: tuple[tuple[str, str, int | None, str], ...]
    staged: tuple[tuple[str, int, tuple[int, ...], str], ...]


def build_note_candidates_runtime(
    cfg: Config, *, client: Any, effort: str | None = None,
    dreamer_dir: str | Path | None = None, model: str | None = None,
    samples_per_fire: int = 1,
) -> tuple[DreamerRuntime, str]:
    """The Dreamer runtime exactly as `composition.build_dependencies` wires
    it for the live model — same model id, same tier scalars, same retry
    policy, same default budget — minus the tool registry (no tools) and the
    schedules (a drive fires on demand). `dreamer_dir` overrides
    `[dreamer] dir` so a prompt VARIANT is a directory of files, hashed and
    stamped like production's, never an inline string. `model` overrides
    `[dreamer] model` (then `[llm] model`) so a run from a dev machine can
    name the DEPLOYED alias (`nora-dreamer` on OmniRoute) rather than the
    template's provider id. `samples_per_fire` is `[notes]
    candidate_samples` for the drive — model calls per fire, unioned."""
    model = model or cfg.dreamer_model()
    reasoning = effort or cfg.dreamer.reasoning_effort or cfg.llm.reasoning_effort
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=model, max_tokens=cfg.llm.max_tokens,
            temperature=cfg.llm.temperature, reasoning_effort=reasoning,
            tool_choice=tool_choice_module.AFTER_SPEAKING,
        ),
    )
    instructions = DreamerInstructions(
        Path(dreamer_dir) if dreamer_dir is not None else cfg.dreamer.dir,
        reload="startup", strict=cfg.dreamer.strict, metrics=Metrics(),
    )
    fallback = cfg.dreamer.budget_for("")
    runtime = DreamerRuntime(
        models=models, instructions=instructions, tools=_NoDreamTools(),
        budgets={},
        default_budget=AdaptorBudget(
            turns=fallback.turns, total_tokens=fallback.total_tokens,
            output_tokens=fallback.output_tokens,
            timeout_seconds=fallback.timeout_seconds,
        ),
        retry=factory.build_retry_policy(timeout_seconds=cfg.llm.timeout_seconds),
        metrics=Metrics(), model_name=model,
        # The production schedule entry's shape (composition.py), with the
        # per-fire sample count under measurement; the interval and batch
        # limit are inert for an on-demand drive.
        schedules={
            "note-candidates": AdaptorSchedule(
                min_interval_seconds=3600, batch_limit=200,
                samples=samples_per_fire,
            ),
        },
    )
    return runtime, f"{model}/{reasoning}/x{samples_per_fire}"


async def run_note_candidates_case(
    runtime: DreamerRuntime, *, cfg: Config, workspace: str, scope: str,
    batch: Sequence[dict[str, Any]], notebook: Sequence[dict[str, Any]],
    now_ms: int,
) -> NoteCandidatesDrive:
    """One case: seed the notebook, hand the batch to the real adaptor through
    the real runtime, snapshot the notebook on both sides.

    `batch` rows are `{id, ts_ms, person, name, body, media}`; `notebook`
    entries are `{title, description, anchor_date, body}`. Seeded through the
    tag path's own `create` shape (`established_by="tag:eval"`), so the
    standing notes the adaptor renders look exactly like ones a person made.
    `cfg` supplies the room's zone (`[reminders] timezone`) exactly as
    composition does, so a rendered moment reads the same here as live.
    """
    store = InMemoryNoteStore()
    for entry in notebook:
        await store.create(
            workspace=workspace, title=entry["title"], description=entry["description"],
            uncertainty=entry["description"], closing_condition="the arrangement settles",
            established_in=scope, body=entry["body"], established_by="tag:eval",
            now_ms=now_ms - 86_400_000, anchor_date=int(entry["anchor_date"]),
        )
    adaptor = NoteCandidateAdaptor(
        notes=store, candidates=store, workspace_for=lambda _key: workspace,
        metrics=Metrics(), timezone=ZoneInfo(cfg.reminders.timezone),
    )
    rows = [
        TranscriptRow(
            id=int(m["id"]), sender_id=str(m["person"]), sender_name=str(m["name"]),
            ts=int(m["ts_ms"]), body=m.get("body"), is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
            media_json="[{}]" if m.get("media") else None,
            person_id=str(m["person"]), person_name=str(m["name"]),
        )
        for m in batch
    ]

    async def _snapshot() -> tuple[tuple[str, str, int | None, str], ...]:
        return tuple(
            (n.id, n.title, n.anchor_date, n.head_revision_id)
            for n in await store.standing(workspace=workspace)
        )

    before = await _snapshot()
    outcome = await runtime.run(adaptor=adaptor, key=scope, batch=rows)
    after = await _snapshot()
    staged = tuple(
        (c.title, int(c.anchor_date), tuple(int(i) for i in c.message_ids), c.status)
        for c in store.create_candidates.values()
    )
    return NoteCandidatesDrive(
        status=outcome.status, stop_reason=outcome.stop_reason,
        instruction_sha256=outcome.instruction_sha256,
        before=before, after=after, staged=staged,
    )


# ---------------------------------------------------------------------------
# The proactive adaptor's two suites (design/dreamer-agent-loop/39 §5, DEC-162
# (10)): the DECIDE suite drives the REAL DreamerRuntime + REAL
# ProactiveAdaptor through the REAL RegistryToolSource — home room, grant,
# `remind` under Dreamer provenance — over in-memory stores with only the
# model live, and is graded on the reminder rows left behind (WHEN). The ACT
# suite drives the REAL FirePath over one proactive row with the REAL
# ProactiveScreen in front of it, graded on what the room heard (speak with
# must_cite, or silent). Everything the harness lacked that 39 §5 named is
# here: a Dreamer tool source with the reminder store wired, a canned memory
# backend, per-room Standing seeding, and a reader over the reminder store.
# ---------------------------------------------------------------------------

#: The eval person, as `Persons` — the same `miles_novak`/`+852111` the harness
#: identity maps, built in memory so the decide drive needs no temp file.
_EVAL_PERSONS = Persons(
    by_person={"miles_novak": PersonRef(person_id="miles_novak", name="Miles", honcho_peer_id="miles_novak")},
    index={("signal", "+852111"): "miles_novak"},
)
_EVAL_MEMBER = "+852111"


class EvalDreamTools:
    """`DreamToolSource` for the decide suite: the REAL `RegistryToolSource`
    over the canned eval registry, re-bound per case.

    Re-bound because a case owns its notebook, its reminder store and its
    clock, while `DreamerRuntime` holds ONE tool source for the run of a
    suite (the note-candidates drive has the same shape, with a source that
    builds nothing). `bind` swaps the inner source; `build` before any `bind`
    is a harness fault and raises rather than building an empty room. The
    filter, the home room, the grant and the `dream_adaptor` provenance are
    all the production code path's — nothing is reimplemented here, which is
    what makes the suite a measurement of the thing that ships.
    """

    def __init__(self) -> None:
        self._source: RegistryToolSource | None = None
        self.builds: list[tuple[str, frozenset[str]]] = []
        # The bound registry's metrics: `nora_reminders_created_total{status}`
        # is where a `remind` the closure REFUSED (too_soon, screened, unknown
        # title, …) leaves its only trace, and a quiet case must not pass on
        # the strength of a refusal (audit 2026-09-09).
        self.metrics: Metrics = Metrics()

    def bind(
        self, *, workspace: str, home: HomeRoom, notes: Any, reminders: Any,
        memory: Any, clock: Callable[[], datetime],
    ) -> None:
        registry = build_eval_tool_registry(
            notes=notes, note_workspace=workspace, reminders=reminders, clock=clock,
        )
        self.metrics = Metrics()
        self._source = RegistryToolSource(
            registry=registry, store=FakeRecordStore(), persons=_EVAL_PERSONS,
            honcho=memory, metrics=self.metrics, ai_peer="nora",
            home_rooms={workspace: home},
            rosters={home.platform: FakeRosterSource(
                groups={home.conversation_id: (frozenset({_EVAL_MEMBER}),)},
            )},
            self_ids=frozenset(),
        )

    def read_only(self) -> frozenset[str]:
        return ToolRegistry().read_only_tools()

    def build(
        self, *, key: str, names: frozenset[str], workspace: str | None = None,
        adaptor: str = "", grant: AdaptorGrant | None = None,
    ) -> list[Any]:
        if self._source is None:
            raise RuntimeError("EvalDreamTools.build before bind: a case must bind its stores")
        built = self._source.build(
            key=key, names=names, workspace=workspace, adaptor=adaptor, grant=grant,
        )
        self.builds.append((adaptor, frozenset(
            str(getattr(t, "tool_name", None) or getattr(t, "__name__", "")) for t in built
        )))
        return built


def build_proactive_runtime(
    cfg: Config, *, client: Any, tools: EvalDreamTools, effort: str | None = None,
    dreamer_dir: str | Path | None = None, model: str | None = None,
) -> tuple[DreamerRuntime, str]:
    """The Dreamer runtime as `composition.build_dependencies` wires it for
    the proactive adaptor — same model id, tier scalars, retry policy,
    budget (`[dreamer.budgets.proactive]` or the default) and the proactive
    GRANT — with the eval tool source in place of the production one and
    no clock-driven schedule (a drive fires on demand). `dreamer_dir` and
    `model` mean what they mean for `build_note_candidates_runtime`."""
    model = model or cfg.dreamer_model()
    reasoning = effort or cfg.dreamer.reasoning_effort or cfg.llm.reasoning_effort
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=model, max_tokens=cfg.llm.max_tokens,
            temperature=cfg.llm.temperature, reasoning_effort=reasoning,
            tool_choice=tool_choice_module.AFTER_SPEAKING,
        ),
    )
    instructions = DreamerInstructions(
        Path(dreamer_dir) if dreamer_dir is not None else cfg.dreamer.dir,
        reload="startup", strict=cfg.dreamer.strict, metrics=Metrics(),
    )
    budget = cfg.dreamer.budget_for("proactive")
    runtime = DreamerRuntime(
        models=models, instructions=instructions, tools=tools,
        budgets={"proactive": AdaptorBudget(
            turns=budget.turns, total_tokens=budget.total_tokens,
            output_tokens=budget.output_tokens, timeout_seconds=budget.timeout_seconds,
        )},
        default_budget=AdaptorBudget(
            turns=budget.turns, total_tokens=budget.total_tokens,
            output_tokens=budget.output_tokens, timeout_seconds=budget.timeout_seconds,
        ),
        retry=factory.build_retry_policy(timeout_seconds=cfg.llm.timeout_seconds),
        metrics=Metrics(), model_name=model,
        schedules={"proactive": AdaptorSchedule(min_interval_seconds=86400, batch_limit=1)},
        tool_budget_overrides=cfg.llm.budgets,
        grants={"proactive": proactive_grant()},
    )
    return runtime, f"{model}/{reasoning}"


@dataclass(frozen=True)
class ProactiveDecideDrive:
    """What one decide run left behind: the runtime's outcome and every row
    in the reminder store afterwards as `(note_title, due_at_iso,
    requester, kind, intent)` — plain tuples, so `evals/` grades without
    importing the domain."""

    status: str
    stop_reason: str | None
    instruction_sha256: str
    rows: tuple[tuple[str | None, str, str, str, str], ...]
    tools_built: tuple[str, ...]
    #: Every `remind` call the model made, created OR refused — the sum of
    #: `nora_reminders_created_total` over its statuses. A quiet case with
    #: attempts and no rows is a false positive the store cannot show.
    remind_attempts: int = 0
    #: Memory reads the turn made — `about` and `ask_memory` (counted on
    #: `nora_room_lookups_total`) plus `search_memory_workspace` (on
    #: `nora_tool_calls_total`), every status. Objective version 2 requires
    #: one before any `remind`; the grader reads this.
    memory_calls: int = 0


_LOG_STATES: Mapping[str, ReminderState] = {
    "sent": ReminderState.DONE,
    "silent": ReminderState.DONE,
    "pending": ReminderState.PENDING,
    "cancelled_note_moved": ReminderState.CANCELLED,
    "cancelled_note_retired": ReminderState.CANCELLED,
    "failed_policy": ReminderState.FAILED,
    "failed_timeout": ReminderState.FAILED,
    "failed_error": ReminderState.FAILED,
}


async def _seed_notes(
    store: InMemoryNoteStore, *, workspace: str, scope: str, notes: Sequence[Mapping[str, Any]],
    now_ms: int,
) -> dict[str, Note]:
    """Case notes through the tag path's own `create` shape, keyed by title.
    `strikes` is reproduced through `record_wake` — the first wake on a
    fresh note is progress (the creation revision is tag-path), each further
    wake without progress is one strike — so the row carries a REAL strike
    count rather than a smuggled column."""
    by_title: dict[str, Note] = {}
    for n, entry in enumerate(notes):
        anchor = entry.get("anchor_date")
        note = await store.create(
            workspace=workspace, title=str(entry["title"]),
            description=str(entry["description"]),
            uncertainty=str(entry.get("uncertainty", entry["description"])),
            closing_condition=str(entry.get("closing_condition", "the room settles it")),
            established_in=scope, body=str(entry["body"]), established_by="tag:eval",
            now_ms=now_ms - 86_400_000 * 3 + n,
            anchor_date=int(anchor) if anchor is not None else None,
            # A dateless note needs a referent of some kind; the store refuses
            # one with neither a date nor an entity, so a case that says
            # «no anchor» gets an entity handle, which is the other legal shape.
            anchor_entity_ids=() if anchor is not None else ("eval-entity",),
        )
        for _ in range(1 + int(entry.get("strikes", 0))) if entry.get("strikes") else ():
            await store.record_wake(
                workspace=workspace, note_id=note.id,
                next_revisit_at=now_ms - 1, now_ms=now_ms - 3_600_000,
            )
        by_title[note.title] = note
    return by_title


async def run_proactive_decide_case(
    runtime: DreamerRuntime, *, tools: EvalDreamTools, workspace: str, now: datetime,
    rooms: Sequence[Mapping[str, Any]], notes: Sequence[Mapping[str, Any]],
    standing: Sequence[Mapping[str, Any]], log: Sequence[Mapping[str, Any]],
    memory: Mapping[str, Any] | None = None,
) -> ProactiveDecideDrive:
    """One decide case: seed the workspace's orient state, run the real
    adaptor through the real runtime with the real tool source bound to
    these stores, and read the reminder store back.

    `rooms` are `{platform, conversation_id, label, home}`; `notes` are
    `{title, description, body, anchor_date|None, strikes?}`; `standing`
    entries are `{room: <label>, body, age_hours}`; `log` entries are
    `{about?, intent, decided_hours_ago, due_hours_ago, outcome}` written as
    Nora's own rows; `memory` seeds the canned Honcho with `conclusions`
    (served by `about`/`ask_memory`/the self card) and `messages` (served by
    `search_memory_workspace`). `now` must be aware.
    """
    now_ms = int(now.timestamp() * 1000)
    ws_rooms = tuple(
        WorkspaceRoom(
            platform=str(r["platform"]), conversation_id=str(r["conversation_id"]),
            label=str(r["label"]), home=bool(r.get("home", False)),
        )
        for r in rooms
    )
    home_room = next(r for r in ws_rooms if r.home)
    home = HomeRoom(
        workspace=workspace, platform=home_room.platform,
        conversation_id=home_room.conversation_id, location_trusted=True,
    )
    note_store = InMemoryNoteStore()
    by_title = await _seed_notes(
        note_store, workspace=workspace, scope=home_room.conversation_id, notes=notes,
        now_ms=now_ms,
    )
    standing_store = InMemoryStandingStore()
    by_label = {r.label: r for r in ws_rooms}
    for entry in standing:
        room = by_label[str(entry["room"])]
        await standing_store.write(standing=Standing(
            platform=room.platform, conversation_id=room.conversation_id,
            body=str(entry["body"]), fold_count=1,
            updated_at=now_ms - int(float(entry.get("age_hours", 1)) * 3_600_000),
        ))
    reminders = FakeReminderStore()
    for entry in log:
        about = entry.get("about")
        linked = by_title.get(str(about)) if about else None
        payload: dict[str, str] = {"intent": str(entry["intent"])}
        if linked is not None:
            payload.update({
                "note_id": linked.id, "revision_id": linked.head_revision_id,
                "note_title": linked.title,
            })
        elif about:
            payload["note_title"] = str(about)
        how = str(entry.get("outcome", "pending"))
        state = _LOG_STATES.get(how, ReminderState.FAILED)
        due = now - timedelta(hours=float(entry.get("due_hours_ago", 0)))
        await reminders.create(Reminder(
            id=uuid.uuid4(), requester_person_id="nora", platform=home_room.platform,
            conversation_id=home_room.conversation_id, conversation_type="group",
            text=str(entry["intent"]),
            created_at=now - timedelta(hours=float(entry.get("decided_hours_ago", 24))),
            due_at=due, state=state,
            fired_at=None if state is ReminderState.PENDING else due,
            outcome=None if how == "pending" else how,
            kind="proactive", payload=payload,
        ))
    seed = memory or {}
    canned = FakeMemoryStore(
        conclusions={"nora": list(seed.get("conclusions") or [])},
    )
    for body in seed.get("messages") or []:
        canned.messages.append(("miles_novak", str(body)))
    tools.bind(
        workspace=workspace, home=home, notes=note_store, reminders=reminders,
        memory=canned, clock=lambda: now,
    )
    adaptor = ProactiveAdaptor(
        notes=note_store, standing=standing_store, reminders=reminders, memory=canned,
        rooms_of=lambda ws: ws_rooms if ws == workspace else (),
        ai_peer="nora", clock=lambda: now, timezone=ZoneInfo("Asia/Hong_Kong"),
        metrics=Metrics(),
    )
    before = {r.id for r in await reminders.history(
        requester_person_id="nora", since=now - timedelta(days=365), limit=1000,
    )}
    tools.builds.clear()
    outcome = await runtime.run(adaptor=adaptor, key=workspace, batch=())
    rows = []
    for row in await reminders.history(
        requester_person_id="nora", since=now - timedelta(days=365), limit=1000,
    ):
        if row.id in before:
            continue
        carried: Mapping[str, str] = row.payload if isinstance(row.payload, Mapping) else {}
        rows.append((
            carried.get("note_title"), row.due_at.isoformat(), row.requester_person_id,
            row.kind, str(carried.get("intent", row.text)),
        ))
    built = tuple(sorted({name for _, names in tools.builds for name in names}))
    attempts = sum(
        value for (name, _labels), value in tools.metrics.snapshot().items()
        if name == "nora_reminders_created_total"
    )
    return ProactiveDecideDrive(
        status=outcome.status, stop_reason=outcome.stop_reason,
        instruction_sha256=outcome.instruction_sha256, rows=tuple(rows),
        tools_built=built, remind_attempts=int(attempts),
        memory_calls=memory_calls_from(tools.metrics),
    )


#: The memory verbs the decide turn holds (39 D3) and the series each counts
#: on — the two room-scoped ones on the room-lookup series (`_record_room`),
#: the workspace one on the tool-call series (`_record`).
_MEMORY_VERB_SERIES: Mapping[str, frozenset[str]] = {
    "nora_room_lookups_total": frozenset({"about", "ask_memory"}),
    "nora_tool_calls_total": frozenset({"search_memory_workspace"}),
}


def memory_calls_from(metrics: Metrics) -> int:
    """How many memory reads a run made, whatever they answered, read off
    the tools' own counters. Every status counts — the rule is that she
    ASKED, and an `unavailable` answer is still an ask."""
    total = 0.0
    for (name, labels), value in metrics.snapshot().items():
        verbs = _MEMORY_VERB_SERIES.get(name)
        if verbs and dict(labels).get("tool") in verbs:
            total += value
    return int(total)


@dataclass(frozen=True)
class ProactiveActDrive:
    """What one act run did: the fire path's outcome string (`sent`,
    `silent`, `failed_*`, or the screen's `cancelled_*`), what the room
    heard, how the loop ended, and whether the silence counter moved.
    `screened` is the `ProactiveScreen` verdict — `None` when the row was
    admitted and a model was called."""

    outcome: str
    sent: tuple[str, ...]
    stop_reason: str | None
    silence: int
    screened: str | None
    #: `nora_silent_failures_total{audience="proactive"}`, summed: a send the
    #: guards refused, a budget cut, an invocation error. A `silent` outcome
    #: with one of these behind it is a malfunction, not a decision.
    silent_failures: int = 0


async def run_proactive_act_case(
    cfg: Config, *, runner: Any, now: datetime, row: Mapping[str, Any],
    transcript: Sequence[Any], standing: str | None, notes: Sequence[Mapping[str, Any]],
    note_moved: bool = False,
) -> ProactiveActDrive:
    """One act case: seed the eval room, build the row the decide turn would
    have written, put it through the REAL `ProactiveScreen` and — if admitted
    — the REAL `FirePath.run` with the proactive runner (`tool_choice=auto`),
    and read back what the room heard.

    `row` is `{note: <title>, intent, decided_hours_ago}`; `transcript`,
    `standing` and `notes` take `seed_from_dict`'s shapes; `note_moved`
    appends a TAG-PATH revision after the row was linked, which is exactly
    what the screen cancels on (39 D11) — no model is called for it.
    """
    seed = seed_from_dict({
        "transcript": list(transcript), "standing": standing or "",
        "notes": [dict(n) for n in notes],
    })
    note_store = InMemoryNoteStore()
    for note, revision in seed.notes:
        note_store.notes[note.id] = note
        note_store.revisions[revision.id] = revision
    linked = next((n for n, _ in seed.notes if n.title == row["note"]), None)
    if linked is None:
        raise ValueError(f"act case names note {row['note']!r} that its notes do not seed")
    reminder = Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="g1", conversation_type="group", text=str(row["intent"]),
        created_at=now - timedelta(hours=float(row.get("decided_hours_ago", 6))),
        due_at=now, state=ReminderState.FIRING, fired_at=None, outcome=None,
        kind="proactive",
        payload={
            "note_id": linked.id, "revision_id": linked.head_revision_id,
            "note_title": linked.title, "intent": str(row["intent"]),
        },
    )
    if note_moved:
        await note_store.append_revision(
            workspace=_EVAL_WORKSPACE, note_id=linked.id,
            body=f"{seed.notes[0][1].body}\nSettled by the room.",
            parent_revision_id=linked.head_revision_id, established_by="tag:eval",
            now_ms=int(now.timestamp() * 1000) - 60_000,
        )
    refusal = await ProactiveScreen(policy=permissive_policy(), notes=note_store).refusal(reminder)
    if refusal is not None:
        _state, why = refusal
        return ProactiveActDrive(outcome=why, sent=(), stop_reason=None, silence=0, screened=why)

    metrics = Metrics()
    records = FakeRecordStore()
    for r in seed.recents:
        records.rows.append({
            "id": r.id, "platform": "signal", "conversation_id": "g1",
            "sender_id": r.sender_id, "sender_name": r.sender_name,
            "person_id": r.person_id, "ts": r.ts, "body": r.body, "is_nora": r.is_nora,
            "delivery_status": r.delivery_status, "quoted_ts": r.quoted_ts,
            "raw_json": r.raw_json, "media_json": r.media_json,
        })
    standing_store = InMemoryStandingStore()
    if seed.standing is not None:
        await standing_store.write(standing=Standing(
            platform="signal", conversation_id="g1", body=seed.standing.body,
            fold_count=1, updated_at=int(now.timestamp() * 1000) - 3_600_000,
        ))
    doctrine = DoctrineStore(
        cfg.doctrine.dir, strict=cfg.doctrine.strict, reload="never",
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text, language_hint=cfg.doctrine.language_hint,
        tool_names=TOOL_NAMES, metrics=metrics,
    )
    adapter = FakeAdapter()
    path = FirePath(
        persons=_EVAL_PERSONS, records=records, memory=FakeMemoryStore(),
        doctrine_store=doctrine, metrics=metrics, runner=runner,
        adapters={"signal": cast(Any, adapter)},
        registry=build_eval_tool_registry(notes=note_store),
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        ai_peer="nora", context_window=cfg.store.context_window,
        limits=factory.build_limits(
            output_tokens=cfg.llm.limits.output_tokens, turns=cfg.llm.limits.turns,
            total_tokens=cfg.llm.limits.total_tokens,
        ),
        timeout_seconds=cfg.heartbeat.timeout_seconds, clock=lambda: now,
        rosters={"signal": FakeRosterSource(groups={"g1": (frozenset({_EVAL_MEMBER}),)})},
        event_registry={"proactive": proactive_occasion},
        standing=standing_store, notes=note_store, policy=permissive_policy(),
        proactive_runner=runner,
    )
    outcome = await path.run([reminder])
    counts: dict[str, float] = {}
    for (name, labels), value in metrics.snapshot().items():
        if name == "nora_llm_stop_reason_total" and value:
            reason = dict(labels).get("reason", "")
            if reason:
                counts[reason] = counts.get(reason, 0.0) + value
    stop_reason = (
        sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counts else None
    )
    silent_failures = sum(
        value for (name, labels), value in metrics.snapshot().items()
        if name == "nora_silent_failures_total"
        and dict(labels).get("audience") == "proactive"
    )
    return ProactiveActDrive(
        outcome=outcome, sent=tuple(adapter.texts), stop_reason=stop_reason,
        silence=int(metrics.get("nora_proactive_silence_total", platform="signal")),
        screened=None, silent_failures=int(silent_failures),
    )
