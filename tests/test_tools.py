import json
import re

import pytest

from nora.agent.guards import Deliverer
from nora.agent.markdown import render_markdown
from nora.agent.outbound import OutboundComposer
from nora.agent.room import Room
from nora.agent.tools import (
    KNOWLEDGE_SEARCH_LIMIT,
    LOAD_TRANSCRIPT_DEFAULT,
    LOAD_TRANSCRIPT_MAX,
    NATIVE_TOOL_BUDGETS,
    build_tools,
)
from nora.doctrine.loader import DoctrineStore
from nora.domain.media import GifCandidate, MediaObject
from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef, Quote
from nora.domain.ports import ImageResult, MessageHits, OutboundAttachment, Roster
from nora.domain.utf16 import utf16_len
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
from tests.fakes import FakeHoncho, SinkSender
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
            # The knowledge tier is workspace-scoped, so a fixture that did
            # not name one would exercise only the no-workspace refusal —
            # which is a real path and has its own test, and is not the path
            # any of these are about.
            **{"workspace_for": lambda *_a: "ws-main", **extra},
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
    honcho = FakeHoncho(cards={"miles": ["likes espresso"]})
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
               "persons": persons, "room": room}
    finally:
        await store.aclose()


def test_every_declared_tool_is_built(wiring):
    assert set(wiring["tools"]) == {
        # DEC-146: the window-scoped rung of the recall ladder, below
        # `load_transcript`. Exercised in tests/test_who_said.py.
        "who_said",
        "now", "load_transcript", "send_reply", "search_gifs", "send_gif",
        "send_to",
        "group_members", "lookup_person", "about", "ask_memory", "search_memory",
        "recall_lookups",
        "calculate", "transit", "route",
        "place_info", "saved_places",
        "where_is",
        # DEC-155: the derived-presence history tool (where someone HAS BEEN).
        "where_been",
        "media_fetch",
        "generate_image",
        "media_search", "media_send",
        "web_search", "web_fetch",
        # social_fetch (DEC-151): READ-ONLY X/Instagram via the host bridge.
        "social_fetch",
        "weather",
        # TD open-data parking vacancy (data.gov.hk hk-td-tis_5, 2026-08-31).
        "parking_status",
        # Google Nearby Search parking discovery around a named place/position.
        "find_parking",
        # Discord-only music (design/discord-music/07-agent-tool.md).
        "play_music", "skip_music", "top_music", "stop_music", "music_status",
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
        # The knowledge tier's retrieval verb (23 §9), its graph reads
        # (32 §3.4), and the workspace-wide memory variant.
        "search_knowledge",
        "get_knowledge", "knowledge_premises", "knowledge_asserters",
        "knowledge_entity", "search_memory_workspace",
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


def test_exactly_eight_tools_are_cacheable():
    from nora.agent.tools import NATIVE_TOOLS
    cacheable = {name for name, spec in NATIVE_TOOLS.items() if spec.cacheable}
    assert cacheable == {
        "place_info", "route", "web_search", "web_fetch", "weather", "transit",
        # social_fetch (DEC-151): a public social read carries no room/person
        # disclosure decision baked into the moment it ran — same exemption
        # shape as `web_search`/`web_fetch`.
        "music_status", "social_fetch",
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
    assert set(
        wiring["tools"]["send_reply"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"text"}


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
        ("send_reply", {"conversation_id": "gEVIL", "conversation_type": "group",
                        "text": "hi"}, {"text": "hi"}),
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
    assert await _call(wiring["tools"]["send_reply"], text="hello") is True
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
    )
    for raw, expected in cases:
        assert strip(raw) == expected, f"{raw!r} -> {strip(raw)!r}"


async def test_send_reply_strips_media_markers_before_delivery(wiring):
    """Integration: the stripped text (not the model's raw text) reaches the
    deliverer. Two sends stay under the R-2 6-message turn latch."""
    assert await _call(wiring["tools"]["send_reply"], text="改完 [image c6a9cd]") is True
    assert wiring["sink"].texts[-1] == "改完 "
    assert await _call(wiring["tools"]["send_reply"], text="[image] 佔位") is True
    assert wiring["sink"].texts[-1] == " 佔位"


class _RosterHolding:
    """A `RosterSource` that reports exactly the members it was told about.

    Deliberately not a stub that says yes to everything: DEC-86's whole gate is
    that «in identity.toml» and «in this room» are different questions, so a
    roster answering the same way for both would make the positive half of the
    test below assert nothing.
    """

    platform = "signal"

    def __init__(self, *members: str) -> None:
        self._members = tuple(frozenset({m}) for m in members)

    async def roster(self, *, conversation_id: str) -> Roster:
        return Roster(available=True, members=self._members)


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

        present = OutboundComposer(
            platform="signal", conversation_id="g1", persons=persons,
            metrics=Metrics(), roster_source=_RosterHolding(JORDAN_ACI),
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
            metrics=Metrics(), roster_source=_RosterHolding(OTHER_ACI),
        )
        assert (await absent.compose(render_markdown(line))).mentions == ()


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
        assert await _call(tools["send_reply"], text=text) is True
    assert await _call(tools["send_reply"], text="one too many") is False
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
    assert row.body == "[GIF via giphy]"
    assert json.loads(row.raw_json or "{}")["media"] == {
        "provider": "giphy", "id": "happy-1", "type": "gif",
    }
    assert wiring["deliverer"].sent_texts == ()


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
    assert await _call(tools["send_reply"], text="搵唔到張啱嘅 GIF，不過笑死") is True


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


class _FakeMediaObjectStore:
    """Fake `MediaObjectStore` — in-memory rows, no SQL."""

    def __init__(self, objs: list[MediaObject] | None = None):
        self.objs = list(objs or [])
        self.saved: list[MediaObject] = []

    async def save(self, *, obj: MediaObject) -> bool:
        self.saved.append(obj)
        self.objs.append(obj)
        return True

    async def search(
        self, *, query: str, limit: int = 20,
        sources: tuple[str, ...] = (),
    ) -> tuple[MediaObject, ...]:
        pool = self.objs
        if sources:
            pool = [o for o in pool if o.source in sources]
        if not query:
            return tuple(pool[:limit])
        q = query.casefold()
        return tuple(
            o for o in pool[:limit]
            if q in o.description.casefold() or q in o.prompt.casefold()
            or q in o.title.casefold()
        )

    async def get(self, *, sha256: str) -> MediaObject | None:
        return next((o for o in self.objs if o.sha256 == sha256), None)

    async def by_prefix(
        self, *, handle: str, limit: int = 20,
    ) -> tuple[MediaObject, ...]:
        return tuple(
            o for o in self.objs[:limit] if o.sha256.startswith(handle)
        )


class _FakeBlobStore:
    """Fake `BlobStore` — a content-addressed dict."""

    def __init__(self, blobs: dict[str, bytes] | None = None):
        self.blobs = dict(blobs or {})

    async def put(self, *, data: bytes) -> str:
        import hashlib
        key = hashlib.sha256(data).hexdigest()
        self.blobs[key] = data
        return key

    async def get(self, *, key: str) -> bytes | None:
        return self.blobs.get(key)


def _make_image(
    sha256: str = "aabbccddeeff00112233445566778899",
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
    store = _FakeMediaObjectStore([_make_image()])
    tools = _with_image_tools(wiring, generator=_ImageGenerator(), media_store=store)
    result = await _call(tools["media_search"], query="cat")
    assert result["status"] == "ok"
    assert result["results"][0]["handle"] == "aabbcc"
    assert result["results"][0]["prompt"] == "a cat"


async def test_media_search_never_returns_inbound(wiring):
    """DEC-138 custody scope: media_search searches Nora's OWN media
    (generated/fetched), never inbound rows — other people's photos are not
    her library."""
    store = _FakeMediaObjectStore([
        _make_image(sha256="aabbccddeeff00112233445566778899", prompt="a cat", source="generated"),
        _make_image(sha256="112233445566778899aabbccddeeff00", prompt="a cat", source="inbound"),
    ])
    tools = _with_image_tools(wiring, generator=_ImageGenerator(), media_store=store)
    result = await _call(tools["media_search"], query="cat")
    assert result["status"] == "ok"
    assert len(result["results"]) == 1
    assert result["results"][0]["handle"] == "aabbcc"


async def test_media_send_sends_stored_bytes(wiring):
    store = _FakeMediaObjectStore([_make_image()])
    blobs = _FakeBlobStore({_make_image().sha256: b"PNGDATA"})
    tools = _with_image_tools(wiring, generator=_ImageGenerator(),
                              media_store=store, blob_store=blobs)
    result = await _call(tools["media_send"], handle="aabbcc")
    assert result["status"] == "ok"
    assert wiring["deliverer"].delivered is True


async def test_media_send_unknown_handle_is_nothing_found(wiring):
    store = _FakeMediaObjectStore([])
    blobs = _FakeBlobStore({})
    tools = _with_image_tools(wiring, generator=_ImageGenerator(),
                              media_store=store, blob_store=blobs)
    result = await _call(tools["media_send"], handle="ffffff")
    assert result["status"] == "nothing_found"
    assert wiring["deliverer"].delivered is False


async def test_media_send_collision_is_ambiguous(wiring):
    """DEC-138: a 6-hex prefix collision is a QUESTION, never a default —
    the old images_resend silently picked the first; media_send must refuse
    exactly like media_fetch does."""
    store = _FakeMediaObjectStore([
        _make_image(sha256="aabbcc14924244741244445555667788", prompt="cat one"),
        _make_image(sha256="aabbcc99998888777766665555444433", prompt="cat two"),
    ])
    blobs = _FakeBlobStore({})
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


# ---------------------------------------------------------------------------
# search_knowledge — the body, which appeared in tests only as a NAME
# ---------------------------------------------------------------------------


class _FakeKnowledge:
    def __init__(self, hits=(), *, status="ok", partial=False) -> None:
        from nora.language.knowledge_search import SearchResult
        self._result = SearchResult(
            hits=tuple(hits), status=status, partial=partial,
        )
        self.calls: list[dict] = []

    async def search(self, *, scope, query, limit):
        self.calls.append({"scope": scope, "query": query, "limit": limit})
        return self._result


def _hit(row_key="0191f2c4-0000-7000-8000-00000000000a",
         text="big walk │ means │ a walk", *, disputed=False):
    from nora.language.knowledge_search import Hit
    return Hit(
        row_key=row_key, text=text, score=0.016, channels=("lexical",),
        disputed=disputed,
    )


async def test_search_knowledge_returns_no_identifier_of_any_kind(wiring_with):
    """**The rule that caught three adaptors, and this verb had nothing
    holding it.** Anything a model echoes back is an ordinal handle, never a
    stored identifier — and `Hit.row_key` IS `fact.id`, a `pg.UUID`.

    The comment above the return is a careful argument for why no identifier
    is in the shape; a mutation adding one back was green across the whole
    suite. On the Dreamer path `dream_screen` drops an envelope item carrying
    a uuid at ITEM granularity — silently — so the leak would not announce
    itself: the adaptor would just quietly return less.

    Asserted on the SERIALISED result rather than on the shape, because a
    uuid nested anywhere in it is the thing that matters, not which key holds
    it.
    """
    uuid_ish = "0191f2c4-0000-7000-8000-00000000000a"
    tools = wiring_with(knowledge=_FakeKnowledge(hits=(_hit(uuid_ish),)))

    out = await tools["search_knowledge"]("who made big walk")

    assert out["status"] == "ok"
    assert uuid_ish not in json.dumps(out), (
        "no stored identifier may reach a prompt; dream_screen drops such an "
        "item silently and the tier's counters stay healthy"
    )
    assert out["facts"] == ["big walk │ means │ a walk"]


async def test_search_knowledge_carries_the_dispute_mark(wiring_with):
    """R-79 reaching the model. A claim she holds under objection must not
    read as one she does not — the block renders the mark and this path has to
    as well, or the second read path silently drops it."""
    tools = wiring_with(
        knowledge=_FakeKnowledge(hits=(_hit(disputed=True),)),
    )

    out = await tools["search_knowledge"]("big walk")

    assert out["facts"] == ["big walk │ means │ a walk — disputed"]


async def test_search_knowledge_with_no_collaborator_is_unavailable(wiring):
    """The default wiring — the tool is granted and nothing is behind it.
    `unavailable`, never `nothing_found`: a wiring gap is «I could not check»,
    and reporting «there is nothing» would have her tell the room they never
    discussed something she simply cannot search."""
    out = await wiring["tools"]["search_knowledge"]("big walk")

    assert out["status"] == "unavailable"


async def test_search_knowledge_refuses_an_empty_query(wiring_with):
    """`refused`, which is neither of R-27's silences: the caller can fix it
    and call again, and telling them so is more useful than either."""
    knowledge = _FakeKnowledge()
    tools = wiring_with(knowledge=knowledge)

    out = await tools["search_knowledge"]("   ")

    assert out["status"] == "refused"
    assert knowledge.calls == [], "an empty query must cost no search"


async def test_search_knowledge_passes_its_own_limit_not_the_models(
    wiring_with,
):
    """DEC-24: a parameter is a value the model must invent on every call, so
    each one buys a way to get the call wrong. The limit is the tool's, and it
    is 8 to match the room slice so the verb and the block speak in one
    unit."""
    knowledge = _FakeKnowledge(hits=(_hit(),))
    tools = wiring_with(knowledge=knowledge)

    await tools["search_knowledge"]("big walk")

    assert knowledge.calls[0]["limit"] == KNOWLEDGE_SEARCH_LIMIT
    assert KNOWLEDGE_SEARCH_LIMIT == 8


async def test_search_knowledge_reports_nothing_found_as_itself(wiring_with):
    """The answer that must survive: «the room has not discussed this» is
    real and useful, and the tool's own description tells the model not to
    confuse it with `unavailable`."""
    tools = wiring_with(knowledge=_FakeKnowledge(hits=(), status="nothing_found"))

    out = await tools["search_knowledge"]("never mentioned")

    assert out["status"] == "nothing_found"


async def test_search_knowledge_does_not_dress_unavailable_as_empty(
    wiring_with,
):
    """The inversion this whole tier is written against, on the verb that
    faces a user."""
    tools = wiring_with(knowledge=_FakeKnowledge(hits=(), status="unavailable"))

    out = await tools["search_knowledge"]("big walk")

    assert out["status"] == "unavailable"
    assert "facts" not in out


async def test_search_knowledge_with_no_workspace_is_unavailable(wiring_with):
    """**A wiring gap is «I could not check», never «there is nothing».**

    A conversation policy cannot name a workspace for is one nothing was ever
    written under — but that is a gap in the mapping, not a statement about
    what the room has discussed, and reporting `nothing_found` would have her
    tell them they never talked about something she cannot search for.

    This branch was untested and flipping it to `nothing_found` was green: the
    only fixture that reached the tool always supplied a workspace, and the
    one that did not supplied no collaborator either, so it returned earlier
    for a different reason. Two absent things covering for each other.
    """
    tools = wiring_with(
        knowledge=_FakeKnowledge(hits=(_hit(),)), workspace_for=None,
    )

    out = await tools["search_knowledge"]("big walk")

    assert out["status"] == "unavailable"


# ---------------------------------------------------------------------------
# the knowledge tier's graph verbs and the workspace-wide memory variant
# (design/dreamer-agent-loop/32 §3.4)
# ---------------------------------------------------------------------------


def _fact(fact_id="0191f2c4-0000-7000-8000-00000000000a",
          subject="Big Walk", predicate="means", obj="a long walk",
          *, disputed=False):
    from nora.domain.facts import Fact
    return Fact(
        id=fact_id, workspace="ws-main", subject_id=subject, predicate=predicate,
        object_entity_id=None, object_literal=obj, valid_from=1, created_at=1,
        instruction_sha256="a" * 64, model="m", established_by="run",
        source_url="https://x.test/a", source_excerpt="the excerpt",
        disputed_at=2 if disputed else None,
        disputed_by="miles" if disputed else None,
        disputed_reason="heard otherwise" if disputed else None,
        disputed_tag_id="t1" if disputed else None,
    )


def _entity(name="Big Walk", aliases=("big walk", "大步行")):
    from nora.domain.facts import FactEntity
    return FactEntity(
        id="0191f2c4-0000-7000-8000-0000000000e1", workspace="ws-main",
        canonical_name=name, created_at=1, aliases=tuple(aliases),
    )


class _FakeFacts:
    """`FactReadCollaborator`'s shape, with the outage as a first-class case —
    a fake that cannot fail answers every request the same way, which is how
    «the store is down» gets reported as «there is nothing» (R-27)."""

    def __init__(self, *, standing=(), asserters=(), premises=(), raises=False):
        self._standing = tuple(standing)
        self._asserters = tuple(asserters)
        self._premises = tuple(premises)
        self._raises = raises
        self.standing_calls: list[dict] = []

    async def standing_facts(self, *, workspace, subject_ids, limit):
        self.standing_calls.append(
            {"workspace": workspace, "subject_ids": list(subject_ids), "limit": limit})
        if self._raises:
            raise RuntimeError("fact store down")
        return self._standing

    async def asserters(self, *, fact_id):
        if self._raises:
            raise RuntimeError("fact store down")
        return self._asserters

    async def premises(self, *, fact_id):
        if self._raises:
            raise RuntimeError("fact store down")
        return self._premises


class _FakeEntities:
    def __init__(self, entity=None, *, raises=False):
        self._entity = entity
        self._raises = raises
        self.lookups: list[dict] = []

    async def lookup(self, *, workspace, name):
        self.lookups.append({"workspace": workspace, "name": name})
        if self._raises:
            raise RuntimeError("entity store down")
        return self._entity


async def test_get_knowledge_resolves_then_reads_the_surface_names(wiring_with):
    """The 32 §3.4 contract: `fact.subject_id` holds surface TEXT, not entity
    ids, so the facts read is asked with the canonical name AND the aliases —
    the strings that actually appear in `subject_id`."""
    facts = _FakeFacts(standing=(_fact(),))
    entities = _FakeEntities(_entity())
    tools = wiring_with(facts=facts, entities=entities)

    out = await tools["get_knowledge"]("big walk")

    assert out["status"] == "ok"
    assert facts.standing_calls == [{
        "workspace": "ws-main",
        "subject_ids": ["Big Walk", "big walk", "大步行"],
        "limit": KNOWLEDGE_SEARCH_LIMIT,
    }]
    assert out["facts"] == [{
        "id": "0191f2c4-0000-7000-8000-00000000000a",
        "claim": "Big Walk │ means │ a long walk",
    }]


async def test_get_knowledge_carries_the_dispute_mark(wiring_with):
    """R-79 on the graph read, exactly as on `search_knowledge`: a claim held
    under objection must not read as one she does not."""
    tools = wiring_with(
        facts=_FakeFacts(standing=(_fact(disputed=True),)),
        entities=_FakeEntities(_entity()),
    )

    out = await tools["get_knowledge"]("big walk")

    assert out["facts"][0]["claim"].endswith(" — disputed")


async def test_get_knowledge_nothing_found_when_the_name_resolves_to_nothing(
    wiring_with,
):
    """«A name that resolves to nothing is `nothing_found`, never an empty ok»
    (32 §3.4) — and no fact read is spent on a name nothing holds."""
    facts = _FakeFacts(standing=(_fact(),))
    tools = wiring_with(facts=facts, entities=_FakeEntities(None))

    out = await tools["get_knowledge"]("nobody ever said this")

    assert out["status"] == "nothing_found"
    assert facts.standing_calls == []


async def test_get_knowledge_with_no_collaborators_is_unavailable(wiring):
    """The default wiring — the tool is granted and nothing is behind it
    (`[facts] enabled = false`). `unavailable`, never `nothing_found`: a
    wiring gap is «I could not check», not «there is nothing» (R-5/R-27)."""
    out = await wiring["tools"]["get_knowledge"]("big walk")

    assert out["status"] == "unavailable"


async def test_get_knowledge_an_outage_is_unavailable_not_nothing(wiring_with):
    """`SqlEntityStore.lookup` RAISES on an unreachable store precisely so this
    branch can exist — the collapse of «store down» into «no such entity» is
    the one R-27 forbids."""
    tools = wiring_with(
        facts=_FakeFacts(standing=(_fact(),)),
        entities=_FakeEntities(_entity(), raises=True),
    )

    out = await tools["get_knowledge"]("big walk")

    assert out["status"] == "unavailable"


async def test_the_supplied_workspace_wins_over_the_room_resolver(wiring_with):
    """The key-path fix itself (32 §3.4, `08-state.md` §10): a workspace-keyed
    Dreamer adaptor's key IS the workspace, SUPPLIED to the build — so the
    verb must read IT, not the room-derived resolver's answer. The resolver
    answering a DIFFERENT workspace is what makes the assertion discriminate:
    against the old code this call read `ws-main`."""
    entities = _FakeEntities(_entity())
    tools = wiring_with(
        facts=_FakeFacts(standing=(_fact(),)), entities=entities,
        supplied_workspace="ridgeline",
    )

    out = await tools["get_knowledge"]("big walk")

    assert entities.lookups[0]["workspace"] == "ridgeline"
    assert out["status"] == "ok"


async def test_the_supplied_workspace_covers_a_missing_resolver(wiring_with):
    """The Dreamer's actual shape: no `workspace_for` at all (the synthetic
    room has no platform for policy to name), and the supplied key still
    reaches the store. Before the fix this was `unavailable` on every call,
    forever — the seam looking wired while failing closed."""
    entities = _FakeEntities(_entity())
    tools = wiring_with(
        facts=_FakeFacts(standing=(_fact(),)), entities=entities,
        workspace_for=None, supplied_workspace="ridgeline",
    )

    out = await tools["get_knowledge"]("big walk")

    assert out["status"] == "ok"
    assert entities.lookups[0]["workspace"] == "ridgeline"


async def test_get_knowledge_refuses_a_blank_subject(wiring_with):
    entities = _FakeEntities(_entity())
    tools = wiring_with(facts=_FakeFacts(), entities=entities)

    out = await tools["get_knowledge"]("   ")

    assert out["status"] == "refused"
    assert entities.lookups == [], "a blank subject must cost no lookup"


async def test_knowledge_entity_renders_the_canonical_name_and_aliases(
    wiring_with,
):
    tools = wiring_with(entities=_FakeEntities(_entity()))

    out = await tools["knowledge_entity"]("大步行")

    assert out == {
        "status": "ok",
        "canonical_name": "Big Walk",
        "aliases": ["big walk", "大步行"],
    }


async def test_knowledge_entity_nothing_found_and_unavailable(wiring_with):
    """Unresolved is a real answer; a store outage is not one."""
    unresolved = await wiring_with(entities=_FakeEntities(None))[
        "knowledge_entity"]("nothing by this name")
    down = await wiring_with(entities=_FakeEntities(raises=True))[
        "knowledge_entity"]("big walk")
    off = await wiring_with()["knowledge_entity"]("big walk")

    assert unresolved["status"] == "nothing_found"
    assert down["status"] == "unavailable"
    assert off["status"] == "unavailable"


async def test_knowledge_premises_lists_the_edges(wiring_with):
    from nora.domain.facts import FactPremise
    fid = "0191f2c4-0000-7000-8000-00000000000a"
    premise = "0191f2c4-0000-7000-8000-00000000000b"
    tools = wiring_with(
        facts=_FakeFacts(premises=(FactPremise(fact_id=fid, premise_fact_id=premise),)),
    )

    out = await tools["knowledge_premises"](fid)

    assert out == {"status": "ok", "premises": [premise]}


async def test_knowledge_premises_nothing_found_is_honest_about_zero_rows(
    wiring_with,
):
    """`fact_premise` holds 0 rows today — the read shipped ahead of any
    writer (32 §3.4) — so «no premises recorded» is the ORDINARY answer and
    must be `nothing_found`, not an error and not an empty `ok`."""
    tools = wiring_with(facts=_FakeFacts())

    out = await tools["knowledge_premises"]("0191f2c4-0000-7000-8000-00000000000a")

    assert out["status"] == "nothing_found"


async def test_knowledge_premises_an_outage_is_unavailable(wiring_with):
    tools = wiring_with(facts=_FakeFacts(raises=True))

    out = await tools["knowledge_premises"]("0191f2c4-0000-7000-8000-00000000000a")

    assert out["status"] == "unavailable"


async def test_knowledge_asserters_renders_what_the_rows_carry(wiring_with):
    from nora.domain.facts import FactAsserter
    fid = "0191f2c4-0000-7000-8000-00000000000a"
    tools = wiring_with(facts=_FakeFacts(asserters=(
        FactAsserter(fact_id=fid, person_id="miles", message_id=1932,
                     asserted_at=1755100000000),
    )))

    out = await tools["knowledge_asserters"](fid)

    assert out == {
        "status": "ok",
        "asserters": [{
            "person_id": "miles", "message_id": 1932,
            "asserted_at": 1755100000000,
        }],
    }


async def test_knowledge_asserters_nothing_found_and_unavailable(wiring_with):
    unknown = await wiring_with(facts=_FakeFacts())[
        "knowledge_asserters"]("0191f2c4-0000-7000-8000-00000000000a")
    down = await wiring_with(facts=_FakeFacts(raises=True))[
        "knowledge_asserters"]("0191f2c4-0000-7000-8000-00000000000a")
    off = await wiring_with()[
        "knowledge_asserters"]("0191f2c4-0000-7000-8000-00000000000a")

    assert unknown["status"] == "nothing_found"
    assert down["status"] == "unavailable"
    assert off["status"] == "unavailable"


class _RecordingHoncho:
    """`FakeHoncho` records the query but not the SCOPE, and the scope is the
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


def _workspace_memory_tools(wiring, **over):
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
        workspace_for=lambda *_a: "ws-main", **over,
    )
    return {t.tool_name: t for t in tools}


async def test_search_memory_workspace_filters_by_the_workspace_roster(wiring):
    """32 §5 closed decision 1: the roster is the WORKSPACE's people
    (`persons.by_person`), not one room's live view — and the per-message
    guard is unchanged: a peer nobody can name never comes back, and neither
    does Nora's own."""
    honcho = _RecordingHoncho(hits=(
        ("miles", "big walk 聽日朝早"),
        ("a_peer_nobody_mapped", "should never surface"),
        ("nora", "her own words are not the group's memory"),
    ))
    tools = _workspace_memory_tools(wiring, honcho=honcho)

    out = await tools["search_memory_workspace"]("big walk")

    assert out["status"] == "ok"
    assert out["results"] == [{"who": "Miles", "said": "big walk 聽日朝早"}]
    # No room to scope to and none passed — the workspace-wide endpoint.
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
    tools = _workspace_memory_tools(wiring, honcho=honcho, metrics=metrics)

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
    tools = _workspace_memory_tools(wiring, honcho=_RecordingHoncho(reachable=False))

    out = await tools["search_memory_workspace"]("big walk")

    assert out["status"] == "unavailable"


async def test_search_memory_workspace_without_a_roster_is_unavailable(wiring):
    """No persons wired at all is a WIRING gap, not an empty address book:
    an empty one legitimately filters every hit, a missing one means «I
    could not check» — and the two never share a status."""
    tools = _workspace_memory_tools(
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
