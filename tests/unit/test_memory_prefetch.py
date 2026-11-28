"""The scoped recall budget and the relevance gate's read boundary."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.memory_search import (
    MIN_RESULTS,
    PACKET_TOKENS,
    PINNED_LIMIT,
    PREFETCH_TOKENS,
    Hit,
    Step,
    exact_terms_of,
    render_packet,
    scope_for,
    walk,
)
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.memory_index import IndexedNode
from nora.testing.memory_index import FakeMemoryIndex

ROOM = "signal:g1"
OLD = 1_000
SHA = "ab" * 32


def _hits(n: int) -> list[Hit]:
    return [
        Hit(IndexedNode("message", str(i), "word " * 120, OLD, origin_room=ROOM), 0.9,
            (Step("message", how="matched"),))
        for i in range(n)
    ]


def test_the_prefetch_budget_cuts_sooner_than_the_tools() -> None:
    now = datetime.now(UTC)

    def shown(budget: int) -> int:
        items, _ = render_packet(
            _hits(30), name_of=str, room_label=str, now=now, budget=budget,
        )
        return len(items)

    assert PREFETCH_TOKENS < PACKET_TOKENS
    assert MIN_RESULTS <= shown(PREFETCH_TOKENS) < shown(PACKET_TOKENS)
    assert shown(PACKET_TOKENS) == len(render_packet(
        _hits(30), name_of=str, room_label=str, now=now)[0])


async def test_picture_pins_share_the_literal_pins_limit() -> None:
    """Pictures pin first and the literal references fill what is left of
    `PINNED_LIMIT`, so a pre-fetch never shows more pinned items than a
    search does."""
    nodes = [
        IndexedNode("media", f"a{i}", f"poster {i}", OLD - i, origin_room=ROOM, sha256=SHA)
        for i in range(3)
    ] + [
        # Another unit's sharing of the same picture: out of scope.
        IndexedNode("media", "far", "poster", OLD, origin_room="signal:x", sha256=SHA),
    ] + [
        IndexedNode("message", f"m{i}", f"see https://x.example/p {i}", OLD - i,
                    origin_room=ROOM)
        for i in range(PINNED_LIMIT)
    ]
    index = FakeMemoryIndex(nodes)
    target = RenderRoom(
        room=RoomFacts("signal", frozenset({"g1"}), "u1", frozenset({"miles"})),
        live=LiveRoom(person_ids=frozenset({"miles"})),
    )
    scope = scope_for(target, social_off=())
    assert scope is not None
    hits = await walk(
        index, scope, terms=[], person_id=None, hops=0, admits=lambda n: True,
        exact=exact_terms_of("https://x.example/p"), media=[SHA],
    )
    pinned = sorted((h for h in hits if h.pin), key=lambda h: -h.pin)
    assert len(pinned) == PINNED_LIMIT
    assert [h.node.kind for h in pinned[:3]] == ["media"] * 3


async def test_more_sharings_of_a_picture_than_pins_never_outrank_the_rest() -> None:
    """A picture's earlier sharings carry no cosine; one past the pins would
    rank at 1.0 above every scored hit. So only as many are read as there
    are pins, and every one is pinned."""
    index = FakeMemoryIndex(
        [IndexedNode("media", f"a{i}", f"poster {i}", OLD - i, origin_room=ROOM, sha256=SHA)
         for i in range(PINNED_LIMIT + 3)],
    )
    target = RenderRoom(
        room=RoomFacts("signal", frozenset({"g1"}), "u1", frozenset({"miles"})),
        live=LiveRoom(person_ids=frozenset({"miles"})),
    )
    scope = scope_for(target, social_off=())
    assert scope is not None
    hits = await walk(
        index, scope, terms=[], person_id=None, hops=0, admits=lambda n: True, media=[SHA],
    )
    assert len(hits) == PINNED_LIMIT and all(h.pin for h in hits)


def test_the_relevance_gate_gained_no_memory_reader() -> None:
    """The gate «never sees notes, facts or the
    peer card, so it cannot — and must not pretend to» answer «does Nora hold
    something true to add». That question belongs to the turn BEHIND it, which
    is what this whole change feeds.

    Asserted on the SOURCE rather than on a rendered prompt, because the
    rendered-prompt half already exists and is stronger
    (`tests/unit/test_relevance_gate.py::test_the_prompt_is_exactly_the_
    instruction_plus_the_two_rendered_blocks` rebuilds it byte-for-byte).
    This adds the one thing that check cannot see: whether the module has been
    given a collaborator it could start reading tomorrow.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "nora" / "ingest" / "relevance_gate.py"
    ).read_text()

    for forbidden in (
        "MemoryStore", "representation", "peer_card", "people_in_window", "self_account",
    ):
        assert forbidden not in source, (
            f"{forbidden!r} reached nora/ingest/relevance_gate.py — "
            f"docs/design/cognition/loop.md §2.3 "
            f"says the gate sees transcript + Standing and nothing else"
        )
