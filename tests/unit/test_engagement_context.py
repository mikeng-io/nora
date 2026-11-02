"""`TranscriptInjection` — its two failure-shaped behaviours, plus transcript
ordering and port conformance.

`nora.testing.FakeRecordStore` is the audit double throughout, per the same
project rule `tests/unit/test_ingest_stages.py` documents: a port never
gains a second implementation without a shared contract suite, and
`FakeRecordStore` already has one. `MemoryStore` has no such suite yet, so
`FakeMemory` below stays local to this test module, matching
`test_ingest_stages.py`'s `FakeMemory`.
"""

from __future__ import annotations

from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    Quote,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    ContextAssembler,
    EngageDecision,
    EngagementRequest,
    MemoryHealth,
    Recollection,
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


class FakeMemory:
    """Local `MemoryStore` double — see module docstring."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.queries: list[str] = []
        self.representation_unreachable = False

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str, **_route: str | None) -> None:
        self.calls.append(("ensure_peer", peer_id))

    async def feed(self, *, peer_id: str, text: str) -> bool:
        self.calls.append(("feed", peer_id))
        return True

    async def peer_card(self, *, peer_id: str, **_route: str | None) -> list[str]:
        self.calls.append(("peer_card", peer_id))
        return ["likes coffee"]

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 5,
        target: str | None = None, **_route: str | None,
    ) -> Recollection:
        # Records the QUERY, not just the call: `_recall_tagger` curates its
        # conclusions to the inbound message, and a double that ignores the
        # argument cannot tell a working call from one passing "".
        self.calls.append(("representation", peer_id))
        self.queries.append(search_query)
        if self.representation_unreachable:
            return Recollection(reachable=False)
        return Recollection(reachable=True, conclusions=("nora told Aaron they would talk",))

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


_MILES = Persons(
    by_person={"miles": PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")},
    index={("signal", "+852111"): "miles"},
)


async def test_a_transcript_read_failure_yields_an_empty_transcript_not_an_error():
    store = FakeRecordStore()
    store.fail_on_recent = True
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript == ()


async def test_a_transcript_read_failure_counts_nora_errors_total_stage_context():
    store = FakeRecordStore()
    store.fail_on_recent = True
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=metrics,
    )

    await assembler.assemble(request=_request())

    assert metrics.get("nora_errors_total", stage="context") == 1


async def test_an_unmapped_tagger_yields_no_peer_card_and_no_honcho_call():
    memory = FakeMemory()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=memory,
        persons=Persons(by_person={}, index={}),   # nobody mapped
        context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request(_msg(sender_id="+85299999999")))

    assert ctx.tagger is None
    assert ctx.peer_card == ()
    assert memory.calls == []


async def test_the_transcript_is_oldest_first_and_window_bounded():
    store = FakeRecordStore()
    for i in range(5):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=3, metrics=Metrics(),
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
        audit=store, memory=FakeMemory(), persons=_MILES,
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=Metrics(),
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=Metrics(),
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=Metrics(),
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=3, metrics=Metrics(),
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=Metrics(),
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10, metrics=Metrics(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript[0].person_name is None
    assert ctx.transcript[0].sender_name == "A Stranger"


def test_it_satisfies_the_context_assembler_port():
    assembler: ContextAssembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES, context_window=10,
        metrics=Metrics(),
    )
    assert isinstance(assembler, ContextAssembler)


# ---- the assembler's helper -------------------------------------------------
#
# The `<self>` read is tested in `tests/unit/test_self_block.py`.


def _assembler(memory, **kw):
    return TranscriptInjection(
        audit=FakeRecordStore(), memory=memory, persons=_MILES,
        context_window=10, metrics=kw.pop("metrics", Metrics()),
        ai_peer=kw.pop("ai_peer", "nora"),
    )


async def test_a_thrown_recall_never_costs_the_turn():
    """A raising Honcho is the same shape as an unreachable one from the
    group's side — the reply still happens, thinner."""
    class Boom(FakeMemory):
        async def representation(self, **kw):
            raise RuntimeError("honcho is down")

    ctx = await _assembler(Boom()).assemble(request=_request())

    assert ctx.peer_card == ("likes coffee",)     # the rest of the turn survived


# ---------------------------------------------------------------------------
# The note tier's read.
#
# The ONE read in this assembler that is not room-keyed: `standing` is scoped
# by UNIT (DEC-105), so the fake below is a unit→notes map
# rather than a store that says yes — a `standing` implementation ignoring its
# argument would pass every assertion here and prove nothing about the
# scoping, which is the only property deciding who sees a note.


def _note(note_id: str, unit: str, title: str) -> Note:
    return Note(
        id=note_id, unit=unit, title=title,
        description="Three venues on the table; nobody has picked one.",
        uncertainty="which venue, and whether the date still works",
        closing_condition="a venue is booked, or the date is abandoned",
        established_in="g1", created_at=1, head_revision_id=f"{note_id}-r1",
        anchor_date=20260909,
    )


def _policy(unit: str = "ridgeline", conversation_id: str = "g1") -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({conversation_id}), persons=None,
        primary_id=conversation_id, unit=unit,
    )
    return Policy(
        conversations=(convo,), _by_id={("signal", conversation_id): convo},
    )


class FakeNoteStore:
    """A unit→notes map answering `()` for a unit it does not hold —
    the shape `SqlNoteStore.standing` has (`WHERE unit = :unit`), not
    a double that returns its whole contents whatever it is asked."""

    def __init__(self, by_unit: dict[str, tuple[Note, ...]]) -> None:
        self._by_unit = by_unit
        self.calls: list[str] = []

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        self.calls.append(unit)
        return self._by_unit.get(unit, ())


class RaisingNoteStore:
    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        raise RuntimeError("note store is down")


async def test_standing_notes_reach_the_tag_context_when_a_store_is_wired():
    note = _note("n1", "ridgeline", "9 Sept dinner")
    store = FakeNoteStore({"ridgeline": (note,)})
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), notes=store, policy=_policy(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.notes == (note,)


async def test_notes_are_empty_when_no_store_is_configured():
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), policy=_policy(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.notes == ()


async def test_the_notes_read_is_keyed_on_the_unit_not_the_conversation():
    """The unit scoping is the whole point of this read: `g1` belongs to unit `ridgeline`,
    and a note established in the OTHER unit must not appear however alike
    the two rooms are. The ARGUMENT is asserted as well as the result, because
    a read keyed on the conversation id would answer `()` here — the right
    result for the wrong reason, and the wrong result the day a unit has
    two rooms."""
    mine = _note("n1", "ridgeline", "9 Sept dinner")
    theirs = _note("n2", "friends", "Someone else's wedding")
    store = FakeNoteStore({"ridgeline": (mine,), "friends": (theirs,)})
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), notes=store,
        policy=_policy(unit="ridgeline"),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.notes == (mine,)
    assert store.calls == ["ridgeline"], "scoped by unit, never by room"


async def test_a_failing_notes_read_yields_an_empty_index_not_a_dead_turn():
    store = FakeRecordStore()
    await store.append(message=_msg())
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10,
        metrics=Metrics(), notes=RaisingNoteStore(), policy=_policy(),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.notes == ()
    assert ctx.transcript, "the other reads must still have completed"


async def test_a_failing_notes_read_counts_nora_errors_total_stage_notes():
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, notes=RaisingNoteStore(),
        policy=_policy(),
    )

    await assembler.assemble(request=_request())

    assert metrics.get("nora_errors_total", stage="notes", platform="signal") == 1


async def test_a_room_the_policy_does_not_know_reads_no_notes_and_is_counted():
    """A store wired with no unit to scope it by is a WIRING GAP, not an
    empty notebook: engagement and ingest disagree about which `Policy` they
    were given (`EngagementPipeline`'s own `convo is not None` note). It
    defaults safely — a thinner prompt, never notes from a guessed unit —
    and it is COUNTED, because the alternative is the `<notes>` block quietly
    never appearing for the life of a deployment."""
    metrics = Metrics()
    store = FakeNoteStore({"ridgeline": (_note("n1", "ridgeline", "9 Sept dinner"),)})
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, notes=store,
        policy=_policy(conversation_id="another-room"),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.notes == ()
    assert store.calls == [], "no unit means no read at all, not a guess"
    assert metrics.get("nora_errors_total", stage="notes", platform="signal") == 1


# ---- nora_note_index_entries, the gauge config.toml calls "the tell" -------
#
# The four tests below are one test each of the four states this gauge has to
# keep apart, because a gauge that could not tell them apart would
# mislead: a notebook with notes in it, a notebook that is wired
# and empty, a tier that is switched off, and a read that failed. Only the
# first two are readings; the last two must be ABSENT rather than `0`, which is
# the whole reason the publish sits on the success path.


async def test_the_index_gauge_reports_how_many_notes_she_was_actually_shown():
    notes = tuple(
        _note(f"n{i}", "ridgeline", f"note {i}") for i in range(3)
    )
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, notes=FakeNoteStore({"ridgeline": notes}),
        policy=_policy(),
    )

    ctx = await assembler.assemble(request=_request())

    # Bound to what reached the context, not to a number written beside it: a
    # gauge that agreed with a constant while `ctx.notes` disagreed would be
    # measuring the test.
    assert metrics.get("nora_note_index_entries", unit="ridgeline") == len(ctx.notes) == 3


async def test_the_index_gauge_is_labelled_so_two_units_do_not_overwrite():
    """The label is the reason this gauge means anything on this deployment.
    `friends` and `ridgeline` are separate notebooks; unlabelled, whichever room
    tagged last would own the reading and the exported number would mean «the
    size of the notebook consulted most recently»."""
    store = FakeNoteStore({
        "ridgeline": (_note("n1", "ridgeline", "9 Sept dinner"),),
        "friends": tuple(_note(f"f{i}", "friends", f"note {i}") for i in range(4)),
    })
    metrics = Metrics()

    for conversation_id, unit in (("g1", "ridgeline"), ("g2", "friends")):
        assembler = TranscriptInjection(
            audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
            context_window=10, metrics=metrics, notes=store,
            policy=_policy(conversation_id=conversation_id, unit=unit),
        )
        await assembler.assemble(
            request=_request(_msg(conversation_id=conversation_id)))

    assert metrics.get("nora_note_index_entries", unit="ridgeline") == 1
    assert metrics.get("nora_note_index_entries", unit="friends") == 4


async def test_an_empty_notebook_reads_zero_rather_than_going_absent():
    """A wired, reached notebook holding nothing. `0` here is a measurement — «I looked, there is
    nothing» — and it is what separates a quiet notebook from a broken one."""
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, notes=FakeNoteStore({"ridgeline": ()}),
        policy=_policy(),
    )

    await assembler.assemble(request=_request())

    # PRESENCE, then value — and in that order on purpose. `Metrics.get` sums
    # the series matching its labels and answers `0.0` when there are none, so
    # `get(...) == 0` is true of a gauge that was never published and cannot
    # tell this state from the one the test below covers. Asserting membership
    # in `snapshot()` is what makes the zero a measurement.
    assert ("nora_note_index_entries", (("unit", "ridgeline"),)) in metrics.snapshot()
    assert metrics.get("nora_note_index_entries", unit="ridgeline") == 0


async def test_a_failed_read_leaves_the_gauge_absent_rather_than_zero():
    """R-27 in one gauge. A failing store returns `()` like an empty notebook
    does, and publishing `0` here would export «the notebook is empty» for «I
    could not read the notebook» — the `unavailable`-as-`nothing_found`
    collapse, in the one series meant to answer that exact question. The wiring
    gap keeps `nora_errors_total{stage="notes"}`; absence is what says no
    reading was taken."""
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, notes=RaisingNoteStore(),
        policy=_policy(),
    )

    await assembler.assemble(request=_request())

    assert ("nora_note_index_entries", (("unit", "ridgeline"),)) not in metrics.snapshot()
    assert metrics.get("nora_errors_total", stage="notes", platform="signal") == 1


# --- Standing's read half ---------------------------------------------------

async def test_no_shared_reader_is_the_dormant_default():
    """`[standing] enabled = false` — no shared reader wired, no view, the
    same "not yet wired" shape `notes` uses."""
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, shared=None,
    )
    ctx = await assembler.assemble(request=_request())
    assert ctx.shared is None
    assert metrics.get("nora_errors_total", stage="standing") == 0


async def test_the_position_is_read_by_platform_and_conversation():
    """The room's Standing paragraph is a READ ADAPTER in the shared view
    (design 20): the store is still keyed `(platform, conversation_id)`, and
    a store called with the wrong key would return a plausible position
    belonging to another room."""
    from nora.agent.shared_state import shared_reader
    from nora.domain.standing import Standing
    from nora.testing import InMemoryStandingStore

    class _Spy(InMemoryStandingStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads: list[tuple[str, str]] = []

        async def read(self, *, platform: str, conversation_id: str):
            self.reads.append((platform, conversation_id))
            return await super().read(
                platform=platform, conversation_id=conversation_id)

    store = _Spy()
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="a position",
        fold_count=1, updated_at=1,
    ))
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), policy=_policy(),
        shared=shared_reader(
            standing=store, ai_peer="nora",
            rooms_of_unit=lambda unit: (("signal", "g1"),)),
    )
    ctx = await assembler.assemble(request=_request())
    assert store.reads == [("signal", "g1")]
    pos = next(e for e in ctx.shared.entries if e.kind == "standing")
    assert pos.subject == "a position"


async def test_a_failed_standing_read_is_unavailable_never_nothing_found():
    """The THIRD state, and it is not the same as the other two. A position
    that exists and could not be fetched is `unavailable`; folding it in with
    "no store" and "no row" reports it as `nothing_found`, which is exactly the
    substitution R-27 exists to prevent. Fail-SOFT on the value — a thinner
    prompt beats no reply — and never silent."""
    class _Broken:
        async def read(self, *, platform: str, conversation_id: str):
            raise RuntimeError("db down")

    from nora.agent.shared_state import shared_reader

    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, policy=_policy(),
        shared=shared_reader(
            standing=_Broken(), ai_peer="nora",
            rooms_of_unit=lambda unit: (("signal", "g1"),)),
    )
    ctx = await assembler.assemble(request=_request())
    assert ctx.shared is not None and ctx.shared.status == "unavailable"
    # The standing store's source in the view is `findings` — the read
    # adapter that also carries the position paragraph.
    assert ("findings", "unavailable") in ctx.shared.sources


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
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), presence=_Reader(),
    )
    bare = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
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


async def test_assemble_emits_a_span_per_store_read(exported_spans):
    """PROBE: replace any one `traced_read(...)` call in `TranscriptInjection`
    with a bare `await` and its name drops out of this list."""
    notes = _note("n1", "ridgeline", "9 Sept dinner")
    memory = FakeMemory()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=memory, persons=_MILES,
        context_window=10, metrics=Metrics(),
        ai_peer="nora", tagger_card_conclusions=3,
        notes=FakeNoteStore({"ridgeline": (notes,)}), policy=_policy(),
    )

    await assembler.assemble(request=_request())

    names = [s.name for s in exported_spans.get_finished_spans()]
    assert names.count("store.record.recent") == 1
    assert names.count("store.memory.peer_card") == 1
    # `_recall_tagger` calls `MemoryStore.representation`: one read, one span.
    assert names.count("store.memory.representation") == 1
    assert names.count("store.notes.standing") == 1


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
        audit=store, memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(),
    )

    await assembler.assemble(request=_request())

    (recorded,) = [
        s for s in exported_spans.get_finished_spans()
        if s.name == "store.record.recent"
    ]
    assert recorded.attributes["nora.store.row_count"] == 2
    assert recorded.attributes["nora.store.duration_ms"] >= 0


async def test_presence_and_culture_reads_are_also_traced(exported_spans):
    """`_culture` and `_presence` are gathered alongside the six wrapped reads
    `test_assemble_emits_a_span_per_store_read` covers, and are wrapped the
    same way: same store-port boundary, same class, so every `build_prefill`
    read is visible.

    PROBE: replace either `traced_read(...)` call — the one wrapping
    `self._culture_reader.head(...)` or `self._presence_reader.read(...)` —
    with a bare `await` and its name drops out of this list.
    """
    from nora.domain.self_presence import Presence
    from nora.testing import InMemoryCultureStore

    culture_store = InMemoryCultureStore()
    await culture_store.append(
        key="g1", body="a running joke about pineapple pizza",
        written_at=1, established_by="fold",
    )

    class _Reader:
        async def read(self, *, platform: str, conversation_id: str, direct: bool = False):
            return Presence(platform=platform, label="Friends", here_state="up")

    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(),
        presence=_Reader(), culture=culture_store, policy=_policy(),
    )

    await assembler.assemble(request=_request())

    names = [s.name for s in exported_spans.get_finished_spans()]
    assert names.count("store.presence.read") == 1
    assert names.count("store.culture.head") == 3  # the room's, its unit's (C3a), hers


async def test_memory_representation_row_count_is_the_conclusions_count(
    exported_spans,
):
    """`Recollection` has no `__len__`, so plain
    `_row_count` leaves `store.memory.representation` with no row count at
    all — measurable, but never measured. `traced_read`'s `count=` callback
    is what closes that, not a special case inside `_row_count` itself.

    PROBE: drop `count=lambda r: len(r.conclusions)` from either
    `traced_read(..., "representation", ...)` call in
    `_recall_tagger` and `nora.store.row_count` disappears from its span.
    """
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(),
        ai_peer="nora", tagger_card_conclusions=3,
    )

    await assembler.assemble(request=_request())

    representation_spans = [
        s for s in exported_spans.get_finished_spans()
        if s.name == "store.memory.representation"
    ]
    assert len(representation_spans) == 1
    for recorded in representation_spans:
        # `FakeMemory.representation` answers one conclusion per call
        # (module docstring / `FakeMemory.representation`'s own body).
        assert recorded.attributes["nora.store.row_count"] == 1


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
        audit=store, memory=FakeMemory(), persons=_MILES,
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
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=context_window,
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
        example_config, audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        metrics=Metrics(), policy=permissive_policy(),
    )
    assert assembler._prompt_window == example_config.store.effective_prompt_window == 20



# ---- <memos> (DEC-306): keyed on the unit, like the notes ------------------------


async def _kept(store: object, unit: str, title: str) -> object:
    return await store.create(  # type: ignore[attr-defined]
        unit=unit, platform="signal", conversation_id="g1", title=title,
        body="## Kept\n- kept [1]", citations=("1",), mode="told",
        established_by="cognition:u", model=None, now_ms=1,
    )


async def test_the_memos_read_is_this_rooms_unit_only():
    from nora.testing.memo_store import InMemoryMemoStore

    store = InMemoryMemoStore()
    mine = await _kept(store, "ridgeline", "wifi password")
    await _kept(store, "friends", "their door code")
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), memos=store,
        policy=_policy(unit="ridgeline"),
    )
    ctx = await assembler.assemble(request=_request())
    assert [m.id for m in ctx.memos] == [mine.id]


async def test_a_failing_memos_read_is_counted_and_renders_none():
    class _Down:
        async def kept(self, *, unit: str) -> tuple[object, ...]:
            raise RuntimeError("memo store is down")

    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, memos=_Down(), policy=_policy(),
    )
    ctx = await assembler.assemble(request=_request())
    assert ctx.memos == ()
    assert metrics.get("nora_errors_total", stage="memos", platform="signal") == 1


async def test_a_room_the_policy_does_not_know_reads_no_memos_and_is_counted():
    """A wired memo store with no unit to scope it by is a
    wiring gap, counted as `_notes` counts it, never a silent empty list."""
    from nora.testing.memo_store import InMemoryMemoStore

    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, memos=InMemoryMemoStore(),
        policy=_policy(conversation_id="some-other-room"),
    )
    ctx = await assembler.assemble(request=_request())
    assert ctx.memos == ()
    assert metrics.get("nora_errors_total", stage="memos", platform="signal") == 1
