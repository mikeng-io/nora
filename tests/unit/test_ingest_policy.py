"""TriggerTextPolicy — the deterministic gate.

Deliberately has no LLM and cannot raise. The future `ClassifierPolicy` can,
which is why `EngagePolicy` is async.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagePolicy
from nora.ingest.policy import (
    CompositePolicy,
    RelevanceGatePolicy,
    TriggerTextPolicy,
    VoiceWakePolicy,
)
from nora.policy import Conversation, Policy


def _msg(body: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body=body, raw={},
    )


async def test_a_genuine_trigger_engages():
    policy = TriggerTextPolicy("nora.000")
    decision = await policy.should_engage(message=_msg("hey nora.000 what's up"))
    assert decision.engage is True


async def test_an_untagged_message_does_not_engage():
    policy = TriggerTextPolicy("nora.000")
    decision = await policy.should_engage(message=_msg("just chatting"))
    assert decision.engage is False
    assert decision.reason


async def test_the_reason_is_a_stable_category_not_free_text():
    """`reason` is a category consumed by metrics; `rationale` is the free-text
    field. Swapping them would put unbounded cardinality into a label."""
    policy = TriggerTextPolicy("nora.000")
    engaged = await policy.should_engage(message=_msg("nora.000 hi"))
    skipped = await policy.should_engage(message=_msg("hi"))
    assert engaged.reason == "trigger_text"
    assert skipped.reason == "no_trigger"
    assert engaged.rationale is None


async def test_trigger_validation_is_delegated_to_tag_is_tagged():
    """R-11's word-boundary, code-span and Unicode-normalisation rules live in
    `nora.tag.is_tagged` and are tested there by `test_trigger_matrix`. This
    policy must not reimplement them — a second implementation would drift."""
    policy = TriggerTextPolicy("nora.000")
    assert (await policy.should_engage(message=_msg("`nora.000`"))).engage is False
    assert (await policy.should_engage(message=_msg("nora.0000"))).engage is False


def test_it_satisfies_the_engage_policy_port():
    policy: EngagePolicy = TriggerTextPolicy("nora.000")
    assert isinstance(policy, EngagePolicy)


async def test_it_returns_an_engage_decision():
    policy = TriggerTextPolicy("nora.000")
    assert isinstance(await policy.should_engage(message=_msg("hi")), EngageDecision)


def _allowlist(*, relevance_gate: bool) -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({"g1"}), persons=None,
        primary_id="g1", relevance_gate=relevance_gate,
    )
    return Policy(
        conversations=(convo,), _by_id={("signal", "g1"): convo},
    )


async def test_relevance_gate_policy_marks_an_opted_in_room_without_engaging():
    """The verdict is a CANDIDATE marker, never
    an admission — `engage` stays False so nothing downstream can read it as a
    tag."""
    policy = RelevanceGatePolicy(_allowlist(relevance_gate=True))
    decision = await policy.should_engage(message=_msg("just chatting"))
    assert decision.engage is False
    assert decision.relevance_gate is True
    assert decision.reason == "relevance_gate_room"


async def test_relevance_gate_policy_passes_a_room_that_is_not_opted_in():
    policy = RelevanceGatePolicy(_allowlist(relevance_gate=False))
    decision = await policy.should_engage(message=_msg("just chatting"))
    assert decision.relevance_gate is False
    assert decision.engage is False


async def test_relevance_gate_policy_passes_an_unlisted_room():
    policy = RelevanceGatePolicy(_allowlist(relevance_gate=True))
    decision = await policy.should_engage(
        message=_msg("hi").__class__(
            platform="signal", conversation_id="elsewhere",
            conversation_type="group", sender_id="+852111", sender_name="Miles",
            timestamp=1, body="hi", raw={},
        )
    )
    assert decision.relevance_gate is False


async def test_the_composite_passes_a_relevance_gate_verdict_through():
    """First match wins for the gate verdict too: a later `no_trigger` must not
    overwrite the candidate marker, and a real tag still wins over both."""
    composite = CompositePolicy((
        TriggerTextPolicy("nora.000"),
        RelevanceGatePolicy(_allowlist(relevance_gate=True)),
    ))
    tagged = await composite.should_engage(message=_msg("nora.000 hi"))
    assert tagged.engage is True and tagged.relevance_gate is False
    candidate = await composite.should_engage(message=_msg("just chatting"))
    assert candidate.engage is False and candidate.relevance_gate is True
    plain = CompositePolicy((TriggerTextPolicy("nora.000"),))
    skipped = await plain.should_engage(message=_msg("just chatting"))
    assert skipped.engage is False and skipped.relevance_gate is False


async def test_voice_wake_transcript_engages() -> None:
    """A transcript that arrived via the voice-wake channel engages her.

    Without this policy the transcript lands in the
    audit row and the engage gate says ``no_trigger`` — DB row inserted,
    reply count 0. The wake service only POSTs after the operator said
    「喂 Nora」, the voice channel's closest thing to an explicit tag.
    """
    policy = VoiceWakePolicy()
    decision = await policy.should_engage(
        message=_msg("喂Nora你好").__class__(
            platform="discord",
            conversation_id="100000000000000203/1476449443033260077",
            conversation_type="group",
            sender_id="100000000000000001",
            sender_name=None,
            timestamp=1,
            body="喂Nora你好，喂Nora你好",
            raw={"voice": True, "user_id": "100000000000000001"},
        )
    )
    assert decision.engage is True
    assert decision.reason == "voice_wake"


async def test_typed_message_with_same_body_does_not_engage() -> None:
    """Typed traffic is untouched — only the raw["voice"] marker routes."""
    policy = VoiceWakePolicy()
    decision = await policy.should_engage(message=_msg("喂Nora你好"))
    assert decision.engage is False
    assert decision.reason == "no_trigger"
