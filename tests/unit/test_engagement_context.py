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

from nora.domain.models import NormalizedMessage, PersonRef
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
