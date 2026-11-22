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


# ---------------------------------------------------------------------------
# what stops it lying
# ---------------------------------------------------------------------------


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
