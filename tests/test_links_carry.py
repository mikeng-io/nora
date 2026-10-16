"""DEC-89 — the link a tool minted reaches the group even when the model drops it.

THE PROBE these tests are built around: four restaurant recommendations, a
`place_info` `maps_url` minted for every one, and not a single link in any
reply. The tool's own docstring asks for it. So the assertion that matters is
the end-to-end one at the bottom — the tools and the sender share ONE carrier
and a named place carries its link out — and everything above it is the
narrowness that keeps this from rewriting her words more than it has to.
"""

import pytest

from nora.agent.links import LinkCarry
from nora.agent.tools import build_tools
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import ConversationTarget
from nora.obs.metrics import Metrics
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

SHORT = "https://www.google.com/maps/search/?api=1&query=Soi.29"
OTHER = "https://www.google.com/maps/search/?api=1&query=zzz1111"


# ---- LinkCarry on its own ---------------------------------------------------

def test_a_link_is_appended_only_when_the_reply_names_the_place():
    """The anchor gate. She looked up four places and recommended two: the two
    she NAMED get their links, and a tool call is not a promise to link."""
    carry = LinkCarry()
    carry.offer(anchor="Soi.29", url=SHORT)
    carry.offer(anchor="泰斗", url=OTHER)
    text, added = carry.apply("Soi.29 好食嘅，不過要排隊")
    assert added == 1
    assert text == "Soi.29 好食嘅，不過要排隊\n" + SHORT
    assert OTHER not in text


def test_a_reply_that_already_carries_the_link_is_untouched():
    """The whole no-op path for a model that behaved. This is what makes the
    counter readable: a rising rate means the model is dropping links."""
    carry = LinkCarry()
    carry.offer(anchor="Soi.29", url=SHORT)
    original = f"Soi.29 啦，{SHORT}"
    text, added = carry.apply(original)
    assert (text, added) == (original, 0)


def test_a_reply_carrying_the_link_decoded_is_untouched():
    """With no shortener the link is the full Maps URL,
    mostly `%E9...` escapes for a Chinese name. A model that writes it with the
    characters themselves, or `+` for a space, already carries it; read as missing,
    the link was appended a second time under her own copy."""
    url = "https://www.google.com/maps/search/?api=1&query=%E9%8A%85%E9%91%BC%E7%81%A3+Times"
    carry = LinkCarry()
    carry.offer(anchor="銅鑼灣", url=url)
    reply = "去銅鑼灣啦 https://www.google.com/maps/search/?api=1&query=銅鑼灣 Times"
    assert carry.apply(reply) == (reply, 0)


def test_two_names_for_one_place_append_one_line():
    """`place_info` offers both the asked name and Google's resolved name, so
    dedupe is by URL — otherwise a place she names twice over gets two links."""
    carry = LinkCarry()
    carry.offer(anchor="Soi.29", url=SHORT)
    carry.offer(anchor="Soi 29 Thai Kitchen", url=SHORT)
    text, added = carry.apply("去咗 Soi 29 Thai Kitchen，即係 Soi.29 嗰間")
    assert added == 1
    assert text.count(SHORT) == 1


def test_the_resolved_name_alone_is_enough_to_earn_the_link():
    """She rarely writes back the exact string she searched with. Matching only
    the asked name would make this fire on almost nothing real."""
    carry = LinkCarry()
    carry.offer(anchor="soi29", url=SHORT)
    carry.offer(anchor="Soi 29 Thai Kitchen", url=SHORT)
    text, added = carry.apply("Soi 29 Thai Kitchen 抵食")
    assert added == 1 and text.endswith(SHORT)


def test_matching_is_case_folded_but_never_a_one_character_anchor():
    """Two characters is the floor because Cantonese place names really are two
    (譚仔, 泰斗, 池記) — the same reason `strip_markdown` does not reason
    about an alphabet Nora does not write in."""
    carry = LinkCarry()
    carry.offer(anchor="YOHO MALL", url=SHORT)
    carry.offer(anchor="X", url=OTHER)          # too short to be evidence
    text, added = carry.apply("yoho mall 樓下有 X")
    assert added == 1 and SHORT in text and OTHER not in text


def test_links_land_in_offer_order():
    carry = LinkCarry()
    carry.offer(anchor="譚仔", url=SHORT)
    carry.offer(anchor="池記", url=OTHER)
    text, added = carry.apply("池記同譚仔都得")
    assert added == 2
    assert text.splitlines()[1:] == [SHORT, OTHER]


def test_nothing_offered_or_nothing_named_changes_nothing():
    assert LinkCarry().apply("食咩好") == ("食咩好", 0)
    carry = LinkCarry()
    carry.offer(anchor="Soi.29", url=SHORT)
    assert carry.apply("今晚唔出街喇") == ("今晚唔出街喇", 0)


def test_an_unusable_offer_is_dropped_rather_than_raised():
    """A tool must never fail because its decoration did."""
    carry = LinkCarry()
    carry.offer(anchor="Soi.29", url="")
    carry.offer(anchor="", url=SHORT)
    carry.offer(anchor="  ", url=SHORT)
    assert carry.offered == ()


# ---- place_info actually offers ---------------------------------------------

class _Places:
    """A route client that answers `place_info` the way the real one does —
    including the `name` Google resolves to, which is the second anchor."""

    async def place_info(self, name: str) -> dict:
        return {"status": "ok", "name": "Soi 29 Thai Kitchen",
                "address": "銅鑼灣", "rating": 4.3}


def _place_info_tool(links):
    from nora.agent.room import Room
    from nora.persons import Persons

    persons = Persons(by_person={}, index={})
    tools = build_tools(
        store=None, conversation_id="g1", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=persons),
        transit_source=None, route_source=_Places(),
        question="Soi.29 好唔好食", links=links,
    )
    return {t.tool_name: t for t in tools}["place_info"]


async def test_place_info_offers_both_the_asked_and_the_resolved_name():
    links = LinkCarry()
    out = await _place_info_tool(links)._tool_func(name="Soi.29")
    assert out["maps_url"] == SHORT
    assert links.offered == (SHORT,)
    # Either name she might write back earns it.
    assert links.apply("Soi.29 得")[1] == 1
    assert LinkCarry.apply(links, "Soi 29 Thai Kitchen 得")[1] == 1


async def test_a_refused_or_unavailable_lookup_offers_nothing():
    """No `maps_url` was minted, so there is nothing to carry — the same
    three-state discipline R-27 puts on every other tool result."""
    links = LinkCarry()
    tool = _place_info_tool(links)
    await tool._tool_func(name="+85298765432")            # refused, R-28
    assert links.offered == ()

    class _Down:
        async def place_info(self, name): return {"status": "unavailable"}

    from nora.agent.room import Room
    from nora.persons import Persons
    persons = Persons(by_person={}, index={})
    down = {t.tool_name: t for t in build_tools(
        store=None, conversation_id="g1", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=persons),
        transit_source=None, route_source=_Down(), links=links,
    )}["place_info"]
    await down._tool_func(name="Soi.29")
    assert links.offered == ()


# ---- through the Deliverer, which is where it actually has to happen --------

TARGET = ConversationTarget("signal", "g1", "group")
GUARDRAIL_BODY = (
    "# Guardrails\n\n"
    "You never post an **error message**, a **stack trace**, or a failure notice at all."
)


@pytest.fixture
async def store(pg_url):
    from nora.infra.sql.audit import SqlRecordStore
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.migrations_runner import run_migrations

    run_migrations(pg_url)
    s = SqlRecordStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


@pytest.fixture
def doctrine(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "constraints.md", name="guardrails", order=40, body=GUARDRAIL_BODY)
    return DoctrineStore(d, canary="CANARY-1", display_name="Nora", metrics=Metrics()).get()


def _deliverer(store, doctrine, sink, links, metrics=None):
    from nora.agent.guards import Deliverer
    from nora.agent.tools import NATIVE_TOOL_BUDGETS

    return Deliverer(store=store, adapter=sink, target=TARGET, doctrine=doctrine,
                     canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
                     message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag",
                     metrics=metrics or Metrics(), links=links)


async def test_the_group_gets_the_link_the_model_dropped(store, doctrine):
    links = LinkCarry()
    links.offer(anchor="Soi.29", url=SHORT)
    sink = SinkSender()
    metrics = Metrics()
    assert await _deliverer(store, doctrine, sink, links, metrics).deliver(
        "去 Soi.29 啦，唔使諗",
    ) is True
    assert sink.texts == ["去 Soi.29 啦，唔使諗\n" + SHORT]
    assert metrics.get("nora_reply_links_carried_total", outcome="appended") == 1


async def test_the_audit_row_carries_the_link_the_group_saw(store, doctrine):
    """Same argument DEC-82 makes about the strip: the row, `sent_texts` and the
    Honcho feed must all carry the ONE form the group actually read."""
    links = LinkCarry()
    links.offer(anchor="Soi.29", url=SHORT)
    d = _deliverer(store, doctrine, SinkSender(), links)
    await d.deliver("Soi.29 好食")
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.body.endswith(SHORT)
    assert d.sent_texts == ("Soi.29 好食\n" + SHORT,)


async def test_an_empty_reply_is_never_rescued_into_a_link(store, doctrine):
    """R-4 runs FIRST. The reverse is the obvious guess, which is exactly why
    `strip_markdown` states it too — a reply that is nothing but a URL is not a
    reply she wrote."""
    links = LinkCarry()
    links.offer(anchor="Soi.29", url=SHORT)
    sink = SinkSender()
    metrics = Metrics()
    assert await _deliverer(store, doctrine, sink, links, metrics).deliver("   ") is False
    assert sink.texts == []
    assert metrics.get(
        "nora_silent_failures_total", reason="empty_reply", platform="signal",
        audience="tag",
    ) == 1


async def test_a_reply_naming_nothing_is_sent_exactly_as_written(store, doctrine):
    links = LinkCarry()
    links.offer(anchor="Soi.29", url=SHORT)
    sink = SinkSender()
    metrics = Metrics()
    await _deliverer(store, doctrine, sink, links, metrics).deliver("今晚煮飯")
    assert sink.texts == ["今晚煮飯"]
    assert metrics.get("nora_reply_links_carried_total", outcome="appended") == 0


async def test_the_leak_guard_sees_the_appended_bytes(store, doctrine):
    """The carry runs BEFORE R-6/R-18, so the guards measure what LEAVES. A
    transformation sitting in front of a fail-closed guard without the guard
    seeing its output is the hole DEC-82's strip avoids; this proves the
    ordering by carrying a poisoned url."""
    links = LinkCarry()
    links.offer(anchor="Soi.29", url="https://x.test/+85298765432")
    sink = SinkSender()
    metrics = Metrics()
    assert await _deliverer(store, doctrine, sink, links, metrics).deliver(
        "Soi.29 得",
    ) is False
    assert sink.texts == []
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_deliverer_built_without_a_carrier_behaves_exactly_as_before(store, doctrine):
    """The inert case is a working case, not a broken one — the reason `links`
    is defaulted here the way `composer` is."""
    from nora.agent.guards import Deliverer
    from nora.agent.tools import NATIVE_TOOL_BUDGETS

    sink = SinkSender()
    d = Deliverer(store=store, adapter=sink, target=TARGET, doctrine=doctrine,
                  canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
                  message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag",
                  metrics=Metrics())
    assert await d.deliver("Soi.29 好食") is True
    assert sink.texts == ["Soi.29 好食"]


# ---- the wiring, which is the half that can ship inert ----------------------

async def test_deliver_build_hands_out_the_same_carrier_it_gave_the_sender(store, doctrine):
    """Two carriers is a feature that collects perfectly and appends nothing —
    DEC-42's failure shape. `deliver.build` mints ONE and the handle exposes it,
    so a caller cannot wire the tools to a carrier the sender never reads."""
    from nora.agent.tools import NATIVE_TOOL_BUDGETS
    from nora.engagement.stages import deliver

    handle = deliver.build(
        store=store, adapter=SinkSender(), target=TARGET, doctrine=doctrine,
        canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
        conversation_id="g1", budgets=NATIVE_TOOL_BUDGETS,
        terminal=("send_reply",), metrics=Metrics(), audience="tag", composer=None,
    )
    handle.links.offer(anchor="Soi.29", url=SHORT)
    await handle.deliverer.deliver("Soi.29 好食")
    assert handle.deliverer.sent_texts == ("Soi.29 好食\n" + SHORT,)


async def test_place_info_through_a_shared_carrier_reaches_the_group(store, doctrine):
    """END TO END, and the only test here that catches the dropped-link
    defect: the tool mints, the model writes a reply that names the place and
    forgets the link, and the group gets the link anyway."""
    from nora.agent.tools import NATIVE_TOOL_BUDGETS
    from nora.engagement.stages import deliver

    sink = SinkSender()
    handle = deliver.build(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine,
        canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
        conversation_id="g1", budgets=NATIVE_TOOL_BUDGETS,
        terminal=("send_reply",), metrics=Metrics(), audience="tag", composer=None,
    )
    await _place_info_tool(handle.links)._tool_func(name="Soi.29")
    await handle.deliverer.deliver("Soi.29 4.3 分，抵食，不過唔設訂位")
    assert sink.texts == ["Soi.29 4.3 分，抵食，不過唔設訂位\n" + SHORT]
