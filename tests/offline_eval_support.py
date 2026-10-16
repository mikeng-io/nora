"""Config, agent, doctrine, and tag pieces the offline eval harness reads.

Pieces from `nora.agent.factory`, `nora.agent.guards`, `nora.config`,
`nora.doctrine.loader`, `nora.persons`, and `nora.tag` funnel through here.
Most names below are straight re-exports or thin pass-through wrappers that
change no behavior.

This is test support, not deployed code: `nora/__main__.py` does not import it
and no deployed path reaches it. Its consumer is `tests/offline_eval_harness.py`,
and `tests/` has no import contract.

Several of the places that build a Nora are in this file, each smaller than
the deployed construction of the same component. They serve two unit tests —
R-97's silence twins and the deterministic evaluators — because those run a
case with a SCRIPTED model and the rebuilt layer cannot.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from nora.agent import factory
from nora.agent import tool_choice as tool_choice_module
from nora.agent.factory import create_client, create_model_provider
from nora.agent.guards import LeakGuard
from nora.agent.registry import ToolRegistry
from nora.agent.structured import ask_structured, build_structured_model
from nora.agent.whereis import Circle
from nora.app.agent_runner import LiveAgentRunner
from nora.app.fire import FirePath, proactive_occasion
from nora.app.proactive_screen import ProactiveScreen
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
from nora.ingest.relevance_gate import (
    GATE_AUDIENCE,
    GateVerdictSchema,
    RelevanceGate,
)
from nora.ingest.relevance_gate import Completion as GateCompletion
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

#: The single person the eval harness identity (`_IDENTITY_TOML`) maps — the
#: harness's `+852111` sender resolves to this person_id (kept as the REAL
#: production person id, e.g. "miles_novak", so a `--seed-production` run reads the
#: actual Honcho card/conclusions instead of a synthetic "miles" that has none).
#: The eval `Circle`'s roster is exactly this,
#: so the harness room (also just this person) is a verified subset and the
#: R-31 disclosure gate PERMITS `where_is`/`saved_places`.
_EVAL_PERSON_ID = "miles_novak"

#: The unit `nora.policy.permissive_policy()` resolves EVERY conversation
#: to (`_PermissivePolicy.conversation`'s hardcoded default) — the note and
#: fact tiers are keyed on `Conversation.unit` (DEC-105), so a seed read
#: under any other unit string would seed rows the eval room's assembler
#: can never reach, silently discarding the seed rather than injecting it.
_EVAL_UNIT = "nora"

#: The SHIPPED table, deliberately WITHOUT `[llm.budgets]`. An eval measures the
#: agent as the repo defines it, so runs stay comparable across deployments and
#: across time — folding in a per-deployment delta would make a trajectory's
#: verdict depend on whichever config.toml happened to be on the machine.
#:
#: The cost: once an operator raises a cap, an eval judges `ToolBudget` against
#: a ceiling production no longer enforces, so a trajectory that is legal in
#: production can fail here. That is the right way round — evals get stricter,
#: never laxer, and the divergence shows up as a failing eval rather than as a
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
    "build_production_seed",
    "create_client",
    "create_model_provider",
    "is_tagged",
    "load_config",
    "load_persons",
    "run_proactive_act_case",
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
#: `x-omniroute-cache: bypass`. A suite reading the cache
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
        # `config.toml` — so a suite run from one would measure the TEMPLATE
        # while reporting numbers as if they were the deployment's: eval keys
        # such as `judge_samples`, `journey_samples` and the judge model differ
        # (`docs/eval-sop.md`).
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
    the eval harness's live path (`tests.offline_eval_harness.run_case(..., live=True)`).

    This is the one place the offline eval harness reaches a live model. It mirrors
    `nora.app.composition.build_dependencies`'s runner tail EXACTLY — same
    `cfg.llm` tier, same `ModelSource` over one client, same retry policy — so a
    live eval drives the SAME model configuration production does. The one
    deliberate difference is `skills_dir=None`: the eval/offline runners leave the
    on-demand skill layer INERT (`nora.app.agent_runner.LiveAgentRunner.__init__`
    documents this convention), so a live eval measures the doctrine+tools loop
    without the DEC-50 skill layer. A run in the deployed shape passes the real
    `cfg.doctrine.dir.parent / "skills"`.

    `effort` overrides `cfg.llm.reasoning_effort` for THIS run only — the
    drift-checking use case ("does the current harness agree with the config
    value, or with the ADR that picked it?") must be able to compare effort
    levels WITHOUT rewriting `config.toml` between runs. None = use the shipped
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
    cfg: Config, *, effort: str | None = None, model: str | None = None,
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

    `model` overrides `cfg.llm.model` the way `effort` overrides the effort.
    It exists because `evals.run --model` was accepted, printed in the reported
    command, and silently ignored by the act suite, so every act number was
    labelled with whatever `config.toml` happened to say.
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
    if model is not None:
        tier = tier.model_copy(update={"model": model})
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
    *, notes: Any | None = None, note_unit: str = _EVAL_UNIT,
    reminders: Any | None = None, clock: Any | None = None,
) -> ToolRegistry:
    """A `ToolRegistry` wired with the canned eval backends (F4): `weather`,
    `web_search`, `place_info`, `route`, `transit`, `where_is` and `saved_places`
    answer `ok` instead of `unavailable`, so the live routing gate no longer
    confounds a real leak with a fallback-on-failure. `BACKED_TOOLS` names exactly
    what this backs. The presence tools clear the R-31 subset gate because the
    eval `Circle`'s roster is the one eval person and the harness room is that same
    person (a verified subset).

    `route`/`transit` are `cast` because `ToolRegistry` types them as the concrete
    `RouteClient`/`TransitClient`, not a port — the canned doubles match the one
    method each tool calls (`place_info`/`route`, `arrivals`) but not the class.

    `notes` is the harness's note store. Without one the four note tools are
    registered with a `None` store, so `note_read` and `note_search` answer
    `unavailable` — and the `<notes>` block renders a title and a description
    only, never the body. A `-known` knowledge-cut twin would then differ from
    its `-unknown` twin by one index line containing no answer, backed by a
    tool that says it cannot look; a pair that cannot flip measures nothing.

    `note_unit` is a constant here rather than a resolver: every seeded
    row lands in the eval unit by construction (`seed_from_dict`), and a
    row keyed anywhere else silently never renders.

    `reminders`/`clock` are the proactive suites' pair: without a store `remind`
    answers `unavailable` and every decide case is ERROR, and the clock must be
    the CASE's `now`, not the wall clock, or `remind`'s past/too-soon checks
    measure the day the suite happened to run. `None` leaves every other caller
    unchanged.
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
        note_unit=(lambda _platform, _conv: note_unit) if notes else None,
        reminders=reminders,
        reminder_caps=ReminderCaps() if reminders is not None else None,
        **extra,
    )


@dataclass(frozen=True)
class ProductionSeed:
    """Read-only production-context snapshot an eval can start from.

    `tests.offline_eval_harness.run_case(..., live=True, seed=...)` injects REAL production
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
    onto the eval room by the caller (`tests.offline_eval_harness.run_case`) — there is no
    "the eval room's own position" in production, so the closest production
    shape is whatever position Nora most recently held anywhere. `notes` pairs
    a `Note` with its HEAD `NoteRevision` (what `InMemoryNoteStore.standing`
    reads), read under `_EVAL_UNIT` so they land where the eval's
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

    This is the one place the eval path touches the live stores. It reads
    exactly what `TranscriptInjection` would
    assemble in production and returns it as a fixed snapshot — the caller hands
    it to `run_case`, which injects it into the FAKE stores, so no eval run ever
    writes to the live database or Honcho.

    `recents_limit` rows are taken from the transcript, newest-first across
    EVERY conversation (a production room's id is not the eval room's, and the
    point is a realistic prompt shape, not one exact thread), remapped onto the
    eval room. If the deployment env is missing (`cfg.store.database_url_env`)
    this raises rather than silently seeding an empty snapshot — a
    seeded run without the deployment env would measure nothing.
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
                "last_folded_row_id FROM standing "
                "ORDER BY updated_at DESC LIMIT 1"
            ))).one_or_none()
            if standing_row is not None:
                standing = Standing(**dict(standing_row._mapping))

            # Notes: every live (unretired) note in the eval unit, paired
            # with its HEAD revision — the exact shape `InMemoryNoteStore.
            # standing` reads back (module docstring), so seeding `.notes`/
            # `.revisions` from these pairs reproduces what `SqlNoteStore.
            # standing` would have returned in production.
            note_rows = (await conn.execute(
                sa.text(
                    "SELECT n.id, n.unit, n.title, n.description, "
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
                    "WHERE n.unit = :ws AND n.retired_at IS NULL "
                    "ORDER BY n.created_at"
                ),
                {"ws": _EVAL_UNIT},
            )).all()
            for nrow in note_rows:
                note = Note(
                    id=nrow.id, unit=nrow.unit, title=nrow.title,
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
    # routing failure. Same read-only constraint: this is the same endpoint the
    # production `about` tool calls, and nothing writes.
    #
    # PERSON-ID NOTE: Honcho keys conclusions under the REAL person id
    # (`_EVAL_PERSON_ID`, "miles_novak"), not a synthetic one like "miles":
    # reading a synthetic id returns an empty card/conclusions even though the
    # person exists. `build_production_seed` keys the seed under the real id;
    # `FakeMemoryStore` is a plain dict keyed however we choose, so the harness
    # can serve it under the production id while the R-31 gate still resolves
    # the synthetic room through `_IDENTITY_TOML`.
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


def _seed_id(n: int, kind: str) -> str:
    """UUID-SHAPED, like the real stores' ids — the same rule
    `InMemoryNoteStore._shaped_id` states: the shape is what R-18's
    identifier screen reads, so a case seed's ids must look like the thing
    they stand in for. Deterministic, so a failure reproduces."""
    return f"{n:08x}-0000-4000-8000-{ord(kind[0]):012x}"


def seed_from_dict(raw: dict[str, Any], *, now_ms: int | None = None) -> ProductionSeed:
    """Build a `ProductionSeed` from a case file's `seed:` mapping
    (`evals/corpora/participation.yaml`) — the per-case seed of `Case.from_dict`.

    EVERYTHING is keyed to `_EVAL_UNIT` ("nora"). That is the footgun:
    the note tier is unit-keyed
    (DEC-105) and the eval room resolves to this unit through
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
          conclusions:                # Honcho, keyed by HONCHO PEER ID
            nora: ["..."]             #   -> <self_card> (observer == observed)
            miles_novak: ["..."]          #   -> <people_in_window> / <peer_history>

    **`conclusions` is keyed by `PersonRef.honcho_peer_id`, which IS the
    person id** (`nora.domain.models.PersonRef`: "== person_id, by
    construction"), and the harness identity maps exactly one — `miles_novak`, the
    sender `+852111` every seeded `"plain body"` row speaks as. A key that is
    not a peer id, or a speaker who is not in `_IDENTITY_TOML`, seeds a
    conclusion nothing can ever read: `FakeMemoryStore.representation` looks up
    `target or peer_id` and an unknown peer is `reachable=True, conclusions=()`
    — reachable, knowing nothing — so the case would grade a prompt missing the
    block it is about and its knowledge-cut twin would pass for the wrong
    reason. That is the same footgun the unit note above describes, on the
    one tier whose key is a PERSON rather than a unit.

    `nora` is the observer-equals-observed key: `cfg.memory.ai_peer` on every
    shipped config, and `<self_card>` is the block it feeds.

    **`now_ms`.** Every seeded timestamp is rendered as an age
    (`nora.agent.prefill._age`), so the base they walk forward from has to be
    a REAL recent instant — the same discipline `_gate_records` uses for the
    Relevance Gate fixture, and for the same reason: a frozen historical
    constant only looks safe. It does not raise, does not fail a test, and the
    age it prints keeps growing every calendar day it sits untouched.
    Defaults to `datetime.now(UTC)` so direct callers (the two direct-call
    tests in `tests/test_evals_participation.py`) need not pass it;
    `tests.offline_eval_harness.run_case` passes the SAME `now_ms` it
    then stamps the judged (incoming, untagged) line with, so the transcript
    ends strictly BEFORE that line — oldest to newest, judged line last —
    instead of the two being computed from two independent reads of the
    clock a few lines apart.
    """
    if now_ms is None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
    recents: list[TranscriptRow] = []
    speakers: dict[str, tuple[str, str]] = {}   # name -> (sender_id, person-ish id)
    transcript_entries = raw.get("transcript") or []
    # The window ends ONE STEP before `now_ms` (last entry at `now_ms -
    # 60_000`), leaving the judged line — stamped at `now_ms` by `run_case` —
    # strictly the newest thing `FakeRecordStore.recent`'s `(ts, id)` sort
    # sees, so it renders LAST rather than sorting above the window it is
    # read against.
    ts_base = now_ms - len(transcript_entries) * 60_000
    for i, entry in enumerate(transcript_entries):
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
            ts_base=ts_base + i * 60_000, id_start=1000 + i,
        ))

    standing = None
    if raw.get("standing"):
        # platform/conversation_id deliberately blank: `run_case` remaps the
        # position onto the eval room (S4's key), exactly as it does for a
        # `--seed-production` snapshot.
        standing = Standing(
            platform="", conversation_id="", body=raw["standing"],
            fold_count=1, updated_at=now_ms,
        )

    notes: list[tuple[Note, NoteRevision]] = []
    for n, entry in enumerate(raw.get("notes") or []):
        note_id = _seed_id(0x1000 + n, "note")
        revision_id = _seed_id(0x2000 + n, "rev")
        note = Note(
            id=note_id, unit=_EVAL_UNIT,
            title=entry["title"], description=entry["description"],
            uncertainty=entry.get("uncertainty", "open"),
            closing_condition=entry.get("closing_condition", "the room settles it"),
            established_in="g1", created_at=now_ms + n,
            head_revision_id=revision_id,
            anchor_date=entry.get("anchor_date", 20260901),
        )
        revision = NoteRevision(
            id=revision_id, note_id=note_id, body=entry["body"],
            parent_revision_id=None, written_at=now_ms + n,
            established_by="case-seed",
        )
        notes.append((note, revision))

    # Honcho, straight through: `ProductionSeed.conclusions` is already the
    # shape `FakeMemoryStore(conclusions=...)` takes, keyed by the OBSERVED
    # peer. Nothing is derived or defaulted — a case that seeds no conclusions
    # gets an empty dict, which is the knowledge cut for the memory tier and
    # is exactly what the `-unknown` twin of a knowledge-cut pair needs.
    conclusions = {
        str(peer): [str(line) for line in (lines or [])]
        for peer, lines in (raw.get("conclusions") or {}).items()
    }

    return ProductionSeed(
        recents=tuple(recents), standing=standing, notes=tuple(notes),
        conclusions=conclusions,
    )


# --- the Relevance Gate's eval seam -----------------------------------------
#
# The gate's construction lives HERE because its consumers are tests. The suite
# keeps what is genuinely its own — the cases, the four measurements, the report.


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
#: under. One constant: if the store were filled under one id and `decide`
#: called with another, `recent` would match nothing and every case would
#: silently judge an EMPTY ROOM. The gate is fail-closed and an empty room is a
#: legitimate no_go, so the suite would still report verdicts.
GATE_PROBE_CONVERSATION = "gate-eval"


async def _gate_records(bodies: tuple[str, ...]) -> FakeRecordStore:
    """`nora.testing.fakes.FakeRecordStore`, filled — NOT a hand-rolled store.

    That fake PASSES `tests/contract/test_audit_store_contract.py`, which pins
    the two properties this suite depends on: `recent` returns rows
    OLDEST-first and its `limit` takes the NEWEST. A local fake that
    gets the first one wrong renders every conversation backwards while a test
    fed rows in that wrong order still passes. A store that a contract
    suite has already judged is the only kind worth measuring against.
    """
    store = FakeRecordStore()
    names = ("Kit", "Sam", "Aaron")
    # REAL-LOOKING TIMES, not `timestamp=i`. The renderer prints an age per row
    # (`(2h ago)`), and `i` milliseconds after the epoch would
    # print «20000d ago» on every line of every case — a fixture artefact the
    # model would have to reason around, in the one suite that exists to grade
    # its reasoning.
    #
    # The shape mirrors a closed quiescence window: the last line just landed
    # and the ones before it walk back a few minutes each, which is what makes
    # a case's «has the room moved past this?» answerable at all.
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    step_ms = 3 * 60_000
    total = len(bodies)
    for i, body in enumerate(bodies, 1):
        await store.append(
            message=NormalizedMessage(
                platform="discord", conversation_id=GATE_PROBE_CONVERSATION,
                conversation_type="group", sender_id=f"p{i % 3}",
                sender_name=names[i % 3],
                timestamp=now_ms - (total - i) * step_ms, body=body, raw={},
            ),
        )
    return store


class _GateStanding:
    """Satisfies `RelevanceGate`'s `StandingReader` — `read` and nothing else.

    A `write` method is deliberately absent: it would have no production
    counterpart and would make the name ambiguous to
    `tests/architecture/test_store_methods_have_production_callers.py`,
    which notices a store method losing its last real caller.
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


class _StrandsGateClient:
    """The gate's model call through the Agent SDK instead of a raw completion.

    Satisfies the SAME `nora.ingest.relevance_gate.ModelClient` and returns a
    `Completion` whose `content` is the verdict JSON, so the gate's own parsing,
    fail-closed rules and metrics are byte-for-byte the path the deployed gate
    runs. The only variable is how the bytes were obtained — a comparison that
    also changed the parser would measure two things and attribute the
    difference to one.

    The SDK call itself lives in `nora.agent.structured`, because `strands`
    may only be imported from `nora/agent/` (`tests/test_boundaries.py`).

    `GateVerdictSchema` is `nora.ingest.relevance_gate`'s, not a copy declared
    here: two schemas is two questions, and this class exists to vary the CALL
    and nothing else.
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
            # Name the eval arm's Strands agent so its traces are filterable
            # apart from the production gate's raw `json_object` call — this
            # one goes through the SDK, which the production gate must not.
            agent_name="gate-measuring",
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
    #: `ToolRegistry.capability_digest()`, or empty. The deployed gate reads the
    #: instruction plus this digest; empty measures the instruction alone and is
    #: the default.
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
            # The deployment's own ceiling, so a suite run cannot report a
            # verdict production would have cut short. Both probe
            # clients bound themselves too; this is the gate holding the same
            # number, as the composition root does.
            call_timeout_seconds=self._cfg.timeout_seconds,
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
# The proactive ACT suite: the REAL FirePath
# over one `kind="proactive"` row with the REAL ProactiveScreen in front of it,
# graded on what the room heard (speak with must_cite, or silent).
# ---------------------------------------------------------------------------

#: The eval person, as `Persons` — the same `miles_novak`/`+852111` the harness
#: identity maps, built in memory so the decide drive needs no temp file.
# A REAL-SHAPED ROOM, not one synthetic member: a roster of one person with no
# aliases cannot express a note written in the group's own handles with
# nothing joining those handles to the roster. A fixture that cannot express
# the defect grades a model on a problem it will never be given.
#
# Aliases are what the group actually types — the `@handle` a note's body is
# written in — so a case can put one in a note and be graded on whether she
# resolves it from the index or spends a tool call on it.
_EVAL_PERSONS = Persons(
    by_person={
        "miles_novak": PersonRef(
            person_id="miles_novak", name="Miles", honcho_peer_id="miles_novak",
            aliases=("熱咖啡", "mapleleaf"),
        ),
        "grant": PersonRef(
            person_id="grant", name="Grant", honcho_peer_id="grant",
            aliases=("明明",),
        ),
        "ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada"),
        "ben": PersonRef(person_id="ben", name="Ben", honcho_peer_id="ben"),
    },
    index={
        ("signal", "+852111"): "miles_novak",
        ("signal", "+852222"): "grant",
        ("signal", "+852333"): "ada",
        ("signal", "+852444"): "ben",
        ("discord", "u-miles"): "miles_novak",
        ("discord", "u-grant"): "grant",
        ("discord", "u-ada"): "ada",
        ("discord", "u-ben"): "ben",
    },
)
_EVAL_MEMBER = "+852111"
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
    """One act case: seed the eval room, build the row the Cognition session's
    `remind` would have written, put it through the REAL `ProactiveScreen`
    and — if admitted — the REAL `FirePath.run` with the proactive runner
    (`tool_choice=auto`), and read back what the room heard.

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
            unit=_EVAL_UNIT, note_id=linked.id,
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
