"""TriggerTextPolicy — the deterministic gate that ships now.

Deliberately has no LLM and cannot fail: spec §4.3 notes stage 5 had no
failure-behaviour row precisely because this policy cannot raise. The future
`ClassifierPolicy` can, which is why `EngagePolicy` is async today.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagePolicy
from nora.ingest.policy import TriggerTextPolicy


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
