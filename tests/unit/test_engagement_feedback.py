"""`feedback.run` — the single post-reply exchange feed carried over from
`Handler._feed_exchange` (`nora/handler.py:137-142`).
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage, PersonRef, TagContext
from nora.engagement.stages import feedback
from nora.obs.metrics import Metrics


class _FakeDeliverer:
    def __init__(self, last_text: str | None) -> None:
        self.last_text = last_text


class _FakeMemory:
    def __init__(self) -> None:
        self.fed: list[tuple[str, str]] = []

    async def feed(self, *, peer_id: str, text: str) -> bool:
        self.fed.append((peer_id, text))
        return True


def _ctx(tagger: PersonRef | None) -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="幾點食飯", raw={},
    )
    return TagContext(
        tag_id="tag-1", message=msg, tagger=tagger, transcript=(), peer_card=(), started_at=0.0,
    )


async def test_the_exchange_is_fed_with_both_sides_of_the_conversation():
    memory = _FakeMemory()
    ctx = _ctx(PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles"))

    await feedback.run(
        memory=memory, ctx=ctx, tagger_name="Miles", display_name="Nora",
        deliverer=_FakeDeliverer("今晚七點半得"), metrics=Metrics(),
    )

    assert memory.fed == [("miles", "[Miles]: 幾點食飯\n[Nora]: 今晚七點半得")]


async def test_an_unmapped_tagger_yields_no_feed_at_all():
    memory = _FakeMemory()
    ctx = _ctx(tagger=None)

    await feedback.run(
        memory=memory, ctx=ctx, tagger_name="Stranger", display_name="Nora",
        deliverer=_FakeDeliverer("reply"), metrics=Metrics(),
    )

    assert memory.fed == []


async def test_a_none_last_text_feeds_an_empty_reply_rather_than_raising():
    memory = _FakeMemory()
    ctx = _ctx(PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles"))

    await feedback.run(
        memory=memory, ctx=ctx, tagger_name="Miles", display_name="Nora",
        deliverer=_FakeDeliverer(None), metrics=Metrics(),
    )

    assert memory.fed == [("miles", "[Miles]: 幾點食飯\n[Nora]: ")]


async def test_a_failed_exchange_feed_is_counted():
    """The last Honcho write that left no trace. It matters because the exact
    failure it would hide has already happened once: `ensure_session` POSTed to
    a list endpoint, so `feed` returned False without an HTTP call and memory
    received nothing for the deployment's whole life. Uncounted here, the
    ambient counters would go red while the tagging path stayed invisible."""
    class _Down:
        async def ensure_peer(self, *, peer_id): ...
        async def feed(self, *, peer_id, text): return False
        async def peer_card(self, *, peer_id): return []
        async def health(self): ...
        async def aclose(self): ...

    metrics = Metrics()
    ctx = _ctx(PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles"))
    await feedback.run(
        memory=_Down(), ctx=ctx, tagger_name="Miles", display_name="Nora",
        deliverer=_FakeDeliverer("reply"), metrics=metrics,
    )
    assert metrics.get("honcho_feed_failure") == 1
    assert metrics.get("honcho_feed_success") == 0
