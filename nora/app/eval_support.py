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
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from nora.agent import factory
from nora.agent import tool_choice as tool_choice_module
from nora.agent.dreamer import AdaptorBudget, AdaptorSchedule, DreamerRuntime
from nora.agent.factory import create_client, create_model_provider
from nora.agent.guards import LeakGuard
from nora.agent.registry import ToolRegistry
from nora.agent.whereis import Circle
from nora.config import Config, ConfigError
from nora.config import load as load_config
from nora.doctrine.loader import Doctrine, DoctrineStore
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import Note, NoteRevision
from nora.domain.standing import Standing
from nora.infra.honcho import HonchoClient
from nora.infra.sql.engine import create_sql_engine
from nora.instructions.dreamer import DreamerInstructions
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
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
    InMemoryNoteStore,
    transcript_rows,
)

from .agent_runner import LiveAgentRunner

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
    "build_production_seed",
    "create_client",
    "create_model_provider",
    "is_tagged",
    "load_config",
    "load_persons",
    "run_note_candidates_case",
    "seed_from_dict",
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
    client = create_client(base_url=tier.base_url, api_key=api_key)
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
        parking=CannedParkingProvider(),
        street_parking=CannedStreetParkingProvider(),
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
