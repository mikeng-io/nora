"""`feedback.run` — the tag path's semantic write: NORA's replies under the Nora
aiPeer, into the conversation's session (Honcho v2, design/24 §3).

The old version fed the tagger's OWN message (a duplicate of the ambient
memory_feed, DEC-36). v2 feeds only Nora's side; the human side is fed by
`nora.ingest.stages.memory_feed`, so these tests assert what changed: her sent
messages go under the aiPeer, EACH of them (multi-message safe), into the right
conversation, and DEC-36 still holds — a human's peer never receives her words.
"""

from __future__ import annotations

from nora.engagement.stages import feedback
from nora.obs.metrics import Metrics


class _FakeMemory:
    def __init__(self, *, fail: bool = False) -> None:
        self.fed: list[tuple[str, str, str]] = []   # (peer_id, text, conversation_id)
        self.peers: list[str] = []
        self._fail = fail

    async def ensure_peer(self, *, peer_id: str) -> None:
        self.peers.append(peer_id)

    async def feed(self, *, peer_id: str, text: str, conversation_id: str,
                   metadata: dict | None = None) -> bool:
        if self._fail:
            return False
        self.fed.append((peer_id, text, conversation_id))
        return True


async def test_noras_replies_are_fed_under_the_nora_peer_into_the_conversation():
    """THE v2 probe: her reply lands under the `nora` aiPeer (building her
    self-model) in the conversation's session — never under a human's peer
    (DEC-36 holds at the message level)."""
    memory = _FakeMemory()
    await feedback.run(
        memory=memory, ai_peer="nora", sent_texts=["今晚七點半得"],
        conversation_id="g1", metrics=Metrics(),
    )
    assert memory.fed == [("nora", "今晚七點半得", "g1")]
    assert "miles" not in [peer for peer, _, _ in memory.fed]
    assert memory.peers == ["nora"]


async def test_every_message_of_a_multi_message_reply_is_fed_not_just_the_last():
    """The bug that killed `last_text` (design/16 §4): a multi-message reply must
    feed EACH message. `sent_texts` is a list; reverting it to last-only reds
    this."""
    memory = _FakeMemory()
    await feedback.run(
        memory=memory, ai_peer="nora",
        sent_texts=["俾阿星", "同你講聲阿珍都嚟"], conversation_id="g1",
        metrics=Metrics(),
    )
    assert [t for _, t, _ in memory.fed] == ["俾阿星", "同你講聲阿珍都嚟"]
    assert {p for p, _, _ in memory.fed} == {"nora"}


async def test_no_replies_means_no_feed():
    memory = _FakeMemory()
    await feedback.run(
        memory=memory, ai_peer="nora", sent_texts=[], conversation_id="g1",
        metrics=Metrics(),
    )
    assert memory.fed == []


async def test_each_failed_feed_is_counted():
    """The last Honcho write that left no trace once — `ensure_session` POSTed to
    a list endpoint, so `feed` returned False with no HTTP call and memory
    received nothing for the deployment's whole life. Counting keeps a silent
    regression on the same dashboard the ambient path uses."""
    memory = _FakeMemory(fail=True)
    metrics = Metrics()
    await feedback.run(
        memory=memory, ai_peer="nora", sent_texts=["a", "b"], conversation_id="g1",
        metrics=metrics,
    )
    assert metrics.get("honcho_feed_failure") == 2
    assert metrics.get("honcho_feed_success") == 0
