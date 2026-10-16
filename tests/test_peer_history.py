"""`<peer_history>` — her memory of the person talking, injected up front.

The block is a DELIBERATE duplicate of the `about` tool, so most of what
matters here is not that it appears but that it does not appear twice, does not
appear when it would be a lie, and does not take the turn down when Honcho is
slow. Those are the tests.

The fake memory below answers `peer_card` and `representation` INDEPENDENTLY
and differently, because the live pair does: they share no items, one rendering
identity claims and the other dated episodes. A double
answering both from one list would model the merge this design specifically
rejects.
"""

from __future__ import annotations

import asyncio

import pytest

from nora.agent.prefill import build_prefill
from nora.agent.room import Room
from nora.domain.models import NormalizedMessage, PersonRef, TagContext
from nora.domain.ports import EngagementRequest, Recollection
from nora.engagement.context import TranscriptInjection
from nora.persons import Persons

BLAKE = PersonRef(
    person_id="blake_lindqvist", name="Blake Lindqvist", honcho_peer_id="blake_lindqvist",
    aliases=("Fung", "峰哥"),
)
CARD = ("IDENTITY: Name: Blake Lindqvist", "IDENTITY: Aliases: Fung (峰)")
EPISODES = ("blake_lindqvist asked what to eat for dinner", "blake_lindqvist said '一個月'")


class _Memory:
    """Answers the two verbs separately — they are different endpoints holding
    different things, and the whole design rests on that."""

    def __init__(
        self, *, card=CARD, episodes=EPISODES, reachable=True,
        card_raises=False, rep_raises=False, delay=0.0,
    ) -> None:
        self._card, self._episodes = card, episodes
        self._reachable, self._delay = reachable, delay
        self._card_raises, self._rep_raises = card_raises, rep_raises
        self.targets: list[str | None] = []
        self.queries: list[str] = []

    async def peer_card(self, *, peer_id: str, **_route: str | None) -> list[str]:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._card_raises:
            raise RuntimeError("honcho down")
        return list(self._card)

    async def representation(
        self, *, peer_id: str, search_query: str,
        max_conclusions: int = 9, target: str | None = None, **kw,
    ) -> Recollection:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._rep_raises:
            raise RuntimeError("honcho down")
        self.targets.append(target)
        self.queries.append(search_query)
        # observer == observed is HER card; a target is somebody else's.
        if target is None:
            return Recollection(reachable=True, conclusions=("nora joined Discord",))
        if not self._reachable:
            return Recollection(reachable=False)
        return Recollection(reachable=True, conclusions=tuple(self._episodes)[:max_conclusions])


class _Audit:
    async def recent(self, **kw):
        return []


class _Metrics:
    def __init__(self) -> None:
        self.counts: dict[tuple, float] = {}

    def inc(self, name, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        self.counts[key] = self.counts.get(key, 0.0) + 1.0

    def total(self, name) -> float:
        return sum(v for (n, _), v in self.counts.items() if n == name)


def _persons() -> Persons:
    return Persons(
        by_person={"blake_lindqvist": BLAKE},
        index={("signal", "uuid-blake"): "blake_lindqvist"},
        life360=(),
    )


def _msg(body: str = "今晚食咩", sender: str = "uuid-blake") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="room", conversation_type="group",
        sender_id=sender, sender_name="Blake", timestamp=0, body=body, raw={},
        mentions=frozenset(), quote=None, media=(), message_id="1", media_blobs=(),
    )


def _assembler(memory, *, metrics=None, tagger_conclusions=8) -> TranscriptInjection:
    return TranscriptInjection(
        audit=_Audit(), memory=memory, persons=_persons(), context_window=10,
        metrics=metrics or _Metrics(), ai_peer="nora",
        tagger_card_conclusions=tagger_conclusions,
    )


async def _assemble(memory, **kw) -> TagContext:
    return await _assembler(memory, **kw).assemble(
        request=EngagementRequest(message=_msg(), decision=None, tag_id="t"),
    )


# ---------------------------------------------------------------------------
# what it is
# ---------------------------------------------------------------------------


async def test_the_tagger_gets_both_their_card_and_their_episodes():
    """The two halves of one person: the card says who they are, the history says
    what happened with them. Live they share no items, so having one is not
    having the other."""
    ctx = await _assemble(_Memory())

    assert ctx.peer_card == CARD
    assert ctx.tagger_card == EPISODES


async def test_the_recall_is_keyed_on_what_they_just_said():
    """Not the newest N. `search_query` is the inbound message, so a promise
    about tomorrow surfaces when somebody mentions tomorrow — the only time it
    is worth its tokens."""
    memory = _Memory()

    await _assemble(memory)

    assert "今晚食咩" in memory.queries


async def test_the_observer_is_nora_and_the_observed_is_the_tagger():
    """Reversing these reads the person's model of NORA, which is
    a different question and not one she should be answering from."""
    memory = _Memory()

    await _assemble(memory)

    assert "blake_lindqvist" in memory.targets, "the tagger must be the OBSERVED"
    assert None not in memory.targets, (
        "her own representation is no longer read: the `<self>` block is her "
        "Character account, not an observer==observed call"
    )


async def test_her_own_card_is_never_injected():
    """The asymmetry is deliberate and load-bearing. Her card asserts «Kind: AI
    chatbot/assistant» and an owner with «admin authority» — both distilled
    from her own output, both contradicting identity.md, and a concrete
    observation beats a prescription. Other people's cards are facts about
    them, so they are fine; hers is a claim about her, so it is not."""
    memory = _Memory()

    await _assemble(memory)

    # `peer_card` is only ever asked about the TAGGER, never about `nora`.
    ctx = await _assemble(memory)
    assert ctx.peer_card == CARD
    blob = str(build_prefill(ctx, display_name="Nora", context_window=10))
    assert "AI chatbot" not in blob


# ---------------------------------------------------------------------------
# what stops it lying
# ---------------------------------------------------------------------------


async def test_an_unreachable_memory_yields_no_block_rather_than_an_empty_one():
    """Fail-open and SILENT-open. This is background she may draw on, never the
    ground for a claim, so «I could not check» has nothing to be reported
    about — `about` remains the path for anything she will actually assert, and
    it still answers `unavailable` honestly."""
    ctx = await _assemble(_Memory(reachable=False))

    assert ctx.tagger_card == ()
    assert "<peer_history" not in str(build_prefill(ctx, display_name="N", context_window=10))


async def test_an_unmapped_sender_triggers_no_recall_at_all():
    """No person, no peer id, nothing to ask about — and asking anyway would be
    a call keyed on a `None`."""
    memory = _Memory()
    ctx = await _assembler(memory).assemble(
        request=EngagementRequest(
            message=_msg(sender="stranger"), decision=None, tag_id="t"),
    )

    assert ctx.tagger is None
    assert ctx.tagger_card == ()
    assert "blake_lindqvist" not in memory.targets


async def test_zero_conclusions_disables_the_block_entirely():
    memory = _Memory()
    ctx = await _assemble(memory, tagger_conclusions=0)

    assert ctx.tagger_card == ()
    assert "blake_lindqvist" not in memory.targets, "disabled means no call, not a discarded one"


@pytest.mark.parametrize("failing", ["card_raises", "rep_raises"])
async def test_one_failing_read_never_cancels_the_others(failing):
    """THE `gather` HAZARD, and the reason every helper catches its own.

    The four reads run concurrently. An exception escaping one would cancel its
    three siblings and take the whole turn down — turning «Honcho hiccupped»
    into «Nora said nothing», which is the exact inversion of the rule that
    memory is never load-bearing.
    """
    ctx = await _assemble(_Memory(**{failing: True}))

    assert ctx.tagger is not None, "the turn must survive"
    if failing == "card_raises":
        assert ctx.peer_card == ()
        assert ctx.tagger_card == EPISODES, "the sibling read must still have run"
    else:
        assert ctx.peer_card == CARD, "the sibling read must still have run"
        assert ctx.tagger_card == ()


async def test_the_reads_are_concurrent_not_sequential():
    """Four reads at 50ms each: ~50ms gathered, ~200ms sequential.

    Not a micro-optimisation — these sit at the FRONT of every tag, before the
    first model call, so serial Honcho round trips are latency the group feels
    on every single message. The threshold is deliberately loose enough that a
    slow CI box cannot fail it while still being impossible to pass serially.
    """
    memory = _Memory(delay=0.05)

    start = asyncio.get_running_loop().time()
    await _assemble(memory)
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 0.15, f"reads look sequential ({elapsed:.3f}s for 4x50ms)"


async def test_an_empty_card_for_a_mapped_person_is_counted():
    """The block's silent non-render is counted.

    `peer_card` answers `[]` for a dead endpoint, a timeout, an unknown peer
    and a genuinely empty card alike, so the block silently does not render and
    nothing says why. The counter does not disambiguate those four; it makes «her card
    stopped appearing» visible at all.
    """
    metrics = _Metrics()
    await _assemble(_Memory(card=()), metrics=metrics)

    assert metrics.total("nora_peer_card_empty_total") == 1.0


async def test_a_present_card_is_not_counted_as_empty():
    metrics = _Metrics()
    await _assemble(_Memory(), metrics=metrics)

    assert metrics.total("nora_peer_card_empty_total") == 0.0


# ---------------------------------------------------------------------------
# what stops the duplicate
# ---------------------------------------------------------------------------


def _room(*, injected: bool) -> Room:
    return Room(
        platform="signal", conversation_id="room", roster_source=None,
        persons=_persons(), tagger=BLAKE, tagger_card_injected=injected,
    )


def test_the_room_reports_whether_the_block_landed():
    assert _room(injected=True).tagger_card_injected is True
    assert _room(injected=False).tagger_card_injected is False


async def test_the_block_is_rendered_with_the_persons_name_and_a_hedge():
    """The framing carries the safety property, as it does for every block
    distilled from what was said. These are conclusions Nora drew from
    messages she witnessed — inferred and occasionally wrong about intent in
    a way an identity fact is not — and the failure this invites is
    confidently telling somebody what they think."""
    ctx = await _assemble(_Memory())

    blob = str(build_prefill(ctx, display_name="Nora", context_window=10))

    assert "<peer_history" in blob
    assert "Blake Lindqvist" in blob
    assert "you may be wrong" in blob
    assert "dinner" in blob


# --- `<culture>` on the tag path (DEC-207) ------------------------------------


class _Policy:
    """The one policy verb the assembler's culture read uses: the arriving
    thread resolves to a room whose `primary_id` DIFFERS from it, so a read by
    the thread id would miss (DEC-181)."""

    def shows_self_in(self, platform, conversation_id):
        return True

    def conversation(self, platform, conversation_id):
        if (platform, conversation_id) != ("signal", "room"):
            return None
        return type("_Convo", (), {"primary_id": "room-primary", "unit": "u", "persons": None})()


async def test_a_tag_turn_carries_the_rooms_account_through_assemble_and_prefill():
    """End to end on the tag path: `assemble` reads the account by the room's
    `primary_id`, puts it on the `TagContext`, and the prefill renders it —
    so dropping it at any of the three hops reddens this."""
    from nora.testing import InMemoryCultureStore

    culture = InMemoryCultureStore()
    await culture.append(
        key="room-primary", body="they decide by attrition",
        written_at=1, established_by="dream:culture:test")
    await culture.append(
        key="u#nora", body="she keeps it short here",
        written_at=2, established_by="dream:character-nora:test")
    culture.reads.clear()
    assembler = TranscriptInjection(
        audit=_Audit(), memory=_Memory(), persons=_persons(), context_window=10,
        metrics=_Metrics(), ai_peer="nora", policy=_Policy(),  # type: ignore[arg-type]
        culture=culture,
    )
    ctx = await assembler.assemble(
        request=EngagementRequest(message=_msg(), decision=None, tag_id="t"))

    # The room's account by its `primary_id`, and its unit's (C3a) by the unit.
    assert sorted(culture.reads) == ["room-primary", "u#nora", "u#unit"]
    assert ctx.culture is not None
    blob = str(build_prefill(ctx, display_name="Nora", context_window=10))
    assert "<culture" in blob and "they decide by attrition" in blob
    # And her own account by the unit, first.
    assert "<self " in blob and "she keeps it short here" in blob
