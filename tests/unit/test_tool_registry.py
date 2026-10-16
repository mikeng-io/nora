"""`ToolRegistry` (Task 2) — the DEC-10 source of native-tool budgets and the
one place that builds them for a tag, adapted to Strands tool objects."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nora.agent import factory
from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.mcp import McpToolGroup
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.surface import WORKER_ONLY_TOOLS
from nora.agent.tools import (
    COMPUTER_TOOLS,
    GRAPH_MEMORY_TOOLS,
    LOADER_TOOL,
    NATIVE_TOOL_BUDGETS,
    SUBAGENT_TOOLS,
    VIDEO_TOOLS,
)
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget
from nora.obs.metrics import Metrics
from nora.testing import FakeBrowserRunner, FakeMemoryStore, FakeSandboxRuntime
from tests.fakes import SinkSender

ROOT = Path(__file__).resolve().parents[2]
TARGET = ConversationTarget("signal", "g1", "group")


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>", doctrine_text="be helpful",
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def resolve(self, platform: str, sender_id: str):
        return None


def _deliverer() -> Deliverer:
    return Deliverer(
        store=_FakeStore(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="t1", nora_sender_id="nora",
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag", metrics=Metrics(),
    )


def _room() -> Room:
    return Room(
        # Discord — the music verbs ride on this platform's catalog, so the
        # full-table registry contracts below see all six of them. A
        # non-Discord room's catalog deliberately omits them (2026-09-16,
        # catalog-level platform filter); that cut is pinned by
        # `test_non_discord_catalog_omits_music_tools`.
        platform="discord", conversation_id="g1", roster_source=None,
        persons=_FakePersons(),
    )


#: A Record read that is never called: `record_query` is offered only when one
#: is wired (`[record_query] enabled`), and the declared-surface tests below
#: ask about every tool.
_RECORD = object()
#: The same for the two Dreamer-only reads (`rollup_read`, `standing_read`).
_ROLLUP = object()
_STANDING = object()
#: `character_read`'s culture store, never called.
_CULTURE = object()
_MEMOS = object()
#: The document service, never called: both document tools are offered only when one
#: is wired (`[documents] enabled`).
_DOCUMENTS = object()


def test_record_query_is_offered_only_when_its_record_read_is_wired():
    """`[record_query] enabled = false` (the shipped default) builds the
    registry with no Record read, and then no turn and no gate digest carries
    the tool; wired, both do."""
    off = ToolRegistry()
    assert "record_query" not in {t.tool_name for t in _built_tools(off)}
    assert "record_query" not in off.capability_digest()
    on = ToolRegistry(record=_RECORD)
    assert "record_query" in {t.tool_name for t in _built_tools(on)}
    assert "record_query" in on.capability_digest()


def test_the_document_tools_are_offered_only_when_documents_are_wired():
    """`[documents] enabled = false` (the shipped default) builds the registry with
    no document service, and then no turn and no gate digest carries either tool:
    the portal pages that open a document ship later. Wired, both are offered."""
    names = {"document_publish", "document_read", "document_set_status"}
    off = ToolRegistry()
    assert names.isdisjoint({t.tool_name for t in _built_tools(off)})
    assert "document_read" not in off.capability_digest()
    on = ToolRegistry(documents=_DOCUMENTS)
    assert names <= {t.tool_name for t in _built_tools(on)}
    # The digest lists what she can ANSWER: the read, never the act.
    assert "document_read" in on.capability_digest()
    assert "document_publish" not in on.capability_digest()
    assert "document_set_status" not in on.capability_digest()


def _surface(registry: ToolRegistry, *, defer: bool):
    return registry.surface(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(), defer=defer,
    )


@pytest.mark.parametrize("defer", [False, True])
def test_the_computer_tools_are_offered_only_when_a_computer_is_wired(defer):
    """`[computer] enabled = false` (the shipped default) builds the registry
    with no runtime, and then no turn's tools, shelf, catalogue or gate digest
    carries a computer tool: they could only answer `unavailable`. Wired, all
    four are offered (deferred, so on the shelf and in the catalogue)."""
    def catalogued(surface) -> set[str]:
        # `- name — ...` for a shelved tool, `* name — ...` for a resident one.
        return {m.group(1) for line in surface.catalogue.splitlines()
                if (m := re.match(r"[-*] (\w+) ", line))}

    off = _surface(ToolRegistry(), defer=defer)
    assert COMPUTER_TOOLS.isdisjoint(off.admitted | catalogued(off))
    assert COMPUTER_TOOLS.isdisjoint(set(off.shelf.names()))
    assert "computer_read" not in ToolRegistry().capability_digest()
    # A computer without a browser runner (`[computer.browse]` off) offers
    # every computer tool but `computer_browse`; with one, all of them.
    no_browser = _surface(ToolRegistry(computer=FakeSandboxRuntime()), defer=defer)
    assert no_browser.admitted >= COMPUTER_TOOLS - {"computer_browse"}
    assert "computer_browse" not in no_browser.admitted | catalogued(no_browser)
    on_registry = ToolRegistry(computer=FakeSandboxRuntime(), browser=FakeBrowserRunner())
    on = _surface(on_registry, defer=defer)
    assert on.admitted >= COMPUTER_TOOLS
    assert catalogued(on) >= COMPUTER_TOOLS
    if defer:
        assert set(on.shelf.names()) >= COMPUTER_TOOLS
    assert "computer_read" in on_registry.capability_digest()


def _built_tools(registry: ToolRegistry, *, dream_adaptor: str = "proactive") -> list:
    """The DECLARED surface, which is what every caller below asks about.

    `dream_adaptor` defaults to a name rather than `""` because
    `search_memory_workspace` is Dreamer-only (#350) and a tag-path build no
    longer carries it — a declared-vs-built comparison run on the tag catalog
    would be comparing against a deliberate subset. The subset itself is
    pinned by `test_a_room_catalog_omits_the_dreamer_only_memory_verb`.
    """
    return registry.build(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(), dream_adaptor=dream_adaptor,
    )


def test_the_registry_exposes_every_declared_tool():
    tools = _built_tools(ToolRegistry(
        record=_RECORD, rollup=_ROLLUP, standing_head=_STANDING, culture=_CULTURE,
        memos=_MEMOS,
        computer=FakeSandboxRuntime(), browser=FakeBrowserRunner(), documents=_DOCUMENTS,
    ))
    assert {t.tool_name for t in tools} == {
        # DEC-146: the window-scoped rung below `load_transcript`.
        "who_said",
        "now", "load_transcript", "send_reply", "search_gifs", "send_gif",
        "send_to",
        # design/self-awareness/07: which rooms this group has, and what is
        # said in one of them.
        "room_list", "room_read",
        # design/cognition-loop/15-map.md §4: the Record over an interval,
        # under lineage.
        "record_query",
        # design/cognition-loop/25 §5, 50 §2: the Dreamer-only reads.
        "rollup_read", "standing_read", "character_read",
        "group_members", "lookup_person", "about", "ask_memory", "search_memory",
        "recall_lookups",
        "calculate", "transit", "route",
        "place_info", "saved_places",
        "where_is",
        # DEC-156: the coordinate -> street-address fetch surface. `where_is`
        # stopped carrying an address in 13c530aa, so this is the ONLY tool that
        # resolves one, and R-57 screens routes against what it disclosed.
        "reverse_geocode",
        # DEC-155: the derived-presence history tool (where someone HAS BEEN).
        "where_been",
        # design/27 §4.3: the Tier 4 forecast behind where_been's own guard.
        "routine_read",
        # design/online-status/: the LIVE digital-activity tool (who is
        # online / playing / in voice right now) — the #248 cross-check.
        "online_now",
        "media_fetch",
        # design/external-files §3: a shared file link, read as text.
        "file_fetch",
        "generate_image",
        "media_search", "media_send",
        "web_search", "web_fetch",
        # x_read and x_search (DEC-201, replacing DEC-151's social_fetch):
        # READ-ONLY X via the host bridge.
        "x_read", "x_search",
        # linkedin_read and linkedin_search (DEC-201): READ-ONLY LinkedIn via
        # the same host bridge, inert unless [social.linkedin] enabled.
        "linkedin_read", "linkedin_search",
        # instagram_read (design/instagram-adoption/18): a person's Instagram
        # profile or story, over the instagram-bridge.
        "instagram_read",
        # instagram_search (design/instagram-adoption/18, P11): Instagram
        # accounts matching free text, over the instagram-bridge.
        "instagram_search",
        "weather",
        # HK parking (2026-08-31): TD open-data vacancy for a named car park,
        # and Google Places nearby-search + street meters for «near X».
        "parking_status", "find_parking",
        # Discord-only music (design/discord-music/07-agent-tool.md).
        "play_music", "skip_music", "top_music", "stop_music", "music_status",
        "set_music_volume",
        "remind", "list_reminders", "cancel_reminder",
        # The Event module (gatherings/RSVP/venue suggestions).
        "event_propose", "event_revise", "event_rsvp", "event_set_status",
        "event_search", "suggest_venue",
        # Nora's computer (design/sandbox-runtime §4, DEC-243).
        "computer_run", "computer_read", "computer_write", "computer_edit",
        "computer_export", "computer_import", "computer_session_list",
        # design/browser-runtime B2: a whole browser task, by a sub-agent.
        "computer_browse",
        # design/artifact §26: a document is published to the web app, read back to edit.
        "document_publish", "document_read", "document_set_status",
        # (the four `subagent_*` tools are tag-turn only: this Dreamer-path build omits them,
        # and tests/unit/test_subagent_tools.py pins where they ARE built.)
        # DEC-97: what she is running, and what was in an earlier version.
        "version",
        # The note tier's four verbs (note-tier spec §5.2/§6).
        "note_read", "note_search", "note_create", "note_update",
        # DEC-306: one kept memo of the unit.
        "memo_read",
        # The remember verb — the conclusion write lane (2026-08-30).
        "remember",
        # The memory search variant, built for the Dreamer's unit key but
        # reaching Honcho's whole workspace — every unit, not one.
        "search_memory_workspace",
    }


def test_non_discord_catalog_omits_music_tools():
    """2026-09-16 catalog-level platform filter: music verbs are Discord-only,
    so a non-Discord room's tools never ship them (they would only ever answer
    unavailable). The bodies still gate independently — the platform check
    stays as the second line of defence — but this test pins the TOKEN-SAVING
    cut.

    Re-derived when the filter moved (design/tool-catalogue, phase B): it was a
    branch inside `build_tool_specs` and is now `ToolScope`, applied by
    `ToolRegistry.surface()` — the one builder every loop takes tools from.
    `build_tool_specs` builds every closure; this asserts what a ROOM gets,
    which is the only place the cut ever mattered."""
    registry = ToolRegistry()
    signal_room = Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_FakePersons(),
    )
    music = {"play_music", "skip_music", "top_music", "stop_music",
             "music_status", "set_music_volume"}
    signal = registry.surface(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=signal_room,
    )
    assert not {t.tool_name for t in signal.tools} & music
    # The catalogue is derived from the SAME admitted set, so it cannot
    # advertise a tool this room cannot load.
    assert not signal.admitted & music
    discord = registry.surface(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(),
    )
    assert music <= {t.tool_name for t in discord.tools}
    assert music <= discord.admitted


def test_budgets_match_todays_table():
    assert dict(ToolRegistry().budgets()) == {
        # design/tool-catalogue/02: 3 calls of up to 4 names covers the
        # measured p90 of 2 cold tools per turn, with a retry after `unknown`.
        "load_tools": 3,
        # 4, sized like `lookup_person`: one reply routinely carries more than
        # one attribution and each is a separate question. The call is free — a
        # scan of a tuple already in hand — so round trips are the only currency.
        "who_said": 4,
        "now": 4, "load_transcript": 4,
        # 3 — the cap on how many messages one tag may send. 1→3 activated
        # multi-message (DEC-42); 3→6 (DEC-95) made the BURST first-class, then
        # 6→3 (2026-09-22, reverted) after a live incident spent all six on
        # four rewordings of one point — the burst was a ceiling with no
        # stated purpose and nothing had measured 6 was needed. 3 still holds
        # a word to a second person alongside the first reply. Still the spam
        # cap: `turns` and the token ceiling are far away, so this number is
        # the only thing bounding one inbound message's outbound volume.
            "send_reply": 3, "search_gifs": 1, "send_gif": 1,
            # DEC-105. Two, not six: relaying is a deliberate act somebody
            # asked for, not a register she can fall into.
            "send_to": 2,
        # design/self-awareness/07. `room_list` 2: one call answers it, a
        # second re-checks a transport after a send. `room_read` 3: two rooms
        # plus a corrected name.
        "room_list": 2, "room_read": 3,
        # design/cognition-loop/15-map.md §4: an interval, then a narrower
        # one, then one person.
        "record_query": 3,
        # Sized against model round trips, not against work — see
        # `NATIVE_TOOL_BUDGETS` for the per-tool reasoning.
        "group_members": 1, "lookup_person": 4, "about": 4,
        # 4 — the dialectic reasoning verb is slow (~14-19s), so the budget is
        # sized by the loop wall clock (240s); four calls (~76s) still fit.
        # Raised from 2 (operator 2026-09-03) for the personalization
        # decomposition's group turns.
        "ask_memory": 4, "search_memory": 4,
        # DEC-316: the graph surface's memory verb, offered instead of the two
        # above while `[memory_index] enabled`; `ask_memory`'s number.
        "memory_search": 4,
        # 2 (tool-recall-log spec §4): the same shape `ask_memory`'s own
        # note describes for a recall verb — run once, notice a different
        # `limit` is needed, run once more.
        "recall_lookups": 2,
        # Nora's computer (design/sandbox-runtime §4). `computer_run` 12: a
        # write, run, read the error, fix, run again loop, twice in a turn.
        # `computer_read` 8: a few files or one long file read on. `write` 8:
        # a small project is several files. `edit` 8: patches across a few
        # files, or a fix after a run.
        "computer_run": 12, "computer_read": 8, "computer_write": 8,
        "computer_edit": 8,
        # S3a: one file out, a revision after a look, one spare (a page and
        # its data); two or three of somebody's files in.
        "computer_export": 3, "computer_import": 3,
        # design/sandbox-runtime §15.2: sessions are derived by code, so the
        # one session tool reads. One call answers «what were you doing»; a
        # second is a different status filter.
        "computer_session_list": 2,
        "computer_browse": 2,
        # design/artifact §26: no-spec catalog read, publish, a fix, an edit; a read.
        "document_publish": 6, "document_read": 2,
        # Archive, delete or restore one document; a second call is an undo or
        # another document.
        "document_set_status": 2,
        # design/subagent §3: one job started (a second call repairs a refused brief), one
        # room's few jobs listed, one job read, one stopped.
        "subagent_create": 2, "subagent_list": 1, "subagent_read": 2, "subagent_cancel": 1,
        # design/subagent §4d: the worker's own place-keeper; it has no per-tool ceiling in use.
        "checkpoint_update": 1,
        # The one tool whose budget is sized by the QUESTION rather than by
        # cost: it is a pure function with no network and no clock, so four
        # calls are four model round trips and nothing else.
        "calculate": 8,
        # One message names two routes often enough (「68X 定 K73 快啲」) and
        # three almost never.
        "transit": 4,
        # 4 -> 8 (operator instruction, 13c530aa 2026-09-06): multi-spot
        # questions stopped fitting the old tight cap once naming a place took
        # the extra where_is -> reverse_geocode round trip.
        "route": 8,
        # Sized like route: one Places lookup per call, a turn may ask after two
        # spots.
        "place_info": 4,
        # 1 like group_members: a fixed per-room list, same answer if called twice.
        "saved_places": 1,
        # 2 -> 8 (operator instruction, 13c530aa 2026-09-06). Sized by the
        # QUESTION now, not the cost: naming a spot takes where_is ->
        # reverse_geocode, so a two-person answer is four calls before anyone
        # asks a follow-up. NOTE the cost this walked away from — each call can
        # still block on the DEC-67 live refresh (~8-10s), so 8 is ~80s of
        # where_is alone against a 120s wall-clock. It is the operator's call;
        # `[llm.budgets]` is the lever if a turn ever times out here.
        "where_is": 8,
        # 4 (DEC-155, the where_been spec §4.4), sized like `transit`/
        # `place_info`: the calls are cheap local SELECTs — no network, no
        # live-refresh block — so the currency is the model round trip, and
        # one question can legitimately walk day -> places in a turn.
        "where_been": 8,
        # design/27 §4.3: declared like where_been (same store, same guard).
        "routine_read": 8,
        # 4, sized on NETWORK cost like `transit` (design/online-status/04):
        # one question is ONE batched Steam GetPlayerSummaries call (the
        # room's whole id set in a single request) and/or one gateway-cache
        # read — a second call is a second question. `cacheable=False` is
        # load-bearing: a cached "playing" replayed later is the stale-note
        # failure (#248) this tool exists to kill.
        "online_now": 4,
        # 2, like `where_is` before the raise: one turn can ask after two
        # coordinates, each a cached Google reverse-geocode.
        "reverse_geocode": 2,
        # The plugin-provided activation tool (DEC-50, activating DEC-48). It is
        # in `budgets()` ONLY so R-7 admits the `AgentSkills` activation call;
        # the registry does not BUILD it (see the tests below). Sized 2 since
        # 2026-09-03: two skills shipped (navigation, personalization) and one
        # turn may span both; the repeat-veto still stops re-activating the SAME
        # skill, which re-injects a body already in context. `self-description`
        # (2026-09-13, #227) made it three and the number did not move — it
        # bounds the domains one MESSAGE can span, not the size of the
        # directory.
        "skills": 2,
        # One message can name two images (a photo AND a screenshot); the
        # per-call vision timeout, not this number, is what keeps two describe
        # calls inside the loop budget (DEC-56). 8 since 2026-10-02: a document with
        # pictures looks at one per section, and 4 ended a turn before it published.
        "media_fetch": 8,
        # A file, its next part or two, or a second file (design/external-files §3).
        "file_fetch": 4,
        # One message can ask for one image, maybe a second take after a bad
        # first; each call is a slow provider round trip on a quota-bounded
        # subscription, so 2 is the honest turn ceiling (DEC-137).
        "generate_image": 2,
        # design/video-generation: one clip a turn; a second call is a second clip nobody
        # asked for, spent on a shared weekly quota.
        "video_create": 2,
        # media_search is a local table read (like recall_lookups); media_send
        # delivers a stored attachment (like send_gif).
        "media_search": 2, "media_send": 2,
        # search (DEC-73, Tavily): sized by model round trips — search, then
        # refine once or twice; the doctrine says a couple is plenty.
        "web_search": 4,
        # fetch (DEC-116, Tavily /extract): a page is heavier than a snippet
        # round trip and there is rarely a reason to open more than one or two
        # links in a turn.
        "web_fetch": 2,
        # x_read (DEC-201): a post, then its author's profile or posts, and a
        # second try. x_search: a search and one refinement.
        "x_read": 3,
        "x_search": 2,
        # linkedin_read: a profile, then its experience or posts.
        # linkedin_search: one search a turn, each spends a monthly limit.
        "linkedin_read": 2,
        "linkedin_search": 1,
        # instagram_read (design/instagram-adoption/18): one read, a second
        # for the other `what` or «no, the other one».
        "instagram_read": 2,
        # instagram_search (P11): one search, and one refinement when the
        # first list does not hold the person.
        "instagram_search": 2,
        # One weather verb (DEC-72): current + warnings + outlook always, the
        # 9-day forecast on a flag. 2 — one HKO round usually answers, a second
        # covers a follow-up.
        "weather": 2,
        # TD open-data feed, keyless like transit — `parking_status` answers a
        # named car park's live vacancy. 2, sized like `weather`: one
        # index+vacancy round usually answers; a second covers a follow-up (a
        # different venue name).
        "parking_status": 2,
        # `find_parking` (2026-08-31): Google Places nearby search plus the TD
        # vacancy cross-ref per hit. 3 — one resolve (or one presence read) +
        # one nearby call + the vacancy cross-references that follow.
        "find_parking": 3,
        # Discord-only music (design/discord-music/07-agent-tool.md), all 2
        # like `weather`: one call is the ordinary shape for each verb, a
        # second covers "no, the other one" after a bad match.
        "play_music": 2, "skip_music": 2, "top_music": 2, "stop_music": 2, "music_status": 2,
        "set_music_volume": 2,
        # The three reminder verbs (DEC-70), all 2 and sized by the
        # CONVERSATION rather than by cost: none leaves the host or the
        # database, so a call is one model round trip, and one message can
        # genuinely set two reminders or set one after cancelling another.
        # Raising them would buy nothing — the real bound on how many
        # reminders exist is `max_pending_per_requester`, checked inside
        # `remind` (§9), which no budget can substitute for.
        "remind": 2, "list_reminders": 2, "cancel_reminder": 2,
        # The Event module: `list_events` is 1 like `group_members` (a
        # room-scoped read, same answer if called twice). `create_event`/
        # `suggest_venue` are the ones that reach further — `suggest_venue`
        # asks Honcho's dialectic once per roster member and resolves venues
        # after, sized like `media_fetch`'s old 4; `event_propose` is 3, between
        # `remind`'s 2 and `suggest_venue`'s 4, for the same round-trip
        # reason (renamed from `create_event`/`update_event`/`rsvp_event`/
        # `cancel_event`/`list_events`, step 2b, DEC-237).
        # `event_revise`/`event_set_status` are 2, sized like the
        # reminder verbs. `event_rsvp` is 1 — a single yes/no/maybe, and a
        # second call in the same turn is a model looping on its own write.
        "event_search": 1, "event_propose": 3, "event_revise": 2,
        "event_rsvp": 1, "event_set_status": 2, "suggest_venue": 4,
        # 2 — one call answers "what version are you", a second covers the
        # follow-up about an earlier one (DEC-79).
        "version": 2,
        # The four note verbs (note-tier spec §6.4). The RATIO is the
        # decision, not the absolute numbers: a revision costs two calls —
        # `note_read` then `note_update` — which is what makes the read
        # mandatory rather than advisory (R-55 enforces it). `note_read` is
        # one higher than `note_update` because comparing two notes before
        # revising one is an ordinary turn and a read writes nothing.
        # `note_search` is 2 like `web_fetch`: one query, one refinement —
        # every title is already in `<notes>`, so a second phrasing is all a
        # second call can buy. `note_create` is 1, the per-turn cap on note
        # spam, and a cap the create/update split (§6.5) is what makes
        # meaningful: under one `note_write` an accidental create would spend
        # the revision budget instead.
        "note_read": 3, "note_search": 2, "note_create": 1, "note_update": 2,
        # `memo_read` is 3 like `note_read` (DEC-306): a couple of memos and a re-read.
        "memo_read": 3,
        # 1, like `note_create`: one write answers the ask; a second call is the
        # model looping on a tool it should have trusted to have worked (the
        # remember verb's write lane, 2026-08-30).
        "remember": 1,
        # `search_memory_workspace` is 4 like its room-scoped sibling — the
        # same verb at a wider scope, the same sizing.
        "search_memory_workspace": 4,
        # `rollup_read` is 4 like `where_been` (cheap local SELECTs, so the round
        # trip is the cost); `standing_read` is 1 (the head does not move inside a
        # session's read of one room). A Cognition session raises both per agenda
        # item (`nora.app.cognition_wiring`).
        "rollup_read": 4, "standing_read": 1, "character_read": 2,
    }


def test_calculate_is_not_terminal_and_ends_no_turn():
    """`terminal` truncates a turn when it is wrong, and arithmetic is the
    middle of an answer rather than the end of one — she works a total out and
    then still has to say it. `time_shiftable` has no meaning here either way:
    unlike `now`, the answer to `1485/7` does not depend on when it is asked."""
    assert ToolRegistry().terminal_tools() == frozenset({"send_reply", "send_gif"})


def test_budgets_do_not_require_runtime_dependencies():
    """`budgets()` is what `ReplyGuard` is constructed with, at a point in the
    pipeline before any tag-specific collaborator (store, deliverer, ...)
    exists — it must not need them."""
    assert ToolRegistry().budgets()


def test_specs_carry_the_same_budgets_as_budgets():
    from nora.agent.tools import NATIVE_TOOLS

    registry = ToolRegistry()
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        # The DECLARED surface — see `_built_tools`. Without an adaptor name
        # this compares the table against the tag catalog, which is missing
        # `search_memory_workspace` on purpose (#350).
        metrics=Metrics(), room=_room(), dream_adaptor="proactive",
    )
    # Every BUILT spec's budget matches `budgets()`. `budgets()` is a superset:
    # it also carries the plugin-provided `skills` budget (DEC-50), which R-7
    # admits but `build_tool_specs` does not build — so compare against the
    # non-plugin budgets, which are exactly the specs.
    # No index wired, so the Honcho memory surface: `memory_search` is the
    # graph surface's and is not built here (DEC-316).
    non_plugin_budgets = {
        name: spec.budget for name, spec in NATIVE_TOOLS.items()
        if not spec.plugin and name not in GRAPH_MEMORY_TOOLS
    }
    assert {s.name: s.budget for s in specs} == non_plugin_budgets


def test_adapting_produces_objects_the_agent_factory_accepts():
    client = factory.create_client(base_url="http://localhost:20128/v1", api_key="test-key")
    model = factory.create_model_provider(
        model="deepseek-v4-flash", max_tokens=400, temperature=0.7, client=client,
    )
    tools = _built_tools(ToolRegistry(
        record=_RECORD, rollup=_ROLLUP, standing_head=_STANDING, culture=_CULTURE,
        memos=_MEMOS,
        computer=FakeSandboxRuntime(), browser=FakeBrowserRunner(), documents=_DOCUMENTS,
    ))
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=tools, pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=ToolRegistry().budgets(),
                        terminal=ToolRegistry().terminal_tools(), metrics=Metrics()),
        trace_attributes={},
        retry=factory.build_retry_policy(timeout_seconds=30.0),
    )
    # The registry BUILDS exactly its native tools — `tool_names()` excludes the
    # plugin-provided `skills` (DEC-50), which this agent (no `skills_dir`) does
    # not have. `NATIVE_TOOL_BUDGETS` is a SUPERSET now: it also carries the
    # `skills` budget so R-7 can admit the plugin's activation tool where it is
    # wired — but that budget is not a built tool here.
    # And `load_tools` is declared but not handed over: `build()` never
    # defers, and the loader ships only when something is on the shelf.
    # (this build is the Dreamer path, which the tag-only `subagent_*` and `video_create` and the
    # worker-only `checkpoint_update` never reach)
    assert set(agent.tool_names) == (
        set(ToolRegistry().tool_names())
        - {LOADER_TOOL, *SUBAGENT_TOOLS, *VIDEO_TOOLS, *WORKER_ONLY_TOOLS}
        # no index wired: the Honcho memory surface (DEC-316)
        - GRAPH_MEMORY_TOOLS
    )
    assert set(agent.tool_names) == set(NATIVE_TOOL_BUDGETS) - {
        "skills", LOADER_TOOL, *SUBAGENT_TOOLS, *VIDEO_TOOLS, *WORKER_ONLY_TOOLS,
        *GRAPH_MEMORY_TOOLS,
    }
    assert "skills" in NATIVE_TOOL_BUDGETS


# ---- time-shiftability (design/14 §5.1, DEC-40) ------------------------------

def test_every_tool_declares_whether_it_can_be_asked_about_another_moment():
    """§5.1's field, and it is three-valued: `None` means the tool has no time
    dimension, `False` means live-only, `True` means it takes a target time.
    `None` is a real answer rather than an omission, so what a test can hold is
    that the SPEC carries the table's value — `_spec()` silently dropping the
    field would give every tool the dataclass default, and a live-only tool
    quietly declaring it never goes stale is the failure worth catching."""
    from nora.agent.tools import NATIVE_TOOLS
    registry = ToolRegistry()
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        # The DECLARED surface — see `_built_tools`. Without an adaptor name
        # this compares the table against the tag catalog, which is missing
        # `search_memory_workspace` on purpose (#350).
        metrics=Metrics(), room=_room(), dream_adaptor="proactive",
    )
    assert {s.name: s.time_shiftable for s in specs} == {
        name: spec.time_shiftable
        for name, spec in NATIVE_TOOLS.items()
        if not spec.plugin  # `skills` is not built as a spec (DEC-50)
        # the Honcho memory surface: no index wired (DEC-316)
        and name not in GRAPH_MEMORY_TOOLS
    }
    assert {s.time_shiftable for s in specs} <= {None, False, True}


def test_transit_is_declared_live_only_and_not_terminal():
    from nora.agent.tools import NATIVE_TOOLS
    assert NATIVE_TOOLS["transit"].time_shiftable is False
    assert NATIVE_TOOLS["transit"].terminal is False
    assert "transit" not in ToolRegistry().terminal_tools()


def test_the_sourcing_priority_lives_in_conduct_not_a_generated_block():
    """E1/DEC-77: the sourcing nudge moved to conduct.md with DEC-150. A
    declared `source_tier` nothing renders is decoration; the gate found the
    model web-searching a fact with a closer source, and the priority now
    lives in conduct's 'Where to look first' section."""
    conduct = (ROOT / "nora" / "doctrine" / "rooms" / "conduct.md").read_text()
    assert "prefer your own" in conduct
    assert "open web, which is the last resort" in conduct


def test_the_closed_world_clause_lives_in_doctrine_not_a_generated_block(): # DEC-90
    """The list of tools was never stated to be the WHOLE list, and she filled
    the gap: a restaurant answer ended «講清楚我幫你睇埋使唔使訂位» — offering to
    check a booking, which no tool here does and none is planned. DEC-150 moved
    the rule to conduct.md; it must read exactly as strongly there.

    RE-POINTED 2026-10-12 (design/context-plan/11 §6, step 2): the rule is law
    and lives in rooms/constraints.md § What you can do. Only its stale parts
    were dropped («the tool schemas are everything you can do», «no following
    up later», which predate the cognition loop, the computer and skills,
    design/context-plan/10); the offer ban, its concrete family and «say plainly
    they will have to do it themselves» are kept."""
    law = " ".join(
        (ROOT / "nora" / "doctrine" / "rooms" / "constraints.md").read_text().split()
    )
    assert "Never offer, promise or volunteer an action that your tools and skills do not " \
           "give you" in law
    # The measured overclaim, and the family it belongs to, named concretely —
    # a rule stated only in the abstract is the one the model reads past.
    assert "no booking a" in law
    assert "no phone call" in law
    assert "say plainly that they will have to do it themselves" in law
    assert "Never say you have done something that no tool call of yours did" in law
    conduct = (ROOT / "nora" / "doctrine" / "rooms" / "conduct.md").read_text()
    assert "The tool schemas are everything you can do" not in conduct
    assert "no following up later" not in law


def test_the_closed_world_clause_forbids_offers_not_ignorance():
    """It must not collide with the rule beside it. She may always say she
    cannot find something out — `constraints.md` and `rooms/constraints.md` both
    insist on it — so the ban is on PROMISED ACTS. Both rules are in the same
    file now, so this reads them side by side: the offer ban speaks of actions
    only, and the could-not-check rule is still there and still says to say it."""
    text = (ROOT / "nora" / "doctrine" / "rooms" / "constraints.md").read_text()
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n")]
    offer = [p for p in paragraphs if p.startswith("Never offer, promise or volunteer an action")]
    broken = "When it comes back broken, say you could not check"
    could_not_check = [p for p in paragraphs if broken in p]
    assert len(offer) == 1 and len(could_not_check) == 1
    # The ban is on acts she has no tool for, never on saying what she could not find out.
    assert "an action" in offer[0]
    for word in ("could not check", "do not know", "find out", "我唔知", "我查唔到"):
        assert word not in offer[0], word
    # And the honesty rule does not turn into an offer to act.
    for word in ("offer", "promise", "volunteer"):
        assert word not in could_not_check[0], word
    constraints = (ROOT / "nora" / "doctrine" / "core" / "constraints.md").read_text()
    assert "我唔知」 and 「我查唔到」 are real" in " ".join(constraints.split())


def test_the_confusable_retrieval_tools_all_declare_a_source_tier():
    """The taxonomy must stay COMPLETE over the tools routing actually confuses —
    a new local tool added untagged would silently sit outside the priority. Pins
    the tier of each tool in the seed confusability clusters (D2)."""
    from nora.agent.tools import NATIVE_TOOLS
    expected = {
        "weather": "confirmed", "transit": "confirmed", "route": "confirmed",
        "place_info": "confirmed", "where_is": "confirmed",
        "reverse_geocode": "confirmed",
        "saved_places": "remembered", "web_search": "web", "web_fetch": "web",
        "x_read": "web", "x_search": "web",
        "linkedin_read": "web", "linkedin_search": "web",
        "instagram_read": "web", "instagram_search": "web",
    }
    for name, tier in expected.items():
        assert NATIVE_TOOLS[name].source_tier == tier, name


# ---- the plugin-provided activation tool (DEC-50, activating DEC-48) ----------

def test_skills_is_budgeted_so_r7_admits_it_but_the_registry_does_not_build_it():
    """The `skills` activation tool is registered by `AgentSkills`
    (`nora.agent.factory`), not by `build_tool_specs`. It needs exactly ONE
    thing from this table — a budget, so R-7's default-deny lets `ReplyGuard`
    admit the activation call — and must appear in NOTHING the registry BUILDS
    or RENDERS, or the offline/eval compositions (which build the capability
    block but never wire the plugin) would promise a tool they do not have."""
    registry = ToolRegistry()
    # Admitted by R-7.
    assert "skills" in registry.budgets()
    assert registry.budgets()["skills"] == 2
    # Not built, not advertised, not in the catalogue gate.
    built = {t.tool_name for t in _built_tools(registry)}
    assert "skills" not in built
    assert "skills" not in registry.tool_names()
    # Neither terminal nor live-only — the honest fields.
    assert "skills" not in registry.terminal_tools()


def test_skills_declares_terminal_false_and_no_time_dimension():
    from nora.agent.tools import NATIVE_TOOLS
    spec = NATIVE_TOOLS["skills"]
    assert spec.plugin is True
    assert spec.terminal is False
    assert spec.time_shiftable is None


# ---- discovered MCP tools (spec §3, Task 4) -----------------------------------

class _FakeMcpTool:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name


def _mcp_groups() -> dict[str, McpToolGroup]:
    return {
        "pulse": McpToolGroup(
            budget=5,
            tools=(_FakeMcpTool("pulse_server_status"), _FakeMcpTool("pulse_weekly")),
        ),
    }


def test_budgets_excludes_mcp_tools_not_in_allowed_mcp():
    """R-7's default-deny must actually cover an unauthorized server's tool
    names (Finding 1, final-review): `budgets()` called with no `allowed_mcp`
    (the default, matching an unauthorized conversation) must not admit
    `pulse_server_status`/`pulse_weekly` at all — not merely leave them
    un-callable elsewhere."""
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets()
    assert "pulse_server_status" not in budgets
    assert "pulse_weekly" not in budgets
    # native budgets are untouched
    assert budgets["now"] == 4


def test_budgets_includes_mcp_tools_only_for_allowed_servers():
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets(allowed_mcp=frozenset({"pulse"}))
    assert budgets["pulse_server_status"] == 5
    assert budgets["pulse_weekly"] == 5
    # native budgets are untouched
    assert budgets["now"] == 4


def test_unauthorized_conversations_veto_message_never_names_mcp_tools():
    """The R-7 unknown-tool veto message is built from `ReplyGuard`'s
    `budgets` dict (`guards.py::_list_tool_names`). A conversation not
    authorized for `pulse` must never have that server's tool names appear in
    the message the MODEL sees when it hallucinates a wrong tool name —
    the spec's non-disclosure claim, concretely (Finding 1)."""
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets()  # allowed_mcp defaults empty: unauthorized
    guard = ReplyGuard(
        conversation_id="g1", budgets=budgets, terminal=registry.terminal_tools(),
        metrics=Metrics(),
    )
    message = guard.evaluate("totally_made_up_tool", {})
    assert message is not None
    assert "pulse_server_status" not in message
    assert "pulse_weekly" not in message


def test_registry_derivations_are_unaffected_by_mcp_tools():
    plain = ToolRegistry()
    with_mcp = ToolRegistry(mcp_tools=_mcp_groups())
    assert with_mcp.tool_names() == plain.tool_names()
    assert with_mcp.terminal_tools() == plain.terminal_tools()


def test_build_omits_mcp_tools_when_allowed_mcp_is_empty():
    tools = _built_tools(ToolRegistry(mcp_tools=_mcp_groups()))
    names = {t.tool_name for t in tools}
    assert "pulse_server_status" not in names
    assert "pulse_weekly" not in names


def test_build_includes_only_the_allowed_mcp_servers_tools():
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    tools = registry.build(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(), allowed_mcp=frozenset({"pulse"}),
    )
    names = {t.tool_name for t in tools}
    assert {"pulse_server_status", "pulse_weekly"} <= names


# ---- [llm.budgets]: a per-deployment DELTA, not a second table -------------


def test_a_budget_override_moves_exactly_the_named_tool():
    """DEC-10 puts the number with the tool and DEC-32 allows exactly one copy
    of it. `[llm.budgets]` does not break that: the table still states every
    default beside the tool it belongs to, config states a delta, and
    `budgets()` is still the single place anything resolves one.

    The alternative that was asked for first — putting `send_reply` alone into
    config.toml — WOULD have broken it: fifteen budgets in code and one in
    config is the split those decisions exist to prevent, and the prose in the
    schema ("up to six") would have gone on lying while the number moved."""
    default = ToolRegistry().budgets()
    overridden = ToolRegistry(budget_overrides={"send_reply": 9}).budgets()

    assert default["send_reply"] != 9                 # the table's own value
    assert overridden["send_reply"] == 9
    # Every OTHER tool is untouched — an override is a delta, not a replacement.
    assert {k: v for k, v in overridden.items() if k != "send_reply"} == \
           {k: v for k, v in default.items() if k != "send_reply"}


def test_no_override_leaves_the_table_exactly_as_it_is():
    assert ToolRegistry(budget_overrides={}).budgets() == ToolRegistry().budgets()
    assert ToolRegistry(budget_overrides=None).budgets() == ToolRegistry().budgets()


def test_cacheable_tools_matches_the_declared_set():
    from nora.agent.registry import ToolRegistry
    assert ToolRegistry().cacheable_tools() == frozenset(
        {"place_info", "route", "web_search", "web_fetch", "weather", "transit",
         "music_status", "x_read", "x_search", "instagram_search"}
        # where_been is deliberately NOT cacheable (Kimi audit B1): the recall
        # log replays a persisted result verbatim with no guard re-check, so a
        # cached `ok` would outlive the roster it was authorized under.
    )


def test_cacheable_tools_is_a_frozenset_not_a_list():
    from nora.agent.registry import ToolRegistry
    assert isinstance(ToolRegistry().cacheable_tools(), frozenset)


# ---- read/act: the Dreamer's ceiling (keystone §4) -------------------------


# Hand-listed HERE, in the test, and DERIVED in the code — deliberately the
# opposite way round from the pre-DEC-29 catalogue. The spec's own list of act
# verbs was four short (the whole events family, added by DEC-117 after the
# mental model behind the list was formed), which is the strongest available
# argument for deriving the set: a plan that copied the six would have handed a
# background agent four write verbs.
ACT_VERBS = frozenset({
    "remind", "cancel_reminder",
    "event_propose", "event_revise", "event_rsvp", "event_set_status",
    # design/sandbox-runtime §4: a command, a file write and a file edit all
    # change the room's workspace.
    "computer_run", "computer_write", "computer_edit",
    # design/browser-runtime B2: the sub-agent clicks and types in a page.
    "computer_browse",
    # S3a: an export publishes (a Drop link, an attachment) or stores, and an
    # import writes into the workspace.
    "computer_export", "computer_import",
    # design/artifact §26: commits a document other people can open.
    "document_publish",
    # Appends a `document_state` row: archives, deletes or restores a document.
    "document_set_status",
    # design/subagent §3: starting a worker and ending one are acts (a worker runs, a row is
    # written); the list and the read are reads.
    "subagent_create", "subagent_cancel",
    # design/subagent §4d: the worker's checkpoint replaces a row's value, an act.
    "checkpoint_update",
    "send_reply", "send_to", "send_gif",
    # DEC-137: generation is an external side effect (provider call +
    # artifact hosting), never a read — the Dreamer may not generate.
    "generate_image",
    # design/video-generation: starts a clip on a provider and writes a job row.
    "video_create",
    # media_send delivers a stored attachment (an act, like send_gif);
    # media_search is a read.
    "media_send",
    # The note tier's two write verbs (landed on master 2026-08-21). A read
    # verb that forgets the flag fails the classification test by name; these
    # two are acts and belong here.
    "note_create", "note_update",
    # The remember verb (2026-08-30): writing a Honcho conclusion is a write —
    # the Dreamer may never be granted it, same as the note verbs.
    "remember",
    # Discord-only music (design/discord-music/07-agent-tool.md) — every verb
    # but `music_status` mutates the voice session or its queue.
    "play_music", "skip_music", "top_music", "stop_music",
    "set_music_volume",
})

# The full read-only set as declared today. Pinned so that ADDING a tool is a
# decision rather than an omission: a new read tool that forgets the flag turns
# this red as MISSING, and a new act verb that wrongly carries it turns the
# test above red as a LEAK. The two directions are what make a fail-closed
# default safe.
READ_ONLY = frozenset({
    # design/tool-catalogue/02: registers shelf tools onto this turn's own
    # agent and nothing else — no store, no room, nothing outside the turn.
    "load_tools",
    "now", "calculate", "transit", "route", "where_is", "place_info",
    # DEC-156: a pure coordinate -> address lookup, no writes anywhere.
    "reverse_geocode",
    # DEC-155: `where_been` reads the derived substrate — a read everywhere
    # (SELECT-only store reads, no writes), Dreamer-ELIGIBLE but NOT granted
    # (spec §7.4): a read verb that forgets the flag turns the classification
    # test red by name, so it is pinned here like every other read.
    "where_been",
    # design/27 §4.3: `routine_read` reads the same substrate through the same
    # guard; a read everywhere, room-roster-scoped, not granted to a Dreamer.
    "routine_read",
    # design/online-status/: `online_now` is a pure read at every layer —
    # one batched Steam GetPlayerSummaries call and the Discord gateway's
    # voice cache, no writes anywhere. Dreamer-ELIGIBLE (the proactive
    # decide turn holds it — the #248 cross-check) but NOT in
    # `UNIT_SAFE_TOOLS`: it is room-roster-scoped, same as where_been.
    "online_now",
    # design/self-awareness/07: the room tools read policy, health, rosters
    # and stored messages, and write nothing.
    "room_list", "room_read",
    # design/cognition-loop/15-map.md §4: a Record read under lineage.
    "record_query",
    "saved_places", "who_said", "load_transcript", "group_members", "lookup_person",
    "about", "ask_memory", "search_memory", "recall_lookups", "media_fetch",
    # DEC-316: the graph surface's memory search, an index read
    "memory_search",
    # design/external-files §3: reads a shared file; the account cannot write.
    "file_fetch",
    "media_search",
    "web_search", "web_fetch", "weather", "list_reminders", "event_search",
    "suggest_venue", "version", "search_gifs",
    # The two parking verbs (2026-08-31): live reads that act on nothing —
    # vacancy lookups and nearby discovery, no writes anywhere.
    "parking_status", "find_parking",
    "note_read", "note_search",
    # DEC-306: one kept memo of the unit, a SELECT.
    "memo_read",
    # design/cognition-loop/25 §5, 50 §2: Dreamer-only SELECTs of the Rollup's
    # current build and of a room's Standing head.
    "rollup_read", "standing_read", "character_read",
    # The unit-wide memory variant — a pure read of a store the tag path
    # already trusts, granted to a unit-keyed adaptor through
    # `UNIT_SAFE_TOOLS`.
    "search_memory_workspace",
    "music_status",
    # DEC-151, DEC-201: the X tools are pure reads at all three layers, the
    # tools' closed vocabulary, the bridge's host-side allowlist, this
    # declaration.
    "x_read", "x_search",
    # DEC-201: the LinkedIn tools, the same three layers.
    "linkedin_read", "linkedin_search",
    # R-86 extended (design/instagram-adoption/18): the same three layers,
    # with a client that only ever issues GET.
    "instagram_read",
    # R-86 again (P11): a one-row vocabulary, and the same GET-only client.
    "instagram_search",
    # design/sandbox-runtime §4: reads a file in the room's own workspace and
    # writes nothing there (its audit row is a log of the call, like
    # `_log_call`, not an effect of it). Eligible, not granted.
    "computer_read",
    # design/sandbox-runtime §15.2: reads Nora's own session rows and the
    # room's observed state; writes nothing.
    "computer_session_list",
    # design/artifact §26: reads one document of the room; writes nothing.
    "document_read",
    # design/subagent §3: this room's background jobs; a read of the outbox row and the
    # worker's in-memory progress. Eligible, never granted (tag-only by `ToolScope`).
    "subagent_list", "subagent_read",
})


def test_no_act_verb_is_ever_read_only():
    """The hard line (keystone §4): the Dreamer may look and may not act.

    A background process that can speak to the group unprompted — or set a
    reminder, or write to the events store — with nobody waiting on it is a
    categorically different thing from one that can only read. This is the test
    that makes that a property of the runtime rather than a convention."""
    assert ACT_VERBS & ToolRegistry().read_only_tools() == frozenset()


def test_every_registered_tool_declares_whether_it_only_reads():
    """A tool that neither reads nor acts does not exist.

    `skills` is the one exception and it is not a hole: `plugin=True` marks a
    tool this table gives a BUDGET but does not build or describe (DEC-50) —
    the activation is registered by Strands' own plugin, so there is nothing
    here to classify. It is excluded from the Dreamer by the fail-closed
    default, which is the correct answer for a tool nobody here builds."""
    from nora.agent.tools import NATIVE_TOOLS

    unclassified = {
        name for name, spec in NATIVE_TOOLS.items()
        if not spec.plugin and not spec.read_only and name not in ACT_VERBS
    }
    assert unclassified == set(), (
        "every native tool must be a read or an act: " + ", ".join(sorted(unclassified))
    )
    assert {n for n, s in NATIVE_TOOLS.items() if s.plugin} == {"skills"}


def test_read_only_tools_is_the_declared_set():
    """The ceiling, pinned. `read_only_tools()` is not a grant — every adaptor
    declares its own narrower subset by name and the runtime refuses anything
    outside this set — so this test asserts what is GRANTABLE, not what any
    Dreamer run actually holds."""
    assert ToolRegistry().read_only_tools() == READ_ONLY


def test_read_only_tools_is_a_frozenset_not_a_list():
    assert isinstance(ToolRegistry().read_only_tools(), frozenset)


def test_the_read_and_act_halves_partition_the_table():
    """Read plus act plus the one plugin is the whole table, with no overlap.

    Stated as a partition rather than as two separate memberships because the
    failure this guards is a tool added to one list and forgotten in the other
    — the exact failure `NativeTool`'s own docstring says the table exists to
    prevent."""
    from nora.agent.tools import NATIVE_TOOLS

    assert READ_ONLY | ACT_VERBS | {"skills"} == set(NATIVE_TOOLS)
    assert not READ_ONLY & ACT_VERBS


def test_a_room_catalog_omits_the_dreamer_only_memory_verb():
    """#350: `search_memory_workspace` returns VERBATIM lines, attributed by
    name, from Honcho's workspace `/search` — the whole deployment, every
    unit — filtered only by identity.toml's address book. Its own body says
    it "exists for the Dreamer … never a quote into any room", and until
    2026-09-19 that sentence was false: it shipped in every room's catalog.

    `dream_adaptor` is the gate, so an ordinary tag turn must not see it and
    a Dreamer build must.
    """
    tag = {t.tool_name for t in _built_tools(ToolRegistry(), dream_adaptor="")}
    dreamer = {t.tool_name for t in _built_tools(ToolRegistry())}

    assert "search_memory_workspace" not in tag
    assert "search_memory_workspace" in dreamer
    # Nothing ELSE moved with it — the gate is one verb wide, not a catalog
    # rewrite that happens to drop this one.
    assert dreamer - tag == {"search_memory_workspace"}
    assert tag - dreamer == set()
