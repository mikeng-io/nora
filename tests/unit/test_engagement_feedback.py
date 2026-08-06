"""`feedback.run` — the tag path's one write to the semantic store.

Carried over from `Handler._feed_exchange` (`nora/handler.py:137-142`), and
narrowed by DEC-36: one peer's stream holds one speaker's words.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage, PersonRef, TagContext
from nora.engagement.stages import feedback
from nora.obs.metrics import Metrics


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


async def test_only_the_taggers_own_words_are_fed_under_the_taggers_peer():
    """THE probe (DEC-36). Honcho's deriver is handed the stream as the "Target
    peer"'s own messages and draws conclusions ABOUT that peer from all of it,
    so feeding Nora's reply under the tagger's peer makes her own words evidence
    about them — a corruption no backfill can undo, because the store keeps the
    CONCLUSION rather than the text it came from.

    Asserted as an exact equality rather than a `not in`: an absence check would
    also pass for a stage that fed nothing at all, and for one that relabelled
    the reply in a way the deriver still cannot see as a second speaker.
    """
    memory = _FakeMemory()
    ctx = _ctx(PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles"))

    await feedback.run(memory=memory, ctx=ctx, metrics=Metrics())

    assert memory.fed == [("miles", "幾點食飯")]


async def test_noras_reply_never_reaches_any_peers_stream():
    """The same claim from the other side, so it survives a change to the exact
    shape above: whatever this stage decides to feed, and under whatever peer,
    the text Nora produced is not part of it.

    `feedback.run` no longer takes a `Deliverer` at all, so the reply is reached
    for here the only way it still can be — off the pipeline's own handle. If
    the parameter ever comes back, this test is what has to be argued with.
    """
    memory = _FakeMemory()
    ctx = _ctx(PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles"))

    await feedback.run(memory=memory, ctx=ctx, metrics=Metrics())

    assert not any("今晚七點半得" in text for _, text in memory.fed)
    assert not any("Nora" in text for _, text in memory.fed)


async def test_an_unmapped_tagger_yields_no_feed_at_all():
    memory = _FakeMemory()
    ctx = _ctx(tagger=None)

    await feedback.run(memory=memory, ctx=ctx, metrics=Metrics())

    assert memory.fed == []


async def test_a_failed_feed_is_counted():
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
    await feedback.run(memory=_Down(), ctx=ctx, metrics=metrics)
    assert metrics.get("honcho_feed_failure") == 1
    assert metrics.get("honcho_feed_success") == 0
