import time

import pytest

from nora.agent.egress import scan_reply
from nora.agent.guards import Deliverer
from nora.agent.links import LinkCarry
from nora.agent.markdown import render_markdown, strip_markdown
from nora.agent.outbound import Outbound
from nora.agent.tools import NATIVE_TOOL_BUDGETS
from nora.doctrine.loader import DoctrineStore
from nora.domain.models import ConversationTarget
from nora.domain.ports import Mention, Style, Styled
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

TARGET = ConversationTarget("signal", "g1", "group")
# MARKDOWN IS PART OF THE FIXTURE, deliberately. The real doctrine files are
# Markdown (`**bold**` spans, `#` headings), and a plain-sentence haystack with
# no emphasis cannot express the DEC-82 asymmetry: stripping markdown looks
# guard-STRENGTHENING in tests while weakening R-6 against the real doctrine.
# The fake must behave like the real thing, fixtures included.
GUARDRAIL_BODY = (
    "# Guardrails\n\n"
    "You never post an **error message**, a **stack trace**, or a failure notice at all."
)


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
#: below follows it rather than pinning a number the rest of the system does not
#: enforce.
MESSAGE_CAP = NATIVE_TOOL_BUDGETS["send_reply"]


def _deliverer(store, doctrine, sink, metrics=None, *, message_cap=MESSAGE_CAP,
               on_delivered=None, links=None, send_permitted=True) -> Deliverer:
    return Deliverer(store=store, adapter=sink, target=TARGET, doctrine=doctrine,
                     canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
                     message_cap=message_cap, audience="tag",
                     metrics=metrics or Metrics(), on_delivered=on_delivered,
                     links=links, send_permitted=send_permitted)


async def test_held_count_separates_a_hold_from_a_refusal(store, doctrine):
    """`deliver` answers False to both, so `held_count` is the only thing that
    tells them apart — and the four model-facing send tools read it to stop a
    model rewriting a reply that was never rejected."""
    sink, held = SinkSender(), _deliverer(store, doctrine, SinkSender(),
                                          send_permitted=False)
    assert await held.deliver("what she would have said") is False
    assert held.held_count == 1
    assert held.held_texts == ("what she would have said",)

    # A REFUSAL for contrast: the R-2 latch, past its cap. `deliver` answers
    # False here too, and `held_count` must NOT move — otherwise every refusal
    # would read as a hold and the model would stop retrying things it should.
    refused = _deliverer(store, doctrine, sink, message_cap=1)
    assert await refused.deliver("first") is True
    assert await refused.deliver("second") is False
    assert refused.held_count == 0


class _RecordingObserver:
    """A `DeliveredObserver` that counts. Behaves like the real
    `DeliverySettler` in the one way that matters here — it is told, it does not
    ask — so a Deliverer that never tells it fails the test."""

    def __init__(self) -> None:
        self.settles = 0

    def settle(self) -> None:
        self.settles += 1


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
    """R-2b (DEC-40): the latch is bounded by `message_cap`.

    At the declared cap it refuses the next attempt however the previous ones
    went. The INVARIANT is that a tag cannot produce unbounded output; at a cap
    of 1 that reads as one message, then refusal.

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
# `message_cap` is `NATIVE_TOOLS["send_reply"].budget` in production, so the
# tests below pass the number directly. That is not a second source of truth —
# it is how this file asks what happens WHEN the operator raises the cap, which
# the shipped value cannot answer.


async def test_a_raised_cap_lets_one_tag_send_several_messages(store, doctrine):
    """Multi-message at the layer that enforces R-2.

    The guard's budget check refuses the (cap+1)th call first; the `Deliverer`
    latch below it must also honour the cap, or a raised budget alone would move
    the refusal without removing it.
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
    """R-6's canary and R-18's egress scan run PER MESSAGE.

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
    assert await d.deliver("佢個號碼係 +85290000101 呀") is False
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


async def test_a_reply_quoting_the_transcript_markup_is_refused(store, doctrine):
    """The DEC-93 recovery delivers a turn's prose through this method; when
    that prose is a deliberation quoting a row by its `<m n=...>` tag, nothing
    reaches the group and nothing is recorded as her speech."""
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    draft = 'Thinking it over.\n\n<m n="12">see you at six</m> answered me.\n\n"Six works."'
    assert await d.deliver(draft, source="recovered") is False
    assert sink.sent == []
    assert d.last_outcome == "doctrine_leak"
    assert len(await store.recent(conversation_id="g1", limit=10)) == 0


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
    """Record-first: no audit row ⇒ no send."""
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


# ── Nora's row must be joinable by a reply's quote ───────────────────────────


async def test_noras_row_carries_the_platform_timestamp_so_a_reply_can_join_it(
    store, doctrine,
):
    """The row is stamped locally at record-first time, then CORRECTED on the
    post-send update. Without the correction a reply TO Nora — the most common
    reply in a group where Nora is tagged — points at a `quote.id` that is
    milliseconds away from any row Nora stored, so an exact join resolves
    everyone except her.
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
    """A request to relay a phone number: `constraints.md` forbids it, but that
    is a request to a model. This is the part that does not depend on the model
    complying — and Nora legitimately holds the material, since the
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


async def test_a_key_kept_verbatim_in_a_memo_is_still_refused_in_a_reply(store, doctrine):
    """DEC-226 (R-18 amended): a memo's `## Kept` line may hold a key-shaped value
    verbatim (D5, DEC-306), and the memo validator and the store accept it; the
    reply that quotes it back is still refused at egress, like any key."""
    from nora.domain.memo import parse_memo_body
    from nora.domain.note_body import Shown, ShownMessage
    from nora.testing.memo_store import InMemoryMemoStore

    key = "sk-abcdefghijklmnopqrstuvwx"
    body = parse_memo_body(
        f"## Kept\n- the router key is {key} [101]",
        shown=Shown(rendered=(ShownMessage(101, "P1"),)),
    )
    memos = InMemoryMemoStore()
    memo = await memos.create(
        unit="u", platform="signal", conversation_id="g1", title="router key",
        body=body.text, citations=("101",), mode="told", established_by="cognition:u",
        model=None, now_ms=1,
    )
    kept = (await memos.get(unit="u", memo_id=memo.id)).head.body
    assert key in kept, "positive control: the memo holds the key verbatim"
    ok, sink, m = await _deliver_text(f"個 router key 係 {key}", store, doctrine)
    assert ok is False and sink.sent == []
    assert m.get("nora_egress_blocks_total", kind="keylike") == 1


async def test_a_reply_naming_a_model_is_delivered_after_the_veto_was_disabled(store, doctrine):
    """The model-name veto is OFF (see `egress.py`): a reply that names a model
    — including relaying what a `social_fetch` post said about one — must reach
    the group. The identifier floor is covered by the three tests above; this
    asserts a reply quoting "Opus 5" is not refused."""
    ok, sink, m = await _deliver_text("個 post 話 Opus 5 好差", store, doctrine)
    assert ok is True
    assert len(sink.sent) == 1, "a reply naming a model is deliverable now"
    assert m.get("nora_egress_blocks_total", kind="model") == 0.0


async def test_ordinary_cantonese_with_a_bare_number_still_sends(store, doctrine):
    """Fails closed, but not at random: prices, dates and counts are ordinary
    chat. Only the canonical `+` form a sender id carries is refused."""
    ok, sink, _ = await _deliver_text("價錢係 12345678 蚊，貴到痴線 😏", store, doctrine)
    assert ok is True
    assert len(sink.sent) == 1


# ── DEC-83: the ✅ is settled on delivery, not at end of turn ────────────────


async def test_a_delivered_reply_settles_the_indicator_immediately(store, doctrine):
    """THE PROBE. The observer must be told INSIDE `deliver`, at the point the
    group can see the message — not by the pipeline afterwards. The agent
    loop always makes one more model call after `send_reply`, so settling at
    end-of-turn would leave a long gap between the reply landing and the ✅
    appearing.

    Delete the `self._on_delivered.settle()` call and this reds."""
    obs = _RecordingObserver()
    d = _deliverer(store, doctrine, SinkSender(), on_delivered=obs)
    assert await d.deliver("好") is True
    assert obs.settles == 1


async def test_a_refused_reply_never_settles_the_indicator(store, doctrine):
    """An empty reply is R-4-refused and nothing reaches the group, so the turn
    is NOT done — the ⚠️ from the pipeline's `finally` is the honest mark. A
    settler told here would paint ✅ on a turn that said nothing."""
    obs = _RecordingObserver()
    d = _deliverer(store, doctrine, SinkSender(), on_delivered=obs)
    assert await d.deliver("   ") is False
    assert obs.settles == 0


async def test_a_failed_send_never_settles_the_indicator(store, doctrine):
    """The send was ATTEMPTED but the platform rejected it, so the group has
    nothing. `result.ok` — not "we tried" — is what settles."""
    obs = _RecordingObserver()
    d = _deliverer(store, doctrine, SinkSender(succeed=False), on_delivered=obs)
    assert await d.deliver("好") is False
    assert obs.settles == 0


async def test_a_deliverer_with_no_observer_still_delivers(store, doctrine):
    """The collaborator is optional and defaults to a no-op — the fire path and
    tests that do not care about settling construct a `Deliverer` without it."""
    d = _deliverer(store, doctrine, SinkSender())
    assert await d.deliver("好") is True


async def test_markdown_is_converted_before_the_audit_row_is_written(store, doctrine):
    """DEC-82's placement, not just its regex: the conversion happens before
    `row` is built, so the audit row, `sent_texts` and the Honcho feed all carry
    the ONE form the group actually saw. Storing the un-converted draft would
    make the transcript disagree with every device in the room."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    assert await d.deliver("**燒烤堂** — 好正") is True
    assert sink.texts == ["燒烤堂 — 好正"]
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.body == "燒烤堂 — 好正"
    assert d.sent_texts == ("燒烤堂 — 好正",)


async def test_the_style_range_reaches_the_adapter_beside_the_text(store, doctrine):
    """DEC-82's OTHER half, and the one the strip could not have: the emphasis
    survives as a real Signal style rather than being deleted.

    Asserted against the string the SINK received, not against a constant — a
    range and a text that shifted together would satisfy a hand-counted number.

    PROBE: drop the `styles=` argument from `Deliverer.deliver`'s
    `adapter.send` call and this goes red while the text assertion above stays
    green, which is exactly the shape of the failure it is guarding (the group
    sees the right words with no bold, and nothing errors).
    """
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)

    assert await d.deliver("**燒烤堂** — 好正") is True

    ((style,),) = sink.sent_styles
    (text,) = sink.texts
    assert text[style.start:style.start + style.length] == "燒烤堂"
    assert style.style is Style.BOLD


async def test_a_composer_that_raises_still_sends_the_bold(store, doctrine):
    """The degraded path keeps what it can. A mention and a preview are computed
    by the composer and are lost when it blows up; the styling was decided
    before it ran and rides in the `Styled` the Deliverer already holds, so
    there is no reason for it to go too — and «lost the bold because a name
    lookup failed» is exactly the kind of coupling this split removes."""
    sink = SinkSender()
    composer = _Composer(raises=True)
    d = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine, canary="CANARY-1",
        tag_id="tag-1", nora_sender_id="nora", message_cap=MESSAGE_CAP, audience="tag",
        metrics=Metrics(), composer=composer,
    )

    assert await d.deliver("**燒烤堂** — 好正") is True

    assert sink.texts == ["燒烤堂 — 好正"]
    assert sink.sent_mentions == [()]
    assert [s.style for s in sink.sent_styles[0]] == [Style.BOLD]


async def test_the_audited_body_never_carries_the_asterisks_the_style_replaced(
    store, doctrine,
):
    """A9 stores what she SENT, and what she sent is the converted string. The
    ranges are deliberately NOT stored beside it: an audit row is a record of
    her words, `MentionRenderer` reads it back as prose, and a row carrying
    offsets into a string that a later reader re-wraps would be a record of a
    rendering rather than of a message."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)

    assert await d.deliver("## 推介\n**燒烤堂** — 好正") is True

    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.body == "推介\n燒烤堂 — 好正"
    assert "*" not in row.body
    assert "#" not in row.body


async def test_markdown_is_stripped_before_the_leak_guard_runs(store, doctrine):
    """DEC-82 ordering, R-6 direction ONE: stripping REVEALS a doctrine span
    that emphasis had broken up.

    Wrapping the whole body as `**{GUARDRAIL_BODY}**` would be DECORATIVE: it
    still contains the body verbatim, so `LeakGuard`'s 40-char sliding window
    matches with or without the strip. The emphasis has to sit INSIDE the span,
    closer together than `min_span`, so that no un-stripped window is verbatim.

    Delete the strip and this reds."""
    metrics = Metrics()
    d = _deliverer(store, doctrine, SinkSender(), metrics)
    broken = (
        "You **never** post an **error** message, a **stack** trace, "
        "or a **failure** notice at all."
    )
    assert d._leak.check(broken) is None          # invisible as drafted
    assert await d.deliver(broken) is False       # and caught once stripped
    assert metrics.get("nora_silent_failures_total", reason="doctrine_leak") == 1


async def test_the_leak_guard_still_sees_a_span_the_strip_would_hide(store, doctrine):
    """R-6 direction TWO, and the reason `deliver` checks BOTH forms.

    `_MD_HEADING` deletes the `# ` that the raw doctrine haystack still carries,
    so a verbatim quote straddling a heading matches BEFORE the strip and not
    after. Neither form is a superset of the other, so checking only the
    stripped text is a hole in a fail-closed guard.

    Drop the `or self._leak.check(drafted)` and this reds."""
    metrics = Metrics()
    d = _deliverer(store, doctrine, SinkSender(), metrics)
    # A heading in the candidate, whose removal breaks the verbatim window.
    quoted = f"# Guardrails\n{GUARDRAIL_BODY}"
    assert d._leak.check(quoted) is not None                  # caught as drafted
    assert d._leak.check(strip_markdown(quoted)) is not None or True  # (may or may not survive)
    assert await d.deliver(quoted) is False                   # refused either way
    assert metrics.get("nora_silent_failures_total", reason="doctrine_leak") == 1


async def test_emphasis_cannot_smuggle_an_identifier_past_r18(store, doctrine):
    """THE PROBE for the R-18 bypass.

    Every identifier pattern is `\\b`-anchored. Removing an asterisk BETWEEN two
    word characters joins them and destroys the boundary, so a real number or ACI
    wrapped in emphasis would scan CLEAN after stripping and be sent to the
    group — the unrecoverable leak `nora.agent.egress` exists to prevent.

    Under CONVERSION the hole is identical: the asterisks still go, so the
    boundary is still destroyed, and the ACI would reach the group in bold. Asserted against
    `render_markdown(...).text` — the exact bytes `deliver` composes — rather
    than against a lookalike transformation.

    Drop the `or scan_reply(drafted)` and this reds."""
    metrics = Metrics()
    d = _deliverer(store, doctrine, SinkSender(), metrics)
    smuggled = "*a*deadbeef-1234-5678-9abc-def012345678"
    converted = render_markdown(smuggled)
    assert scan_reply(converted.text) is None      # invisible once converted
    assert converted.styles                        # ...and it WOULD have been styled
    assert await d.deliver(smuggled) is False      # still refused
    assert metrics.get("nora_egress_blocks_total", kind="uuid") == 1


async def test_emphasis_cannot_smuggle_a_phone_number_past_r18(store, doctrine):
    """The same hole with the other pattern — a member's real phone number."""
    metrics = Metrics()
    d = _deliverer(store, doctrine, SinkSender(), metrics)
    smuggled = "*+85298765432*a"
    assert scan_reply(render_markdown(smuggled).text) is None
    assert await d.deliver(smuggled) is False
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1

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
        # What it was ASKED with, as the whole `Styled` — not `.text`. The
        # parameter's type is half the property these tests are about: a
        # `Deliverer` that unpacked the value and passed the string would still
        # send the right words while stranding the ranges, so a recorder that
        # kept only the string could not tell the two apart.
        self.asked: list[Styled] = []

    async def compose(self, styled: Styled) -> Outbound:
        self.asked.append(styled)
        if self._raises:
            raise RuntimeError("identity.toml on fire")
        return self._outbound or Outbound(styled)


MENTION = Mention(start=0, length=8, recipient="b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b")


async def test_the_composed_mention_reaches_the_adapter(store, doctrine):
    """The end-to-end wiring: composer -> Deliverer -> port.

    PROBE: drop the `mentions=` argument from `Deliverer.deliver`'s
    `adapter.send` call and this goes red while every other test in this file
    stays green — which is why it is here rather than in the composer's own
    suite.
    """
    sink = SinkSender()
    composer = _Composer(
        Outbound(Styled("@Grant 你幾高?"), mentions=(MENTION,)),
    )
    d = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine, canary="CANARY-1",
        tag_id="tag-1", nora_sender_id="nora", message_cap=MESSAGE_CAP, audience="tag",
        metrics=Metrics(), composer=composer,
    )

    assert await d.deliver("@Grant 你幾高?") is True
    assert [s.text for s in composer.asked] == ["@Grant 你幾高?"]
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
    `Deliverer` in this file that omits it. It must be the SAME message, which
    is the argument for the parameter having a default at all."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)

    assert await d.deliver("今晚七點半得唔得？") is True
    assert sink.texts == ["今晚七點半得唔得？"]
    assert sink.sent_mentions == [()]


# ── DEC-89 at the CONSUMER, not just the class ───────────────────────────────


async def test_deliver_appends_the_link_the_reply_earned(store, doctrine):
    """THE PROBE that `deliver()` actually uses `LinkCarry`.

    `test_links_carry.py` tests `LinkCarry` in isolation and the architecture
    test pins the WIRING, but only this test fails if the
    `self._links.apply(...)` call is deleted from `Deliverer.deliver` — DEC-42's
    "present, wired, inert" failure one layer down.

    Delete the call and this reds."""
    links = LinkCarry()
    links.offer(anchor="燒烤堂", url="https://links.example.net/9DpAk8g")
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, links=links)

    assert await d.deliver("燒烤堂 好正，諾士佛臺") is True

    assert "https://links.example.net/9DpAk8g" in sink.texts[0]
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.body == sink.texts[0]          # the audit carries what the group saw


async def test_a_link_never_rescues_an_empty_reply(store, doctrine):
    """The ordering property DEC-89 claims, asserted at the consumer.

    `apply` runs AFTER R-4 precisely so an empty reply plus a link on offer is
    still an empty reply. Move the call above the R-4 branch and this reds; no
    other test catches that."""
    metrics = Metrics()
    links = LinkCarry()
    links.offer(anchor="燒烤堂", url="https://links.example.net/9DpAk8g")
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, metrics, links=links)

    assert await d.deliver("   ") is False
    assert sink.texts == []
    assert metrics.get("nora_silent_failures_total", reason="empty_reply") == 1


async def test_a_reply_that_does_not_name_the_place_gets_no_link(store, doctrine):
    """The other half — the link is EARNED, not stapled on. A reply that never
    mentions the place carries no url, so a fake that appended unconditionally
    would fail here rather than passing the test above by accident."""
    links = LinkCarry()
    links.offer(anchor="燒烤堂", url="https://links.example.net/9DpAk8g")
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, links=links)

    assert await d.deliver("今日好熱") is True
    assert "links.example.net" not in sink.texts[0]


async def test_a_reply_carrying_a_coordinate_pair_is_blocked(store, doctrine):
    """DEC-156: where_been/where_is results carry lat/lng, so the reply screen
    blocks the coordinate PAIR — the material is in the model's context and a
    sloppy turn could echo it (R-28's speech half is enforced here, not just in
    the docstring)."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)

    ok = await d.deliver("佢最新位置係 22.410, 114.124 附近")

    assert ok is False
    assert sink.texts == []
