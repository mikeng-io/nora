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
    build_tools,
)
from nora.doctrine.loader import DoctrineStore
from nora.domain.media import GifCandidate
from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef, Quote
from nora.domain.ports import OutboundAttachment, Roster
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
        "now", "load_transcript", "send_reply", "search_gifs", "send_gif",
        "group_members", "lookup_person", "about", "ask_memory", "search_memory",
        "calculate", "transit", "route",
        "place_info", "saved_places",
        "where_is",
        "media_fetch",
        "web_search",
        "weather",
        "remind", "list_reminders", "cancel_reminder",
        # Nora Compute spec §4 (Task 5).
        "make_artifact",
        # DEC-97: what she is running, and what was in an earlier version.
        "version",
    }


def test_read_peer_card_is_gone_from_the_table_and_the_capability_block():
    """DEC-44: `read_peer_card` was R-21's last structural violation — its only
    parameter was a raw platform id the model is never shown, and it was not
    room-scoped, so a model that guessed another person's id pulled their card
    across conversation boundaries. It is deleted. This pins the deletion in
    both places a tool can exist: `NATIVE_TOOLS` (what makes a tool callable at
    all — R-7 default-denies anything absent) and the generated capability
    block the doctrine carries. Re-adding the tool turns this red."""
    from nora.agent.registry import ToolRegistry
    from nora.agent.tools import NATIVE_TOOLS

    assert "read_peer_card" not in NATIVE_TOOLS
    assert "read_peer_card" not in NATIVE_TOOL_BUDGETS
    assert "read_peer_card" not in ToolRegistry().capability()


def test_feed_honcho_is_not_a_tool(wiring):
    assert "feed_honcho" not in wiring["tools"]


def test_every_tool_has_a_model_facing_docstring(wiring):
    for name, tool in wiring["tools"].items():
        doc = tool.__doc__ or ""
        assert len(doc.strip()) > 40, f"{name} needs a docstring written for the model"


async def test_load_transcript_returns_rows_oldest_first(wiring):
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
    bodies = [r["body"] for r in rows]
    assert bodies == ["m0", "m1", "m2"]
    assert not any(b.startswith("secret") for b in bodies)


async def test_load_transcript_returns_only_model_relevant_fields(wiring):
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
    rows = await _call(wiring["tools"]["load_transcript"], limit=40)
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
        rows = await _call(wiring["tools"]["load_transcript"], limit=hostile)
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
    assert await _call(wiring["tools"]["load_transcript"], limit=40) == []


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
