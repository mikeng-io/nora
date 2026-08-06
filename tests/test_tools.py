import pytest

from nora.agent.guards import Deliverer
from nora.agent.room import Room
from nora.agent.tools import (
    NATIVE_TOOL_BUDGETS,
    READ_BUFFER_DEFAULT,
    READ_BUFFER_MAX,
    build_tools,
)
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import ConversationTarget, NormalizedMessage, Quote
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from tests.fakes import FakeHoncho, SinkSender
from tests.test_doctrine_loader import write

TARGET = ConversationTarget("signal", "g1", "group")


@pytest.fixture
async def wiring(tmp_path):
    dbp = tmp_path / "messages.db"
    run_migrations(dbp)
    store = SqlAuditStore(create_sql_engine(dbp))
    for i in range(3):
        await store.append(message=NormalizedMessage("signal", "g1", "group", "+852111",
                                                      "Miles", i, f"m{i}", {}))
    # A second, unrelated conversation with its own rows — the cross-group
    # leak probe (Task 1) needs a real second group to prove read_buffer
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
                          metrics=Metrics())
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
                        question="who is here", context_window=40)
    try:
        yield {"tools": {t.tool_name: t for t in tools}, "sink": sink,
               "honcho": honcho, "deliverer": deliverer, "store": store, "metrics": metrics}
    finally:
        await store.aclose()


def test_every_declared_tool_is_built(wiring):
    assert set(wiring["tools"]) == {
        "now", "read_buffer", "read_peer_card", "send_reply",
        "group_members", "lookup_person", "about", "calculate",
    }


def test_feed_honcho_is_not_a_tool(wiring):
    assert "feed_honcho" not in wiring["tools"]


def test_every_tool_has_a_model_facing_docstring(wiring):
    for name, tool in wiring["tools"].items():
        doc = tool.__doc__ or ""
        assert len(doc.strip()) > 40, f"{name} needs a docstring written for the model"


async def test_read_buffer_returns_rows_oldest_first(wiring):
    rows = await _call(wiring["tools"]["read_buffer"], limit=40)
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
        wiring["tools"]["read_buffer"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"limit"}
    assert set(
        wiring["tools"]["send_reply"].tool_spec["inputSchema"]["json"]["properties"]
    ) == {"text"}


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
        ("read_buffer", {"conversation_id": "gEVIL", "limit": 5}, {"limit": 5}),
    ):
        assert wiring["tools"][name]._metadata.validate_input(supplied) == expected


async def test_read_buffer_reads_the_bound_conversation_and_nothing_else(wiring):
    """Task 1's PROBE, now unrepresentable as an argument.

    The tools are bound to `g1` and `g2` holds another group's rows. There is
    no longer any input by which the model could ask for `g2` — the parameter
    that used to shadow the closure's bound value is gone — so the leak is
    closed by construction. This still asserts on the returned rows rather than
    on the signature: what matters is that g2's messages never come back.
    """
    rows = await _call(wiring["tools"]["read_buffer"], limit=40)
    bodies = [r["body"] for r in rows]
    assert bodies == ["m0", "m1", "m2"]
    assert not any(b.startswith("secret") for b in bodies)


async def test_read_buffer_returns_only_model_relevant_fields(wiring):
    rows = await _call(wiring["tools"]["read_buffer"], limit=40)
    # No raw_json, no ids. `reply_to` is absent rather than null on an
    # unthreaded row — see test_read_buffer_omits_reply_to_* below.
    assert set(rows[0]) == {"sender", "body", "ts"}


async def test_read_buffer_marks_a_reply_with_the_name_it_answers(wiring):
    """design/11 §2.6: if the prefilled transcript teaches `[A ↳ B]` and
    `read_buffer` returns un-threaded rows, the older messages look
    structurally different and the model reads a missing arrow as «not a
    reply». JSON, not the arrow — a tool returns data; the arrow is prompt
    syntax."""
    store = wiring["store"]
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852333", "Tyler", 9, "你屋企", {},
        quote=Quote(author_id="+852111", text="whatever", quoted_ts=1),
    ))
    rows = await _call(wiring["tools"]["read_buffer"], limit=40)
    assert rows[-1] == {"sender": "Tyler", "body": "你屋企", "ts": 9, "reply_to": "Miles"}


async def test_read_buffer_omits_reply_to_when_the_parent_is_not_in_the_rows(wiring):
    """Resolution happens ONLY within the rows this call already returns — no
    extra query from inside a tool. An unresolved parent drops the key rather
    than reporting a null the model would have to interpret."""
    store = wiring["store"]
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852333", "Tyler", 9, "你屋企", {},
        quote=Quote(author_id="+852111", text="whatever", quoted_ts=999_999),
    ))
    rows = await _call(wiring["tools"]["read_buffer"], limit=40)
    assert "reply_to" not in rows[-1]


async def test_read_buffer_names_nora_the_same_way_in_reply_to_as_in_sender(wiring):
    store = wiring["store"]
    await store.append(
        message=NormalizedMessage("signal", "g1", "group", "nora", None, 8, "七點半", {}),
        is_nora=True,
    )
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852333", "Tyler", 9, "得", {},
        quote=Quote(author_id="nora", text="whatever", quoted_ts=8),
    ))
    rows = await _call(wiring["tools"]["read_buffer"], limit=40)
    assert rows[-2]["sender"] == "Nora"
    assert rows[-1]["reply_to"] == "Nora"


async def test_read_buffer_clamps_a_hostile_limit_before_it_reaches_the_store(wiring, monkeypatch):
    """`limit` is a model-supplied argument. SQLite renders `LIMIT -1` as
    unbounded rather than zero, so an unclamped negative limit would dump the
    entire conversation into context; `limit=0` is equally meaningless. This
    asserts what `read_buffer` actually sends downstream — independent of
    whatever the store itself does with a non-positive limit — so it fails if
    the tool-level clamp regresses even though the store has its own guard."""
    seen: list[int] = []
    real_recent = wiring["store"].recent

    async def spy(*, conversation_id, limit):
        seen.append(limit)
        return await real_recent(conversation_id=conversation_id, limit=limit)

    monkeypatch.setattr(wiring["store"], "recent", spy)
    for hostile in (-1, 0):
        rows = await _call(wiring["tools"]["read_buffer"], limit=hostile)
        assert len(rows) <= 1
    assert seen == [1, 1]


async def test_read_buffer_clamps_the_top_end_to_the_declared_ceiling(wiring, monkeypatch):
    """The other end of the same clamp, and the expensive one (R-24).

    `limit` is MODEL-supplied and nothing downstream bounds it — `store.recent`
    serves whatever it is handed. Measured 2026-08-06, `read_buffer(200)` cost
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
    for greedy in (READ_BUFFER_MAX + 1, 200, 10_000):
        await _call(wiring["tools"]["read_buffer"], limit=greedy)
    assert seen == [READ_BUFFER_MAX, READ_BUFFER_MAX, READ_BUFFER_MAX]
    # Spelled out, not just compared to the constant: an assertion written only
    # against `READ_BUFFER_MAX` asserts the implementation back to itself and
    # would stay green if the ceiling went back to 200.
    assert seen == [60, 60, 60]


async def test_read_buffer_defaults_to_less_than_the_ceiling(wiring, monkeypatch):
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
    await _call(wiring["tools"]["read_buffer"])
    assert seen == [READ_BUFFER_DEFAULT]
    assert seen == [25]                       # the literal, for the same reason as above
    assert READ_BUFFER_DEFAULT < READ_BUFFER_MAX


async def test_read_peer_card_resolves_the_sender_to_a_person(wiring):
    assert await _call(wiring["tools"]["read_peer_card"], sender_id="+852111") == \
        ["likes espresso"]


async def test_read_peer_card_returns_empty_for_an_unmapped_sender(wiring):
    assert await _call(wiring["tools"]["read_peer_card"], sender_id="+852999") == []
    assert wiring["honcho"].peers == []      # no peer is ever created for an unmapped sender


async def test_read_peer_card_returns_empty_when_honcho_is_down(wiring):
    wiring["honcho"].fail = True
    assert await _call(wiring["tools"]["read_peer_card"], sender_id="+852111") == []


async def test_send_reply_delegates_to_the_deliverer(wiring):
    assert await _call(wiring["tools"]["send_reply"], text="hello") is True
    assert wiring["sink"].texts == ["hello"]
    assert wiring["deliverer"].delivered is True


async def test_send_reply_is_the_only_tool_that_can_send(wiring):
    await _call(wiring["tools"]["read_buffer"], limit=40)
    await _call(wiring["tools"]["read_peer_card"], sender_id="+852111")
    await _call(wiring["tools"]["group_members"])
    await _call(wiring["tools"]["lookup_person"], name="Miles")
    await _call(wiring["tools"]["about"], person="Miles")
    assert wiring["sink"].sent == []


async def test_a_tool_never_raises_into_the_agent_loop(wiring, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(wiring["store"], "recent", boom)
    assert await _call(wiring["tools"]["read_buffer"], limit=40) == []


# ---- every tool call is counted, by outcome ---------------------------------


async def test_the_four_tools_without_a_counter_of_their_own_now_have_one(wiring):
    """`nora_room_lookups_total{tool,status}` covered three of the seven; these
    four had nothing, so "did she ever call read_buffer" was unanswerable from
    `/metrics` as well as from the log. Same `{tool, status}` shape rather than
    four bespoke series."""
    tools, metrics = wiring["tools"], wiring["metrics"]
    await _call(tools["now"])
    await _call(tools["read_buffer"], limit=40)
    await _call(tools["read_peer_card"], sender_id="+852111")
    await _call(tools["send_reply"], text="hello")
    for tool in ("now", "read_buffer", "read_peer_card", "send_reply"):
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
    counter that reported `ok` for a `read_buffer` whose store was down would
    be the `peer_card` failure again — a green series over a dead path."""
    metrics = wiring["metrics"]

    async def boom(*a, **kw):
        raise RuntimeError("db down")

    await _call(wiring["tools"]["read_buffer"], limit=40)
    monkeypatch.setattr(wiring["store"], "recent", boom)
    await _call(wiring["tools"]["read_buffer"], limit=40)
    assert metrics.get("nora_tool_calls_total", tool="read_buffer", status="ok") == 1
    assert metrics.get("nora_tool_calls_total", tool="read_buffer", status="unavailable") == 1


async def test_read_peer_card_counts_four_different_answers_behind_one_empty_list(
    wiring, monkeypatch,
):
    """The model sees `[]` for all four; only the call site knows which it got.

    A sender who maps to nobody, a person Honcho has nothing on, a person it
    does have something on, and a raising transport. `read_peer_card` returned
    `[]` for every one of these for the whole life of the deployment, which is
    how a dead endpoint went unnoticed.

    **Honestly bounded.** The live `peer_card` answers `null` rather than
    failing when it cannot help, so a transport that degrades quietly is still
    counted `nothing_known` here — that gap belongs to the endpoint and is
    exactly why DEC-27 replaced it with `about`/`representation`, whose
    `Recollection.reachable` carries the bit. What this counter fixes is the
    part that was recoverable at the call site.
    """
    tools, metrics = wiring["tools"], wiring["metrics"]

    async def boom(**kwargs):
        raise RuntimeError("connection reset")

    await _call(tools["read_peer_card"], sender_id="+852999")     # not in identity.toml
    await _call(tools["read_peer_card"], sender_id="+852111")     # known, has a card
    wiring["honcho"].cards = {}
    await _call(tools["read_peer_card"], sender_id="+852111")     # known, nothing stored
    monkeypatch.setattr(wiring["honcho"], "peer_card", boom)
    await _call(tools["read_peer_card"], sender_id="+852111")     # transport raised
    for status in ("not_found", "ok", "nothing_known", "unavailable"):
        assert metrics.get(
            "nora_tool_calls_total", tool="read_peer_card", status=status,
        ) == 1, status


async def test_a_refused_send_is_not_counted_as_a_sent_one(wiring):
    """R-2 latches after the first delivery, so the second call returns False
    without anything reaching the group. Counting both as `ok` would make
    `send_reply` volume look like reply volume."""
    tools, metrics = wiring["tools"], wiring["metrics"]
    assert await _call(tools["send_reply"], text="hello") is True
    assert await _call(tools["send_reply"], text="again") is False
    assert metrics.get("nora_tool_calls_total", tool="send_reply", status="ok") == 1
    assert metrics.get("nora_tool_calls_total", tool="send_reply", status="refused") == 1
    assert wiring["sink"].texts == ["hello"]


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


async def _call(tool, **kwargs):
    """Invoke the underlying function of a @tool-decorated closure."""
    return await tool._tool_func(**kwargs)
