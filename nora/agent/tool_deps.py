"""The shared dependencies every tool area builds from."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nora.agent.catalog.groups import LOAD_TRANSCRIPT_MAX
from nora.agent.computer_ctx import build_computer_ctx
from nora.agent.lineage import LineagePolicy
from nora.agent.links import LinkCarry
from nora.agent.memory_search import MemorySearch
from nora.agent.reaction import ReactionPicker
from nora.agent.relay import Relay
from nora.agent.room import Room
from nora.agent.subagent_desk import SubagentDesk
from nora.agent.subagent_progress import SubagentProgress
from nora.agent.surface import ToolShelf
from nora.agent.toolkit.clock import utc_now
from nora.agent.toolkit.files import (
    _FILE_LOOK_NOTES,
    _FILE_STATUS_NOTES,
    _LOOKABLE,
    _TEXT_KINDS,
    _file_note,
)
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
from nora.agent.toolkit.results import _record, _record_media
from nora.agent.toolkit.rooms import _known_names
from nora.agent.video_desk import VideoDesk
from nora.domain.cognition_session import COGNITION_ADAPTOR
from nora.domain.computer import (
    ComputerToolSettings,
)
from nora.domain.computer_events import (
    DREAMER_ACTOR_PREFIX,
    ComputerEventStore,
)
from nora.domain.computer_exports import (
    ComputerExportStore,
)
from nora.domain.computer_leases import ComputerLeases
from nora.domain.computer_sessions import (
    ComputerSessionStore,
)
from nora.domain.external_files import (
    NOT_ACCEPTED,
    ExternalFileError,
    ExternalFileRef,
    FileBytes,
    resolve_file_code,
)
from nora.domain.lineage import (
    LiveRoom,
    RenderRoom,
    RoomFacts,
)
from nora.domain.media import (
    HANDLE_CHARS,
    GifCandidate,
    MediaObject,
    born_in_room,
)
from nora.domain.media_join import MediaJoin
from nora.domain.memo import MemoStore
from nora.domain.mobility_forecast import (
    place_words,
)
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
from nora.infra.social.readings import READ_CONCURRENCY as _INSTAGRAM_READ_CONCURRENCY
from nora.infra.vision.preprocess import (
    VIDEO_CHUNK_SECONDS,
    VIDEO_MAX_CHUNKS,
)
from nora.language.profile_render import ProfileHeads

log = logging.getLogger("nora.tools")


@dataclass(frozen=True)
class ToolDeps:
    store: Any
    conversation_id: str
    conversation_type: str
    persons: Any
    honcho: Any
    deliverer: Any
    metrics: Any
    room: Room
    transit_source: Any
    route_source: Any = None
    vision_source: Any = None
    blob_store: Any = None
    vision_cache: Any = None
    video_readings: Any = None
    describe_models: Mapping[str, str] | None = None
    describe_providers: Mapping[str, str] | None = None
    describe_max_bytes: Mapping[str, int] | None = None
    video_chunk_seconds: int = VIDEO_CHUNK_SECONDS
    video_max_chunks: int = VIDEO_MAX_CHUNKS
    media_join: MediaJoin | None = None
    search_source: Any = None
    fetch_source: Any = None
    fetched_pages: Any = None
    social_source: Any = None
    linkedin_source: Any = None
    weather_source: Any = None
    parking_source: Any = None
    street_parking_source: Any = None
    presence_source: Any = None
    presence_circles: Any = ()
    location_trusted: bool = False
    mobility: Any = None
    refresher: Any = None
    geocoder: Any = None
    question: str = ""
    context_window: int = 40
    transcript: tuple[TranscriptRow, ...] | None = None
    lookup_window: tuple[TranscriptRow, ...] | None = None
    ai_peer: str = "nora"
    display_name: str = ""
    fire_turn: bool = False
    relay_turn: bool = False
    dream_adaptor: str = ""
    grant: frozenset[str] | None = None
    subagent_job: str = ""
    checkpoint_sink: SubagentProgress | None = None
    reminders: ReminderStore | None = None
    reminder_caps: ReminderCaps | None = None
    clock: Callable[[], datetime] | None = None
    links: LinkCarry | None = None
    versions: Any = None
    releases: Any = None
    giphy_source: GifSource | None = None
    reaction_picker: ReactionPicker | None = None
    tool_call_log_store: ToolCallLogStore | None = None
    computer: SandboxRuntime | None = None
    computer_events: ComputerEventStore | None = None
    computer_settings: ComputerToolSettings | None = None
    computer_sessions: ComputerSessionStore | None = None
    computer_leases: ComputerLeases | None = None
    computer_exports: ComputerExportStore | None = None
    browser: BrowserRunner | None = None
    computer_admission: Any = None
    browser_manager: Any = None
    computer_dm_scope_for: Callable[[str, str], str | None] | None = None
    computer_context_invalidate: Callable[[str], None] | None = None
    artifact_link_base: str = ""
    relay: Relay | None = None
    events: EventsCollaborator | None = None
    notes: NoteCollaborator | None = None
    note_unit: Callable[[str, str], str | None] | None = None
    memos: MemoStore | None = None
    unit_for: Callable[[str, str], str | None] | None = None
    supplied_unit: str | None = None
    music: MusicToolBridge | None = None
    online_source: SteamPresenceProvider | None = None
    voice_source: VoicePresenceSource | None = None
    presence: DiscordPresenceSource | None = None
    image_generator: ImageGenerator | None = None
    image_refs_allowed: bool = False
    media_store: MediaObjectStore | None = None
    documents: DocumentPublisher | None = None
    shelf: ToolShelf | None = None
    on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None
    rooms: RoomDirectory | None = None
    record: RecordQuerySource | None = None
    observations: Any = None
    lineage: LineagePolicy | None = None
    rollup: RollupReadSource | None = None
    standing_head: StandingHeadSource | None = None
    ledger: StandingLedger | None = None
    culture: CultureReader | None = None
    social_items: SocialItemsSource | None = None
    subagent: SubagentDesk | None = None
    video: VideoDesk | None = None
    video_ref_kinds: frozenset[str] = frozenset()
    external_files: ExternalFilesSource | None = None
    # `[google] account`: Nora's own Google mailbox, named in the NOT_SHARED
    # note so the owner knows who to share with. "" → a generic phrasing.
    google_account: str = ""
    memory_retrieval: MemorySearch | None = None
    all_memory_surfaces: bool = False
    profile: ProfileWriter | None = None
    profile_read: ProfileHeads | None = None
    content_checks: bool = True
    caps: Any = field(init=False, default=None)
    now_fn: Any = field(init=False, default=None)
    link_carry: Any = field(init=False, default=None)
    bound_conversation_id: Any = field(init=False, default=None)
    bound_window: Any = field(init=False, default=None)
    lookup_rows: Any = field(init=False, default=None)
    _obs_off: Any = field(init=False, default=None)
    by_id: Any = field(init=False, default=None)
    graph_reads: Any = field(init=False, default=None)
    _route_person_names: Any = field(init=False, default=None)
    _disclosed_live_addresses: Any = field(init=False, default=None)
    _bound_places: Any = field(init=False, default=None)
    _been_day_ms: Any = field(init=False, default=None)
    _been_max_days: Any = field(init=False, default=None)
    _been_tz_name: Any = field(init=False, default=None)
    _been_arms: Any = field(init=False, default=None)
    _been_everyone: Any = field(init=False, default=None)
    _been_anomaly_min_samples: Any = field(init=False, default=None)
    _been_anomaly_z: Any = field(init=False, default=None)
    _been_novelty_radius_m: Any = field(init=False, default=None)
    _routine_ladder: Any = field(init=False, default=None)
    _place_words: Any = field(init=False, default=None)
    _routine_rough: Any = field(init=False, default=None)
    _routine_days: Any = field(init=False, default=None)
    _routine_live_max_age_ms: Any = field(init=False, default=None)
    _routine_match_m: Any = field(init=False, default=None)
    _routine_day: Any = field(init=False, default=None)
    _online_states: Any = field(init=False, default=None)
    _online_arms: Any = field(init=False, default=None)
    _media_window: Any = field(init=False, default=None)
    video_on_tag: Any = field(init=False, default=None)
    _search_person_names: Any = field(init=False, default=None)
    unwired_owners: Any = field(init=False, default=None)
    _instagram_slots: Any = field(init=False, default=None)
    _instagram_story_urls: Any = field(init=False, default=None)
    _instagram_pages: Any = field(init=False, default=None)
    note_heads: Any = field(init=False, default=None)
    notes_off: Any = field(init=False, default=None)
    memos_off: Any = field(init=False, default=None)
    reaction_asked: Any = field(init=False, default=None)
    gif_candidates: Any = field(init=False, default=None)
    gif_query: Any = field(init=False, default=None)
    relay_requester: Any = field(init=False, default=None)
    relay_platform: Any = field(init=False, default=None)
    document_actor: Any = field(init=False, default=None)
    document_asked: Any = field(init=False, default=None)
    subagent_on_tag: Any = field(init=False, default=None)
    subagent_read_notes: Any = field(init=False, default=None)
    computer_ctx: Any = field(init=False, default=None)

    def __post_init__(self) -> None:
        caps = self.reminder_caps or ReminderCaps()
        object.__setattr__(self, "caps", caps)
        now_fn = self.clock or utc_now
        object.__setattr__(self, "now_fn", now_fn)
        link_carry = self.links if self.links is not None else LinkCarry()
        object.__setattr__(self, "link_carry", link_carry)
        if self.documents is not None:
            self.link_carry.claim_documents(getattr(self.documents, "link_base", ""))
        bound_conversation_id = self.conversation_id
        object.__setattr__(self, "bound_conversation_id", bound_conversation_id)
        bound_window: tuple[TranscriptRow, ...] = tuple(self.transcript or ())
        object.__setattr__(self, "bound_window", bound_window)
        lookup_rows: tuple[TranscriptRow, ...] = tuple(self.lookup_window or self.bound_window)
        object.__setattr__(self, "lookup_rows", lookup_rows)
        _obs_off = "I can't read what the source recorded right now. Say so."
        object.__setattr__(self, "_obs_off", _obs_off)
        by_id = self.dream_adaptor == COGNITION_ADAPTOR
        object.__setattr__(self, "by_id", by_id)
        graph_reads: dict[str, Any] = {}
        object.__setattr__(self, "graph_reads", graph_reads)
        _route_person_names = _known_names(self.persons)
        object.__setattr__(self, "_route_person_names", _route_person_names)
        _disclosed_live_addresses: set[str] = set()
        object.__setattr__(self, "_disclosed_live_addresses", _disclosed_live_addresses)
        _bound_places: dict[str, tuple[int, str]] = {}
        object.__setattr__(self, "_bound_places", _bound_places)
        _been_day_ms = 24 * 60 * 60 * 1000
        object.__setattr__(self, "_been_day_ms", _been_day_ms)
        _been_max_days = 90
        object.__setattr__(self, "_been_max_days", _been_max_days)
        _been_tz_name = "Asia/Hong_Kong"
        object.__setattr__(self, "_been_tz_name", _been_tz_name)
        _been_arms = ("day", "places", "rhythm", "routes")
        object.__setattr__(self, "_been_arms", _been_arms)
        _been_everyone = frozenset({"everyone", "大家"})
        object.__setattr__(self, "_been_everyone", _been_everyone)
        _been_anomaly_min_samples = 5
        object.__setattr__(self, "_been_anomaly_min_samples", _been_anomaly_min_samples)
        _been_anomaly_z = 3.0
        object.__setattr__(self, "_been_anomaly_z", _been_anomaly_z)
        _been_novelty_radius_m = 100.0
        object.__setattr__(self, "_been_novelty_radius_m", _been_novelty_radius_m)
        _routine_ladder = ((85, 3), (60, 2), (35, 1))
        object.__setattr__(self, "_routine_ladder", _routine_ladder)
        _place_words = place_words
        object.__setattr__(self, "_place_words", _place_words)
        _routine_rough = ("nearby_hours", "day_type", "overall")
        object.__setattr__(self, "_routine_rough", _routine_rough)
        _routine_days = (
            "Mondays",
            "Tuesdays",
            "Wednesdays",
            "Thursdays",
            "Fridays",
            "Saturdays",
            ("Sundays"),
        )
        object.__setattr__(self, "_routine_days", _routine_days)
        _routine_live_max_age_ms = 30 * 60000
        object.__setattr__(self, "_routine_live_max_age_ms", _routine_live_max_age_ms)
        _routine_match_m = 150.0
        object.__setattr__(self, "_routine_match_m", _routine_match_m)
        _routine_day = (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
        object.__setattr__(self, "_routine_day", _routine_day)
        _online_states = {
            0: "offline",
            1: "online",
            2: "busy",
            3: "away",
            4: "snooze",
            5: ("looking_to_trade"),
            6: "looking_to_play",
        }
        object.__setattr__(self, "_online_states", _online_states)
        _online_arms = ("online", "playing", "voice")
        object.__setattr__(self, "_online_arms", _online_arms)
        _media_window = self.context_window + LOAD_TRANSCRIPT_MAX
        object.__setattr__(self, "_media_window", _media_window)
        video_on_tag = not (self.fire_turn or self.dream_adaptor or self.subagent_job)
        object.__setattr__(self, "video_on_tag", video_on_tag)
        _search_person_names = _known_names(self.persons)
        object.__setattr__(self, "_search_person_names", _search_person_names)
        unwired_owners = (
            frozenset() if self.external_files is not None else frozenset({"file_fetch"})
        )
        object.__setattr__(self, "unwired_owners", unwired_owners)
        _instagram_slots = asyncio.Semaphore(_INSTAGRAM_READ_CONCURRENCY)
        object.__setattr__(self, "_instagram_slots", _instagram_slots)
        _instagram_story_urls: set[str] = set()
        object.__setattr__(self, "_instagram_story_urls", _instagram_story_urls)
        _instagram_pages: dict[str, tuple[tuple[str, str], list[dict], str | None]] = {}
        object.__setattr__(self, "_instagram_pages", _instagram_pages)
        note_heads: dict[str, str] = {}
        object.__setattr__(self, "note_heads", note_heads)
        notes_off = {
            "status": "unavailable",
            "note": (
                "I can't reach my notes right now. Say so "
                "plainly: «there's nothing written down» and «I couldn't look» are different "
                "answers."
            ),
        }
        object.__setattr__(self, "notes_off", notes_off)
        memos_off = {
            "status": "unavailable",
            "note": (
                "I can't reach what this group has kept "
                "right now. Say so plainly: «nothing is kept» and «I couldn't look» are different "
                "answers."
            ),
        }
        object.__setattr__(self, "memos_off", memos_off)
        reaction_asked = [False]
        object.__setattr__(self, "reaction_asked", reaction_asked)
        gif_candidates: dict[str, GifCandidate] = {}
        object.__setattr__(self, "gif_candidates", gif_candidates)
        gif_query = [""]
        object.__setattr__(self, "gif_query", gif_query)
        relay_requester = self.room.tagger if self.room is not None else None
        object.__setattr__(self, "relay_requester", relay_requester)
        relay_platform = self.room.platform if self.room is not None else ""
        object.__setattr__(self, "relay_platform", relay_platform)
        document_actor = (
            "subagent"
            if self.subagent_job
            else (f"{DREAMER_ACTOR_PREFIX}{self.dream_adaptor}")
            if self.dream_adaptor
            else "fire"
            if self.fire_turn
            else "tag"
        )
        object.__setattr__(self, "document_actor", document_actor)
        document_asked = frozenset({"tag", "subagent"})
        object.__setattr__(self, "document_asked", document_asked)
        subagent_on_tag = not (self.fire_turn or self.dream_adaptor or self.subagent_job)
        object.__setattr__(self, "subagent_on_tag", subagent_on_tag)
        subagent_read_notes = {
            "running": (
                "It is still running. Say so, with its age if asked; do "
                "not guess when it will finish. A `checkpoint` is the worker's own unchecked "
                "account "
                "of where it is; only its Outputs are recorded by code."
            ),
            "finishing": "It has ended and its result is on its way to the room as a message. Say "
            "it "
            "is about to arrive.",
            "done": "It finished. Say only what its `report` "
            "says. The room already got the result message.",
            "failed": "It did not finish. Say why in plain words from `failure`.",
            "cancelled": "It was cancelled. Say so.",
        }
        object.__setattr__(self, "subagent_read_notes", subagent_read_notes)
        object.__setattr__(self, "computer_ctx", build_computer_ctx(self))

    def _now_ms(self) -> int:
        """Epoch milliseconds off the SAME clock every other tool here reads.

        `now_fn` is `ToolRegistry`'s injected clock, so a test that freezes time
        freezes the note tier with it. Never `time.time()` — a second clock in
        one module is how a note's `written_at` ends up in a different era from
        the reminder set in the same turn.
        """
        return int(self.now_fn().timestamp() * 1000)

    async def _lineage_target(self, tool: str) -> tuple[RenderRoom, RoomFacts, Any] | None:
        """This room's `RenderRoom` with its live roster read, the room's own
        facts and the roster view; `None` (counted) when the room is not in
        the policy or lineage is not wired."""
        if self.lineage is None:
            _record(self.metrics, tool, "unavailable", reason="unwired")
            return None
        target = self.lineage.render_room(
            platform=self.room.platform, conversation_id=self.bound_conversation_id
        )
        if target.room is None:
            _record(self.metrics, tool, "unavailable", reason="room")
            return None
        view = await self.room.scope()
        return (
            target.with_live(
                LiveRoom(
                    person_ids=frozenset(p.person_id for p in view.people),
                    unnamed=view.unnamed,
                    available=view.available,
                    degraded=view.degraded,
                )
            ),
            target.room,
            view,
        )

    def _in_library(self, obj: MediaObject) -> bool:
        """Whether the library (`media_search`, `media_send`) may show this row
        to THIS room. What Nora generated is hers and global (DEC-138). A picture
        she kept from the web belongs to the room whose look kept it: it is
        found and sent there only."""
        if obj.source == "generated":
            return True
        return obj.source == "fetched" and born_in_room(
            obj, platform=self.room.platform, conversation_id=self.bound_conversation_id
        )

    async def _keep_fetched_image(self, url: str, data: bytes, media_type: str) -> str | None:
        """Put a looked-at web picture into custody as a `fetched` row born in
        this room, and return its short code, or `None` when it could not be
        kept (custody off, a store error) or when the same bytes were first
        indexed in another room: those do not resolve here, so no code is
        offered for them. Never raises."""
        if self.blob_store is None or self.media_store is None:
            return None
        try:
            key = await self.blob_store.put(data=data)
            await self.media_store.save(
                obj=MediaObject(
                    sha256=key,
                    media_type=media_type,
                    size=len(data),
                    source=("fetched"),
                    description="",
                    provenance={
                        "url": url,
                        "platform": self.room.platform,
                        "conversation_id": self.bound_conversation_id,
                    },
                    created_at_ms=int(datetime.now(UTC).timestamp() * 1000),
                )
            )
            row = await self.media_store.get(sha256=key)
        except Exception:
            log.exception("media_fetch could not keep a fetched picture")
            return None
        if row is None or not born_in_room(
            row, platform=self.room.platform, conversation_id=self.bound_conversation_id
        ):
            return None
        return key[:HANDLE_CHARS]

    async def _shared_file_bytes(self, ref: ExternalFileRef) -> FileBytes | dict:
        """A shared file's bytes for `media_fetch` to look at, fetched afresh
        through the one `ExternalFilesSource` (its own byte cap applies), or
        the answer that stops the look. Nothing keeps the bytes: the caller
        reads them once and drops them. The answer names `ref.handle`, never
        the file id or the link (R-18). Never raises."""
        if self.external_files is None:
            _record_media(self.metrics, "unavailable", reason="files_unwired")
            return {
                "status": "unavailable",
                "code": ref.handle,
                "note": _FILE_LOOK_NOTES[("unavailable")],
            }
        try:
            return await self.external_files.fetch_bytes(ref=ref, accept=_LOOKABLE)
        except ExternalFileError as err:
            if err.reason == NOT_ACCEPTED:
                _record_media(self.metrics, "not_an_image", reason="shared_file")
                return {
                    "status": "not_an_image",
                    "code": ref.handle,
                    "note": self._not_lookable_note(ref.handle, err.kind),
                }
            _record_media(self.metrics, err.status, reason=f"file_{err.reason[:40]}")
            return {
                "status": err.status,
                "code": ref.handle,
                "note": _FILE_LOOK_NOTES.get(err.reason)
                or _file_note(err.reason, self.google_account)
                or _FILE_LOOK_NOTES.get(err.status)
                or _FILE_STATUS_NOTES[err.status],
            }
        except Exception:
            log.exception("media_fetch: a shared file could not be fetched")
            _record_media(self.metrics, "unavailable", reason="files_error")
            return {
                "status": "unavailable",
                "code": ref.handle,
                "note": _FILE_LOOK_NOTES[("unavailable")],
            }

    def _not_lookable_note(self, handle: str, kind: str | None) -> str:
        """What `media_fetch` says of a shared file that is not a picture or
        a video: `file_fetch` reads only the text kinds; anything else is
        neither looked at nor read here (and can be copied when her computer
        is on)."""
        if kind in _TEXT_KINDS:
            return (
                f"That shared file is not a picture or a video. To read what it says, use "
                f"file_fetch with code {handle}."
            )
        note = (
            "That shared file is not a picture or a video, and it is not something I can look "
            "at or read here. Say so; don't guess what is in it."
        )
        if self.computer_ctx.computer_ready and self.computer is not None:
            note += f" You can copy it onto your computer with computer_import and code {handle}."
        return note

    async def _shared_file_by_code(self, hex_code: str) -> ExternalFileRef | dict:
        """The one shared file a handle's code names among this room's own
        file links, or the answer that stops the look: `unavailable` when the
        record cannot be read (or the read was cut short with nothing found),
        `ambiguous` for two links, `nothing_found` for none. Never a guess."""
        found = await resolve_file_code(
            self.store,
            code=hex_code,
            conversation_id=self.bound_conversation_id,
            now_ms=self._now_ms(),
        )
        if found.status == "unavailable" or (found.status == "nothing_found" and found.truncated):
            _record_media(self.metrics, "unavailable", reason="files_lookup")
            return {
                "status": "unavailable",
                "code": hex_code,
                "note": (
                    "I couldn't look up that file just now. Say so; don't guess what it shows."
                ),
            }
        if found.status == "ambiguous":
            _record_media(self.metrics, "ambiguous", candidates=len(found.refs))
            return {
                "status": "ambiguous",
                "code": hex_code,
                "note": (
                    "That code matches more than "
                    "one shared file's link here. Ask which one they mean, or for the link."
                ),
            }
        if found.status == "nothing_found":
            _record_media(self.metrics, "nothing_found", reason="files_code")
            return {
                "status": "nothing_found",
                "code": hex_code,
                "note": (
                    "No shared file link "
                    "with that code was sent in this chat recently. Say you can't find it; don't "
                    "guess."
                ),
            }
        return found.refs[0]

    def _notebook(self) -> NoteCollaborator | None:
        """The wired store, or None — BOTH halves of the wiring checked as one.

        A store with no unit is as unusable as no store at all (a note is
        unit-keyed), and answering «nothing found» from either state
        is the `unavailable`-reported-as-`nothing_found` failure R-27 exists to
        prevent. Returning the store rather than a bool is what lets each tool
        bind a non-optional local, so the narrowing is the type checker's job
        rather than an `assert` restating what this function already decided.
        """
        return self.notes if self._unit() else None

    def _unit(self) -> str | None:
        """This turn's unit, or `None` when it cannot be named.

        **`supplied_unit` WINS when set**: a unit-keyed Dreamer adaptor's key
        IS the unit, and the Dreamer's synthetic `Room` carries it in the
        conversation slot with no platform, so the resolver below could never
        name it and every unit-scoped verb would answer `unavailable` on a
        fully-wired store. A SUPPLIED unit is the truth about what the run is
        keyed on; the resolver is the fallback for the tag path, which has a
        real room and no supplier.

        Resolved per turn from `room.platform` and the bound conversation —
        never a process-wide constant. `None` means policy could not name the
        room, and every verb that needs one answers `unavailable` on it:
        reading the wrong unit's notebook is worse than reading none.

        **Shared by every unit-keyed verb**, because it is one value —
        `policy.conversation(...).unit` — and two resolvers for it would
        be two definitions of which social unit a room belongs to.
        `unit_for` is preferred and `note_unit` is the fallback, so a verb
        outside the note tier does not depend on the NOTE tier being wired,
        which it would if it read only `note_unit`.
        """
        if self.supplied_unit:
            return self.supplied_unit
        resolver = self.unit_for or self.note_unit
        if resolver is None:
            return None
        try:
            return resolver(self.room.platform, self.bound_conversation_id)
        except Exception:
            log.exception("note unit resolution failed")
            return None

    def _memory_route(self) -> dict[str, str]:
        """The key every Honcho verb here is ROUTED by (DEC-214): the unit
        this turn is for, or its room.

        A Dreamer adaptor's `supplied_unit` IS the unit, and its synthetic
        `Room` holds that unit in the conversation slot — so the room form
        would name a conversation that does not exist and fail closed. The
        tag and fire paths have a real room, and the memory client resolves
        it to its unit from the same `policy.toml` entry `_unit()` reads,
        so a room with no `unit_for` wired here is still routed correctly.
        Never a model-supplied value: both are captured once (DEC-24)."""
        if self.supplied_unit:
            return {"unit": self.supplied_unit}
        return {"conversation_id": self.bound_conversation_id}
