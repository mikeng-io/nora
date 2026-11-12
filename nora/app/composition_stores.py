from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from nora.agent.dream_screen import text_offence
from nora.agent.registry import ToolRegistry
from nora.app.composition_helpers import _otp_secrets
from nora.app.memory_routing import MemoryRoutingError
from nora.app.otp import OtpService
from nora.app.startup import StartupError
from nora.config_reload import ReloadFiles
from nora.doctrine.loader import DoctrineError, DoctrineStore
from nora.domain.compute import ComputeJobStore
from nora.domain.decision_log import DecisionLogRecorder
from nora.domain.ports import CultureReader
from nora.domain.tool_call_log import ToolCallLogStore
from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations
from nora.infra.sql.compute_jobs import SqlComputeJobStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.decision_log import SqlDecisionLogStore
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.otp import SqlOtpStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.reasoning_strip import SqlReasoningStripStore
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.instructions.dreamer import DreamerInstructions
from nora.instructions.frontmatter import InstructionError
from nora.language.lesson_render import ActiveLessons
from nora.language.profile_render import ProfileHeads
from nora.persons import PersonsError
from nora.policy import PolicyError

"""Config, identity, database and shared-store wiring steps."""


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class IdentityWiring:
    allowlist: Any
    persons: Any
    reload_files: Any


def identity(cfg: Any, channel_directory: Any, metrics: Any)-> IdentityWiring:

    try:
        reload_files = ReloadFiles(
            policy_path=cfg.policy_file,
            identity_path=cfg.identity_file,
            ai_peer=cfg.honcho.ai_peer,
            default_workspace=cfg.honcho.workspace_id,
            metrics=metrics,
            policy_channels=channel_directory,
        )
    except (PersonsError, PolicyError, ValueError, MemoryRoutingError) as exc:
        raise StartupError(f"identity/policy error: {exc}") from exc
    persons, allowlist = reload_files.persons, reload_files.policy
    if allowlist.dm_persons:
        log.warning(
            "policy.toml lists %d DM principal(s), but the Signal parser still "
            "drops every non-group message — this section does nothing yet",
            len(allowlist.dm_persons),
        )
    return IdentityWiring(allowlist=allowlist, persons=persons, reload_files=reload_files)


@dataclass(frozen=True)
class DatabaseWiring:
    engine: Any


def database(allowlist: Any, cfg: Any, metrics: Any, persons: Any)-> DatabaseWiring:


    database_url = cfg.credentials.get(cfg.store.database_url_env, "")
    if not database_url:
        raise StartupError(
            f"environment variable {cfg.store.database_url_env} is not set — "
            "the PostgreSQL URL (postgresql+asyncpg://…) must be injected by the "
            "host (DEC-8); refusing to start"
        )
    try:
        run_migrations(database_url)
    except Exception as exc:
        raise StartupError(f"migration error: {exc}") from exc
    # ONE engine (one pool) shared by the record store and the presence reader — the shape
    # `PresenceStore`'s own docstring asks for. `audit.aclose()` (in
    # `nora.__main__.App.shutdown`) disposes it for both; `PresenceStore.aclose` is…
    engine = create_sql_engine(database_url)
    if cfg.memory.graph.enabled:
        from nora.app.graph_project import projection_context

        # produce=False: the session reads stimuli; nothing drains work items now.
        engine.update_execution_options(
            memory_graph=projection_context(
                allowlist,
                persons,
                nora_name=cfg.doctrine.display_name,
                metrics=metrics,
                produce=False,
            )
        )
    return DatabaseWiring(engine=engine)


@dataclass(frozen=True)
class AuditWiring:
    audit: Any


def build_audit(cfg: Any, engine: Any, metrics: Any)-> AuditWiring:

    audit = SqlRecordStore(
        engine,
        max_per_conversation=cfg.store.max_per_conversation,
        metrics=metrics,
    )
    return AuditWiring(audit=audit)


@dataclass(frozen=True)
class RuntimeStoresWiring:
    compute_jobs: Any
    tool_call_log_store: Any


def runtime_stores(engine: Any)-> RuntimeStoresWiring:

    # The compute-job outbox, REUSING `engine` for the same reason `reminders` does above.
    # Built HERE, unconditionally, beside the other stores: boot recovery a few hundred
    # lines down (beside `reminders`' own unconditional sweep) must find a store to sweep…
    compute_jobs: ComputeJobStore = SqlComputeJobStore(engine)
    tool_call_log_store: ToolCallLogStore = SqlToolCallLogStore(engine)
    return RuntimeStoresWiring(compute_jobs=compute_jobs, tool_call_log_store=tool_call_log_store)


@dataclass(frozen=True)
class ObsStoresWiring:
    reasoning_strip_store: Any
    tag_trace_store: Any


def obs_stores(engine: Any)-> ObsStoresWiring:

    # The durable half of the reasoning-strip counter (`reasoning_strip_log`, ADR 0013),
    # REUSING `engine` for the same reason the three stores above do. UNCONDITIONAL and
    # behind no flag: an instrument that is off by default answers nothing, and the…
    reasoning_strip_store = SqlReasoningStripStore(engine)
    # The reasoning-continuity retention store, REUSING `engine` for the same reason
    # `reminders`/`compute_jobs`/ `tool_call_log_store` do above. UNCONDITIONAL, unlike the
    # Dreamer's own stores just below: the `[debrief]` flag gates the FOLD, not the raw…
    tag_trace_store = SqlTagTraceStore(engine)
    return ObsStoresWiring(
        reasoning_strip_store=reasoning_strip_store, tag_trace_store=tag_trace_store
    )


@dataclass(frozen=True)
class ObservationWiring:
    observation_store: Any


def observation(engine: Any)-> ObservationWiring:

    # The typed platform-observation Record (design/platform-observations 02): persisted
    # whenever a bridge ingests typed source evidence. Cognition OFF still retains the rows
    # — they simply wake no session (the obligation enqueued in the append's transaction is…
    observation_store = SqlObservationStore(engine)
    return ObservationWiring(observation_store=observation_store)


@dataclass(frozen=True)
class DecisionLogWiring:
    decision_log: Any


def build_decision_log(allowlist: Any, clock: Any, engine: Any, metrics: Any)-> DecisionLogWiring:

    # The shared decision log. Built unconditionally beside `standing_store` for its reason
    # — it costs one attribute where nothing reads it — and there is a second reason here:
    # the log has FOUR writers on three different paths, so a flag gating it would be four…
    decision_log = DecisionLogRecorder(
        store=SqlDecisionLogStore(engine),
        metrics=metrics,
        clock=clock,
        unit_of=lambda platform, conversation_id: (
            convo.unit
            if (convo := allowlist.conversation(platform, conversation_id)) is not None
            else None
        ),
    )
    return DecisionLogWiring(decision_log=decision_log)


@dataclass(frozen=True)
class LanguageStoreWiring:
    language_store: Any


def build_language_store(cfg: Any, engine: Any, metrics: Any)-> LanguageStoreWiring:

    # The Dreamer's CURSOR, REUSING `engine` for the same reason
    # `reminders`/`compute_jobs`/`tool_call_log_store` do above. UNLIKE those, this one IS
    # gated on the feature flag: `SqlLanguageStore` owns `consolidation_watermark` —…
    language_store: SqlLanguageStore | None = (
        SqlLanguageStore(engine, metrics=metrics) if cfg.dreamer.enabled else None
    )
    return LanguageStoreWiring(language_store=language_store)


@dataclass(frozen=True)
class DoctrineWiring:
    doctrine: Any
    doctrine_store: Any


def build_doctrine(cfg: Any, content_checks: Any, metrics: Any)-> DoctrineWiring:

    # `[speech.tts]`'s speaker: wired ONLY when enabled AND a provider is named AND that
    # provider's key is present — the same inert-ship rule as the transcriber above, plus
    # the keyless- `route`/`search` shape. Off (or providerless, or keyless) leaves no…
    try:
        doctrine_store = DoctrineStore(
            cfg.doctrine.dir,
            strict=cfg.doctrine.strict,
            reload=cfg.doctrine.reload,
            canary=cfg.doctrine.canary,
            display_name=cfg.doctrine.display_name,
            trigger=cfg.engagement.trigger.text,
            language_hint=cfg.doctrine.language_hint,
            tool_names=ToolRegistry(content_checks=content_checks).tool_names(),
            metrics=metrics,
        )
        doctrine = doctrine_store.get(applies_to="tag")
    except DoctrineError as exc:
        raise StartupError(f"doctrine error: {exc}") from exc
    return DoctrineWiring(doctrine=doctrine, doctrine_store=doctrine_store)


@dataclass(frozen=True)
class InstructionsWiring:
    dreamer_instructions: Any


def instructions(cfg: Any, metrics: Any)-> InstructionsWiring:


    # The Dreamer's own instruction corpus — its doctrine plus one objective per adaptor.
    # Bind-mounted read-only the way `nora/doctrine` is, so an operator edit is live on the
    # next Dreamer tick with no rebuild. Built unconditionally rather than under…
    dreamer_instructions = DreamerInstructions(
        cfg.dreamer.dir,
        reload=cfg.dreamer.reload,
        strict=cfg.dreamer.strict,
        metrics=metrics,
    )
    try:
        dreamer_instructions.objective_names()
    except InstructionError as exc:
        raise StartupError(f"Dreamer instruction error: {exc}") from exc

    # Two machine ids for one agent: `[agent].name` (trace lineage) and `[honcho].ai_peer`
    # (the Honcho observer peer). A clone ("delia") must set BOTH; if they diverge, the
    # agent traces under one name but builds its memory under another — a silent split.…
    if cfg.agent.name != cfg.honcho.ai_peer:
        log.warning(
            "agent.name (%r) != honcho.ai_peer (%r): traces and Honcho memory "
            "will use different agent identities — set both to the same value "
            "unless this split is intended",
            cfg.agent.name,
            cfg.honcho.ai_peer,
        )
    return InstructionsWiring(dreamer_instructions=dreamer_instructions)


@dataclass(frozen=True)
class ReadersWiring:
    culture_reader: Any
    lessons_reader: Any
    profile_index: Any
    profile_reader: Any
    profile_store_reader: Any


def readers(cfg: Any, content_checks: Any, engine: Any)-> ReadersWiring:

    # THE CULTURE TIER'S READ HALF (DEC-207): ONE read-only reader, handed to the tag path's
    # assembler, the fire path and every Dreamer adaptor whose prompt carries `<culture>`.
    # `None` while `[culture] enabled = false`, the shipped default, so no occasion renders…
    culture_reader: CultureReader | None = SqlCultureStore(engine) if cfg.culture.enabled else None
    # `<lessons>` on tag and fire turns: read-only,
    # bodies of active lessons. `None` while `[lessons] render` is off.
    lessons_reader: ActiveLessons | None = (
        ActiveLessons(SqlLessonStore(engine)) if cfg.lessons.render else None
    )
    # THE SWITCH (design/unified-memory/45 §5, 00 «Sequence» step 8): the Profile accounts'
    # read half — one store, read-only by the Protocol the readers are typed against
    # (`ProfileHeads` has `account_heads` and nothing else), handed to the tag assembler,…
    profile_store_reader = (
        SqlProfileStore(
            engine,
            screen=functools.partial(text_offence, content_checks=content_checks),
        )
        if cfg.memory.profile.write and cfg.memory.profile.read
        else None
    )
    profile_reader: ProfileHeads | None = profile_store_reader
    # The same gate, for the writers (the dream sessions and the graph surface's
    # `remember`): on, a profile write projects its lines into the memory index in its own
    # transaction (45 §1, 25 §7); off is the shadow, and the shadow's lines join at the…
    profile_index = cfg.memory.profile.write and cfg.memory.profile.read
    return ReadersWiring(
        culture_reader=culture_reader,
        lessons_reader=lessons_reader,
        profile_index=profile_index,
        profile_reader=profile_reader,
        profile_store_reader=profile_store_reader,
    )


@dataclass(frozen=True)
class LanguageScopesWiring:
    language_scopes: Any


def build_language_scopes(allowlist: Any)-> LanguageScopesWiring:


    # `dream_loop`'s `scopes` callable — the policy.toml conversations, re-evaluated fresh
    # each tick through the live policy holder. No platform filter: this loop only READS
    # `RecordStore.since`, which needs no adapter for the platform.
    language_scopes: Callable[[], Sequence[str]] = lambda: tuple(  # noqa: E731
        conv.primary_id for conv in allowlist.rooms() if conv.primary_id and conv.absorbed
    )
    return LanguageScopesWiring(language_scopes=language_scopes)


@dataclass(frozen=True)
class OtpWiring:
    otp: Any
    otp_api_token: Any


def build_otp(
    audit: Any, cfg: Any, doctrine_store: Any, engine: Any,
    metrics: Any, persons: Any, registry: Any,
) -> OtpWiring:


    otp: OtpService | None = None
    otp_api_token = ""
    if cfg.otp.enabled:
        otp_secret, otp_api_token = _otp_secrets(cfg)
        otp = OtpService(
            store=SqlOtpStore(engine),
            persons=persons,
            adapters=dict(registry.adapters),
            records=audit,
            doctrine_store=doctrine_store,
            canary=cfg.doctrine.canary,
            metrics=metrics,
            secret=otp_secret,
            ttl=timedelta(seconds=cfg.otp.ttl_seconds),
            max_attempts=cfg.otp.max_attempts,
            max_per_hour=cfg.otp.max_per_hour,
            max_per_day=cfg.otp.max_per_day,
            clock=lambda: datetime.now(UTC),
        )
    return OtpWiring(otp=otp, otp_api_token=otp_api_token)
