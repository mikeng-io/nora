"""`CharacterNoraAdaptor` — her own account in one unit, from how its people
responded to her .

The property this piece exists for is the ECHO RULE: her own words alone never
ground a change to her Character. A revision citing only her sends is refused by the verb.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.culture import Episode, nora_character_key
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import Proposal
from nora.language.character import (
    CharacterNoraAdaptor,
    CharacterRoom,
    NoraEvidence,
    NoraKey,
)
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

T0 = 1_790_000_000_000
NOW = datetime.fromtimestamp((T0 + 86_400_000) / 1000, tz=UTC)
KEY = nora_character_key("friends")
NK = NoraKey(unit="friends", rooms=(CharacterRoom(key="room-a", platform="signal",
                                                  label="Friends"),))


def _reply(rid: int, body: str) -> TranscriptRow:
    return TranscriptRow(
        id=rid, sender_id="+85200000000", sender_name="spoofed", ts=T0, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id="ada",
    )


EPISODES = (
    Episode(send_id=100, room="room-a", text="dinner at 8?", outcome="responded",
            reactions=2, edits=0, replies=(_reply(101, "yes!! finally a plan"),)),
    Episode(send_id=110, room="room-a", text="here is a long list", outcome="unanswered",
            reactions=0, edits=0),
)


def _adaptor(store: InMemoryCultureStore | None = None,
             metrics: Metrics | None = None) -> CharacterNoraAdaptor:
    return CharacterNoraAdaptor(
        culture=store or InMemoryCultureStore(),
        evidence=NoraEvidence(store=InMemoryCharacterEvidence(), keys={KEY: NK}),
        keys={KEY: NK}, name_of=lambda pid: {"ada": "Ada"}.get(pid or "", "someone"),
        metrics=metrics, clock=lambda: NOW,
    )


async def _verbs(adaptor: CharacterNoraAdaptor):
    await adaptor.inputs(key=KEY, batch=EPISODES)
    return adaptor.verbs(key=KEY)


async def test_each_episode_is_handed_as_her_send_paired_with_what_followed():
    out = await _adaptor().inputs(key=KEY, batch=EPISODES)
    assert 'n="response:100"' in out and 'outcome="responded"' in out
    assert 'reactions="2"' in out and 'room="Friends"' in out
    assert '<m n="100" from="you">dinner at 8?</m>' in out
    assert '<m n="101" from="Ada">yes!! finally a plan</m>' in out
    assert 'outcome="unanswered"' in out
    assert "spoofed" not in out and "+852" not in out


async def test_a_revision_citing_only_her_own_sends_is_refused_by_the_echo_rule():
    """THE ECHO RULE, enforced in the verb: citations that are all her own
    sends are refused with `echo`, and nothing is staged."""
    m = Metrics()
    adaptor = _adaptor(metrics=m)
    revise, _keep = await _verbs(adaptor)
    answer = await revise(entity="nora", body="I make plans", citations=["100", "110"])
    assert (answer["status"], answer["reason"]) == ("refused", "echo")
    assert m.get("nora_character_session_total", kind="nora", outcome="refused") == 1


async def test_an_episode_or_someone_elses_reply_grounds_a_revision():
    store = InMemoryCultureStore()
    adaptor = _adaptor(store)
    revise, _keep = await _verbs(adaptor)
    assert await revise(entity="nora", body="a plan from me lands",
                        citations=["response:100"]) == {"status": "ok"}
    assert await revise(entity="nora", body="a plan from me lands",
                        citations=["response:100", "101"]) == {"status": "ok"}
    proposals = await adaptor.accept(key=KEY, items=())
    await adaptor.commit(key=KEY, now_ms=T0, proposals=[Proposal(
        payload=p, instruction_sha256="a" * 64, model="m", established_by="r",
    ) for p in proposals])
    head = await store.head(key=KEY)
    assert head is not None and head.citations == ("response:100", "101")


async def test_an_episode_it_was_not_shown_is_refused():
    revise, _keep = await _verbs(_adaptor())
    answer = await revise(entity="nora", body="x", citations=["response:999"])
    assert answer["reason"] == "unresolved_citation"


async def test_no_closed_episode_calls_no_model():
    assert await _adaptor().inputs(key=KEY, batch=()) == ""


async def test_the_evidence_is_evidence_episodes_by_send_id_never_an_open_or_unanswered_one():
    """DEC-290 (4): the wake counts closed episodes THAT ARE EVIDENCE. An
    `unanswered` one holds nothing anyone did, so it neither counts toward the
    wake nor is read."""
    store = InMemoryCharacterEvidence()
    store.add_episode(unit="friends", episode=EPISODES[0])
    store.add_episode(unit="friends", episode=EPISODES[1])
    store.add_episode(unit="friends", open_=True, episode=Episode(
        send_id=120, room="room-a", text="open", outcome="open", reactions=0, edits=0))
    store.add_episode(unit="friends", episode=Episode(
        send_id=125, room="room-a", text="who is in?", outcome="silent",
        reactions=0, edits=0))
    store.add_episode(unit="wedding", episode=Episode(
        send_id=130, room="room-w", text="x", outcome="responded", reactions=1, edits=0))
    evidence = NoraEvidence(store=store, keys={KEY: NK})
    assert await evidence.count(key=KEY, after=0) == 2
    assert await evidence.count(key=KEY, after=100) == 1
    items, cursor = await evidence.newest(key=KEY, after=0, limit=1)
    assert [e.send_id for e in items] == [125] and cursor == 125


UNKNOWN = Episode(send_id=120, room="room-a", text="anyone free sat?", outcome="unknown",
                  reactions=0, edits=0)


async def test_an_unknown_episode_with_no_response_cannot_meet_the_echo_rule():
    """Her own send relabelled as an episode would be the cheapest way past the
    echo rule: an `unknown` one holds nothing anyone did. It is not handed over
    and cannot be cited, alone or beside her sends; nor can an `unanswered` one
    (shown for context)."""
    adaptor = _adaptor()
    out = await adaptor.inputs(key=KEY, batch=(*EPISODES, UNKNOWN))
    assert "response:120" not in out and "anyone free sat?" not in out
    revise, _keep = adaptor.verbs(key=KEY)
    for cited in (["response:120"], ["120", "response:120"], ["response:110"],
                  ["110", "response:110"]):
        answer = await revise(entity="nora", body="I make plans", citations=cited)
        assert (answer["status"], answer["reason"]) == ("refused", "unresolved_citation"), cited


def _evidence_episode(send_id: int) -> Episode:
    return Episode(send_id=send_id, room="room-a", text=f"plan {send_id}",
                   outcome="responded", reactions=1, edits=0)


FOUR = tuple(_evidence_episode(n) for n in (200, 210, 220, 230))


async def test_a_revision_must_cite_three_evidence_episodes_when_it_was_shown_that_many():
    """DEC-290 (4): an account may not rest on citations that are mostly
    non-evidence. A floor of three DIFFERENT evidence episodes, stated in the
    refusal; a repeated one counts once."""
    adaptor = _adaptor()
    await adaptor.inputs(key=KEY, batch=FOUR)
    revise, _keep = adaptor.verbs(key=KEY)
    for cited in (["response:200"], ["response:200", "response:210"],
                  ["response:200", "response:200", "response:210"],
                  ["response:200", "200", "210", "220"]):
        answer = await revise(entity="nora", body="I plan well", citations=cited)
        assert (answer["status"], answer["reason"]) == ("refused", "thin_evidence"), cited
        assert "at least 3" in str(answer["detail"])
    assert await revise(entity="nora", body="I plan well", citations=[
        "response:200", "response:210", "response:220"]) == {"status": "ok"}


async def test_the_floor_never_exceeds_the_evidence_the_session_was_shown():
    """A stale wake on one episode is not given a bar of three it cannot meet:
    that session would be held and given up on."""
    adaptor = _adaptor()
    await adaptor.inputs(key=KEY, batch=(EPISODES[0], EPISODES[1]))
    revise, _keep = adaptor.verbs(key=KEY)
    assert await revise(entity="nora", body="a plan lands",
                        citations=["response:100"]) == {"status": "ok"}


def test_an_episode_whose_only_responses_are_follows_is_not_evidence():
    only = Episode(send_id=1, room="r", text="t", outcome="responded",
                   reactions=0, edits=0, follows=3)
    assert not only.is_evidence
    assert Episode(send_id=1, room="r", text="t", outcome="responded",
                   reactions=1, edits=0, follows=3).is_evidence
    assert Episode(send_id=1, room="r", text="t", outcome="responded",
                   reactions=0, edits=0).is_evidence  # no follows: unchanged
