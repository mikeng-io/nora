"""`TranscriptInjection` — the two failure-shaped behaviours carried over
from `Handler._collect_context` (task-3-brief.md), plus transcript ordering
and port conformance.

`nora.testing.FakeRecordStore` is the audit double throughout, per the same
project rule `tests/unit/test_ingest_stages.py` documents: a port never
gains a second implementation without a shared contract suite, and
`FakeRecordStore` already has one. `MemoryStore` has no such suite yet, so
`FakeMemory` below stays local to this test module, matching
`test_ingest_stages.py`'s `FakeMemory`.
"""

from __future__ import annotations

from nora.domain.media import MediaBlob, MediaObject
from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    ContextAssembler,
    EngageDecision,
    EngagementRequest,
    MemoryHealth,
    Recollection,
)
from nora.engagement.context import TranscriptInjection
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeMediaObjectStore, FakeRecordStore


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

    async def ensure_peer(self, *, peer_id: str) -> None:
        self.calls.append(("ensure_peer", peer_id))

    async def feed(self, *, peer_id: str, text: str) -> bool:
        self.calls.append(("feed", peer_id))
        return True

    async def peer_card(self, *, peer_id: str) -> list[str]:
        self.calls.append(("peer_card", peer_id))
        return ["likes coffee"]

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 5,
        target: str | None = None,
    ) -> Recollection:
        # Records the QUERY, not just the call: `_recall_self` curates her own
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


async def test_the_transcript_carries_the_identity_name_not_the_profile_name():
    """DEC-80. The assembler is where identity is interpreted for the turn, so
    the `TagContext` must leave with ONE name space — the same canonical name
    `tag_message from=`, `group_members` and `lookup_person` use.

    The row is stored under the SELF-SET Signal profile name («陳志明 Chi Ming»),
    which is what the prefill used to render; `person_name` is the projection
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


# ---- <self_card>: the PRODUCER side (DEC-96) -------------------------------
#
# These exist because the first version of `_recall_self` read `msg.text` on a
# `NormalizedMessage` whose field is `body`. Every tag turn raised
# AttributeError, the fail-open `except` swallowed it, `self_card` was always
# `()`, and `nora_errors_total{stage="self_card"}` incremented on every turn —
# so the feature was 100% inert AND its outage metric read as a permanent
# Honcho failure, which would send an operator to debug the wrong system.
#
# Five tests covered the block at the time. All five built `TagContext(
# self_card=...)` by hand and could not reach this function. That is the repo's
# own war-story exactly: never read an instrument's output as evidence without
# first checking it can measure the thing.


def _assembler(memory, **kw):
    return TranscriptInjection(
        audit=FakeRecordStore(), memory=memory, persons=_MILES,
        context_window=10, metrics=kw.pop("metrics", Metrics()),
        ai_peer=kw.pop("ai_peer", "nora"),
        self_card_conclusions=kw.pop("self_card_conclusions", 8),
    )


async def test_the_self_card_is_recalled_and_curated_to_the_inbound_message():
    """The probe for the `msg.text`/`msg.body` defect. Asserting only that
    `self_card` is non-empty would NOT have caught it — the call has to be made
    with the real message text, so the assertion is on the query."""
    memory = FakeMemory()
    ctx = await _assembler(memory).assemble(
        request=_request(_msg_with_body("聽日朝早記唔記得")))

    assert ctx.self_card == ("nora told Aaron they would talk",)
    assert memory.queries == ["聽日朝早記唔記得"]      # not "", and not a crash
    assert ("representation", "nora") in memory.calls


async def test_an_unreachable_memory_yields_no_self_card_and_counts_the_stage():
    """Fail-open, and SILENT-open: no block rather than a note saying she cannot
    remember. The failure belongs to the operator, on the metric."""
    memory = FakeMemory()
    memory.representation_unreachable = True
    metrics = Metrics()

    ctx = await _assembler(memory, metrics=metrics).assemble(request=_request())

    assert ctx.self_card == ()
    assert metrics.get("nora_errors_total", stage="self_card", platform="signal") == 1


async def test_a_thrown_recall_never_costs_the_turn():
    """A raising Honcho is the same shape as an unreachable one from the
    group's side — the reply still happens, thinner."""
    class Boom(FakeMemory):
        async def representation(self, **kw):
            raise RuntimeError("honcho is down")

    metrics = Metrics()
    ctx = await _assembler(Boom(), metrics=metrics).assemble(request=_request())

    assert ctx.self_card == ()
    assert ctx.peer_card == ("likes coffee",)     # the rest of the turn survived
    assert metrics.get("nora_errors_total", stage="self_card", platform="signal") == 1


async def test_the_block_is_off_entirely_when_the_budget_is_zero():
    """`self_card_conclusions = 0` disables it, and disabling means NOT CALLING
    Honcho — not calling and discarding."""
    memory = FakeMemory()
    ctx = await _assembler(memory, self_card_conclusions=0).assemble(request=_request())

    assert ctx.self_card == ()
    assert not any(c[0] == "representation" for c in memory.calls)


# ---------------------------------------------------------------------------
# The note tier's read (2026-08-21 note-tier design, §5.1).
#
# The ONE read in this assembler that is not room-keyed: `standing` is scoped
# by WORKSPACE (§3 / DEC-105), so the fake below is a workspace→notes map
# rather than a store that says yes — a `standing` implementation ignoring its
# argument would pass every assertion here and prove nothing about the
# scoping, which is the only property deciding who sees a note.


def _note(note_id: str, workspace: str, title: str) -> Note:
    return Note(
        id=note_id, workspace=workspace, title=title,
        description="Three venues on the table; nobody has picked one.",
        uncertainty="which venue, and whether the date still works",
        closing_condition="a venue is booked, or the date is abandoned",
        established_in="g1", created_at=1, head_revision_id=f"{note_id}-r1",
        anchor_date=20260909,
    )


def _policy(workspace: str = "ridgeline", conversation_id: str = "g1") -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({conversation_id}), persons=None,
        primary_id=conversation_id, workspace=workspace,
    )
    return Policy(
        conversations=(convo,), _by_id={("signal", conversation_id): convo},
    )


class FakeNoteStore:
    """A workspace→notes map answering `()` for a workspace it does not hold —
    the shape `SqlNoteStore.standing` has (`WHERE workspace = :workspace`), not
    a double that returns its whole contents whatever it is asked."""

    def __init__(self, by_workspace: dict[str, tuple[Note, ...]]) -> None:
        self._by_workspace = by_workspace
        self.calls: list[str] = []

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        self.calls.append(workspace)
        return self._by_workspace.get(workspace, ())


class RaisingNoteStore:
    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
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


async def test_the_notes_read_is_keyed_on_the_workspace_not_the_conversation():
    """§3 is the whole point of this read: `g1` belongs to workspace `ridgeline`,
    and a note established in the OTHER workspace must not appear however alike
    the two rooms are. The ARGUMENT is asserted as well as the result, because
    a read keyed on the conversation id would answer `()` here — the right
    result for the wrong reason, and the wrong result the day a workspace has
    two rooms."""
    mine = _note("n1", "ridgeline", "9 Sept dinner")
    theirs = _note("n2", "friends", "Someone else's wedding")
    store = FakeNoteStore({"ridgeline": (mine,), "friends": (theirs,)})
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=Metrics(), notes=store,
        policy=_policy(workspace="ridgeline"),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.notes == (mine,)
    assert store.calls == ["ridgeline"], "scoped by workspace, never by room"


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
    """A store wired with no workspace to scope it by is a WIRING GAP, not an
    empty notebook: engagement and ingest disagree about which `Policy` they
    were given (`EngagementPipeline`'s own `convo is not None` note). It
    defaults safely — a thinner prompt, never notes from a guessed workspace —
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
    assert store.calls == [], "no workspace means no read at all, not a guess"
    assert metrics.get("nora_errors_total", stage="notes", platform="signal") == 1


# ---- nora_note_index_entries, the gauge config.toml calls "the tell" -------
#
# The four tests below are one test each of the four states this gauge has to
# keep apart, because the tier spent its first eight days unable to tell any of
# them from any other: a notebook with notes in it, a notebook that is wired
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
    assert metrics.get("nora_note_index_entries", workspace="ridgeline") == len(ctx.notes) == 3


async def test_the_index_gauge_is_labelled_so_two_workspaces_do_not_overwrite():
    """The label is the reason this gauge means anything on this deployment.
    `friends` and `ridgeline` are separate notebooks; unlabelled, whichever room
    tagged last would own the reading and the exported number would mean «the
    size of the notebook consulted most recently»."""
    store = FakeNoteStore({
        "ridgeline": (_note("n1", "ridgeline", "9 Sept dinner"),),
        "friends": tuple(_note(f"f{i}", "friends", f"note {i}") for i in range(4)),
    })
    metrics = Metrics()

    for conversation_id, workspace in (("g1", "ridgeline"), ("g2", "friends")):
        assembler = TranscriptInjection(
            audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
            context_window=10, metrics=metrics, notes=store,
            policy=_policy(conversation_id=conversation_id, workspace=workspace),
        )
        await assembler.assemble(
            request=_request(_msg(conversation_id=conversation_id)))

    assert metrics.get("nora_note_index_entries", workspace="ridgeline") == 1
    assert metrics.get("nora_note_index_entries", workspace="friends") == 4


async def test_an_empty_notebook_reads_zero_rather_than_going_absent():
    """The one state the tier could not report for eight days: wired, reached,
    and holding nothing. `0` here is a measurement — «I looked, there is
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
    assert ("nora_note_index_entries", (("workspace", "ridgeline"),)) in metrics.snapshot()
    assert metrics.get("nora_note_index_entries", workspace="ridgeline") == 0


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

    assert ("nora_note_index_entries", (("workspace", "ridgeline"),)) not in metrics.snapshot()
    assert metrics.get("nora_errors_total", stage="notes", platform="signal") == 1


# --- Standing's read half (design/dreamer-agent-loop/26-standing §4) --------

async def test_no_standing_store_is_the_dormant_default_and_costs_no_query():
    """`[standing] enabled = false` — not an error, not counted, the same
    "not yet wired" shape `notes` uses."""
    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, standing=None,
    )
    ctx = await assembler.assemble(request=_request())
    assert ctx.standing is None
    assert metrics.get("nora_errors_total", stage="standing") == 0


async def test_the_position_is_read_by_platform_and_conversation():
    """S4, asserted on the ARGUMENTS rather than on the value: this is the one
    read in the assembler keyed on `(platform, conversation_id)` rather than on
    a workspace or a scope, and a store called with the wrong key would return
    a plausible position belonging to another room."""
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
        context_window=10, metrics=Metrics(), standing=store,
    )
    ctx = await assembler.assemble(request=_request())
    assert store.reads == [("signal", "g1")]
    assert ctx.standing.body == "a position"


async def test_a_failed_standing_read_is_counted_rather_than_swallowed():
    """The THIRD state, and it is not the same as the other two. A position
    that exists and could not be fetched is `unavailable`; folding it in with
    "no store" and "no row" reports it as `nothing_found`, which is exactly the
    substitution R-27 exists to prevent. Fail-SOFT on the value — a thinner
    prompt beats no reply — and never silent."""
    class _Broken:
        async def read(self, *, platform: str, conversation_id: str):
            raise RuntimeError("db down")

    metrics = Metrics()
    assembler = TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemory(), persons=_MILES,
        context_window=10, metrics=metrics, standing=_Broken(),
    )
    ctx = await assembler.assemble(request=_request())
    assert ctx.standing is None
    assert metrics.get(
        "nora_errors_total", stage="standing", platform="signal") == 1


# ---- the media join (issue #116) -------------------------------------------
#
# `messages.body` carries `[image a3f2c1]` and nothing else; the comprehension
# lives in `media_objects` and is joined HERE, in the assembler, so
# `nora/agent/prefill.py` stays a pure renderer over `TagContext`. These tests
# are written against the assembled `TagContext`, which is exactly what the
# renderer is handed.

_SHA = "a3f2c1" + "0" * 58
_VOICE_SHA = "b7c2d9" + "0" * 58


async def _assembled(store, *, media_objects=None, chars: int = 240):
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10,
        metrics=Metrics(), media_objects=media_objects, media_detail_chars=chars,
    )
    return await assembler.assemble(request=_request())


async def _with_media(store, *, body: str, blob: MediaBlob) -> None:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body=body, raw={},
        media_blobs=(blob,),
    )
    await store.append(message=msg)


def _obj(sha: str, **kw) -> MediaObject:
    base: dict = {
        "sha256": sha, "media_type": "image/png", "size": 4,
        "source": "inbound", "title": "", "description": "", "content": "",
        "created_at_ms": 1,
    }
    base.update(kw)
    return MediaObject(**base)


async def test_a_comprehended_image_renders_its_title_and_description():
    """The short form: `[image a3f2c1: <title> - <description>]`. The stored
    `content` is deliberately NOT here — it is what `media_fetch` serves, and
    putting it in every prefill is the token cost the split exists to avoid."""
    store = FakeRecordStore()
    await _with_media(
        store, body="睇下 [image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )
    media = FakeMediaObjectStore([_obj(
        _SHA, title="a plate of noodles",
        description="a bowl of wonton noodles on a marble table",
        content="生記粥麵 $58",
    )])

    ctx = await _assembled(store, media_objects=media)

    assert ctx.transcript[0].body == (
        "睇下 [image a3f2c1: a plate of noodles - a bowl of wonton noodles "
        "on a marble table]"
    )


async def test_an_uncomprehended_image_renders_the_bare_handle():
    """R-51's render half. Nothing has read those bytes yet — which is exactly
    the state every medium was in before this existed — so the placeholder
    claims nothing. Never `[image a3f2c1: ]`.

    Positive control: the SAME store, the SAME row, with a comprehension on it
    renders the detail (the test above), so this is not passing because the
    join never ran."""
    store = FakeRecordStore()
    await _with_media(
        store, body="睇下 [image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )
    media = FakeMediaObjectStore([_obj(_SHA)])

    ctx = await _assembled(store, media_objects=media)

    assert ctx.transcript[0].body == "睇下 [image a3f2c1]"


async def test_a_voice_note_renders_its_whole_transcript_and_is_never_clipped():
    """Issue #116 decision 4, and the explicit exception in the render bound.
    A voice note is conversation CONTENT — collapsing it to title+description
    would mean Nora stops hearing what people actually said and starts reading
    a summary of it. The clip that binds every other medium does not apply."""
    store = FakeRecordStore()
    words = "喂" * 400
    await _with_media(
        store, body="[voice note b7c2d9]",
        blob=MediaBlob(sha256=_VOICE_SHA, media_type="audio/aac", size=4),
    )
    media = FakeMediaObjectStore([_obj(
        _VOICE_SHA, media_type="audio/aac", title="asking about tonight",
        description="a voice note about dinner", content=words,
    )])

    ctx = await _assembled(store, media_objects=media, chars=40)

    assert ctx.transcript[0].body == f"[voice note b7c2d9: {words}]"


async def test_every_other_mediums_detail_is_bounded_at_render():
    """The STORED comprehension is unbounded on purpose — `media_fetch` is
    what serves the rest — so the bound has to be here, where the tokens are
    actually spent."""
    store = FakeRecordStore()
    await _with_media(
        store, body="[image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )
    media = FakeMediaObjectStore([_obj(_SHA, description="x" * 500)])

    ctx = await _assembled(store, media_objects=media, chars=40)

    assert ctx.transcript[0].body == "[image a3f2c1: " + "x" * 40 + "]"


async def test_an_identifier_in_a_comprehension_costs_the_detail_not_the_row():
    """R-18's floor, applied at RENDER rather than at store time — and that is
    the stricter position, not a relaxation. `media_objects` is a durable cache
    keyed by sha256 and shared across rooms and restarts, so a row screened
    once at write time would be served unscreened for the life of the
    deployment (`nora/ingest/stickers.py`'s own rule about cache hits)."""
    store = FakeRecordStore()
    metrics = Metrics()
    await _with_media(
        store, body="[image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )
    media = FakeMediaObjectStore([_obj(
        _SHA, title="a business card",
        description="a card reading +85212345678",
    )])
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10,
        metrics=metrics, media_objects=media,
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript[0].body == "[image a3f2c1]"
    assert "85212345678" not in ctx.transcript[0].body
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_media_index_outage_leaves_the_transcript_exactly_as_stored():
    """Fail-soft, like every other read in this class: a thinner prompt beats
    no reply. The handles are what the row actually says, so degrading to them
    claims nothing that is not already true."""
    store = FakeRecordStore()
    metrics = Metrics()
    await _with_media(
        store, body="睇下 [image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )
    assembler = TranscriptInjection(
        audit=store, memory=FakeMemory(), persons=_MILES, context_window=10,
        metrics=metrics, media_objects=FakeMediaObjectStore(fail=True),
    )

    ctx = await assembler.assemble(request=_request())

    assert ctx.transcript[0].body == "睇下 [image a3f2c1]"


async def test_no_media_index_wired_reads_nothing_and_renders_the_handles():
    """The inert state — every harness that wires no index, and the deployment
    before this existed. Not an error, and no read at all."""
    store = FakeRecordStore()
    await _with_media(
        store, body="[image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )

    ctx = await _assembled(store, media_objects=None)

    assert ctx.transcript[0].body == "[image a3f2c1]"


async def test_a_bracket_in_the_senders_own_text_is_not_a_placeholder():
    """The join rewrites by REGEX over the closed label set plus fixed-width
    hex, never by position, for `body_with_handles`' reason: the text around a
    placeholder is sender-supplied and may contain brackets, and a positional
    replacement would silently corrupt somebody's message."""
    store = FakeRecordStore()
    await _with_media(
        store, body="who wrote [image] here [image a3f2c1]",
        blob=MediaBlob(sha256=_SHA, media_type="image/png", size=4),
    )
    media = FakeMediaObjectStore([_obj(_SHA, title="a screenshot")])

    ctx = await _assembled(store, media_objects=media)

    assert ctx.transcript[0].body == (
        "who wrote [image] here [image a3f2c1: a screenshot]"
    )
