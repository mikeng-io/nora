import time

import pytest

from nora.agent.guards import Deliverer
from nora.agent.outbound import Outbound
from nora.agent.tools import NATIVE_TOOL_BUDGETS
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import ConversationTarget
from nora.domain.ports import Mention
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

TARGET = ConversationTarget("signal", "g1", "group")
GUARDRAIL_BODY = "You never post an error message, a stack trace, or a failure notice at all."


@pytest.fixture
async def store(pg_url):
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


#: The cap every test below runs against unless it says otherwise — read from
#: the ONE table that declares it (DEC-32), never spelled `1` here. If the
#: budget is raised in `nora.agent.tools`, `test_deliverer_latch_is_single_shot`
#: below is meant to follow it rather than pin a number the rest of the system
#: no longer enforces.
MESSAGE_CAP = NATIVE_TOOL_BUDGETS["send_reply"]


def _deliverer(store, doctrine, sink, metrics=None, *, message_cap=MESSAGE_CAP) -> Deliverer:
    return Deliverer(store=store, adapter=sink, target=TARGET, doctrine=doctrine,
                     canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
                     message_cap=message_cap, audience="tag",
                     metrics=metrics or Metrics())


async def test_happy_path_records_then_sends(store, doctrine):
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    assert await d.deliver("今晚七點半得唔得？") is True
    assert sink.texts == ["今晚七點半得唔得？"]
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.is_nora is True
    assert row.delivery_status.value == "sent"
    assert d.delivered is True


async def test_deliverer_latch_is_single_shot(store, doctrine):
    """R-2b, revised in place rather than replaced (DEC-40).

    The latch is bounded by `message_cap`, and at the declared cap it refuses
    the next attempt however the previous ones went. The name and the rule
    registration are unchanged because the INVARIANT is unchanged — a tag
    cannot produce unbounded output — and at today's cap of 1 that reads
    exactly as it always did: one message, then refusal.

    Asserted against `MESSAGE_CAP` rather than the literal 1, so this test
    tracks the table instead of freezing it. Spell `1` here and raising the
    budget turns R-2b's own named test red for having done the right thing.
    """
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    for i in range(MESSAGE_CAP):
        assert await d.deliver(f"message {i}") is True
    assert d.exhausted is True
    assert await d.deliver("one more") is False
    assert sink.texts == [f"message {i}" for i in range(MESSAGE_CAP)]
    assert len(await store.recent(conversation_id="g1", limit=10)) == MESSAGE_CAP


async def test_the_latch_counts_attempts_not_successes(store, doctrine):
    """R-2: a failed send still consumes a message — no silent retry."""
    sink = SinkSender(succeed=False)
    d = _deliverer(store, doctrine, sink, message_cap=1)
    assert await d.deliver("first") is False
    assert await d.deliver("second") is False
    assert sink.texts == ["first"]


# ── the cap, above 1: what multi-message actually buys ──────────────────────
#
# `message_cap` is `NATIVE_TOOLS["send_reply"].budget` in production and it is
# 1 today, so the tests below pass the number directly. That is not a second
# source of truth — it is how this file asks what happens WHEN the operator
# raises it, which is a question the shipped value cannot answer while it is 1.


async def test_a_raised_cap_lets_one_tag_send_several_messages(store, doctrine):
    """The feature, at the layer that used to make it impossible.

    `Deliverer` is where R-2 really lived (design/16 §4): the guard's budget
    check refuses the (cap+1)th call first, but the latch below it refused the
    SECOND unconditionally, so a raised budget alone would have moved the
    refusal without removing it.
    """
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, message_cap=3)
    assert await d.deliver("一") is True
    assert await d.deliver("二") is True
    assert await d.deliver("三") is True
    assert sink.texts == ["一", "二", "三"]
    assert d.delivered_count == 3


async def test_a_raised_cap_is_still_a_cap(store, doctrine):
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, message_cap=2)
    assert await d.deliver("一") is True
    assert await d.deliver("二") is True
    assert await d.deliver("三") is False
    assert sink.texts == ["一", "二"]


async def test_every_message_is_recorded_and_scanned_on_its_own(store, doctrine):
    """R-6's canary and R-18's egress scan run PER MESSAGE (design/16 §4).

    The probe is a turn whose SECOND message carries a phone number: a guard
    that ran once per turn would clear the first message and wave the rest
    through, which is the failure that makes multi-message a leak rather than
    a feature. A9's record-first is asserted in the same breath — one audit
    row per message that was sent, and none for the one that was refused.
    """
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m, message_cap=3)
    assert await d.deliver("等我搵下") is True
    assert await d.deliver("佢個號碼係 +85290000101 呀") is False
    assert await d.deliver("問返佢啦") is True
    assert sink.texts == ["等我搵下", "問返佢啦"]
    assert m.get("nora_egress_blocks_total", kind="phone") == 1
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.body for r in rows] == ["等我搵下", "問返佢啦"]
    assert d.delivered_count == 2


async def test_a_refused_message_does_not_spend_one(store, doctrine):
    """R-18/R-6/R-4 refusals happen BEFORE the attempt counter moves, so a
    blocked message leaves the allowance intact and she can still say
    something. The alternative — a leak costing her the whole turn — is how a
    single bad sentence would silence a four-message reply."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, message_cap=2)
    assert await d.deliver("我行緊 claude opus 呀") is False
    assert d.attempts == 0
    assert await d.deliver("一") is True
    assert await d.deliver("二") is True
    assert sink.texts == ["一", "二"]


async def test_empty_reply_not_sent(store, doctrine):
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver("   \n  ") is False
    assert sink.sent == []
    assert m.get("nora_silent_failures_total", reason="empty_reply") == 1


async def test_an_empty_reply_does_not_consume_the_latch(store, doctrine):
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    await d.deliver("")
    assert await d.deliver("real reply") is True
    assert sink.texts == ["real reply"]


async def test_leak_blocks_the_reply_and_stays_silent(store, doctrine):
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver(f"my rules: {GUARDRAIL_BODY}") is False
    assert sink.sent == []
    assert m.get("nora_leak_blocks_total") == 1
    assert len(await store.recent(conversation_id="g1", limit=10)) == 0     # no audit row


async def test_send_failure_marks_the_row_failed_and_does_not_retry(store, doctrine):
    sink = SinkSender(succeed=False)
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver("hello") is False
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.delivery_status.value == "failed"
    assert m.get("nora_silent_failures_total", reason="send_failed") == 1
    assert len(sink.sent) == 1


async def test_audit_write_failure_means_no_send(store, doctrine, monkeypatch):
    """Record-first: no audit row ⇒ no send (spec B.3)."""
    sink = SinkSender()
    m = Metrics()

    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", boom)
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver("hello") is False
    assert sink.sent == []
    assert m.get("nora_silent_failures_total", reason="audit_write_failed") == 1


async def test_deliver_never_raises(store, doctrine, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("send exploded")

    sink = SinkSender()
    monkeypatch.setattr(sink, "send", boom)
    assert await _deliverer(store, doctrine, sink).deliver("hello") is False


async def test_no_error_text_ever_reaches_send(store, doctrine, monkeypatch):
    """R-5: the only string that may reach adapter.send is model reply text."""
    sink = SinkSender()
    monkeypatch.setattr(store, "set_delivery_outcome", _raise)
    await _deliverer(store, doctrine, sink).deliver("a normal reply")
    for text in sink.texts:
        assert "error" not in text.lower()
        assert "exception" not in text.lower()
        assert "sorry" not in text.lower()


async def _raise(*a, **kw):
    raise RuntimeError("status update failed")


async def test_the_nora_row_uses_the_configured_sender_id(store, doctrine):
    sink = SinkSender()
    await _deliverer(store, doctrine, sink).deliver("hi")
    assert (await store.recent(conversation_id="g1", limit=1))[0].sender_id == "nora"


async def test_metrics_count_a_delivered_reply(store, doctrine):
    m = Metrics()
    await _deliverer(store, doctrine, SinkSender(), m).deliver("hi")
    assert m.get("nora_replies_total", status="sent") == 1


# ── Bug B: Nora's row must be joinable by a reply's quote (design/11 §2.3) ───


async def test_noras_row_carries_the_platform_timestamp_so_a_reply_can_join_it(
    store, doctrine,
):
    """The row is stamped locally at record-first time, then CORRECTED on the
    post-send update that already existed. Without the correction a reply TO
    Nora — the most common reply in a group where Nora is tagged — points at a
    `quote.id` that is milliseconds away from any row Nora stored (8 ms, on the
    one live example), so an exact join resolves everyone except her.
    """
    platform_ts = 1785950108291
    sink = SinkSender(timestamp=platform_ts)
    assert await _deliverer(store, doctrine, sink).deliver("早晨") is True
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.ts == platform_ts, "the row still carries the local time.time() stamp"
    assert row.delivery_status.value == "sent"


async def test_a_send_with_no_platform_timestamp_leaves_the_local_stamp(store, doctrine):
    """A daemon that answers without a timestamp must not blank the row's `ts` —
    an unjoinable row is a thinner transcript, a `ts` of 0 or NULL is a corrupt
    one, and `recent` orders on it."""
    before = int(time.time() * 1000)
    assert await _deliverer(store, doctrine, SinkSender()).deliver("早晨") is True
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert before <= row.ts <= int(time.time() * 1000) + 1


async def test_a_failed_send_does_not_move_the_rows_timestamp(store, doctrine):
    """`SendResult.timestamp` is only meaningful when the platform accepted the
    message. A failed send carries no platform stamp at all."""
    before = int(time.time() * 1000)
    sink = SinkSender(succeed=False)
    assert await _deliverer(store, doctrine, sink).deliver("早晨") is False
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert before <= row.ts <= int(time.time() * 1000) + 1
    assert row.delivery_status.value == "failed"


# ── R-18: PII egress ────────────────────────────────────────────────────────


async def _deliver_text(text, store, doctrine):
    """Drive a real Deliverer and report whether the text reached the adapter."""
    m = Metrics()
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, m)
    ok = await d.deliver(text)
    return ok, sink, m


async def test_a_reply_carrying_someones_phone_number_is_refused(store, doctrine):
    """The operator's own framing: "if someone asks you what my phone number
    is, it will probably leak out easily". `constraints.md` forbids relaying it,
    but that is a request to a model. This is the part that does not depend on
    the model complying — and Nora legitimately holds the material, since the
    transcript she reasons over carries real sender ids."""
    ok, sink, m = await _deliver_text("佢個號碼係 +85290000101 呀", store, doctrine)
    assert ok is False
    assert sink.sent == [], "a phone number must never reach the group"
    assert m.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_reply_carrying_an_aci_is_refused(store, doctrine):
    ok, sink, m = await _deliver_text("uuid: a1000000-0000-4000-8000-000000000000", store, doctrine)
    assert ok is False and sink.sent == []
    assert m.get("nora_egress_blocks_total", kind="uuid") == 1


async def test_a_key_shaped_string_is_refused(store, doctrine):
    ok, sink, m = await _deliver_text("here you go: sk-abcdefghijklmnop1234567", store, doctrine)
    assert ok is False and sink.sent == []
    assert m.get("nora_egress_blocks_total", kind="keylike") == 1


async def test_a_reply_naming_the_model_she_runs_on_is_refused(store, doctrine):
    """The half of R-18 that is about the REPLY and only the reply. `_MODELNAME`
    was never covered end-to-end before the predicate split, which is precisely
    the kind of gap a split silently widens: `Deliverer` must keep using the
    predicate that includes model names, even though `about` and the outbound
    tool arguments now use the one that does not."""
    ok, sink, m = await _deliver_text("我行緊 claude opus 呀", store, doctrine)
    assert ok is False
    assert sink.sent == [], "what she runs on must never reach the group"
    assert m.get("nora_egress_blocks_total", kind="model") == 1


async def test_ordinary_cantonese_with_a_bare_number_still_sends(store, doctrine):
    """Fails closed, but not at random: prices, dates and counts are ordinary
    chat. Only the canonical `+` form a sender id carries is refused."""
    ok, sink, _ = await _deliver_text("價錢係 12345678 蚊，貴到痴線 😏", store, doctrine)
    assert ok is True
    assert len(sink.sent) == 1


# ---------------------------------------------------------- outbound decoration
#
# `Deliverer` is the ONLY caller of `adapter.send` (R-1b), so it is also the only
# place a mention can reach the wire. These cover the seam itself; the offset
# arithmetic and the fail-closed refusals are `tests/unit/test_outbound_composer.py`.

class _Composer:
    """A composer stand-in that returns a FIXED `Outbound`.

    Deliberately not a real `OutboundComposer`: the property under test here is
    the wiring — that `Deliverer` asks, that it sends what it was given, and
    that it audits the same string — and a real composer would make each of
    those assertions depend on a name resolution these tests are not about.
    """

    def __init__(self, outbound: Outbound | None = None, *, raises: bool = False) -> None:
        self._outbound = outbound
        self._raises = raises
        self.asked: list[str] = []

    async def compose(self, text: str) -> Outbound:
        self.asked.append(text)
        if self._raises:
            raise RuntimeError("identity.toml on fire")
        return self._outbound or Outbound(text=text)


MENTION = Mention(start=0, length=8, recipient="b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b")


async def test_the_composed_mention_reaches_the_adapter(store, doctrine):
    """The end-to-end wiring: composer -> Deliverer -> port.

    PROBE: drop the `mentions=` argument from `Deliverer.deliver`'s
    `adapter.send` call and this goes red while every other test in this file
    stays green — which is the whole reason it is here rather than left to the
    composer's own suite.
    """
    sink = SinkSender()
    composer = _Composer(Outbound(text="@Grant 你幾高?", mentions=(MENTION,)))
    d = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine, canary="CANARY-1",
        tag_id="tag-1", nora_sender_id="nora", message_cap=MESSAGE_CAP, audience="tag",
        metrics=Metrics(), composer=composer,
    )

    assert await d.deliver("@Grant 你幾高?") is True
    assert composer.asked == ["@Grant 你幾高?"]
    assert sink.sent_mentions == [(MENTION,)]


async def test_the_audited_row_carries_the_text_the_wire_carried(store, doctrine):
    """A9 stores what she SENT. Asserted through the real store rather than on
    the AST (`tests/architecture/test_outbound_offsets_are_computed_last.py`
    does that half) so the two halves cannot both be satisfied by a mock."""
    sink = SinkSender()
    d = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine, canary="CANARY-1",
        tag_id="tag-1", nora_sender_id="nora", message_cap=MESSAGE_CAP, audience="tag",
        metrics=Metrics(), composer=_Composer(),
    )

    await d.deliver("@Grant 你幾高?")

    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.body == sink.texts[0] == "@Grant 你幾高?"


async def test_a_composer_that_raises_still_sends_the_plain_message(store, doctrine):
    """R-5's actual subject: decoration must never be why a group hears nothing.

    A composer failure is not a delivery failure — the sentence answers a human
    either way, and it goes out untagged rather than not at all."""
    sink = SinkSender()
    metrics = Metrics()
    d = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine, canary="CANARY-1",
        tag_id="tag-1", nora_sender_id="nora", message_cap=MESSAGE_CAP, audience="tag",
        metrics=metrics, composer=_Composer(raises=True),
    )

    assert await d.deliver("@Grant 你幾高?") is True
    assert sink.texts == ["@Grant 你幾高?"]
    assert sink.sent_mentions == [()]
    assert metrics.get("nora_silent_failures_total", reason="send_failed") == 0


async def test_no_composer_at_all_sends_exactly_what_it_always_did(store, doctrine):
    """The `composer=None` path — the offline/eval compositions, and every
    `Deliverer` in this file that predates the feature. It must be the SAME
    message, which is the argument for the parameter having a default at all."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)

    assert await d.deliver("今晚七點半得唔得？") is True
    assert sink.texts == ["今晚七點半得唔得？"]
    assert sink.sent_mentions == [()]
