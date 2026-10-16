"""Test doubles and builders, shipped inside the package.

This is the package `tests/` and `evals/` are meant to share, so there is
exactly one definition of each fake. `evals/harness.py` and
`tests/integration/harness.py` both build their pipeline from
`FakeRecordStore`/`FakeMemoryStore`/`FakeAdapter` plus `ScriptedTurn`/
`FakeAgentRunner`/`group_event` from here. `tests/` outside
`tests/contract/` imports from here too (`tests/test_health.py` takes
`FakeRecordStore`).

Every fake that is a second implementation of one of `nora/domain/ports.py`'s
ports is bound by a contract suite in `tests/contract/` that it and the
production implementation both pass — `FakeRecordStore` (`RecordStore`),
`FakeMemoryStore` (`MemoryStore`), `FakeRosterSource` (`RosterSource`,
bound to `nora.platform.signal.roster.SignalRoster` by
`tests/contract/test_roster_source_contract.py`), `FakeMediaSource`
(`MediaSource`, bound to `nora.platform.signal.media.SignalMediaSource` by
`tests/contract/test_media_source_contract.py`) `FakeBlobStore`
(`BlobStore`, bound to `nora.infra.blobs.FilesystemBlobStore` by
`tests/contract/test_blob_store_contract.py`) and `FakeLinkPreviewSource`
(`LinkPreviewSource`, bound to `nora.infra.preview.OpenGraphPreviewSource` by
`tests/contract/test_link_preview_contract.py`) and `FakeSocialProvider`
(`SocialProvider`, bound to `nora.infra.social`'s THREE implementations —
`OpenCliBridgeClient`, `InstagramSocialClient` and the `SocialRouter` in front
of them — by `tests/contract/test_social_provider_contract.py`, which also
drives `FakeSocialSubjectIndex` (`SocialSubjectIndex`, bound to
`nora.infra.social.PersonsSubjectIndex` by
`tests/contract/test_social_subject_index_contract.py`) and
`FakeInstagramAccountLookup` (`InstagramAccountLookup`, bound to
`nora.infra.social.InstagramSocialClient` by
`tests/contract/test_instagram_account_lookup_contract.py`). Adding such a
fake without one is a project rule violation, not a shortcut.

`FakeComputeJobStore` is the same rule applied to `nora.domain.compute`'s own
port (`ComputeJobStore`) rather than one of
`nora/domain/ports.py`'s — bound to
`nora.infra.sql.compute_jobs.SqlComputeJobStore` by
`tests/contract/test_compute_job_store_contract.py`.

`InMemoryStandingStore` is the same rule again for Standing, bound to
`nora.infra.sql.standing.SqlStandingStore` by
`tests/contract/test_standing_store_contract.py`. Its one production refusal
is the body ceiling, and it inherits it structurally by holding `Standing`
objects rather than dicts.

`InMemoryCultureStore` is the same rule for the culture tier (DEC-207), bound
to `nora.infra.sql.culture.SqlCultureStore` by
`tests/contract/test_culture_store_contract.py`. Its chain is parent links,
never insertion order, and it records every key `head` was asked for.

`InMemorySummonsLedger` is the same rule for the public summons ledger,
bound to
`nora.infra.sql.summons.SqlSummonsLedger` by `tests/contract/test_summons_ledger_contract.py`. Its
claim is atomic within one event loop, as the SQL one is across processes.

`InMemoryNoteStore` is the same rule for the note tier, bound to
`nora.infra.sql.notes.SqlNoteStore` by
`tests/contract/test_note_store_contract.py`. It lives in its own module
(`nora.testing.note_store`). Like `InMemoryStandingStore` it makes its production sibling's
refusals rather than accepting everything — the compare-and-swap on a note's
head is the one that matters: a fake that took a stale parent would turn a
missing guarantee into a passing gate.

`FakeSteamPresenceProvider` (`SteamPresenceProvider`, bound to
`nora.infra.steam.SteamPresenceClient` by
`tests/contract/test_steam_presence_contract.py`),
`FakeVoicePresenceSource` (`VoicePresenceSource`, bound to
`nora.platform.discord.voice_presence.DiscordVoicePresence` by
`tests/contract/test_voice_presence_contract.py`) and
`FakeDiscordPresenceSource` (`DiscordPresenceSource`, bound to
`nora.platform.discord.discord_presence.DiscordPresence` by
`tests/contract/test_discord_presence_contract.py`) are the same rule for
the `online_now` tool's ports, in their own module (`nora.testing.online`).

`FakeAdapter` is exempt *as a whole*: it keeps
`.parse()` public and `.listen()` a no-op rather than mirroring
`SignalAdapter.listen(*, on_message=...)`'s internal parse-and-drop (nothing
drives `FakeAdapter` through a real SSE loop), so it never claims to satisfy
`PlatformAdapter` as a second implementation — see `FakeAdapter`'s docstring.
That exemption is scoped to the methods it names, and does NOT extend to
`FakeAdapter.react`: `react()` IS on `PlatformAdapter` (unlike `parse`/
`listen`'s shape), so it is a genuine second implementation and is bound to
`SignalAdapter.react` by `tests/contract/test_reaction_contract.py`, one suite
parametrized over both — nor to `FakeAdapter.send`, which takes `mentions`/
`preview` like the port and is bound to `SignalAdapter.send` by
`tests/contract/test_send_contract.py`.
`ScriptedTurn`/`FakeAgentRunner` satisfy `nora.engagement.stages.invoke.
AgentRunner`, a stage-level `Protocol`, not one of `nora/domain/ports.py`'s
ports — also exempt; see their docstrings.
"""

from nora.testing.character_evidence import InMemoryCharacterEvidence
from nora.testing.culture_store import InMemoryCultureStore
from nora.testing.decision_log import (
    DuplicateDecisionError,
    InMemoryDecisionLogStore,
)
from nora.testing.dream_schedule import InMemoryDreamScheduleClock
from nora.testing.eval_backends import (
    BACKED_TOOLS,
    EVAL_CIRCLE_ID,
    CannedParkingProvider,
    CannedPresenceReader,
    CannedRouteSource,
    CannedSearchProvider,
    CannedStreetParkingProvider,
    CannedTransitSource,
    CannedWeatherProvider,
)
from nora.testing.fakes import (
    FakeAdapter,
    FakeBlobStore,
    FakeBrowserRunner,
    FakeComputeBackend,
    FakeComputeJobStore,
    FakeComputerEventStore,
    FakeComputerExportStore,
    FakeComputerSessionStore,
    FakeDocumentStore,
    FakeGeocodeCacheStore,
    FakeInstagramAccountLookup,
    FakeLinkPreviewSource,
    FakeMediaDescriptionStore,
    FakeMediaInterpreter,
    FakeMediaObjectStore,
    FakeMediaSource,
    FakeMemoryStore,
    FakeRecordStore,
    FakeReverseGeocoder,
    FakeRosterSource,
    FakeSandboxRuntime,
    FakeSocialProvider,
    FakeSocialSubjectIndex,
    FakeToolCallLogStore,
    FakeTranscriber,
    FakeVideoReadingStore,
    transcript_rows,
)
from nora.testing.note_store import InMemoryNoteStore
from nora.testing.online import (
    FakeDiscordPresenceSource,
    FakeSteamPresenceProvider,
    FakeVoicePresenceSource,
)
from nora.testing.scripted_agent import (
    TOOL_CHOICE_FIRST_CALL,
    FakeAgentRunner,
    ScriptedModel,
    ScriptedTurn,
    group_event,
    limit_stop_reason,
)
from nora.testing.standing_store import InMemoryStandingStore
from nora.testing.summons_ledger import InMemorySummonsLedger

__all__ = [
    "BACKED_TOOLS",
    "EVAL_CIRCLE_ID",
    "TOOL_CHOICE_FIRST_CALL",
    "CannedParkingProvider",
    "CannedPresenceReader",
    "CannedRouteSource",
    "CannedSearchProvider",
    "CannedStreetParkingProvider",
    "CannedTransitSource",
    "CannedWeatherProvider",
    "DuplicateDecisionError",
    "FakeAdapter",
    "FakeAgentRunner",
    "FakeBlobStore",
    "FakeBrowserRunner",
    "FakeComputeBackend",
    "FakeComputeJobStore",
    "FakeComputerEventStore",
    "FakeComputerExportStore",
    "FakeComputerSessionStore",
    "FakeDiscordPresenceSource",
    "FakeDocumentStore",
    "FakeGeocodeCacheStore",
    "FakeInstagramAccountLookup",
    "FakeLinkPreviewSource",
    "FakeMediaDescriptionStore",
    "FakeMediaInterpreter",
    "FakeMediaObjectStore",
    "FakeMediaSource",
    "FakeMemoryStore",
    "FakeRecordStore",
    "FakeReverseGeocoder",
    "FakeRosterSource",
    "FakeSandboxRuntime",
    "FakeSocialProvider",
    "FakeSocialSubjectIndex",
    "FakeSteamPresenceProvider",
    "FakeToolCallLogStore",
    "FakeTranscriber",
    "FakeVideoReadingStore",
    "FakeVoicePresenceSource",
    "InMemoryCharacterEvidence",
    "InMemoryCultureStore",
    "InMemoryDecisionLogStore",
    "InMemoryDreamScheduleClock",
    "InMemoryNoteStore",
    "InMemoryStandingStore",
    "InMemorySummonsLedger",
    "ScriptedModel",
    "ScriptedTurn",
    "group_event",
    "limit_stop_reason",
    "transcript_rows",
]
