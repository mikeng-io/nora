"""`TranscriptInjection` — its two failure-shaped behaviours, plus transcript
ordering and port conformance.

`nora.testing.FakeRecordStore` is the audit double throughout, per the same
project rule `tests/unit/test_ingest_stages.py` documents: a port never
gains a second implementation without a shared contract suite, and
`FakeRecordStore` already has one.
"""

from __future__ import annotations

from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    Quote,
)
from nora.domain.ports import (
    ContextAssembler,
    EngageDecision,
    EngagementRequest,
)
from nora.domain.quote_window import QuoteWindow
from nora.engagement.context import TranscriptInjection
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeRecordStore


def _msg(sender_id: str = "+852111", conversation_id: str = "g1") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )


def _msg_with_body(body: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body=body, raw={},
    )


def _request(msg: NormalizedMessage | None = None, tag_id: str = "tag-1") -> EngagementRequest:
    decision = EngageDecision(engage=True, reason="trigger_text")
    return EngagementRequest(message=msg or _msg(), decision=decision, tag_id=tag_id)


_MILES = Persons(
    by_person={"miles": PersonRef(person_id="miles", name="Miles")},
    index={("signal", "+852111"): "miles"},
)


async def test_a_transcript_read_failure_yields_an_empty_transcript_not_an_error():
    store = FakeRecordStore()
    store.fail_on_recent = True
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript == ()


async def test_a_transcript_read_failure_counts_nora_errors_total_stage_context():
    store = FakeRecordStore()
    store.fail_on_recent = True
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=metrics,
    )

    await assembler.assemble(request=_request())

    assert metrics.get("nora_errors_total", stage="context") == 1


async def test_an_unmapped_tagger_yields_no_tagger_and_no_peer_card():
    assembler = TranscriptInjection(
        audit=FakeRecordStore(),
        persons=Persons(by_person={}, index={}),   # nobody mapped
        context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request(_msg(sender_id="+85299999999")))

    assert ctx.tagger is None
    assert ctx.peer_card == ()


async def test_the_transcript_is_oldest_first_and_window_bounded():
    store = FakeRecordStore()
    for i in range(5):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=3, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert [row.body for row in ctx.transcript] == ["m2", "m3", "m4"]  # bounded, oldest-first


async def _seeded(
    count: int, *, context_window: int, quote: Quote | None = None,
    quote_window: QuoteWindow | None = None,
):
    """A store with `count` rows (`ts`/`id` 0..count-1, bodies `m0`..`m{count-1}`)
    and an assembler over it. The tagging message is the LAST row's
    successor — not itself one of the seeded rows, matching how a live reply
    arrives after what it quotes. `quote_window=None` (the default) exercises
    the plain-window fallback shape; pass a real `QuoteWindow` to exercise the
    growth this module tests."""
    store = FakeRecordStore()
    for i in range(count):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES,
        context_window=context_window, metrics=Metrics(), quote_window=quote_window,
    )
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=count, body="reply", raw={},
        quote=quote,
    )
    return await assembler.assemble(request=_request(msg))


def _quote_window(store: FakeRecordStore, **kw) -> QuoteWindow:
    return QuoteWindow(audit=store, metrics=Metrics(), **kw)


async def test_a_quote_outside_the_window_pulls_the_parent_back_into_it():
    """A reply's quote that points past `context_window` must not leave the
    parent out of the window (else `_parent_of` resolves to nothing and Nora
    answers as though the reply were unthreaded).

    25 rows seeded, `quoted_ts=5` — `depth_to` is 20
    (ts 5..24), past `context_window=10`, so this grows to `depth + margin`
    = 30, which reaches every row (25 < 30)."""
    store = FakeRecordStore()
    for i in range(25):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=Metrics(),
        quote_window=_quote_window(store),
    )
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=25, body="reply", raw={},
        quote=Quote(author_id="x", text="", quoted_ts=5),
    )

    ctx = await assembler.assemble(request=_request(msg))

    assert ctx.transcript[0].body == "m0"          # margin reached the start
    assert any(row.body == "m5" for row in ctx.transcript)   # and the parent itself


async def test_no_quote_window_wired_is_the_pre_dec_195_fallback():
    """`quote_window=None` (a harness that has not opted in to DEC-195) must
    fetch the plain `context_window`, with no growth, even when the quote's
    parent is out of range."""
    ctx = await _seeded(25, context_window=10, quote=Quote(author_id="x", text="", quoted_ts=5))
    assert [row.body for row in ctx.transcript] == [f"m{i}" for i in range(15, 25)]


async def test_a_resolvable_quote_inside_the_window_does_not_grow_it():
    """A quote whose parent is already inside the plain window must leave the
    window exactly as it would render without one — `depth_to` finding it
    still in range (`depth=2` against `context_window=10`) must not somehow
    shrink or reorder it."""
    store = FakeRecordStore()
    for i in range(5):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=Metrics(),
        quote_window=_quote_window(store),
    )
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=5, body="reply", raw={},
        quote=Quote(author_id="x", text="", quoted_ts=3),
    )

    ctx = await assembler.assemble(request=_request(msg))

    assert [row.body for row in ctx.transcript] == ["m0", "m1", "m2", "m3", "m4"]


async def test_a_quote_beyond_the_cap_falls_back_to_the_plain_window():
    """`quote_window_multiplier` bounds the growth. depth = 100 rows back
    against `context_window=10`, `quote_window_multiplier=2` (cap 20): even
    with the margin, `depth + margin` exceeds the cap, so this degrades to
    EXACTLY the plain-window behaviour — the plain 10-row window, parent
    unresolved, reply renders unthreaded."""
    store = FakeRecordStore()
    for i in range(105):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=Metrics(),
        quote_window=_quote_window(store, quote_window_multiplier=2),
    )
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=105, body="reply", raw={},
        quote=Quote(author_id="x", text="", quoted_ts=5),
    )

    ctx = await assembler.assemble(request=_request(msg))

    assert len(ctx.transcript) == 10
    assert not any(row.body == "m5" for row in ctx.transcript)


async def test_a_quote_with_no_resolvable_parent_falls_back_to_the_plain_window():
    """`depth_to` answers `None` for a `ts` no row carries (pruned,
    pre-migration, a foreign conversation) — the fetch must not blow up or
    silently return everything; it degrades to the plain window."""
    store = FakeRecordStore()
    for i in range(5):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=3, metrics=Metrics(),
        quote_window=_quote_window(store),
    )
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=5, body="reply", raw={},
        quote=Quote(author_id="x", text="", quoted_ts=999),
    )

    ctx = await assembler.assemble(request=_request(msg))

    assert [row.body for row in ctx.transcript] == ["m2", "m3", "m4"]


async def test_the_transcript_carries_the_identity_name_not_the_profile_name():
    """DEC-80. The assembler is where identity is interpreted for the turn, so
    the `TagContext` must leave with ONE name space — the same canonical name
    `tag_message from=`, `group_members` and `lookup_person` use.

    The row is stored under the SELF-SET Signal profile name («陳志明 Chi Ming»),
    which is what the prefill would otherwise render; `person_name` is the projection
    that makes the transcript join to the rest of the prompt. Both are kept, so
    the rewrite is auditable rather than silent. Delete the `name_transcript`
    call in `assemble` and this reds."""
    store = FakeRecordStore()
    await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="陳志明 Chi Ming", timestamp=1, body="hi", raw={},
    ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript[0].person_name == "Miles"            # what gets rendered
    assert ctx.transcript[0].sender_name == "陳志明 Chi Ming"   # provenance, still there


async def test_an_unmapped_speaker_keeps_a_null_person_name():
    """The fallback the renderer depends on: an account not in identity.toml gets
    NO canonical name rather than a guessed one, and `_label` then falls back to
    the profile name so they stay visible as somebody. Filling this in with
    anything — the sender_id, "unknown" — would be inventing an identity."""
    store = FakeRecordStore()
    await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+85299999999", sender_name="A Stranger", timestamp=1, body="hi", raw={},
    ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript[0].person_name is None
    assert ctx.transcript[0].sender_name == "A Stranger"


def test_it_satisfies_the_context_assembler_port():
    assembler: ContextAssembler = TranscriptInjection(
        audit=FakeRecordStore(), persons=_MILES, context_window=10,
        metrics=Metrics(),
    )
    assert isinstance(assembler, ContextAssembler)


def _policy(unit: str = "ridgeline", conversation_id: str = "g1") -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({conversation_id}), persons=None,
        primary_id=conversation_id, unit=unit,
    )
    return Policy(
        conversations=(convo,), _by_id={("signal", conversation_id): convo},
    )


async def test_the_tag_path_reads_presence_at_turn_start():
    """The assembler hands the reader the turn's
    own room, and what it returns reaches `TagContext.presence` — the one
    field `<presence>` renders from. Without a reader the field stays `None`
    and the prefill falls back to the network alone."""
    from nora.domain.self_presence import Presence

    asked: list[tuple[str, str, bool]] = []

    class _Reader:
        async def read(self, *, platform: str, conversation_id: str, direct: bool = False):
            asked.append((platform, conversation_id, direct))
            return Presence(platform=platform, label="Friends", here_state="up")

    wired = TranscriptInjection(
        audit=FakeRecordStore(), persons=_MILES,
        context_window=10, metrics=Metrics(), presence=_Reader(),
    )
    bare = TranscriptInjection(
        audit=FakeRecordStore(), persons=_MILES,
        context_window=10, metrics=Metrics(),
    )

    ctx = await wired.assemble(request=_request())
    bare_ctx = await bare.assemble(request=_request())

    assert asked == [("signal", "g1", False)]
    assert ctx.presence == Presence(platform="signal", label="Friends", here_state="up")
    assert bare_ctx.presence is None


# ---------------------------------------------------------------------------
# STORE-READ SPANS. `traced_read` (`nora.obs.tracing`) is the ONE wrapper every
# read below shares — asserted here on EXPORTED spans (`exported_spans`,
# `tests/conftest.py`), the same discipline `tests/test_prefill.py` uses for
# `agent.prefill`: a test that only checked `traced_read` was CALLED would stay
# green if the wrapper lost its attributes or a call site stopped going through
# it.
# ---------------------------------------------------------------------------


async def test_a_store_read_span_carries_row_count_and_duration(exported_spans):
    """An unbounded or slow read must be visible. Row
    count and duration are what make a span say THAT rather than just THAT IT
    HAPPENED.

    PROBE: delete the `annotate(...)` call in `traced_read` and both
    attributes disappear from the exported span.
    """
    store = FakeRecordStore()
    await store.append(message=_msg())
    await store.append(message=_msg())
    assembler = TranscriptInjection(
        audit=store, persons=_MILES,
        context_window=10, metrics=Metrics(),
    )

    await assembler.assemble(request=_request())

    (recorded,) = [
        s for s in exported_spans.get_finished_spans()
        if s.name == "store.record.recent"
    ]
    assert recorded.attributes["nora.store.row_count"] == 2
    assert recorded.attributes["nora.store.duration_ms"] >= 0


async def test_the_called_presence_read_is_traced(exported_spans):
    from nora.domain.self_presence import Presence

    class Reader:
        async def read(self, *, platform, conversation_id, direct=False):
            return Presence(platform=platform, label=conversation_id, here_state="up")

    assembler = TranscriptInjection(
        audit=FakeRecordStore(), persons=_MILES,
        context_window=10, metrics=Metrics(), presence=Reader(), policy=_policy(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.presence.label == "g1"
    assert sum(s.name == "store.presence.read" for s in exported_spans.get_finished_spans()) == 1


async def test_a_failing_read_ends_its_span_with_error_status(exported_spans):
    """The transcript read fails soft (`_recent`'s own try/except: a thinner
    reply beats no reply) — but the SPAN for it must still say the read
    failed, or a store timing out reads identically to one that answered
    fast, which is exactly the visibility these spans exist for.

    PROBE: wrap `traced_read`'s body in a `try/except Exception: return
    default` and the span goes back to reading OK on a store that raised.
    """
    store = FakeRecordStore()
    store.fail_on_recent = True
    assembler = TranscriptInjection(
        audit=store, persons=_MILES,
        context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript == (), "the turn itself still degrades gracefully"
    (recorded,) = [
        s for s in exported_spans.get_finished_spans()
        if s.name == "store.record.recent"
    ]
    assert recorded.status.status_code.name == "ERROR"
    assert recorded.attributes["nora.outcome"] == "RuntimeError"
    assert "nora.store.row_count" not in recorded.attributes, (
        "a failed read never fabricates a count for what it did not get back"
    )


async def _seeded_prompt(count: int, *, context_window: int, prompt_window: int,
                         quote: Quote | None = None, quote_window_for_store=False):
    store = FakeRecordStore()
    for i in range(count):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, persons=_MILES, context_window=context_window,
        prompt_window=prompt_window, metrics=Metrics(),
        quote_window=_quote_window(store) if quote_window_for_store else None,
    )
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=count, body="reply", raw={},
        quote=quote,
    )
    return await assembler.assemble(request=_request(msg))


async def test_the_prompt_carries_at_most_prompt_window_rows_and_the_read_stays_whole():
    ctx = await _seeded_prompt(45, context_window=40, prompt_window=20)
    assert [r.body for r in ctx.transcript] == [f"m{i}" for i in range(25, 45)]
    assert [r.body for r in ctx.lookup_transcript] == [f"m{i}" for i in range(5, 45)]


async def test_a_quoted_parent_beyond_the_prompt_window_is_never_cut():
    """Parent m10 is 35 rows back: inside the 40-row read, outside the 20-row
    prompt. The render reaches back to it; the newest rows all stay."""
    ctx = await _seeded_prompt(
        45, context_window=40, prompt_window=20,
        quote=Quote(author_id="x", text="", quoted_ts=10),
    )
    assert ctx.transcript[0].body == "m10"
    assert ctx.transcript[-1].body == "m44"
    assert len(ctx.transcript) == 35


async def test_a_quote_past_the_read_window_still_resolves_through_the_quote_resolver():
    """Parent m5 is 55 rows back, past `context_window=40`: DEC-195 grows the
    READ (cap 120), and the prompt reaches back to the parent."""
    ctx = await _seeded_prompt(
        60, context_window=40, prompt_window=20,
        quote=Quote(author_id="x", text="", quoted_ts=5), quote_window_for_store=True,
    )
    assert any(r.body == "m5" for r in ctx.transcript)


async def test_a_window_no_deeper_than_the_prompt_leaves_lookup_empty():
    ctx = await _seeded_prompt(12, context_window=40, prompt_window=20)
    assert len(ctx.transcript) == 12 and ctx.lookup_transcript == ()


def test_the_composition_root_hands_the_configured_prompt_window_to_the_assembler(example_config):
    """`[store] prompt_window` reaches `TranscriptInjection` through
    `build_assembler`; without this wire the key is decoration and the prompt
    stays at 40."""
    from nora.app.assembler import build_assembler
    from nora.policy import permissive_policy

    assembler = build_assembler(
        example_config, audit=FakeRecordStore(), persons=_MILES,
        metrics=Metrics(), policy=permissive_policy(),
    )
    assert assembler._prompt_window == example_config.store.effective_prompt_window == 20
