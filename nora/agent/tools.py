"""The model-facing tools (spec C.7). Thin wrappers over injected dependencies.

Since ADR 0037 the tool bodies live in their areas'
adapters (`nora/<area>/adapters/tools*`); this module is
the assembly point — `build_tool_specs` keeps the signature
and surface every caller knows, and the runtime/toolkit
helpers they share sit in `nora.agent.toolkit`,
`nora.agent.tool_deps` and `nora.agent.computer_ctx`.

Docstrings are the LLM-facing schema — they are written for a model, not a developer.
Strands derives BOTH halves of what the model sees from the callable itself
(`strands.tools.decorator.FunctionToolMetadata.extract_metadata`): the text above the
`Args:` block becomes the tool `description`, and each annotated parameter becomes one
`inputSchema` property carrying its `Args:` line as that property's description. So a
parameter that exists here is a value the model is REQUIRED to invent on every call —
which is why none of them describes a conversation (DEC-24): both scoped tools resolve
their conversation from the closure, so asking for it adds nothing and costs one
reply per wrong guess.

feed_honcho is deliberately NOT a tool: the handler owns the single post-reply feed.

`conversation_type` and `context_window` are injected but unread. They stay on the
builder because `ToolRegistry` passes the same set to every tool it will ever
register, not because anything here uses them. `metrics` is injected too, and is read
by EVERY tool, because the counter cannot live anywhere else: Honcho answers
200-with-an-empty-string for a peer it has never heard of, so "asked and nothing
known" and "could not ask" are indistinguishable at the transport and only the call
site knows which it got. See `_log_call` for the other half of the same argument —
the one an operator greps rather than alerts on.

`room` and `question` are what the three room-scoped tools close over so the model
never has to supply them: `room` is this conversation's membership (see
`nora.agent.room` — scope is enforced where the answer is assembled, never at
egress), and `question` is the tagging message, which `about` uses as its search
query. Both are values Nora already holds, so under DEC-24 neither may appear in a
schema.

`build_tool_specs` builds each tool as a plain, undecorated `ToolSpec`
(`nora.agent.ports`) closed over this tag's dependencies — no `strands` import
here at all. `build_tools` is the thin, Strands-flavoured wrapper over it
(`nora.agent.ports.adapt` per spec), kept so every existing caller (the engagement
pipeline, `tests/test_tools.py`) that wants ready-to-use Strands tool objects
still can, without knowing `ToolSpec` exists. `NATIVE_TOOL_BUDGETS` is the single
source both draw budgets from — `nora.agent.registry.ToolRegistry.budgets()`
reads the very same mapping, so the number `ReplyGuard` enforces and the number
each tool is declared to cost can never drift apart (DEC-10)."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from nora.agent.catalog.surface import _apply_link_owners, on_memory_surface
from nora.agent.lineage import LineagePolicy
from nora.agent.links import LinkCarry
from nora.agent.memory_search import MemorySearch
from nora.agent.ports import ToolSpec, adapt
from nora.agent.reaction import ReactionPicker
from nora.agent.relay import Relay
from nora.agent.room import Room
from nora.agent.runtime_tools import build as _runtime_build
from nora.agent.subagent_desk import SubagentDesk
from nora.agent.subagent_progress import SubagentProgress
from nora.agent.surface import ToolShelf
from nora.agent.tool_deps import ToolDeps
from nora.agent.toolkit.ports import (
    DocumentPublisher,
    EventsCollaborator,
    NoteCollaborator,
    RecordQuerySource,
    RollupReadSource,
    RoomDirectory,
    SocialItemsSource,
    StandingHeadSource,
)
from nora.agent.turn_recording import in_room
from nora.agent.video_desk import VideoDesk
from nora.domain.computer import ComputerToolSettings
from nora.domain.computer_events import ComputerEventStore
from nora.domain.computer_exports import ComputerExportStore
from nora.domain.computer_leases import ComputerLeases
from nora.domain.computer_sessions import (
    ComputerSessionStore,
)
from nora.domain.media_join import MediaJoin
from nora.domain.memo import MemoStore
from nora.domain.models import (
    TranscriptRow,
)
from nora.domain.ports import (
    BrowserRunner,
    CultureReader,
    DiscordPresenceSource,
    ExternalFilesSource,
    GifSource,
    ImageGenerator,
    MediaObjectStore,
    MusicToolBridge,
    SandboxRuntime,
    SteamPresenceProvider,
    VoicePresenceSource,
)
from nora.domain.profile import (
    ProfileWriter,
)
from nora.domain.reminders import (
    ReminderCaps,
    ReminderStore,
)
from nora.domain.standing_ledger import StandingLedger
from nora.domain.tool_call_log import ToolCallLogStore
from nora.infra.vision.preprocess import (
    VIDEO_CHUNK_SECONDS,
    VIDEO_MAX_CHUNKS,
)
from nora.language.profile_render import ProfileHeads

_AREAS = (
    "nora.location",
    "nora.people",
    "nora.conversation",
    "nora.memory",
    "nora.media",
    "nora.documents",
    "nora.social",
    "nora.voice",
    "nora.agenda",
    "nora.computer",
    "nora.subagents",
)
_ORDER = [
    "now",
    "calculate",
    "transit",
    "route",
    "place_info",
    "saved_places",
    "where_is",
    "reverse_geocode",
    "where_been",
    "routine_read",
    "online_now",
    "who_said",
    "load_transcript",
    "room_list",
    "room_read",
    "record_query",
    "poll_list",
    "poll_read",
    "room_state_read",
    "rollup_read",
    "standing_read",
    "character_read",
    "social_read",
    "group_members",
    "lookup_person",
    "about",
    "ask_memory",
    "search_memory",
    "memory_search",
    "memory_read",
    "recall_lookups",
    "media_fetch",
    "file_fetch",
    "generate_image",
    "video_create",
    "media_search",
    "media_send",
    "document_publish",
    "document_read",
    "document_list",
    "document_revise",
    "document_set_status",
    "web_search",
    "web_fetch",
    "x_read",
    "x_search",
    "linkedin_read",
    "linkedin_search",
    "instagram_read",
    "instagram_search",
    "weather",
    "parking_status",
    "find_parking",
    "play_music",
    "skip_music",
    "top_music",
    "stop_music",
    "music_status",
    "set_music_volume",
    "remind",
    "list_reminders",
    "cancel_reminder",
    "event_search",
    "event_propose",
    "event_revise",
    "event_rsvp",
    "event_set_status",
    "suggest_venue",
    "search_memory_workspace",
    "note_read",
    "note_search",
    "memo_read",
    "note_create",
    "note_revise",
    "remember",
    "profile_dispute",
    "computer_run",
    "computer_read",
    "computer_write",
    "computer_edit",
    "computer_export",
    "computer_import",
    "computer_session_list",
    "computer_browse",
    "computer_capture",
    "computer_cancel",
    "version_list",
    "version_read",
    "send_reply",
    "reaction_send",
    "send_to",
    "search_gifs",
    "send_gif",
    "subagent_create",
    "subagent_list",
    "subagent_read",
    "subagent_cancel",
    "checkpoint_update",
    "load_tools",
]
_ORDER_SET = frozenset(_ORDER)


def build_tool_specs(
    *,
    store,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    metrics,
    room: Room,
    transit_source,
    route_source=None,
    vision_source=None,
    blob_store=None,
    vision_cache=None,
    video_readings=None,
    describe_models: Mapping[str, str] | None = None,
    describe_providers: Mapping[str, str] | None = None,
    describe_max_bytes: Mapping[str, int] | None = None,
    # `[media.describe.video]`'s chunk geometry, threaded in as
    # VALUES like every other describer knob, so the deliberate read
    # (`media_fetch`) and the automatic one (the comprehension pass) use the
    # same ceiling and cannot disagree about how much of a clip was seen.
    video_chunk_seconds: int = VIDEO_CHUNK_SECONDS,
    video_max_chunks: int = VIDEO_MAX_CHUNKS,
    # The SHARED media join. `load_transcript` is a transcript builder and the
    # only one inside a tool: it hands the model
    # rows the prefill has already rendered WITH their comprehensions, so it
    # must render them the same way or one prompt carries two answers about
    # one photo.
    media_join: MediaJoin | None = None,
    search_source=None,
    # `web_fetch`'s own backend when `[search] fetch_provider` splits it;
    # None means `search_source` serves both verbs.
    fetch_source=None,
    # `web_fetch`'s page index: a live row serves `offset`/`about` reads from
    # the sealed copy. None (offline, eval) means no persistence.
    fetched_pages=None,
    social_source=None,
    linkedin_source=None,
    weather_source=None,
    parking_source=None,
    street_parking_source=None,
    presence_source=None,
    presence_circles=(),
    location_trusted: bool = False,
    mobility=None,
    refresher=None,
    geocoder=None,
    question: str = "",
    context_window: int = 40,
    transcript: tuple[TranscriptRow, ...] | None = None,
    lookup_window: tuple[TranscriptRow, ...] | None = None,
    ai_peer: str = "nora",
    display_name: str = "",
    fire_turn: bool = False,
    # THIS turn is a relay: another room's words arrived through `Relay`, so `send_to` is
    # refused here as well as left off the surface (8765_44837).
    relay_turn: bool = False,
    dream_adaptor: str = "",
    # The Dreamer adaptor's tool ceiling (`surface(grant=...)`), kept here
    # only so a queued computer segment's durable origin can record what it
    # was admitted under; the deferred dispatch re-applies it through
    # `ToolScope`. Never read by the live path — `surface` enforces it.
    grant: frozenset[str] | None = None,
    # THE THIRD provenance: the id of the background job this set
    # is built for, `""` for every tag, fire turn and Dreamer pass. Non-empty makes the
    # computer and document actor `subagent`, the audit path `subagent`, and the
    # computer lease its own (`job:<id>`), so a tag turn holding the room's lease never
    # blocks the worker. It changes nothing else: WHICH tools the worker holds is the
    # grant `nora.agent.subagent_tools` passes to `ToolRegistry.surface`.
    subagent_job: str = "",
    # The running worker's progress record: where `checkpoint_update`
    # writes. `None` for every occasion that is not a worker, where the tool is not offered.
    checkpoint_sink: SubagentProgress | None = None,
    reminders: ReminderStore | None = None,
    reminder_caps: ReminderCaps | None = None,
    clock: Callable[[], datetime] | None = None,
    links: LinkCarry | None = None,
    versions=None,
    releases=None,
    giphy_source: GifSource | None = None,
    reaction_picker: ReactionPicker | None = None,
    tool_call_log_store: ToolCallLogStore | None = None,
    # Nora's computer (DEC-243): the runtime, its
    # audit store and its bounds. `computer=None` is `[computer]` off (and
    # every offline/eval composition): each computer tool answers
    # `unavailable`. A runtime without `computer_events` is refused the same
    # way, so no call runs unaudited.
    computer: SandboxRuntime | None = None,
    computer_events: ComputerEventStore | None = None,
    computer_settings: ComputerToolSettings | None = None,
    # Sessions, derived by code: every
    # computer call continues the room's open session or opens one. `None`
    # (offline, eval) runs every call without a session. Leases: the
    # process's one `ComputerLeases`; `None` locks nothing.
    computer_sessions: ComputerSessionStore | None = None,
    computer_leases: ComputerLeases | None = None,
    # `computer_export`: its record of what left
    # the computer. `None` answers the destinations that need it
    # `unavailable`.
    computer_exports: ComputerExportStore | None = None,
    # `computer_browse`: the sub-agent that
    # drives the room's browser. `None` (`[computer.browse]` off) answers the
    # tool `unavailable`; the registry withholds it then.
    browser: BrowserRunner | None = None,
    # The scoped-computer runtime: `ComputerAdmission` (per-scope FIFO,
    # `nora.app.computer_queue`) and `ScopeBrowserManager` (task-owned tabs,
    # `nora.infra.computer.manager`). `None` — the shared-VM shape — runs
    # every computer call synchronously exactly as before and answers
    # `computer_capture`/`computer_cancel` `unavailable`.
    computer_admission: Any = None,
    browser_manager: Any = None,
    # The canonical-DM scope resolver (`design/private-dm/05-cross-platform.md`):
    # `(platform, conversation_id)` → the DM scope id (the `(person, anchor
    # unit)` binding), hashed here to the workspace key. `None` — DM admission
    # is not wired — leaves DM-turn computer calls `unavailable`; never a
    # group unit, never a person-only key, never a shared fallback.
    computer_dm_scope_for: Callable[[str, str], str | None] | None = None,
    # Called with the scope after a mutating segment lands, so the context
    # block's cache cannot serve the turn a stale workspace/browser view.
    computer_context_invalidate: Callable[[str], None] | None = None,
    # `computer_export(to="link")`'s portal origin (ADR 0023), `[documents]
    # public_url`: a link is `<origin>/artifacts/<artifact_id>`. `""` answers a
    # link `unavailable`.
    artifact_link_base: str = "",
    relay: Relay | None = None,
    events: EventsCollaborator | None = None,
    notes: NoteCollaborator | None = None,
    note_unit: Callable[[str, str], str | None] | None = None,
    memos: MemoStore | None = None,
    unit_for: Callable[[str, str], str | None] | None = None,
    supplied_unit: str | None = None,
    music: MusicToolBridge | None = None,
    online_source: SteamPresenceProvider | None = None,
    voice_source: VoicePresenceSource | None = None,
    presence: DiscordPresenceSource | None = None,
    image_generator: ImageGenerator | None = None,
    image_refs_allowed: bool = False,
    media_store: MediaObjectStore | None = None,
    # `document_publish`/`document_read`. `None` answers
    # `unavailable`; the registry withholds both tools before that matters.
    documents: DocumentPublisher | None = None,
    shelf: ToolShelf | None = None,
    # WHAT A DREAMER PASS LOADED. Called
    # with the rows `load_transcript` and `room_read` return, so the pass's
    # grounding (`Shown`) can admit a citation of a row the model read through
    # a tool, not only one rendered in its prompt. Set only by
    # `RegistryToolSource.build` for an adaptor that asks for it, and then the
    # two tools also carry each row's id (`n`), the number a batch line
    # carries, so a loaded row can be cited at all. `None` — every tag turn —
    # is the unchanged result: no ids, nothing recorded.
    on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
    # The room tools' one collaborator: the
    # presence reader, seen through `RoomDirectory`. `None` (offline, eval,
    # every test that does not care) makes `room_list` and `room_read` answer
    # `unavailable`, never an empty list of rooms.
    rooms: RoomDirectory | None = None,
    # `record_query`'s two collaborators:
    # the Record read, and the process's one `LineagePolicy` (who each room of
    # the policy is, `nora.app.assembler.lineage_policy`). `None` for either
    # (offline, eval, every test that does not care) answers `unavailable`.
    record: RecordQuerySource | None = None,
    # The typed platform-observation Record (`ObservationStore`), for
    # `record_query`'s `kind` branch: `None` answers it `unavailable`,
    # never an empty list that reads as «no such objects».
    observations: Any = None,
    lineage: LineagePolicy | None = None,
    # `rollup_read`'s Rollup read and `standing_read`'s Standing head.
    # `None` (offline, eval, every tag
    # path) answers `unavailable`; both tools are Dreamer-only besides.
    rollup: RollupReadSource | None = None,
    standing_head: StandingHeadSource | None = None,
    ledger: StandingLedger | None = None,
    culture: CultureReader | None = None,
    # `social_read`'s read of the social stream's Record. `None` answers
    # `unavailable`; the registry withholds the tool before that matters.
    social_items: SocialItemsSource | None = None,
    # The sub-agent desk: `subagent_create`/`list`/`read`/`cancel`. `None`
    # (`[subagent] enabled = false`, offline, eval) answers `unavailable`; the registry
    # withholds the four before that matters, and the scope admits them to a tag turn only.
    subagent: SubagentDesk | None = None,
    # `video_create`'s desk and the reference kinds the `[video_gen]
    # gates allow (`allow_reference_images` / `_video` / `_audio`: `image`, `video`, `audio`).
    # `None` answers `unavailable`; the registry withholds the tool before that matters.
    video: VideoDesk | None = None,
    video_ref_kinds: frozenset[str] = frozenset(),
    # `file_fetch`'s source: the process's one
    # read-only `ExternalFilesSource`, from `ToolRegistry`. `None`
    # (`[external_files]` off, a credential missing, offline, eval) answers
    # `unavailable`; the tool is still offered, so a claimed link has
    # somewhere to go and «I can't open that» is said, not guessed around.
    external_files: ExternalFilesSource | None = None,
    # `[google] account`: Nora's own Google
    # mailbox, named in `file_fetch`/`computer_import`'s NOT_SHARED note so the
    # owner knows who to share with. "" → the note says "Nora's Google account".
    google_account: str = "",
    # `[memory.index] read`: the retrieval
    # service `ask_memory` answers from instead of Honcho. `None` (the
    # shipped default, offline, eval) is the Honcho path.
    memory_retrieval: MemorySearch | None = None,
    all_memory_surfaces: bool = False,
    # The Profile write (design/unified-memory/45): the graph surface's
    # `remember` writes a `told` line through it. `None` answers `unavailable`.
    profile: ProfileWriter | None = None,
    # THE SWITCH (45 §5): wired only while `[memory.profile] read` is on beside
    # `[memory.profile] write`, and `character_read` then answers from the Profile
    # accounts instead of the culture store — the same accounts the prompt's
    # `<profile>` blocks render, so the tool and the prompt cannot disagree.
    profile_read: ProfileHeads | None = None,
    content_checks: bool = True,
) -> tuple[ToolSpec, ...]:
    """Every native tool, closed over one turn's collaborators.

    `fire_turn` is this turn's PROVENANCE: True when the heartbeat's fire path
    built it (`nora.app.fire.FirePath`), False for a tag. A tool cannot
    otherwise tell the two apart — the synthetic message a fire turn is built
    from is a well-formed `NormalizedMessage` by construction, which is exactly
    what makes it indistinguishable.

    It defaults False and is threaded from `ToolRegistry.build`. The consumer
    is `remind`, which refuses inside a fire turn — a reminder that can set a
    reminder is a loop with no bound, and the turn it would fire into is the
    one it is already in.

    `dream_adaptor` is the SECOND provenance, the same shape (DEC-162): the
    Dreamer adaptor's NAME when `nora.agent.dreamer.RegistryToolSource` built
    this set, `""` otherwise. `remind` is again the consumer, and under it the
    closure stamps what the model cannot: `kind="proactive"`, the `ai_peer` as
    owner, the home room as destination, and a payload naming the note the row
    is about — with the model's text screened by `dream_screen` before
    `create`, because the tool call is the one sink a Dreamer write has.

    `reminders`/`reminder_caps`/`clock` come from `ToolRegistry`, which owns all
    three for the life of the process. `reminders=None` is the honest
    "not configured" state (R-5) the offline and eval compositions ship in, not
    an error; the caps and the clock fall back to `ReminderCaps()` and
    `utc_now`, so a caller that never sets a reminder needs neither.

    `versions` is the immutable process-lifetime build stamp and shipped
    release history. Both version tools read this same snapshot.

    `tool_call_log_store` is `recall_lookups`'s own collaborator, wired the
    identical way: `None` in the offline/eval compositions and any composition
    that has not wired the tool-recall log, which is what makes
    `recall_lookups` answer `unavailable` (R-5) rather than crash.

    `mobility` is `where_been`'s own collaborator (DEC-155): the
    derived-presence read the tool answers from — `SqlMobilityStore`, the SAME
    instance the composition root builds for the web dashboards (one pool, one
    lifecycle; the tool reads through the store only, never `nora.analytics`
    internals). Wired the honest way `reminders`/`versions` are: `None` in the
    offline/eval compositions and any composition that has not built the
    substrate, which is what makes `where_been` answer `unavailable` (R-5),
    never `nothing_found` — an unwired store is «I could not check», never «no
    derived pattern». The disclosure side (`room`, `presence_circles`,
    `location_trusted`) is the SAME seam `where_is` speaks through.

    `notes`/`note_unit` are the note tier's pair, and they are ONE piece of
    wiring in two arguments rather than two independent ones: a note is keyed on
    the unit, so a store with no unit to read is as unusable
    as no store, and both states answer `unavailable` — never `nothing_found`,
    which would report an unconsulted notebook as an empty one.

    **`note_unit` RESOLVES a room to its unit; it is not a constant.** A
    deployment with more than one unit, deliberately never paired in
    `policy.toml`, would otherwise hand one room the OTHER's notebook — a
    cross-unit read and write, the boundary the note scope exists to hold and
    DEC-105/R-49 hold for `send_to`. So it takes `(platform, conversation_id)`
    and answers `None` for a room policy cannot name, which is `unavailable`
    rather than a guess.

    `transcript` is the window the prefill SHOWED (`TagContext.transcript`,
    what `nora.agent.prefill._transcript_block` numbered `#1..#N`): `send_reply`'s
    `reply_to` and a note's `[#n]` index exactly these rows and nothing deeper.
    `lookup_window` is the deeper read (`TagContext.lookup_transcript`, when
    `[store] prompt_window` shows fewer rows than were read) and is `who_said`'s
    alone, DEC-146's whole world, falling back to `transcript`. Both are
    passed in rather than re-read. That is
    comprehend-once — the window is interpreted at assembly and every consumer
    computes on that one fixed model, so the tool cannot disagree with the block
    the model is looking at. Re-querying the store for it would be a second
    interpretation of the same messages and would drift the first time the
    `limit` or the naming pass changed on one side.

    `None` is the honest "no window was wired" state every other optional
    collaborator above uses, and it answers `unavailable` — never
    `nothing_found`, which would report an unread window as an empty one. An
    EMPTY tuple is the same answer for the same reason: `EngagementContext.
    _recent` degrades a FAILED store read to `()` (a thinner reply beats no
    reply), so at this seam "the read broke" and "the room has said nothing"
    are one value, and R-27 resolves that ambiguity towards `unavailable`.

    `music` is `MusicToolBridge` (`nora.domain.ports`) — `None` in every
    composition that has not wired `DiscordMusicToolBridge`, the same honest
    wiring-gap shape every other optional collaborator above uses. All four
    music tools ALSO refuse on `room.platform != "discord"` before ever
    touching it, so a non-Discord room never reaches this collaborator at all.

    `online_source`/`voice_source`/`presence` are `online_now`'s live-state
    reads: the Steam summaries client, the Discord voice-membership source
    (the narrow `voice` sub-arm), and the Discord per-person PRESENCE source
    (the `online` arm's Discord half — status + rich presence; presence, not
    voice membership, is what "Discord online" means). Each is `None` in every
    composition that has not wired it — the same honest wiring-gap shape as
    `mobility` above: an arm whose source is missing answers `unavailable`
    (R-5), never `nothing_found`, and never crashes; the `online` arm
    answers from whichever half IS wired and says so
    (`steam_wired`/`discord_wired`/`voice_wired`).

    `supplied_unit` is the Dreamer's half of the key path: a UNIT-keyed
    adaptor's key IS the unit, so the runtime SUPPLIES it here rather than
    letting `_unit()` derive it from the room — the Dreamer's synthetic `Room`
    has no platform and carries the unit in its conversation slot, so the
    derived path answers `None` and every unit-scoped verb would read
    `unavailable` forever while looking wired. When truthy it WINS over the
    resolver; `None` (every tag-path caller) leaves `_unit()` deriving it from
    the room.
    """
    deps = ToolDeps(
        store=store,
        conversation_id=conversation_id,
        conversation_type=conversation_type,
        persons=persons,
        honcho=honcho,
        deliverer=deliverer,
        metrics=metrics,
        room=room,
        transit_source=transit_source,
        route_source=route_source,
        vision_source=vision_source,
        blob_store=blob_store,
        vision_cache=vision_cache,
        video_readings=video_readings,
        describe_models=describe_models,
        describe_providers=describe_providers,
        describe_max_bytes=describe_max_bytes,
        video_chunk_seconds=video_chunk_seconds,
        video_max_chunks=video_max_chunks,
        media_join=media_join,
        search_source=search_source,
        fetch_source=fetch_source,
        fetched_pages=fetched_pages,
        social_source=social_source,
        linkedin_source=linkedin_source,
        weather_source=weather_source,
        parking_source=parking_source,
        street_parking_source=street_parking_source,
        presence_source=presence_source,
        presence_circles=presence_circles,
        location_trusted=location_trusted,
        mobility=mobility,
        refresher=refresher,
        geocoder=geocoder,
        question=question,
        context_window=context_window,
        transcript=transcript,
        lookup_window=lookup_window,
        ai_peer=ai_peer,
        display_name=display_name,
        fire_turn=fire_turn,
        relay_turn=relay_turn,
        dream_adaptor=dream_adaptor,
        grant=grant,
        subagent_job=subagent_job,
        checkpoint_sink=checkpoint_sink,
        reminders=reminders,
        reminder_caps=reminder_caps,
        clock=clock,
        links=links,
        versions=versions,
        releases=releases,
        giphy_source=giphy_source,
        reaction_picker=reaction_picker,
        tool_call_log_store=tool_call_log_store,
        computer=computer,
        computer_events=computer_events,
        computer_settings=computer_settings,
        computer_sessions=computer_sessions,
        computer_leases=computer_leases,
        computer_exports=computer_exports,
        browser=browser,
        computer_admission=computer_admission,
        browser_manager=browser_manager,
        computer_dm_scope_for=computer_dm_scope_for,
        computer_context_invalidate=computer_context_invalidate,
        artifact_link_base=artifact_link_base,
        relay=relay,
        events=events,
        notes=notes,
        note_unit=note_unit,
        memos=memos,
        unit_for=unit_for,
        supplied_unit=supplied_unit,
        music=music,
        online_source=online_source,
        voice_source=voice_source,
        presence=presence,
        image_generator=image_generator,
        image_refs_allowed=image_refs_allowed,
        media_store=media_store,
        documents=documents,
        shelf=shelf,
        on_rows=on_rows,
        rooms=rooms,
        record=record,
        observations=observations,
        lineage=lineage,
        rollup=rollup,
        standing_head=standing_head,
        ledger=ledger,
        culture=culture,
        social_items=social_items,
        subagent=subagent,
        video=video,
        video_ref_kinds=video_ref_kinds,
        external_files=external_files,
        google_account=google_account,
        memory_retrieval=memory_retrieval,
        all_memory_surfaces=all_memory_surfaces,
        profile=profile,
        profile_read=profile_read,
        content_checks=content_checks,
    )
    built = {}
    for spec in _runtime_build(deps):
        built[spec.name] = spec
    for area in _AREAS:
        _wire = importlib.import_module(area).wire
        for spec in _wire.tool_specs(deps):
            built[spec.name] = spec
    if set(built) != _ORDER_SET:
        raise RuntimeError(
            f"tool surface drifted: missing {sorted(_ORDER_SET - set(built))} "
            f"extra {sorted(set(built) - _ORDER_SET)}"
        )
    specs = tuple(built[n] for n in _ORDER)
    return _apply_link_owners(
        tuple(
            (
                in_room(
                    spec, platform=deps.room.platform, conversation_id=deps.bound_conversation_id
                )
                if deps.room is not None
                else spec
            )
            for spec in specs
            if on_memory_surface(
                spec.name,
                graph=deps.memory_retrieval is not None,
                everything=deps.all_memory_surfaces,
            )
        ),
        metrics=deps.metrics,
        unwired=deps.unwired_owners,
    )


def build_tools(
    *,
    store,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    metrics,
    room: Room,
    transit_source,
    route_source=None,
    vision_source=None,
    blob_store=None,
    vision_cache=None,
    video_readings=None,
    describe_models: Mapping[str, str] | None = None,
    describe_providers: Mapping[str, str] | None = None,
    describe_max_bytes: Mapping[str, int] | None = None,
    # `[media.describe.video]`'s chunk geometry, threaded in as
    # VALUES like every other describer knob, so the deliberate read
    # (`media_fetch`) and the automatic one (the comprehension pass) use the
    # same ceiling and cannot disagree about how much of a clip was seen.
    video_chunk_seconds: int = VIDEO_CHUNK_SECONDS,
    video_max_chunks: int = VIDEO_MAX_CHUNKS,
    # The SHARED media join. `load_transcript` is the only transcript
    # builder inside a tool: it hands the model
    # rows the prefill has already rendered WITH their comprehensions, so it
    # must render them the same way or one prompt carries two answers about
    # one photo.
    media_join: MediaJoin | None = None,
    search_source=None,
    fetch_source=None,
    fetched_pages=None,
    social_source=None,
    linkedin_source=None,
    weather_source=None,
    parking_source=None,
    street_parking_source=None,
    presence_source=None,
    presence_circles=(),
    location_trusted: bool = False,
    mobility=None,
    refresher=None,
    geocoder=None,
    giphy_source: GifSource | None = None,
    reaction_picker: ReactionPicker | None = None,
    question: str = "",
    context_window: int = 40,
    transcript: tuple[TranscriptRow, ...] | None = None,
    lookup_window: tuple[TranscriptRow, ...] | None = None,
    ai_peer: str = "nora",
    display_name: str = "",
    links: LinkCarry | None = None,
    notes: NoteCollaborator | None = None,
    note_unit: Callable[[str, str], str | None] | None = None,
    memos: MemoStore | None = None,
    unit_for: Callable[[str, str], str | None] | None = None,
    supplied_unit: str | None = None,
    # Forwarded, not defaulted away: a wrapper that dropped it would build
    # the tools for a Dreamer caller as if for a tag turn.
    dream_adaptor: str = "",
    image_generator: ImageGenerator | None = None,
    image_refs_allowed: bool = False,
    media_store: MediaObjectStore | None = None,
    documents: DocumentPublisher | None = None,
    online_source: SteamPresenceProvider | None = None,
    voice_source: VoicePresenceSource | None = None,
    presence: DiscordPresenceSource | None = None,
    rooms: RoomDirectory | None = None,
    clock: Callable[[], datetime] | None = None,
    on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
    record: RecordQuerySource | None = None,
    lineage: LineagePolicy | None = None,
    rollup: RollupReadSource | None = None,
    standing_head: StandingHeadSource | None = None,
    ledger: StandingLedger | None = None,
    culture: CultureReader | None = None,
    # The typed platform-observation Record: `record_query`'s `kind` branch,
    # `poll_list`/`poll_read`/`room_state_read` and the source events a
    # transcript read carries. `None` answers those `unavailable`.
    observations: Any = None,
    # `social_read`'s read of the social stream's Record. `None` answers
    # `unavailable`; the registry withholds the tool before that matters.
    social_items: SocialItemsSource | None = None,
    subagent: SubagentDesk | None = None,
    video: VideoDesk | None = None,
    video_ref_kinds: frozenset[str] = frozenset(),
    memory_retrieval: MemorySearch | None = None,
    profile: ProfileWriter | None = None,
    profile_read: ProfileHeads | None = None,
    relay: Relay | None = None,
    relay_turn: bool = False,
    content_checks: bool = True,
) -> list:
    """The Strands-ready form of `build_tool_specs` — `adapt()` each spec so
    the docstrings above become the tool's model-facing description/schema
    (empty `description`/`schema` on every `ToolSpec` here means "derive from
    the callable", see `nora.agent.ports.adapt`)."""
    specs = build_tool_specs(
        store=store, conversation_id=conversation_id, conversation_type=conversation_type,
        persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics, room=room,
        transit_source=transit_source, route_source=route_source, vision_source=vision_source,
        blob_store=blob_store, vision_cache=vision_cache, video_readings=video_readings,
        describe_models=describe_models, describe_providers=describe_providers,
        search_source=search_source, weather_source=weather_source, fetch_source=fetch_source,
        fetched_pages=fetched_pages, parking_source=parking_source,
        street_parking_source=street_parking_source, social_source=social_source,
        linkedin_source=linkedin_source, presence_source=presence_source,
        presence_circles=presence_circles, location_trusted=location_trusted, mobility=mobility,
        refresher=refresher, geocoder=geocoder, question=question, context_window=context_window,
        transcript=transcript, lookup_window=lookup_window, ai_peer=ai_peer,
        display_name=display_name, links=links, giphy_source=giphy_source,
        reaction_picker=reaction_picker, notes=notes, note_unit=note_unit, memos=memos,
        unit_for=unit_for, supplied_unit=supplied_unit, dream_adaptor=dream_adaptor,
        image_generator=image_generator, image_refs_allowed=image_refs_allowed,
        media_store=media_store, documents=documents, online_source=online_source,
        voice_source=voice_source, presence=presence, on_rows=on_rows,
        # FORWARDED: `build_tools` declares every parameter `build_tool_specs`
        # does and hands them on by name, so one accepted and dropped here is a
        # caller configuring something that silently keeps its default.
        # `tests/architecture/test_tool_builders_forward_everything.py` compares
        # this call's keywords against `build_tools`' own parameters, so a
        # dropped one is red.
        describe_max_bytes=describe_max_bytes, video_chunk_seconds=video_chunk_seconds,
        video_max_chunks=video_max_chunks, media_join=media_join, rooms=rooms, clock=clock,
        record=record, lineage=lineage, rollup=rollup, standing_head=standing_head, ledger=ledger,
        culture=culture, observations=observations, social_items=social_items, subagent=subagent,
        video=video, video_ref_kinds=video_ref_kinds, memory_retrieval=memory_retrieval,
        profile=profile, profile_read=profile_read, relay=relay, relay_turn=relay_turn,
        content_checks=content_checks
    )
    return [adapt(spec) for spec in specs]
