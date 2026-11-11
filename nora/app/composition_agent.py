from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Any

from nora.agent.dream_screen import text_offence
from nora.agent.dreamer import TOOL_RESULT_CAPS
from nora.agent.mcp import McpMount, build_mcp_tools
from nora.agent.registry import ToolRegistry
from nora.agent.subagent_desk import SubagentDesk
from nora.app.composition_helpers import _note_unit_of, _reasoning_effort
from nora.app.composition_media import _describe_max_bytes, _describe_models, _describe_providers
from nora.app.composition_readers import external_files_source, google_credentials
from nora.app.subagent_backend import SubagentBackend
from nora.app.subagent_wiring import build_subagent_backend
from nora.domain.ports import ComputeBackend
from nora.domain.subagent import SUBAGENT_KIND
from nora.infra.releases import GithubReleases
from nora.infra.sql.fetched_pages import SqlFetchedPageStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.social_stream import readable_social_store
from nora.infra.sql.video_readings import SqlVideoReadingStore
from nora.infra.version import VersionManifest

"""Agent surface wiring steps: registry, MCP, backends."""


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class BackendsWiring:
    compute_backends: Any


def backends() -> BackendsWiring:
    # The generic deferred-delivery path: the `ComputeBackend` port, `ComputeJobPoller`,
    # `compute_jobs` and boot recovery. The poller below is never built while no backend is
    # registered. `kind -> ComputeBackend`: the poller resolves a row's backend by its…
    compute_backends: dict[str, ComputeBackend] = {}
    return BackendsWiring(compute_backends=compute_backends)


@dataclass(frozen=True)
class McpWiring:
    mcp_tool_groups: Any
    version_manifest: Any


def mcp(cfg) -> McpWiring:
    # `nora.agent` may not import `nora.config` (import-linter's "only app and
    # __main__ read config") — this is the conversion point, the same shape
    # `ReminderCaps(**cfg.reminders.model_dump())` already uses just below.
    mcp_mounts = {
        name: McpMount(
            name=name,
            base_url=mount.base_url,
            token_env=mount.token_env,
            budget=mount.budget,
        )
        for name, mount in cfg.tools.mcp.items()
        if mount.enabled
    }
    mcp_tool_groups = build_mcp_tools(mcp_mounts, credentials=cfg.credentials)
    # Running identity and release history share the immutable build snapshot.
    version_manifest = VersionManifest()
    return McpWiring(mcp_tool_groups=mcp_tool_groups, version_manifest=version_manifest)


@dataclass(frozen=True)
class DeskWiring:
    subagent_desk: Any


def desk(cfg, clock, compute_jobs, document_service) -> DeskWiring:
    # The sub-agent desk the four `subagent_*` tools call: built ONLY under
    # `[subagent] enabled`, so off the registry withholds the tools. The worker is attached below,
    # once built, because its closed tool list is built from this registry.
    subagent_desk = (
        SubagentDesk(
            jobs=compute_jobs,
            clock=clock,
            documents=document_service if cfg.documents.enabled else None,
        )
        if cfg.subagent.enabled
        else None
    )
    return DeskWiring(subagent_desk=subagent_desk)


@dataclass(frozen=True)
class RegistryWiring:
    tool_registry: Any


def registry(
    allowlist, blobs, browser, browser_manager, cfg, clock, computer, computer_admission,
    computer_context, computer_dm_scope_for, computer_events, computer_exports,
    computer_leases, computer_sessions, computer_settings, content_checks, culture_reader,
    discord_presence, document_service, engine, events, fetch_client, geocoder,
    giphy_client, image_generator, image_refs_allowed, ledger_store, lineage,
    mcp_tool_groups, media_descriptions, media_join, media_objects, memo_store,
    memory_search, metrics, mobility, music_bridge, note_store, observation_store, persons,
    presence_circles, presence_reader, presence_store, profile_index, profile_reader,
    reaction_picker, refresher, relay, reminder_caps, reminders, route_client,
    search_client, social_client, standing_store, steam_client, subagent_desk,
    tool_call_log_store, version_manifest, video_desk, vision_client, voice_presence,
    weather_client
) -> RegistryWiring:
    return RegistryWiring(
        tool_registry=_build_tool_registry(
        allowlist=allowlist, blobs=blobs, browser=browser, browser_manager=browser_manager,
        cfg=cfg, clock=clock, computer=computer, computer_admission=computer_admission,
        computer_context=computer_context, computer_dm_scope_for=computer_dm_scope_for,
        computer_events=computer_events, computer_exports=computer_exports,
        computer_leases=computer_leases, computer_sessions=computer_sessions,
        computer_settings=computer_settings, content_checks=content_checks,
        culture_reader=culture_reader, discord_presence=discord_presence,
        document_service=document_service, engine=engine, events=events,
        fetch_client=fetch_client, geocoder=geocoder, giphy_client=giphy_client,
        image_generator=image_generator, image_refs_allowed=image_refs_allowed,
        ledger_store=ledger_store, lineage=lineage, mcp_tool_groups=mcp_tool_groups,
        media_descriptions=media_descriptions, media_join=media_join,
        media_objects=media_objects, memo_store=memo_store, memory_search=memory_search,
        metrics=metrics, mobility=mobility, music_bridge=music_bridge,
        note_store=note_store, observation_store=observation_store, persons=persons,
        presence_circles=presence_circles, presence_reader=presence_reader,
        presence_store=presence_store, profile_index=profile_index,
        profile_reader=profile_reader, reaction_picker=reaction_picker,
        refresher=refresher, relay=relay, reminder_caps=reminder_caps, reminders=reminders,
        route_client=route_client, search_client=search_client,
        social_client=social_client, standing_store=standing_store,
        steam_client=steam_client, subagent_desk=subagent_desk,
        tool_call_log_store=tool_call_log_store, version_manifest=version_manifest,
        video_desk=video_desk, vision_client=vision_client, voice_presence=voice_presence,
        weather_client=weather_client
        )
    )


def _build_tool_registry(
    profile_index, browser_manager, music_bridge, presence_circles, reaction_picker,
    steam_client, cfg, route_client, computer_admission, computer_sessions, video_desk,
    fetch_client, persons, computer_leases, media_descriptions, media_join, geocoder,
    metrics, note_store, media_objects, computer_dm_scope_for, content_checks,
    culture_reader, subagent_desk, giphy_client, discord_presence, computer,
    presence_store, mcp_tool_groups, image_generator, ledger_store, memo_store,
    memory_search, profile_reader, observation_store, browser, computer_context,
    computer_settings, presence_reader, search_client, engine, events, version_manifest,
    allowlist, voice_presence, relay, weather_client, vision_client, refresher,
    image_refs_allowed, clock, document_service, lineage, blobs, standing_store, reminders,
    reminder_caps, tool_call_log_store, social_client, computer_exports, mobility,
    computer_events
):
    """The native tool surface's collaborator registry."""
    return ToolRegistry(
        display_name=cfg.doctrine.display_name,
        # `[external_files]` (ships off): the source a shared file link is
        # opened through; None answers the open `unavailable`.
        external_files=external_files_source(
            cfg, google=google_credentials(cfg, metrics=metrics)
        ),
        google_account=cfg.google.account,
        subagent=subagent_desk,
        ai_peer=cfg.honcho.ai_peer,
        mcp_tools=mcp_tool_groups,
        # The document tools: ships OFF, and off the
        # registry withholds all three from every turn.
        documents=document_service if cfg.documents.enabled else None,
        # the six event tools (`create_event`/`update_event`/`rsvp_event`/
        # `cancel_event`/`list_events`/`suggest_venue`) answer `unavailable` (R-5) whenever a
        # composition has not wired one; every deployment here has, since it needs nothing this…
        events=events,
        # `[llm.budgets]`, the per-deployment delta over the table. Threaded here for the same
        # reason `reminder_caps` is: `nora.agent` may not import `nora.config`, so the
        # composition root is the conversion point. Names are validated at config load, so…
        budget_overrides=cfg.llm.budgets,
        content_checks=content_checks,
        **_memory_tool_args(
            cfg=cfg, content_checks=content_checks, culture_reader=culture_reader,
            engine=engine, ledger_store=ledger_store, lineage=lineage,
            memo_store=memo_store, memory_search=memory_search,
            profile_index=profile_index, profile_reader=profile_reader,
            standing_store=standing_store,
        ),
        **_record_tool_args(
            cfg=cfg, engine=engine, observation_store=observation_store,
            persons=persons, tool_call_log_store=tool_call_log_store,
        ),
        **_media_tool_args(
            blobs=blobs, cfg=cfg, engine=engine, image_generator=image_generator,
            image_refs_allowed=image_refs_allowed,
            media_descriptions=media_descriptions, media_join=media_join,
            media_objects=media_objects, video_desk=video_desk,
        ),
        **_computer_tool_args(
            browser=browser, browser_manager=browser_manager, cfg=cfg,
            computer=computer, computer_admission=computer_admission,
            computer_context=computer_context,
            computer_dm_scope_for=computer_dm_scope_for,
            computer_events=computer_events, computer_exports=computer_exports,
            computer_leases=computer_leases, computer_sessions=computer_sessions,
            computer_settings=computer_settings,
        ),
        **_room_tool_args(
            discord_presence=discord_presence, geocoder=geocoder,
            mobility=mobility, presence_circles=presence_circles,
            presence_reader=presence_reader, presence_store=presence_store,
            steam_client=steam_client, voice_presence=voice_presence,
            weather_client=weather_client,
        ),
        **_note_tool_args(
            allowlist=allowlist, clock=clock, note_store=note_store,
            reminder_caps=reminder_caps, reminders=reminders,
        ),
        **_lookup_tool_args(
            cfg=cfg, fetch_client=fetch_client, giphy_client=giphy_client,
            music_bridge=music_bridge, reaction_picker=reaction_picker,
            refresher=refresher, relay=relay, route_client=route_client,
            search_client=search_client, social_client=social_client,
            version_manifest=version_manifest, vision_client=vision_client,
        ),
    )


def _memory_tool_args(
    cfg,
    content_checks,
    culture_reader,
    engine,
    ledger_store,
    lineage,
    memo_store,
    memory_search,
    profile_index,
    profile_reader,
    standing_store,
):
    """The memory tier's tools: index search, graph verbs, standing, ledger."""
    return {
        # `[memory.index] read` (ships off): one memory surface: `memory_search` over the index;
        # off, the Honcho verbs as before (DEC-316, `nora.agent.tools.GRAPH_MEMORY_TOOLS`).
        # Built once above: the assembler's pre-fetch holds the same one.
        "memory_search": memory_search,
        # The graph surface's `remember` writes a Profile `told` line
        # (design/unified-memory/45); wired with `memory_search`, never alone.
        "profile": (
            SqlProfileStore(
                engine,
                screen=functools.partial(
                    text_offence, content_checks=content_checks
                ),
                index=profile_index,
            )
            if memory_search is not None
            else None
        ),
        # THE SWITCH (45 §5): while `[memory.profile] read` is on, `character_read`
        # answers from the Profile accounts — the same reader the tag
        # assembler and the fire path hold, so tool and prompt cannot disagree.
        "profile_read": profile_reader,
        "lineage": lineage,
        # The current Rollup build's rows and the Standing head. SELECTs, so no flag. The
        # Standing head is Dreamer-only (`DREAMER_ONLY_TOOLS`); the Rollup read is also a tag
        # turn's (`TAG_READABLE_LOOP_TOOLS`), under the same lineage as the session's.
        "rollup": SqlRollupRead(engine),
        "standing_head": standing_store,
        "ledger": ledger_store,
        # `character_read`: withheld while `[culture]` is off.
        "culture": culture_reader,
        # `memo_read` (DEC-306): the memo store, unit-resolved like the note reads.
        "memos": memo_store,
    }


def _record_tool_args(cfg, engine, observation_store, persons, tool_call_log_store):
    """The Record surface's tools: queries, observations, social items, call log."""
    return {
        # `[record_query] enabled` (ships off): without the read the registry
        # withholds the tool from every turn.
        "record": SqlRecordQuery(engine) if cfg.record_query.enabled else None,
        # `record_query`'s typed `kind` branch (design/platform-observations
        # 05): the same store the bridge writes; `None` only in a harness.
        "observations": observation_store,
        # `social_read`: the social stream's recorded items, a SELECT, so no flag (an empty
        # Record answers `nothing_found`). Its store leaves out every item of a person whose
        # `identity.toml` block sets `social = false`, the same set the poller's store is given.
        "social_items": readable_social_store(engine, persons),
        # `web_fetch`'s own backend ([search] fetch_provider) and the page
        # index its offset/about reads answer from; the store is unconditional
        # like `tool_call_log_store` (one pool, one lifecycle).
        "fetched_pages": SqlFetchedPageStore(engine),
        # The third consumer of `tool_call_log_store` (the first two are
        # EngagementPipeline/FirePath, via `deliver.build`) —
        # `recall_lookups`'s own collaborator.
        "tool_call_log_store": tool_call_log_store,
    }


def _media_tool_args(
    blobs,
    cfg,
    engine,
    image_generator,
    image_refs_allowed,
    media_descriptions,
    media_join,
    media_objects,
    video_desk,
):
    """The media tier's tools: describe geometry, blobs, readings, generators."""
    return {
        "video": video_desk,
        "video_ref_kinds": frozenset(
            kind
            for kind, allowed in (
                ("image", cfg.video_gen.allow_reference_images),
                ("video", cfg.video_gen.allow_reference_video),
                ("audio", cfg.video_gen.allow_reference_audio),
            )
            if allowed
        ),
        "describe_models": _describe_models(cfg),
        "describe_providers": _describe_providers(cfg),
        # `[media.describe.video]`'s chunk geometry: the SAME two numbers the comprehension pass
        # is built with just above, so `media_fetch`'s deliberate look reads a clip exactly as
        # far as the automatic read did.
        "video_chunk_seconds": cfg.media.describe.video.chunk_seconds,
        "video_max_chunks": cfg.media.describe.video.chunk_max,
        "media_join": media_join,
        "describe_max_bytes": _describe_max_bytes(cfg),
        "blobs": blobs,
        "descriptions": media_descriptions,
        "video_readings": SqlVideoReadingStore(engine),
        # The unified media index store — same engine, same lifecycle.
        "media_store": media_objects,
        "image_generator": image_generator,
        "image_refs_allowed": image_refs_allowed,
    }


def _computer_tool_args(
    browser,
    browser_manager,
    cfg,
    computer,
    computer_admission,
    computer_context,
    computer_dm_scope_for,
    computer_events,
    computer_exports,
    computer_leases,
    computer_sessions,
    computer_settings,
):
    """The computer tier's tools: runtime, queues, sessions, exports."""
    return {
        # Nora's computer: `computer` is `None` whenever `[computer] enabled`
        # is false; the audit store is always wired (see where it is built).
        "computer": computer,
        "computer_events": computer_events,
        "computer_settings": computer_settings,
        "computer_sessions": computer_sessions,
        "computer_leases": computer_leases,
        "computer_exports": computer_exports,
        "artifact_link_base": cfg.documents.public_url,
        "browser": browser,
        # The scoped computer ([computer.scoped]): the per-scope FIFO the
        # write-path tools queue through, and the task-owned tab manager
        # `computer_capture` calls. Both `None` in the shared-VM shape.
        "computer_admission": computer_admission,
        "browser_manager": browser_manager,
        # The canonical-DM resolver and the context cache's invalidation —
        # the SAME scope resolution the queue and the context block use, so
        # tools, block and worker jobs land on one VM.
        "computer_dm_scope_for": computer_dm_scope_for,
        "computer_context_invalidate": (
            computer_context.invalidate if computer_context is not None else None
        ),
    }


def _room_tool_args(
    discord_presence,
    geocoder,
    mobility,
    presence_circles,
    presence_reader,
    presence_store,
    steam_client,
    voice_presence,
    weather_client,
):
    """The room-aware tools: presence, where-been, weather, online state."""
    return {
        "weather": weather_client,
        "presence": presence_store,
        "circles": presence_circles,
        # `where_been`'s read seam (DEC-155): the SAME `SqlMobilityStore` the
        # web dashboards read through, built once above on the shared engine —
        # no second store, no new lifecycle (one pool, one lifecycle).
        "mobility": mobility,
        # `room_list`/`room_read` read through the
        # SAME presence reader `<presence>` renders from: one model of which
        # rooms exist, not a second one for the tools.
        "rooms": presence_reader,
        "geocoder": geocoder,
        # `online_now`'s two live-state reads. Either None leaves its arms answering
        # `unavailable` (R-5), never `nothing_found` — the same optional-collaborator shape
        # `mobility` above uses.
        "online": steam_client,
        "voice": voice_presence,
        "discord_presence": discord_presence,
    }


def _note_tool_args(allowlist, clock, note_store, reminder_caps, reminders):
    """The note tier's tools plus the `remind` store and caps."""
    return {
        # The SAME store the tick reads from — two would be two queues, and the
        # rows `remind` wrote would sit in one while the heartbeat watched the
        # other.
        "reminders": reminders,
        # The reminder caps as the DOMAIN value, the SAME `reminder_caps` instance the Cognition
        # session is built with — see that variable's own comment for why they must not be two
        # separately constructed copies. `nora.agent` may not import `nora.config` (import-…
        "reminder_caps": reminder_caps,
        # The same wall clock the tick runs on. `remind` compares `when` against it for three of
        # its six refusals, and a registry clock that disagreed with the tick's would let a row
        # be accepted as "two minutes out" and then be found already due.
        "clock": clock,
        # The note tier's four tools. `None` while `[notes] enabled = false`, so all four answer
        # `unavailable` (R-5) rather than a guessed empty result — the same optional-
        # collaborator shape `events` and `reminders` above use. `note_unit` resolves a room to…
        "notes": note_store,
        # RESOLVER, not a constant — see `build_tools`' own note. This deployment has two units
        # (`ridgeline`, `friends`) that `policy.toml` says are never paired, so a fixed string
        # would hand one room the other's notebook. `None` for a room policy cannot name, which…
        "note_unit": _note_unit_of(allowlist),
        # ONE resolver for every unit-keyed verb, so a verb outside the
        # note tier does not silently depend on the NOTE tier being wired —
        # two absences covering for each other.
        "unit_for": _note_unit_of(allowlist),
    }


def _lookup_tool_args(
    cfg,
    fetch_client,
    giphy_client,
    music_bridge,
    reaction_picker,
    refresher,
    relay,
    route_client,
    search_client,
    social_client,
    version_manifest,
    vision_client,
):
    """The outbound-lookup tools: search, fetch, route, vision, social, media lookups."""
    return {
        "route": route_client,
        "vision": vision_client,
        "search": search_client,
        "fetch": fetch_client,
        "social": social_client,
        # The same router, for the two LinkedIn tools, only when LinkedIn is
        # switched on. The config
        # refuses `[social.linkedin]` without `[social]`, so a router exists.
        "linkedin": social_client if cfg.social.linkedin.enabled else None,
        "relay": relay,
        "refresher": refresher,
        "versions": version_manifest,
        "releases": GithubReleases(),
        "giphy": giphy_client,
        "reaction": reaction_picker,
        "music": music_bridge,
    }


@dataclass(frozen=True)
class SubagentBackendWiring:
    subagent_backend: Any


def backend(
    audit,
    cfg,
    clock,
    compute_backends,
    compute_jobs,
    computer_admission,
    computer_scope_for,
    memory,
    metrics,
    model_client_for,
    persons,
    registry,
    rosters,
    route_model,
    subagent_desk,
    subagents_context,
    tier,
    tool_registry,
) -> SubagentBackendWiring:
    # The sub-agent worker, registered under its kind for the poller below.
    # OFF by default: nothing is built and `compute_backends` stays empty. It needs the tool
    # registry (its closed tool list is built from it), which is why it sits here.
    subagent_backend: SubagentBackend | None = None
    if cfg.subagent.enabled:
        subagent_backend = build_subagent_backend(
            cfg=cfg,
            client=model_client_for("subagent"),
            tier=tier.model_copy(update={"model": route_model("subagent", tier.model)}),
            registry=tool_registry,
            store=audit,
            persons=persons,
            honcho=memory,
            metrics=metrics,
            rosters=rosters,
            self_ids=registry.self_aliases(),
            clock=clock,
            result_caps=TOOL_RESULT_CAPS,
            reasoning_effort=str(
                _reasoning_effort(
                    cfg.subagent.reasoning_effort,
                    source="[subagent] reasoning_effort",
                )
            ),
            jobs=compute_jobs,
            # The scoped computer's FIFO: when it is wired AND the job's room resolves a scope, the
            # job runs AS a `worker` segment — its own durable row the queue can grant, cancel and
            # drain, never a computer caller outside the FIFO it calls into.
            admission=computer_admission,
            scope_for=computer_scope_for,
        )
        compute_backends[SUBAGENT_KIND] = subagent_backend
        if subagent_desk is not None:
            subagent_desk.attach(subagent_backend)
        if subagents_context is not None:
            subagents_context.attach(subagent_backend)
    return SubagentBackendWiring(subagent_backend=subagent_backend)


@dataclass(frozen=True)
class BackendsCheckWiring:
    pass


def backends_check(cfg, compute_backends) -> BackendsCheckWiring:
    if cfg.compute.enabled and not compute_backends:
        log.warning(
            "[compute] enabled = true but no ComputeBackend is registered (the "
            "per-job container backend was retired, design/sandbox-runtime "
            "decision 4; `[subagent] enabled` registers the sub-agent worker, `[video_gen]` "
            "the video backend); the "
            "compute-job poller stays idle. Set enabled = false."
        )
    return BackendsCheckWiring()
