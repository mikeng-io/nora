"""The failure taxonomy DEC-4 requires, and the domain purity rule."""

from nora.domain.errors import FailureClass, NoraError


def test_failure_classes_cover_the_three_dec4_cases():
    assert FailureClass.INFRASTRUCTURE
    assert FailureClass.GUARD_VETO
    assert FailureClass.REQUESTED_TOOL


def test_only_requested_tool_failures_are_disclosable():
    """R-5 says stay silent when Nora is broken. DEC-4 carves out exactly one
    exception: a tool the user explicitly asked for. Silence there reads as
    being ignored, which is a worse failure than admitting the tool failed."""
    assert FailureClass.REQUESTED_TOOL.disclosable is True
    assert FailureClass.INFRASTRUCTURE.disclosable is False
    assert FailureClass.GUARD_VETO.disclosable is False


def test_nora_error_carries_its_failure_class():
    err = NoraError("llm timed out", failure_class=FailureClass.INFRASTRUCTURE)
    assert err.failure_class is FailureClass.INFRASTRUCTURE
    assert "llm timed out" in str(err)


def test_nora_error_defaults_to_the_silent_class():
    """An uncategorised failure must default to silence, never to speech."""
    assert NoraError("something broke").failure_class is FailureClass.INFRASTRUCTURE
    assert NoraError("something broke").failure_class.disclosable is False


def test_domain_models_are_importable_from_domain():
    from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef, TagContext
    assert NormalizedMessage and ConversationTarget and PersonRef and TagContext
