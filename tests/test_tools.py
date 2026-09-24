import json
import re

import pytest

from nora.agent.guards import Deliverer
from nora.agent.markdown import render_markdown
from nora.agent.outbound import OutboundComposer
from nora.agent.room import Room
from nora.agent.tools import (
    LOAD_TRANSCRIPT_DEFAULT,
    LOAD_TRANSCRIPT_MAX,
    NATIVE_TOOL_BUDGETS,
    build_tool_specs,
    build_tools,
    note_handle,
)
from nora.doctrine.loader import DoctrineStore
from nora.domain.media import GifCandidate, MediaObject
from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef, Quote
from nora.domain.ports import ImageResult, MessageHits, OutboundAttachment, SendResult
from nora.domain.utf16 import utf16_len
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
from nora.testing import (
    FakeBlobStore,
    FakeMediaObjectStore,
    FakeMemoryStore,
    FakeRosterSource,
)
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

TARGET = ConversationTarget("signal", "g1", "group")

# `transit` is the one tool here with a collaborator outside the host, and
# these fixtures are not about it. A real `TransitClient` aimed at a closed port
# is used rather than a stand-in object: CLAUDE.md forbids a port gaining a
# second implementation without a shared contract suite, and a one-method stub
# here would be exactly that. Pointed at `127.0.0.1:1` it refuses immediately
# and the tool degrades to `unavailable`, which is real behaviour rather than a
# fake agreeing with whatever it is asked. `tests/test_transit.py` exercises it
# against a server that behaves like the two real operators.
UNREACHABLE_TRANSIT = TransitClient(
    kmb_base_url="http://127.0.0.1:1",
    mtr_schedule_url="http://127.0.0.1:1/getSchedule",
    mtr_routes_url="http://127.0.0.1:1/mtr_bus_routes.csv",
    timeout_seconds=0.5,
)



@pytest.fixture
def wiring_with(wiring):
    """Rebuild the tool set with one extra collaborator, reusing everything
    `wiring` already stood up.

    A separate fixture rather than parameters on `wiring`, because the point
    of `wiring` is the DEFAULT production shape — the one where a collaborator
    is absent — and several tests below depend on that being what they get.
    """
    def _build(**extra):
        tools = build_tools(
            store=wiring["store"], conversation_id="g1",
            conversation_type="group", persons=wiring["persons"],
            honcho=wiring["honcho"], deliverer=wiring["deliverer"],
            metrics=wiring["metrics"], room=wiring["room"],
            transit_source=UNREACHABLE_TRANSIT,
            question="who is here", context_window=40,
            # Several verbs are unit-scoped, so a fixture that did not
            # name one would exercise only the no-unit refusal — which is
            # a real path with its own test, and is not the path any of these
            # are about.
            **{"unit_for": lambda *_a: "ws-main", **extra},
        )
        return {t.tool_name: t for t in tools}

    return _build


@pytest.fixture
async def wiring(pg_url, tmp_path):
    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url))
    for i in range(3):
        await store.append(message=NormalizedMessage("signal", "g1", "group", "+852111",
                                                      "Miles", i, f"m{i}", {}))
    # A second, unrelated conversation with its own rows — the cross-group
    # leak probe (Task 1) needs a real second group to prove load_transcript
    # cannot be steered into it by a model-supplied conversation_id.
    for i in range(3):
        await store.append(message=NormalizedMessage("signal", "g2", "group", "+852222",
                                                      "Alice", i, f"secret{i}", {}))
    persons_file = tmp_path / "identity.toml"
    persons_file.write_text(
        "[persons.miles]\nname = \"Miles\"\n[[persons.miles.signal]]\nphone = \"+852111\"\n"
    )
    pdir = tmp_path / "doctrine"
    pdir.mkdir()
    write(pdir, "identity.md", name="soul", order=10, body="You are Nora.")
    doctrine = DoctrineStore(pdir, canary="C", metrics=Metrics()).get()
    sink = SinkSender()
    honcho = FakeMemoryStore(cards={"miles": ["likes espresso"]})
    deliverer = Deliverer(store=store, adapter=sink, target=TARGET, doctrine=doctrine,
                          canary="C", tag_id="t1", nora_sender_id="nora",
                          message_cap=NATIVE_TOOL_BUDGETS["send_reply"],
                          audience="tag", metrics=Metrics())
    metrics = Metrics()
    persons = load_persons(persons_file)
    # No roster source: this fixture is about the tools that do not need one.
    # `Room` reports that as UNAVAILABLE, never as an empty room —
    # tests/test_room_tools.py is where the three room-scoped tools are
    # exercised against a real roster.
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=persons)
    tools = build_tools(store=store, conversation_id="g1", conversation_type="group",
                        persons=persons, honcho=honcho,
                        deliverer=deliverer, metrics=metrics, room=room,
                        transit_source=UNREACHABLE_TRANSIT,
                        question="who is here", context_window=40)
    try:
        yield {"tools": {t.tool_name: t for t in tools}, "sink": sink,
               "honcho": honcho, "deliverer": deliverer, "store": store, "metrics": metrics,
               "persons": persons, "room": room, "doctrine": doctrine}
    finally:
        await store.aclose()


def test_every_declared_tool_is_built(wiring):
    assert set(wiring["tools"]) == {
        # DEC-146: the window-scoped rung of the recall ladder, below
        # `load_transcript`. Exercised in tests/test_who_said.py.
        "who_said",
        "now", "load_transcript", "send_reply", "search_gifs", "send_gif",
        "send_to",
        # design/self-awareness/07: the room tools.
        "room_list", "room_read",
        "group_members", "lookup_person", "about", "ask_memory", "search_memory",
        "recall_lookups",
        # `search_memory_workspace` IS built here — `build_tools` builds every
        # closure. Which ones a ROOM sees is `ToolScope`'s decision, applied
        # by `ToolRegistry.surface()` (design/tool-catalogue, phase B), and the
        # Dreamer-only cut (#350) is pinned there:
        # tests/unit/test_tool_registry.py (`dreamer - tag`).
        "search_memory_workspace",
        "calculate", "transit", "route",
        "place_info", "saved_places",
        "where_is",
        # DEC-156: the fetch surface — coordinate to place, the read half.
        "reverse_geocode",
        # DEC-155: the derived-presence history tool (where someone HAS BEEN).
        "where_been",
        # design/online-status/: live digital activity (online / playing /
        # voice) — exercised in tests/unit/test_online_now.py.
        "online_now",
        "media_fetch",
        "generate_image",
        "media_search", "media_send",
        "web_search", "web_fetch",
        # social_fetch (DEC-151): READ-ONLY X via the host bridge.
        "social_fetch",
        # instagram_read (design/instagram-adoption/18): a person's Instagram
        # profile or story, over the instagram-bridge.
        "instagram_read",
        # instagram_search (design/instagram-adoption/18, P11): Instagram
        # accounts matching free text, over the instagram-bridge.
        "instagram_search",
        "weather",
        # TD open-data parking vacancy (data.gov.hk hk-td-tis_5, 2026-08-31).
        "parking_status",
        # Google Nearby Search parking discovery around a named place/position.
        "find_parking",
        # The six music verbs are built here too. The Discord-only cut
        # (2026-09-16) is `ToolScope`'s, pinned by
        # `test_non_discord_catalog_omits_music_tools`.
        "play_music", "skip_music", "top_music", "stop_music",
        "music_status", "set_music_volume",
        # design/tool-catalogue/02 — the loader. Built for every turn;
        # `surface()` hands it to the model only when something is deferred.
        "load_tools",
        "remind", "list_reminders", "cancel_reminder",
        # The Event module (gatherings/RSVP/venue suggestions).
        "create_event", "update_event", "rsvp_event", "cancel_event",
        "list_events", "suggest_venue",
        # Nora Compute spec §4 (Task 5).
        "make_artifact",
        # DEC-97: what she is running, and what was in an earlier version.
        "version",
        # The note tier's four verbs (note-tier spec §5.2/§6).
        "note_read", "note_search", "note_create", "note_update",
        # The remember verb — the conclusion write lane (2026-08-30).
        "remember",
        # The unit-wide memory variant of `search_memory`.
    }


def test_read_peer_card_is_gone_from_the_table():
    """DEC-44: `read_peer_card` was R-21's last structural violation — its only
    parameter was a raw platform id the model is never shown, and it was not
    room-scoped, so a model that guessed another person's id pulled their card
    across conversation boundaries. It is deleted. This pins the deletion in
    the two places a tool can exist: `NATIVE_TOOLS` (what makes a tool callable
    at all — R-7 default-denies anything absent) and the budgets table. (The
    capability block that used to be a third assertion was deleted in DEC-150.)
    Re-adding the tool turns this red."""
    from nora.agent.tools import NATIVE_TOOLS

    assert "read_peer_card" not in NATIVE_TOOLS
    assert "read_peer_card" not in NATIVE_TOOL_BUDGETS


def test_exactly_nine_tools_are_cacheable():
    from nora.agent.tools import NATIVE_TOOLS
    cacheable = {name for name, spec in NATIVE_TOOLS.items() if spec.cacheable}
    assert cacheable == {
        "place_info", "route", "web_search", "web_fetch", "weather", "transit",
        # social_fetch (DEC-151): a public social read carries no room/person
        # disclosure decision baked into the moment it ran — same exemption
        # shape as `web_search`/`web_fetch`.
        "music_status", "social_fetch",
        # instagram_search (design/instagram-adoption/18, P11): free text in,
        # public accounts out, no roster or Tier 0 decision in the answer.
        "instagram_search",
        # where_been is deliberately NOT cacheable (Kimi audit B1): its R-31
        # gate runs against the live roster, and the recall log replays
        # verbatim with no guard re-check.
    }


def test_feed_honcho_is_not_a_tool(wiring):
    assert "feed_honcho" not in wiring["tools"]


def test_every_tool_has_a_model_facing_docstring(wiring):
    for name, tool in wiring["tools"].items():
        doc = tool.__doc__ or ""
        assert len(doc.strip()) > 40, f"{name} needs a docstring written for the model"


async def test_load_transcript_returns_rows_oldest_first(wiring):
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    assert [r["body"] for r in rows] == ["m0", "m1", "m2"]


def test_no_tool_asks_the_model_which_conversation_it_is_in(wiring):
    """DEC-24, asserted on the SCHEMA rather than on the signature.

    `inputSchema` is what Strands actually sends the model, derived from the
    callable by `FunctionToolMetadata.extract_metadata`, so this is the only
    assertion that fails if a conversation parameter comes back under any name
    or any default. Task 1's cross-group read leak lived in exactly such a
    parameter, and the first real tag died because the model could not guess
    the value of one.
    """
    for name, tool in wiring["tools"].items():
        properties = set(tool.tool_spec["inputSchema"]["json"].get("properties") or {})
        assert not properties & {"conversation_id", "conversation_type"}, (
            f"{name} makes the model invent a conversation nothing reads"
        )
    assert set(
        wiring["tools"]["load_transcript"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"limit"}
    # `reply_to` is the one thing DEC-24 permits that looks like what it
    # forbids. The rule is against a parameter the model must INVENT — a
    # conversation id it cannot know, a limit nothing downstream honours
    # better than the window's own size. `reply_to` is the opposite: `#n` is
    # printed on the transcript line in front of it, the range is closed
    # (1..N), and an out-of-range value resolves to None and sends unthreaded
    # rather than reaching some other row. It is `note_handle`'s shape — a
    # handle the model can READ — not an id it has to produce.
    assert set(
        wiring["tools"]["send_reply"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"text", "reply_to"}


def test_the_send_reply_schema_carries_the_three_reply_defects(wiring):
    """DEC-92's tool-surface half — and the reason it is HERE and not only in
    `voice.md`.

    DEC-78 measured that an always-on prose maxim did not move routing at all
    while a schema-token change moved it 0/16 → 16/16, and DEC-39 established
    the general form: the schema is the surface the model matches on. All three
    of these rules already exist in the doctrine, thousands of tokens back in
    the system prompt, and all three were violated in one live session anyway.
    This is the same sentence read at the moment the reply is actually being
    written.

    NOT a claim that it works. Unlike DEC-82's strip and DEC-89's link carry,
    nothing here is enforced — a docstring is still text addressed to a model,
    and whether it moves anything is a live measurement nobody has taken. What
    this test pins is that the surface exists and stays in step with the
    doctrine, so the measurement has something stable to measure.
    """
    # Whitespace-normalised: `adapt()` keeps the docstring's own line breaks and
    # indentation, so a clause that happens to wrap is still one sentence to the
    # model and must be one string to this test.
    description = " ".join(
        wiring["tools"]["send_reply"].tool_spec["description"].split()
    )
    # Length: the essay shape, and the specific excuse that produced it.
    assert "One or two sentences answers almost everything" in description
    assert "However much you looked up, that is not content" in description
    # The chatbot tail, which is also where the capability overclaim came from.
    assert "有咩再搵我" in description
    assert "you have no way to book, order, phone or apply for anything" in description
    # Sycophancy, at the one moment it happens.
    assert "A thing you looked up does not lose to an assertion" in description
    assert "never invent a mistake you did not make just to have something " \
           "to concede" in description
    # And the dropped link (DEC-89's prose half; the egress carry is the half
    # that does not depend on the model reading this).
    assert "pasted exactly" in description


def test_strands_drops_an_out_of_schema_argument_instead_of_rejecting_the_call(wiring):
    """The SDK behaviour DEC-24's scope counter is built on, pinned here.

    Deleting the conversation parameters only works if a model that supplies one
    anyway still gets its reply through — otherwise the fix trades a veto for a
    validation error and the turn dies the same way. Strands validates tool
    input through a pydantic model built from the signature, and pydantic
    ignores undeclared properties, so the argument is dropped and the call
    proceeds. If a future SDK tightened that to `extra="forbid"`, this test
    turns red before production does.
    """
    for name, supplied, expected in (
        # `reply_to: None` comes back because it is an OPTIONAL parameter with
        # a default, so validation fills it in. The property under test is
        # that the two CONVERSATION keys were dropped, not that the result has
        # exactly one key.
        ("send_reply", {"conversation_id": "gEVIL", "conversation_type": "group",
                        "text": "hi"}, {"text": "hi", "reply_to": None}),
        ("load_transcript", {"conversation_id": "gEVIL", "limit": 5}, {"limit": 5}),
    ):
        assert wiring["tools"][name]._metadata.validate_input(supplied) == expected


async def test_load_transcript_reads_the_bound_conversation_and_nothing_else(wiring):
    """Task 1's PROBE, now unrepresentable as an argument.

    The tools are bound to `g1` and `g2` holds another group's rows. There is
    no longer any input by which the model could ask for `g2` — the parameter
    that used to shadow the closure's bound value is gone — so the leak is
    closed by construction. This still asserts on the returned rows rather than
    on the signature: what matters is that g2's messages never come back.
    """
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    bodies = [r["body"] for r in rows]
    assert bodies == ["m0", "m1", "m2"]
    assert not any(b.startswith("secret") for b in bodies)


async def test_load_transcript_returns_only_model_relevant_fields(wiring):
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    # No raw_json, no ids. `reply_to` is absent rather than null on an
    # unthreaded row — see test_load_transcript_omits_reply_to_* below.
    assert set(rows[0]) == {"sender", "body", "ts"}


async def test_load_transcript_names_speakers_the_same_way_the_prefill_does(wiring):
    """DEC-80's THIRD renderer, which the first pass missed (both 2026-08-10
    reviews). `store.recent` hands back rows with `person_name` unset, so without
    the projection this tool renders the self-set Signal profile name while the
    prefill, `group_members` and `lookup_person` all render the identity one.

    That reproduces the original bug INSIDE ONE CONTEXT WINDOW, on the path that
    exists precisely for looking further back than the prefill: one prompt would
    hold 「Miles」 in the prefilled lines and 「陳志明 Chi Ming」 in these rows.

    Delete the `name_transcript` call in `load_transcript` and this reds."""
    store = wiring["store"]
    # Same account as the fixture's rows (+852111 → "Miles" in identity.toml),
    # but presenting a different self-set profile name on the wire.
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852111", "陳志明 Chi Ming", 8, "我返到屋企", {},
    ))
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    row = next(r for r in rows if r["body"] == "我返到屋企")
    assert row["sender"] == "Miles"
    assert not any(r["sender"] == "陳志明 Chi Ming" for r in rows)


async def test_load_transcript_keeps_the_profile_name_for_an_unmapped_speaker(wiring):
    """The same fallback `prefill._label` uses, so the two renderers cannot
    disagree in EITHER direction. `who()` used to collapse an unmapped account to
    "unknown" where the prefill kept their profile name — a second, quieter way
    for one person to have two names in one prompt."""
    store = wiring["store"]
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+85299999999", "A Stranger", 7, "hi", {},
    ))
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    row = next(r for r in rows if r["body"] == "hi")
    assert row["sender"] == "A Stranger"          # not "unknown"


async def test_load_transcript_marks_a_reply_with_the_name_it_answers(wiring):
    """design/11 §2.6: if the prefilled transcript teaches `[A ↳ B]` and
    `load_transcript` returns un-threaded rows, the older messages look
    structurally different and the model reads a missing arrow as «not a
    reply». JSON, not the arrow — a tool returns data; the arrow is prompt
    syntax."""
    store = wiring["store"]
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852333", "Tyler", 9, "你屋企", {},
        quote=Quote(author_id="+852111", text="whatever", quoted_ts=1),
    ))
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    assert rows[-1] == {"sender": "Tyler", "body": "你屋企", "ts": 9, "reply_to": "Miles"}


async def test_load_transcript_omits_reply_to_when_the_parent_is_not_in_the_rows(wiring):
    """Resolution happens ONLY within the rows this call already returns — no
    extra query from inside a tool. An unresolved parent drops the key rather
    than reporting a null the model would have to interpret."""
    store = wiring["store"]
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852333", "Tyler", 9, "你屋企", {},
        quote=Quote(author_id="+852111", text="whatever", quoted_ts=999_999),
    ))
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    assert "reply_to" not in rows[-1]


async def test_load_transcript_names_nora_the_same_way_in_reply_to_as_in_sender(wiring):
    store = wiring["store"]
    await store.append(
        message=NormalizedMessage("signal", "g1", "group", "nora", None, 8, "七點半", {}),
        is_nora=True,
    )
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852333", "Tyler", 9, "得", {},
        quote=Quote(author_id="nora", text="whatever", quoted_ts=8),
    ))
    rows = (await _call(wiring["tools"]["load_transcript"], limit=40))["messages"]
    assert rows[-2]["sender"] == "Nora"
    assert rows[-1]["reply_to"] == "Nora"


async def test_load_transcript_clamps_a_hostile_limit(wiring, monkeypatch):
    """`limit` is a model-supplied argument. The old SQLite backend rendered
    `LIMIT -1` as unbounded rather than zero — a full-conversation dump into
    context — and Postgres rejects a negative LIMIT outright; either way the
    clamp owns the bound, and `limit=0` is equally meaningless. This
    asserts what `load_transcript` actually sends downstream — independent of
    whatever the store itself does with a non-positive limit — so it fails if
    the tool-level clamp regresses even though the store has its own guard."""
    seen: list[int] = []
    real_recent = wiring["store"].recent

    async def spy(*, conversation_id, limit):
        seen.append(limit)
        return await real_recent(conversation_id=conversation_id, limit=limit)

    monkeypatch.setattr(wiring["store"], "recent", spy)
    for hostile in (-1, 0):
        rows = (await _call(wiring["tools"]["load_transcript"], limit=hostile))["messages"]
        assert len(rows) <= 1
    assert seen == [1, 1]


async def test_load_transcript_clamps_the_top_end_to_the_declared_ceiling(wiring, monkeypatch):
    """The other end of the same clamp, and the expensive one (R-24).

    `limit` is MODEL-supplied and nothing downstream bounds it — `store.recent`
    serves whatever it is handed. Measured 2026-08-06, `load_transcript(200)` cost
    6,592 tokens per call and stayed in context for every later call of the
    loop; at `budget: 2` that was 13,184 tokens, more than half the
    `total_tokens = 24000` in force at the time. This asserts what actually
    reaches the store, not what the tool returns, so it fails even on a
    conversation too short to notice the difference.
    """
    seen: list[int] = []
    real_recent = wiring["store"].recent

    async def spy(*, conversation_id, limit):
        seen.append(limit)
        return await real_recent(conversation_id=conversation_id, limit=limit)

    monkeypatch.setattr(wiring["store"], "recent", spy)
    for greedy in (LOAD_TRANSCRIPT_MAX + 1, 200, 10_000):
        await _call(wiring["tools"]["load_transcript"], limit=greedy)
    assert seen == [LOAD_TRANSCRIPT_MAX, LOAD_TRANSCRIPT_MAX, LOAD_TRANSCRIPT_MAX]
    # Spelled out, not just compared to the constant: an assertion written only
    # against `LOAD_TRANSCRIPT_MAX` asserts the implementation back to itself and
    # would stay green if the ceiling went back to 200.
    assert seen == [60, 60, 60]


async def test_load_transcript_defaults_to_less_than_the_ceiling(wiring, monkeypatch):
    """A model that does not name a `limit` is asking for a little more than
    the prefill already gave it, not for the whole room — so the default is
    not the maximum. Strands fills an omitted argument from the signature's
    own default, so this is what an unqualified call really costs."""
    seen: list[int] = []
    real_recent = wiring["store"].recent

    async def spy(*, conversation_id, limit):
        seen.append(limit)
        return await real_recent(conversation_id=conversation_id, limit=limit)

    monkeypatch.setattr(wiring["store"], "recent", spy)
    await _call(wiring["tools"]["load_transcript"])
    assert seen == [LOAD_TRANSCRIPT_DEFAULT]
    assert seen == [25]                       # the literal, for the same reason as above
    assert LOAD_TRANSCRIPT_DEFAULT < LOAD_TRANSCRIPT_MAX


async def test_send_reply_delegates_to_the_deliverer(wiring):
    assert await _call(wiring["tools"]["send_reply"], text="hello") == "sent"
    assert wiring["sink"].texts == ["hello"]
    assert wiring["deliverer"].delivered is True


def test_strip_media_markers_covers_all_marker_shapes():
    """[image <handle>] / [sticker ...] conventions are Nora-internal
    custody markers (DEC-43) — never legitimate in a visible reply.
    The image is already delivered as a real attachment, so a model that
    quotes the raw handle leaks internal identifiers to the group. The
    stripper is the hard enforcement; these shapes must all collapse."""
    from nora.agent.tools import _strip_media_markers as strip

    cases = (
        ("改完 [image c6a9cd]", "改完 "),
        ("[image c6a9cd] 開頭", " 開頭"),
        ("[image] 未 custody", " 未 custody"),
        ("[sticker 199010: A man wearing glasses, with a puzzled expression] 後記", " 後記"),
        ("[video b9d4e7] 片", " 片"),
        ("[FILE abc123] 大楷", " 大楷"),
        ("[Image c6A9Cd] 混合大小楷", " 混合大小楷"),
        ("[audio] [voice note] [document abc123] [file] 其他種類", "    其他種類"),
        ("正常文字", "正常文字"),
        ("[image a3f2c1][image bbccdd] 連續", " 連續"),
        # A shared item's still picture (2026-09-24). The alternation is built
        # from `LABELS`, so a label the transcript can carry is one a reply
        # cannot leak.
        ("[cover image c0de01] 封面", " 封面"),
        ("[Cover Image c0de01: a beach at dusk] 封面", " 封面"),
    )
    for raw, expected in cases:
        assert strip(raw) == expected, f"{raw!r} -> {strip(raw)!r}"


async def test_send_reply_strips_media_markers_before_delivery(wiring):
    """Integration: the stripped text (not the model's raw text) reaches the
    deliverer. Two sends stay under the R-2 6-message turn latch."""
    assert await _call(wiring["tools"]["send_reply"], text="改完 [image c6a9cd]") == "sent"
    assert wiring["sink"].texts[-1] == "改完 "
    assert await _call(wiring["tools"]["send_reply"], text="[image] 佔位") == "sent"
    assert wiring["sink"].texts[-1] == " 佔位"


JORDAN_ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"
OTHER_ACI = "0f8ab3c1-7d22-4e10-8b41-9c5e6f7a8b90"


async def test_the_tag_form_the_model_is_told_to_write_is_the_one_the_composer_resolves(
    wiring,
):
    """DEC-88. The @-mention MECHANISM landed with DEC-86 and the model was
    never told it exists; this is the join between the two, and the join is
    what can rot silently.

    Two halves, asserted against EACH OTHER rather than each against a literal:

    1. `send_reply`'s SCHEMA — the string Strands actually sends — must carry a
       worked `@handle` example. DEC-78/DEC-82 measured that a maxim in the
       doctrine does not move behaviour and the matched surface does, so an
       affordance the schema never shows is an affordance the model never has.
    2. `nora.agent.outbound.OutboundComposer` must RESOLVE that exact written
       form. The example is lifted OUT of the description and driven through
       the real composer, so `@[Jordan]`, `<@jordan>`, `@ Jordan`, or a parser whose
       boundary rule stops matching a bare `@Name` mid-sentence all turn this
       red — rather than shipping an instruction the machinery ignores.

    The negative half is what stops the fake from agreeing with everything: the
    SAME line, composed against a room this person is not in, yields no mention
    at all. One roster class, two answers, decided by who it was told is there.
    """
    description = wiring["tools"]["send_reply"].tool_spec["description"]
    handles = re.findall(r"@\w+", description)
    assert handles, (
        "send_reply's schema shows the model no @handle example, so DEC-86's "
        "mention mechanism is unreachable from the model's side"
    )
    assert "this conversation" in description.lower(), (
        "the schema must say a tag reaches only somebody in THIS room"
    )

    for handle in handles:
        name = handle[1:]
        persons = Persons(
            by_person={"jordan": PersonRef(person_id="jordan", name=name,
                                         honcho_peer_id="jordan", first_name=name)},
            index={("signal", JORDAN_ACI): "jordan"},
        )
        # The example inside the sentence the schema shows it in, not the bare
        # handle — so the boundary rule is exercised rather than side-stepped.
        line = next(ln.strip() for ln in description.splitlines() if handle in ln)

        # A roster that reports exactly the members it was told about, not a
        # stub that says yes: DEC-86's whole gate is that «in identity.toml»
        # and «in this room» are different questions, so a roster answering
        # the same way for both would make the positive half assert nothing.
        present = OutboundComposer(
            platform="signal", conversation_id="g1", persons=persons,
            metrics=Metrics(),
            roster_source=FakeRosterSource(groups={"g1": (frozenset({JORDAN_ACI}),)}),
        )
        # DEC-82 made `compose` take a `Styled` — the reply and its style
        # ranges never travel separately — so the advertised line goes
        # through the same one-interpretation path a real reply does.
        out = await present.compose(render_markdown(line))
        (mention,) = out.mentions
        assert out.text == line                        # the text is never rewritten
        assert mention.recipient == JORDAN_ACI
        assert mention.start == utf16_len(line[:line.index(handle)])
        assert mention.length == utf16_len(handle)
        assert JORDAN_ACI not in out.text                # R-18: the id is never text

        absent = OutboundComposer(
            platform="signal", conversation_id="g1", persons=persons,
            metrics=Metrics(),
            roster_source=FakeRosterSource(groups={"g1": (frozenset({OTHER_ACI}),)}),
        )
        assert (await absent.compose(render_markdown(line))).mentions == ()


async def test_send_reply_schema_says_a_second_call_must_earn_its_place(wiring):
    """The burst-restatement defect, measured live 2026-09-22 in the Signal
    "Friends" room: one inbound message produced ONE `tag start` and FOUR
    `send_reply` calls, all four restating the same point
    ("I have no IG, and if I made one I'd still be alone") in different
    wordings — not DEC-95's reaction/hit/twist burst.

    The budget was never the defect: `ReplyGuard._before_model` already
    relaxes `tool_choice` to `auto` after the first send, so the model could
    already have stopped. Nothing told it what a SECOND call is FOR, so it
    spent the budget it had on rephrasing. This does not measure whether the
    new wording changes model behaviour on the live route — that needs the
    eval suite, not a unit test — it pins that the schema the model actually
    reads carries the judgement at all, so a future edit cannot silently
    drop it back to the shape that shipped this incident.
    """
    description = wiring["tools"]["send_reply"].tool_spec["description"]
    lowered = description.lower()
    assert "earn its place" in lowered, (
        "send_reply's schema must tell the model a further call has to earn "
        "its place, not just fill the budget"
    )
    assert "restating the same point" in lowered, (
        "send_reply's schema must say plainly that restating the same point "
        "in different words is not a second message"
    )


async def test_send_reply_is_the_only_tool_that_can_send(wiring):
    await _call(wiring["tools"]["load_transcript"], limit=40)
    await _call(wiring["tools"]["group_members"])
    await _call(wiring["tools"]["lookup_person"], name="Miles")
    await _call(wiring["tools"]["about"], person="Miles")
    assert wiring["sink"].sent == []


async def test_a_tool_never_raises_into_the_agent_loop(wiring, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(wiring["store"], "recent", boom)
    dead = await _call(wiring["tools"]["load_transcript"], limit=40)
    assert dead["messages"] == []


# ---- every tool call is counted, by outcome ---------------------------------


async def test_the_tools_without_a_counter_of_their_own_now_have_one(wiring):
    """`nora_room_lookups_total{tool,status}` covered three of them; these had
    nothing, so "did she ever call load_transcript" was unanswerable from
    `/metrics` as well as from the log. Same `{tool, status}` shape rather than
    bespoke series."""
    tools, metrics = wiring["tools"], wiring["metrics"]
    await _call(tools["now"])
    await _call(tools["load_transcript"], limit=40)
    await _call(tools["send_reply"], text="hello")
    for tool in ("now", "load_transcript", "send_reply"):
        assert metrics.get("nora_tool_calls_total", tool=tool, status="ok") == 1, tool


async def test_the_new_counter_never_double_counts_a_room_scoped_tool(wiring):
    """Metric names are append-only and `nora_room_lookups_total` is already
    alerted on (R-22), so the three tools that had a counter keep exactly the
    one they had. A tool appearing in both series would silently double every
    dashboard that sums tool volume."""
    tools, metrics = wiring["tools"], wiring["metrics"]
    await _call(tools["group_members"])
    await _call(tools["lookup_person"], name="Miles")
    await _call(tools["about"], person="Miles")
    for tool in ("group_members", "lookup_person", "about"):
        assert metrics.get("nora_tool_calls_total", tool=tool) == 0, tool
    assert metrics.get("nora_room_lookups_total") == 3


async def test_a_tool_that_could_not_answer_is_counted_apart_from_one_that_did(
    wiring, monkeypatch,
):
    """R-22's distinction, applied to the four tools that never had it. A
    counter that reported `ok` for a `load_transcript` whose store was down would
    be the `peer_card` failure again — a green series over a dead path."""
    metrics = wiring["metrics"]

    async def boom(*a, **kw):
        raise RuntimeError("db down")

    await _call(wiring["tools"]["load_transcript"], limit=40)
    monkeypatch.setattr(wiring["store"], "recent", boom)
    await _call(wiring["tools"]["load_transcript"], limit=40)
    assert metrics.get("nora_tool_calls_total", tool="load_transcript", status="ok") == 1
    assert metrics.get("nora_tool_calls_total", tool="load_transcript", status="unavailable") == 1


async def test_a_refused_send_is_not_counted_as_a_sent_one(wiring):
    """Multi-message (DEC-42): deliveries up to the cap succeed; the one past it
    is refused by the latch and reaches nothing. Counting a refusal as `ok` would
    make `send_reply` volume look like reply volume.

    The cap is read from the registry (DEC-10/DEC-32 keep exactly one copy of
    that number) — a literal here rotted at 3→6, DEC-95."""
    from nora.agent.registry import ToolRegistry

    tools, metrics = wiring["tools"], wiring["metrics"]
    cap = ToolRegistry().budgets()["send_reply"]
    sent = [f"msg{i}" for i in range(cap)]
    for text in sent:
        assert await _call(tools["send_reply"], text=text) == "sent"
    assert await _call(tools["send_reply"], text="one too many") == "refused"
    assert metrics.get("nora_tool_calls_total", tool="send_reply", status="ok") == cap
    assert metrics.get("nora_tool_calls_total", tool="send_reply", status="refused") == 1
    assert wiring["sink"].texts == sent


# ---- calculate ---------------------------------------------------------------
#
# The evaluator itself is exercised in tests/unit/test_arithmetic.py, against
# the measurements that motivated it. What is asserted here is only what the
# TOOL adds: the wiring, the counter, and the two things the tool is
# responsible for that the pure function is not.


async def test_calculate_hands_back_the_expression_beside_the_result(wiring):
    """DEC-31's shape. A model that divides by 5 when there were 6 people is
    still wrong, and this tool cannot fix that — the expression is what makes
    the mistake visible to somebody reading the trace instead of only to the
    group, who see a confident number and no way to check it."""
    out = await _call(wiring["tools"]["calculate"], expression="1485/6")
    assert out["status"] == "ok"
    assert out["expression"] == "1485/6"
    assert out["result"] == "247.5"


async def test_calculate_is_counted_by_how_it_answered(wiring):
    """`ok` and `refused` counted apart. A refusal counted as `ok` would make
    «is the model writing expressions she can actually evaluate» unanswerable
    from `/metrics`, which is the DEC-31 failure in a new place."""
    tools, metrics = wiring["tools"], wiring["metrics"]
    await _call(tools["calculate"], expression="(1200 + 285) / 5")
    await _call(tools["calculate"], expression="__import__('os').system('x')")
    assert metrics.get("nora_tool_calls_total", tool="calculate", status="ok") == 1
    assert metrics.get("nora_tool_calls_total", tool="calculate", status="refused") == 1


async def test_calculate_never_raises_into_the_agent_loop(wiring):
    """The rule the whole module is built around: an exception here costs the
    entire reply, and a turn that dies is silent rather than wrong (R-5)."""
    for expression in ("2**9999999999", "(1).__class__", "1 +", "", "1/0",
                       "x", "lambda: 1", "(" * 5000 + "1" + ")" * 5000):
        out = await _call(wiring["tools"]["calculate"], expression=expression)
        assert out["status"] == "refused", expression
        assert out["note"], expression


async def test_calculate_does_not_echo_an_identifier_back_at_the_model(wiring):
    """`expression` is MODEL-SUPPLIED, and `+85290000101 + 0` is real
    arithmetic — a unary plus on an integer. `_asked_about` exists because a
    model does put phone numbers in a string parameter, and the same hole is
    open here: an identifier handed back inside a tool result is unsafe at
    that exit, and R-18 then fails closed on the whole reply.

    HONESTLY BOUNDED, and the bound is inherent. The RESULT of that expression
    is `85290000101`, which is a number — there is nothing about it that
    distinguishes it from arithmetic, here or at egress. What this drops is the
    echo, which is the part that is recoverable at the call site.
    """
    out = await _call(wiring["tools"]["calculate"], expression="+85290000101 + 0")
    assert out["status"] == "ok"
    assert "expression" not in out
    # An ordinary calculation keeps its echo — the guard must not cost the
    # thing the echo exists for.
    assert (await _call(
        wiring["tools"]["calculate"], expression="1485/7",
    ))["expression"] == "1485/7"


async def test_calculate_needs_none_of_the_tags_collaborators(wiring):
    """It is a pure function: no store, no daemon, no Honcho, no clock. That
    is why `unavailable` has no branch — there is nothing to be unavailable —
    and why breaking every collaborator changes nothing about its answer."""
    async def boom(*a, **kw):
        raise RuntimeError("everything is down")

    for dep in (wiring["store"], wiring["honcho"], wiring["deliverer"]):
        for attr in ("recent", "peer_card", "representation", "deliver"):
            if hasattr(dep, attr):
                setattr(dep, attr, boom)
    out = await _call(wiring["tools"]["calculate"], expression="1485/7")
    assert out["status"] == "ok"
    assert out["split"]["leftover"] == "0.02"


async def test_weather_is_unavailable_without_a_provider(wiring):
    """The wiring path the review flagged: this fixture wires no `weather_source`
    (the offline/`[weather]` off shape), so the closure must answer `unavailable`
    — never crash, never a bare empty — the same inert-by-config state keyless
    `route` gives (R-5)."""
    out = await _call(wiring["tools"]["weather"])
    assert out["status"] == "unavailable"
    assert "note" in out


async def _call(tool, **kwargs):
    """Invoke the underlying function of a @tool-decorated closure."""
    return await tool._tool_func(**kwargs)


class _GifSource:
    candidate = GifCandidate("giphy", "happy-1", "Happy dance", "A person dances happily",
                             ("happy", "dance"), "g")

    def __init__(self, *, attachment: OutboundAttachment | None = None) -> None:
        self.attachment = attachment or OutboundAttachment(b"GIF89a", "image/gif", "reaction.gif")
        self.queries: list[str] = []

    async def search(self, *, query: str) -> tuple[GifCandidate, ...]:
        self.queries.append(query)
        return (self.candidate,)

    async def fetch(self, *, candidate: GifCandidate) -> OutboundAttachment | None:
        return self.attachment if candidate == self.candidate else None


async def test_gif_tools_bind_turn_candidate_and_audit_a_gif_only_reply(wiring):
    source = _GifSource()
    tools = {tool.tool_name: tool for tool in build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=wiring["deliverer"],
        metrics=wiring["metrics"], room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        giphy_source=source,
    )}

    found = await _call(tools["search_gifs"], query="happy dance")
    assert found["metadata_source"] == "untrusted_provider"
    assert found["candidates"][0]["id"] == "happy-1"
    assert await _call(tools["send_gif"], candidate_id="happy-1") == {"status": "ok"}
    assert wiring["sink"].texts == [""]
    assert wiring["sink"].sent_attachments == [(source.attachment,)]
    row = (await wiring["store"].recent(conversation_id="g1", limit=1))[0]
    assert row.body == "[GIF: happy dance]"
    assert json.loads(row.raw_json or "{}")["media"] == {
        "provider": "giphy", "id": "happy-1", "type": "gif",
    }
    assert wiring["deliverer"].sent_texts == ()


async def test_a_gif_caption_is_stripped_of_media_markers_like_a_reply(wiring):
    """A caption is text the room sees, so a quoted marker is stripped from it
    exactly as `send_reply` strips one: from what is SENT and from what is
    RECORDED as hers."""
    source = _GifSource()
    tools = {tool.tool_name: tool for tool in build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=wiring["deliverer"],
        metrics=wiring["metrics"], room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        giphy_source=source,
    )}

    await _call(tools["search_gifs"], query="happy dance")
    out = await _call(
        tools["send_gif"], candidate_id="happy-1",
        caption="睇下呢個 [cover image c0de01] [image a3f2c1]",
    )

    assert out == {"status": "ok"}
    assert wiring["sink"].texts == ["睇下呢個  "]
    row = (await wiring["store"].recent(conversation_id="g1", limit=1))[0]
    assert "c0de01" not in row.body and "a3f2c1" not in row.body
    assert row.body == "睇下呢個 [GIF: happy dance]"


async def test_gif_send_failure_raises_so_the_agent_can_send_a_text_reply(wiring):
    source = _GifSource(attachment=None)
    source.attachment = None
    tools = {tool.tool_name: tool for tool in build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=wiring["deliverer"],
        metrics=wiring["metrics"], room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        giphy_source=source,
    )}

    await _call(tools["search_gifs"], query="happy dance")
    with pytest.raises(RuntimeError, match="GIF unavailable"):
        await _call(tools["send_gif"], candidate_id="happy-1")
    assert await _call(tools["send_reply"], text="搵唔到張啱嘅 GIF，不過笑死") == "sent"


# ---- the reaction picker on send_reply (nora/agent/reaction.py) -----------


class _Picker:
    """Stands in for `ReactionPicker`: answers from a script, counts asks."""

    def __init__(self, reaction=None) -> None:
        self.reaction = reaction
        self.asked: list[str] = []

    async def pick(self, *, recent: str, reply: str):
        self.asked.append(reply)
        return self.reaction


def _nod(mode: str):
    from nora.agent.reaction import Reaction
    return Reaction(mode=mode, query="dog nodding", candidate=_GifSource.candidate,
                    attachment=_GifSource().attachment)


async def test_a_picked_gif_goes_out_under_her_words_in_one_message(wiring, wiring_with):
    picker, source = _Picker(_nod("with")), _GifSource()
    tools = wiring_with(giphy_source=source, reaction_picker=picker)
    assert await _call(tools["send_reply"], text="乖你個頭") == "sent"
    assert wiring["sink"].texts == ["乖你個頭"]
    assert wiring["sink"].sent_attachments == [(source.attachment,)]
    row = (await wiring["store"].recent(conversation_id="g1", limit=1))[0]
    assert row.body == "乖你個頭 [GIF: dog nodding]"


async def test_a_reaction_only_reply_can_be_replaced_by_the_gif(wiring, wiring_with):
    picker, source = _Picker(_nod("alone")), _GifSource()
    tools = wiring_with(giphy_source=source, reaction_picker=picker)
    assert await _call(tools["send_reply"], text="好，抖下。") == "sent"
    assert wiring["sink"].texts == [""]
    assert wiring["sink"].sent_attachments == [(source.attachment,)]


async def test_a_longer_reply_goes_out_first_and_the_gif_follows_it(wiring, wiring_with):
    long = "10 點開波，12 點左右散，三多見，陀螺盤記得帶埋出嚟"
    picker, source = _Picker(_nod("alone")), _GifSource()
    tools = wiring_with(giphy_source=source, reaction_picker=picker)
    assert await _call(tools["send_reply"], text=long) == "sent"
    # Never replaced, whatever the picker said: it was judged after it went.
    assert wiring["sink"].texts == [long, ""]
    assert wiring["sink"].sent_attachments == [(), (source.attachment,)]
    row = (await wiring["store"].recent(conversation_id="g1", limit=1))[0]
    assert row.body == "[GIF: dog nodding]"


async def test_a_held_reply_gets_no_gif_after_it(wiring):
    picker = _Picker(_nod("with"))
    store, doctrine = wiring["store"], wiring["doctrine"]
    held = Deliverer(
        store=store, adapter=SinkSender(), target=TARGET, doctrine=doctrine,
        canary="C", tag_id="t-held", nora_sender_id="nora",
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag",
        metrics=Metrics(), send_permitted=False,
    )
    tools = {t.tool_name: t for t in build_tools(
        store=store, conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=held,
        metrics=Metrics(), room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        giphy_source=_GifSource(), reaction_picker=picker,
    )}
    long = "一個幾長嘅答案，講晒成件事點解會咁，同埋下一步點做"
    answer = await _call(tools["send_reply"], text=long)
    assert answer == "held_for_review" and picker.asked == []


async def test_only_the_first_reply_of_a_turn_is_offered_to_the_picker(wiring, wiring_with):
    picker = _Picker(None)
    tools = wiring_with(giphy_source=_GifSource(), reaction_picker=picker)
    await _call(tools["send_reply"], text="一")
    await _call(tools["send_reply"], text="二")
    assert picker.asked == ["一"]
    assert wiring["sink"].sent_attachments == [(), ()]


class _NoAttachmentsSink(SinkSender):
    """A platform that refuses any send carrying an attachment."""

    async def send(self, target, text, mentions=(), styles=(), preview=None,
                   attachments=(), quote=None):
        if attachments:
            return SendResult(ok=False, timestamp=None)
        return await super().send(target, text, mentions, styles, preview, attachments, quote)


async def test_a_gif_the_platform_refuses_still_sends_her_words(wiring):
    sink = _NoAttachmentsSink()
    deliverer = Deliverer(
        store=wiring["store"], adapter=sink, target=TARGET, doctrine=wiring["doctrine"],
        canary="C", tag_id="t-noatt", nora_sender_id="nora",
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag", metrics=Metrics(),
    )
    tools = {t.tool_name: t for t in build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=deliverer,
        metrics=Metrics(), room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        giphy_source=_GifSource(), reaction_picker=_Picker(_nod("alone")),
    )}
    assert await _call(tools["send_reply"], text="係囉") == "sent"
    assert sink.texts == ["係囉"]


async def test_a_fire_turn_never_asks_the_picker(wiring):
    picker = _Picker(_nod("with"))
    specs = build_tool_specs(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=wiring["deliverer"],
        metrics=wiring["metrics"], room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        giphy_source=_GifSource(), reaction_picker=picker, fire_turn=True,
    )
    reply = next(s for s in specs if s.name == "send_reply")
    assert await reply.call("聽日食飯") == "sent"
    assert picker.asked == [] and wiring["sink"].sent_attachments == [()]


# ---- generate_image (DEC-137, design/proposed/29) -------------------------


class _ImageGenerator:
    """A fake `ImageGenerator` the tool tests inject — no provider, no
    network. The behavioural contract over the REAL adapter lives in
    `tests/contract/test_image_generator_contract.py`; these tests pin the
    tool's own layer: the docstring gate, the wiring-gap state, the
    reference privacy gate, the direct-send path."""

    def __init__(self, result: ImageResult | None = None):
        self.result = result or ImageResult(
            status="ok", artifact="data:image/png;base64,AA==", provider="xai",
            raw=b"AA", media_type="image/png",
        )
        self.calls: list[dict] = []

    async def generate(self, **kwargs) -> ImageResult:
        self.calls.append(kwargs)
        return self.result


def _with_image_tools(wiring, *, generator=None, refs_allowed=False,
                      media_store=None, blob_store=None):
    return {tool.tool_name: tool for tool in build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=wiring["deliverer"],
        metrics=wiring["metrics"], room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        image_generator=generator, image_refs_allowed=refs_allowed,
        media_store=media_store, blob_store=blob_store,
    )}


async def test_generate_image_wiring_gap_is_unavailable(wiring):
    """`[image_gen] enabled = false` — the shipped state. The tool exists,
    and answers `unavailable` with a plain note that does NOT tell the model
    there is no such image (that would be a fabricated negative)."""
    tools = _with_image_tools(wiring, generator=None)
    result = await _call(tools["generate_image"], prompt="a cat")
    assert result["status"] == "unavailable"
    # The note states the CAPABILITY gap plainly — and instructs the model
    # not to claim the image doesn't exist (a fabricated negative).
    assert "can't make images" in result["note"].lower()


async def test_generate_image_ok_returns_the_artifact(wiring):
    gen = _ImageGenerator()
    tools = _with_image_tools(wiring, generator=gen)
    result = await _call(tools["generate_image"], prompt="a cat")
    assert result["status"] == "ok"
    # Raw bytes → direct-send path: delivered as an attachment, no URL.
    assert result["sent"] is True
    assert gen.calls[0]["prompt"] == "a cat"
    assert wiring["deliverer"].delivered is True


async def test_generate_image_reference_respects_the_privacy_gate(wiring):
    """`allow_reference_images = false` (the shipped default): a reference
    request is `unavailable` with a capability note — NOT a lookup miss and
    NOT a network call."""
    gen = _ImageGenerator()
    tools = _with_image_tools(wiring, generator=gen, refs_allowed=False)
    result = await _call(tools["generate_image"], prompt="edit this", reference="a3f2c1")
    assert result["status"] == "unavailable"
    assert gen.calls == []  # the gate refused before the provider was touched


# ---- media_search / media_send (DEC-138 — unified media library) ---------


def _make_image(
    sha256: str = "aabbccddeeff00112233445566778899" * 2,
    prompt: str = "a cat",
    source: str = "generated",
) -> MediaObject:
    return MediaObject(
        sha256=sha256, media_type="image/png", size=4, source=source,
        description=prompt,
        provenance={
            "prompt": prompt, "title": prompt[:80], "description": prompt,
            "provider": "xai", "model": "grok-imagine-image",
            "platform": "signal", "conversation_id": "g1",
        },
        created_at_ms=1_700_000_000_000,
    )


async def test_media_search_wiring_gap_is_unavailable(wiring):
    tools = _with_image_tools(wiring, generator=_ImageGenerator(), media_store=None)
    result = await _call(tools["media_search"], query="cat")
    assert result["status"] == "unavailable"


async def test_media_search_returns_metadata(wiring):
    store = FakeMediaObjectStore([_make_image()])
    tools = _with_image_tools(wiring, generator=_ImageGenerator(), media_store=store)
    result = await _call(tools["media_search"], query="cat")
    assert result["status"] == "ok"
    assert result["results"][0]["handle"] == "aabbcc"
    assert result["results"][0]["prompt"] == "a cat"


async def test_media_search_never_returns_inbound(wiring):
    """DEC-138 custody scope: media_search searches Nora's OWN media
    (generated/fetched), never inbound rows — other people's photos are not
    her library."""
    store = FakeMediaObjectStore([
        _make_image(sha256="aabbccddeeff00112233445566778899" * 2, prompt="a cat",
                    source="generated"),
        _make_image(sha256="112233445566778899aabbccddeeff00" * 2, prompt="a cat",
                    source="inbound"),
    ])
    tools = _with_image_tools(wiring, generator=_ImageGenerator(), media_store=store)
    result = await _call(tools["media_search"], query="cat")
    assert result["status"] == "ok"
    assert len(result["results"]) == 1
    assert result["results"][0]["handle"] == "aabbcc"


async def test_media_send_sends_stored_bytes(wiring):
    store = FakeMediaObjectStore([_make_image()])
    blobs = FakeBlobStore()
    blobs.blobs[_make_image().sha256] = b"PNGDATA"
    tools = _with_image_tools(wiring, generator=_ImageGenerator(),
                              media_store=store, blob_store=blobs)
    result = await _call(tools["media_send"], handle="aabbcc")
    assert result["status"] == "ok"
    assert wiring["deliverer"].delivered is True


async def test_media_send_unknown_handle_is_nothing_found(wiring):
    store = FakeMediaObjectStore([])
    blobs = FakeBlobStore()
    tools = _with_image_tools(wiring, generator=_ImageGenerator(),
                              media_store=store, blob_store=blobs)
    result = await _call(tools["media_send"], handle="ffffff")
    assert result["status"] == "nothing_found"
    assert wiring["deliverer"].delivered is False


async def test_media_send_collision_is_ambiguous(wiring):
    """DEC-138: a 6-hex prefix collision is a QUESTION, never a default —
    the old images_resend silently picked the first; media_send must refuse
    exactly like media_fetch does."""
    store = FakeMediaObjectStore([
        _make_image(sha256="aabbcc14924244741244445555667788" * 2, prompt="cat one"),
        _make_image(sha256="aabbcc99998888777766665555444433" * 2, prompt="cat two"),
    ])
    blobs = FakeBlobStore()
    tools = _with_image_tools(wiring, generator=_ImageGenerator(),
                              media_store=store, blob_store=blobs)
    result = await _call(tools["media_send"], handle="aabbcc")
    assert result["status"] == "ambiguous"
    assert "could_be" in result
    assert wiring["deliverer"].delivered is False


async def test_generate_image_references_passed_as_custody_handle(wiring):
    """Gate on: the marker code travels as an `ImageReference` handle, not a
    URL and not raw bytes — custody stays Nora's, adapter-side."""
    gen = _ImageGenerator()
    tools = _with_image_tools(wiring, generator=gen, refs_allowed=True)
    result = await _call(tools["generate_image"], prompt="edit this", reference="a3f2c1")
    assert result["status"] == "ok"
    refs = gen.calls[0].get("reference_images")
    assert refs is not None
    assert refs[0].marker_code == "a3f2c1"


async def test_generate_image_three_references_travel_in_call_order(wiring):
    """Up to 3 photos in one call (grok-imagine-image-2.0's multi-image
    edits): `reference_2`/`reference_3` join `reference` as handles, in the
    order passed — the first slot is not special."""
    gen = _ImageGenerator()
    tools = _with_image_tools(wiring, generator=gen, refs_allowed=True)
    result = await _call(
        tools["generate_image"],
        prompt="combine these", reference="111aaa",
        reference_2="222bbb", reference_3="333ccc",
    )
    assert result["status"] == "ok"
    refs = gen.calls[0].get("reference_images") or ()
    assert [r.marker_code for r in refs] == ["111aaa", "222bbb", "333ccc"]


async def test_generate_image_gap_in_reference_slots_is_ignored(wiring):
    """`reference_2` alone (no `reference`) still works — each slot is
    independent; the tool does not require the first to be set."""
    gen = _ImageGenerator()
    tools = _with_image_tools(wiring, generator=gen, refs_allowed=True)
    result = await _call(tools["generate_image"], prompt="edit", reference_2="222bbb")
    assert result["status"] == "ok"
    refs = gen.calls[0].get("reference_images") or ()
    assert [r.marker_code for r in refs] == ["222bbb"]


async def test_generate_image_bad_second_reference_is_nothing_found(wiring):
    """A short/garbled marker in ANY slot fails the whole call as
    `nothing_found` — never a partial edit with half the references."""
    gen = _ImageGenerator()
    tools = _with_image_tools(wiring, generator=gen, refs_allowed=True)
    result = await _call(
        tools["generate_image"], prompt="edit", reference="a3f2c1", reference_2="??",
    )
    assert result["status"] == "nothing_found"
    assert gen.calls == []


async def test_generate_image_quota_maps_to_a_plain_note(wiring):
    gen = _ImageGenerator(result=ImageResult(status="unavailable", reason="quota", provider="xai"))
    tools = _with_image_tools(wiring, generator=gen)
    result = await _call(tools["generate_image"], prompt="a cat")
    assert result["status"] == "unavailable"
    assert "quota" in result["note"].lower()


async def test_generate_image_auth_maps_to_a_plain_note(wiring):
    gen = _ImageGenerator(result=ImageResult(status="unavailable", reason="auth", provider="xai"))
    tools = _with_image_tools(wiring, generator=gen)
    result = await _call(tools["generate_image"], prompt="a cat")
    assert result["status"] == "unavailable"
    assert "re-authorise" in result["note"].lower()


class _RecordingHoncho:
    """`FakeMemoryStore` records the query but not the SCOPE, and the scope is the
    whole point of this verb: `conversation_id=None` is what makes the search
    workspace-wide (the client's `/search` endpoint), so the fake has to
    remember it."""

    def __init__(self, hits=(), *, reachable=True):
        self._hits = tuple(hits)
        self._reachable = reachable
        self.calls: list[dict] = []

    async def search(self, *, query, conversation_id=None, limit=8,
                     workspace_id=None):
        self.calls.append({"query": query, "conversation_id": conversation_id})
        return MessageHits(reachable=self._reachable, hits=self._hits)


def _unit_memory_tools(wiring, **over):
    """`build_tools` over the `wiring` fixture's collaborators, with any of
    them swapped out — `wiring_with` cannot do this (it passes `honcho`,
    `persons` and `metrics` itself), and the roster/source ARE the thing
    under test for this verb."""
    tools = build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=over.pop("persons", wiring["persons"]),
        honcho=over.pop("honcho", wiring["honcho"]),
        deliverer=wiring["deliverer"],
        metrics=over.pop("metrics", wiring["metrics"]),
        room=wiring["room"], transit_source=UNREACHABLE_TRANSIT,
        question="who is here", context_window=40,
        # `dream_adaptor` is what SHIPS this verb at all (#350): it is
        # Dreamer-only, and a tag-path build no longer carries it. Naming an
        # adaptor here is not decoration — drop it and `tools` has no
        # `search_memory_workspace` to call.
        unit_for=lambda *_a: "ws-main", dream_adaptor="proactive", **over,
    )
    return {t.tool_name: t for t in tools}


async def test_search_memory_workspace_filters_by_the_address_book(wiring):
    """The roster is `persons.by_person` — identity.toml's WHOLE address book,
    not one room's live view and not one unit's people (`PersonRef` has no
    unit). This pins the per-message guard only: a peer nobody can name never
    comes back, and neither does Nora's own. **It does not pin a scope**, and
    naming it after the unit said it did."""
    honcho = _RecordingHoncho(hits=(
        ("miles", "big walk 聽日朝早"),
        ("a_peer_nobody_mapped", "should never surface"),
        ("nora", "her own words are not the group's memory"),
    ))
    tools = _unit_memory_tools(wiring, honcho=honcho)

    out = await tools["search_memory_workspace"]("big walk")

    assert out["status"] == "ok"
    assert out["results"] == [{"who": "Miles", "said": "big walk 聽日朝早"}]
    # No room to scope to and none passed — Honcho's WORKSPACE endpoint,
    # which is the whole deployment, every unit.
    assert honcho.calls[0]["conversation_id"] is None
    # The room verb's marker of a DEGRADED view has no meaning here: there is
    # no room view to degrade, so the key is absent rather than False.
    assert "partial_view" not in out


async def test_search_memory_workspace_withholds_what_she_may_not_repeat(
    wiring,
):
    """The egress filter is SHARED with `search_memory`
    (`_memory_hits_result`): a hit she cannot legally utter is `withheld`,
    never `nothing_found` — the words exist and she may not repeat them."""
    honcho = _RecordingHoncho(
        hits=(("miles", "個 script 我 send 咗俾 +85290000101 呀"),))
    metrics = Metrics()
    tools = _unit_memory_tools(wiring, honcho=honcho, metrics=metrics)

    out = await tools["search_memory_workspace"]("script")

    assert out["status"] == "withheld"
    assert out["found_but_unquotable"] == 1
    assert "90000101" not in json.dumps(out)
    # Counted on the TOOL series, not `nora_room_lookups_total` — this verb
    # has no room, and the room series' alert would mislabel it.
    assert metrics.get("nora_tool_calls_total",
                       tool="search_memory_workspace", status="withheld") == 1


async def test_search_memory_workspace_an_outage_is_unavailable(wiring):
    """`reachable=False` is the adapter's own signal that nothing ran — the
    outage must not read as «nothing was said»."""
    tools = _unit_memory_tools(wiring, honcho=_RecordingHoncho(reachable=False))

    out = await tools["search_memory_workspace"]("big walk")

    assert out["status"] == "unavailable"


async def test_search_memory_workspace_without_a_roster_is_unavailable(wiring):
    """No persons wired at all is a WIRING gap, not an empty address book:
    an empty one legitimately filters every hit, a missing one means «I
    could not check» — and the two never share a status."""
    tools = _unit_memory_tools(
        wiring, honcho=_RecordingHoncho(hits=(("miles", "big walk"),)),
        persons=None,
    )

    out = await tools["search_memory_workspace"]("big walk")

    assert out["status"] == "unavailable"


async def test_find_parking_unavailable_without_route_source(wiring):
    """No RouteClient wired (`route_source` None — every offline/eval
    composition) → the tool answers `unavailable`, never a crash (R-5)."""
    tools = wiring["tools"]
    assert "find_parking" in tools
    out = await tools["find_parking"](place="銅鑼灣")
    assert out["status"] == "unavailable"


# ---- a HOLD is not a REFUSAL (2026-09-09) -----------------------------------
#
# `Deliverer.deliver` answers False both when a guard refuses a send and when
# the participation cooldown or the shadow gate HOLDS one. `send_reply`
# returned that bool straight to the model, so a held reply read as "it did not
# go" — and the model did the sensible thing and rewrote it shorter.
#
# Measured on the deployment: one participation turn called `send_reply` four
# times, each draft shorter than the last (Langfuse trace 5cb76676…, four
# `send_reply` TOOL spans each followed by its own `execute_event_loop_cycle`).
# Four extra model calls, and four near-identical drafts on `held_texts` that
# made the shadow review unreadable — you could not tell which one she meant.


def _held_tools(wiring):
    """The same tool set, over a Deliverer that holds every send."""
    store, doctrine = wiring["store"], wiring["doctrine"]
    held = Deliverer(
        store=store, adapter=SinkSender(), target=TARGET, doctrine=doctrine,
        canary="C", tag_id="t-held", nora_sender_id="nora",
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="participation",
        metrics=Metrics(), send_permitted=False,
    )
    tools = build_tools(
        store=store, conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=held,
        metrics=Metrics(), room=wiring["room"],
        transit_source=UNREACHABLE_TRANSIT, question="who is here",
        context_window=40, unit_for=lambda *_a: "ws-main",
    )
    return {t.tool_name: t for t in tools}, held


async def test_send_reply_tells_a_held_message_apart_from_a_refused_one(wiring):
    tools, held = _held_tools(wiring)

    answer = await _call(tools["send_reply"], text="天水圍而家 28 度。")

    assert answer == "held_for_review", (
        "a held reply that reads as a failure is what makes the model rewrite "
        "it and call again"
    )
    assert held.held_texts == ("天水圍而家 28 度。",)


async def test_send_reply_still_says_refused_when_a_guard_stops_it(wiring):
    """The contrast that keeps the fix honest: a REAL refusal must not read as
    a hold, or the model would stop retrying things it should retry."""
    sink = SinkSender()
    capped = Deliverer(
        store=wiring["store"], adapter=sink, target=TARGET,
        doctrine=wiring["doctrine"],
        canary="C", tag_id="t-capped", nora_sender_id="nora",
        message_cap=1, audience="tag", metrics=Metrics(),
    )
    tools = {t.tool_name: t for t in build_tools(
        store=wiring["store"], conversation_id="g1", conversation_type="group",
        persons=wiring["persons"], honcho=wiring["honcho"], deliverer=capped,
        metrics=Metrics(), room=wiring["room"],
        transit_source=UNREACHABLE_TRANSIT, question="who is here",
        context_window=40, unit_for=lambda *_a: "ws-main",
    )}

    assert await _call(tools["send_reply"], text="first") == "sent"
    assert await _call(tools["send_reply"], text="second") == "refused"
    assert capped.held_count == 0


def test_the_note_schemas_do_not_teach_her_a_phrase_to_say_out_loud(wiring):
    """Issue #226. Asked point-blank what she had changed, she answered by
    explaining the mechanism — that the room's 「open note」 was still unsettled.
    That is nobody's phrase but the tool layer's: `note_update`'s schema said
    «Call this when an open note's content changes», and a schema is the
    surface the model matches on (DEC-78 measured a schema-token change moving
    routing 0/16 → 16/16 where an always-on doctrine maxim moved nothing).

    A ban in `voice.md` would have been competing with the phrase in her own
    tool schema, so the phrase goes instead. The tools' CONTRACT is untouched —
    the three states, the read-before-write latch, the argument descriptions —
    and this pins the vocabulary.

    **Only the phrase moves, and the replacement stays concrete**, which is not
    a stylistic note: a first attempt replaced it with «when what one of these
    is keeping track of changes» and the act suite's `a01` speak rate fell to
    4/15 and 5/15 against a baseline that ran 5 to 12 of 15. That is inside a very
    wide noise band and so proves nothing about the vaguer wording, but it is
    the direction DEC-78 measured in the other direction (a schema-token change
    moved routing 0/16 → 16/16), and a docstring that opens on a dangling «one
    of these» costs legibility for nothing. «a note's content» drops the phrase
    and keeps the referent.

    Asserted on `tool_spec["description"]`, which is what Strands actually
    sends, not on `__doc__`.
    """
    def described(name: str) -> str:
        return " ".join(wiring["tools"][name].tool_spec["description"].split()).lower()

    for name in ("note_read", "note_update", "note_create", "note_search"):
        assert "open note" not in described(name), (
            f"{name}'s schema teaches «open note» — the tool layer's phrase, "
            "not the room's, and she says what her schemas call things."
        )
    # The latch and the three states are untouched by the rephrasing.
    update = described("note_update")
    assert "you must call note_read on this note first, in this same turn" in update
    for state in ("`ok`", "`refused`", "`unavailable`"):
        assert state.strip("`") in update


# ---- a note title survives the trip through the index (2026-09-10) ----------


def test_a_note_handle_round_trips_through_an_escaped_index():
    """The title IS the handle, and every index escapes what it renders (R-13):
    `note_render` and the proactive `<open_notes>` both put it through
    `escape`. So a note called «Dinner & drinks» reached the model as
    «Dinner &amp;amp; drinks» — and `note_read`/`note_update`/`remind` tell it to
    type the title back "exactly as `<notes>` shows it", then refused that
    string because the stored title has no entity in it.

    The cost was more than a refused call. A proactive `remind` written without
    its `note_id` falls through `ProactiveScreen` to the flag-only path, so the
    cancellation that fires when a note has moved on never runs — the decision
    becomes unlinkable rather than merely retried.
    """
    from nora.domain.prompt import escape
    from nora.language.note_render import render_notes  # noqa: F401  (the index)

    for title in ("Dinner & drinks Saturday", "Trip <Sep> plan", "A & B > C"):
        assert note_handle(escape(title)) == note_handle(title), title


def test_the_handle_still_ignores_case_and_surrounding_space():
    """The properties that were already there, kept alongside the new one — a
    model that types the line back with a trailing space, or in another case,
    has not named a different note."""
    assert note_handle("  Dinner & Drinks  ") == note_handle("dinner & drinks")


def test_a_quote_finds_the_platform_id_under_EITHER_spelling():
    """Discord and WhatsApp key the platform's own message id `id`; Instagram's
    bridge event keys it `message_id`. Reading only `id` meant every threaded
    reply the model made on Instagram went out UNTHREADED — delivered, marked
    `sent`, no visual thread, and nothing logged, because a missing quote id is
    a documented degradation rather than an error.

    Found by audit, not by anyone noticing, which is what a silent degradation
    earns you.

    PROBE: drop `message_id` from the tuple and the Instagram case reds.
    """
    from nora.agent.tools import platform_message_id_of

    assert platform_message_id_of({"id": "1234567890"}) == "1234567890"
    assert platform_message_id_of({"message_id": "mid.$gAASG4nv"}) == "mid.$gAASG4nv"
    # `id` wins when both are present: it is the older spelling and the one
    # three platforms already store, so a row carrying both is not Instagram's.
    assert platform_message_id_of({"id": "a", "message_id": "b"}) == "a"


def test_a_quote_id_is_never_guessed_from_an_unusable_row():
    """A quote is an enhancement. Threading a reply onto the WRONG message is
    worse than not threading it, so anything unusable is None rather than
    coerced — including `True`, which `str()` would happily turn into the
    message id "True"."""
    from nora.agent.tools import platform_message_id_of

    assert platform_message_id_of({}) is None
    assert platform_message_id_of({"id": ""}) is None
    assert platform_message_id_of({"id": True, "message_id": "mid.$real"}) == "mid.$real"
    assert platform_message_id_of("not a dict") is None
    assert platform_message_id_of(None) is None
