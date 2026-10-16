"""What makes a failed Cognition session the AGENDA's fault (DEC-244).
`agenda` strikes human stimuli after five in a row, so it is
reached only on proof: an overflow whose prompt is a known fraction of the
model's window, that carries no sign the provider or the transport failed, and
that repeated on the same agenda. Everything else backs the unit off and
strikes nothing.

The error shapes are what Strands raises:
`ContextWindowOverflowException` for ANY provider message containing «context
length» or «too many tokens», so a 503, a 413 tokens-per-minute and a 429 all
arrive wearing the overflow class.

Named in `scripts/mutations/cognition-failure-cause.json`.
"""

from __future__ import annotations

from typing import Any

import pytest
from strands.types.exceptions import ContextWindowOverflowException

from nora.domain.errors import PartialReplyError
from nora.domain.stimuli import (
    AgendaError,
    classify_failure,
    estimate_tokens_lower_bound,
    is_context_overflow,
    prove_overflow_is_the_agenda,
    transport_evidence,
)
from tests.unit.test_cognition_failure_backoff import _until_calls
from tests.unit.test_cognition_session_lifecycle import (
    ScriptedAgent,
    _rig,
    boom,
    ok,
)


class _Provider(Exception):
    """A provider SDK's error: an HTTP status and a message."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status


def _overflow(message: str, *, status: int | None = None) -> ContextWindowOverflowException:
    """What Strands raises: the overflow class, the provider's own error as its cause."""
    exc = ContextWindowOverflowException(message)
    if status is not None:
        exc.__cause__ = _Provider(status, message)
    return exc


# The exact shapes: (name, exception, is it the size of the request?)
SHAPES: list[tuple[str, BaseException, bool]] = [
    ("400 maximum context length", _overflow(
        "This model's maximum context length is 8192 tokens", status=400), True),
    ("400 with no status attached", _overflow("input is too long for requested model"), True),
    ("503 mentioning context length", _overflow(
        "Error code: 503 - the service is unavailable; context length", status=503), False),
    ("413 tokens per minute", _overflow(
        "Error code: 413 - Request too large: 30000 too many tokens per minute (TPM)",
        status=413), False),
    ("429 rate limit", _overflow(
        "Error code: 429 - rate limit reached, too many tokens", status=429), False),
    ("503 with a bare message", _overflow("context length exceeded", status=503), False),
    ("a per-minute message with no status", _overflow(
        "too many tokens per minute (TPM) for this key"), False),
    ("500 internal error", _overflow(
        "Error code: 500 - internal server error while counting context length",
        status=500), False),
    ("a timeout wrapped as overflow", _overflow("timed out reading context length"), False),
    ("a dropped stream wrapped as overflow", _overflow(
        "peer closed connection without a complete message; too many tokens"), False),
]


@pytest.mark.parametrize(("name", "exc", "size_fault"), SHAPES, ids=[s[0] for s in SHAPES])
def test_transport_evidence_reads_the_error_chain(name, exc, size_fault):
    assert is_context_overflow(exc)
    assert transport_evidence(exc) is (not size_fault)


@pytest.mark.parametrize(("name", "exc", "size_fault"), SHAPES, ids=[s[0] for s in SHAPES])
def test_only_a_size_fault_that_repeated_on_a_big_prompt_is_the_agenda(name, exc, size_fault):
    def proof(**kw: Any) -> bool:
        args: dict[str, Any] = {
            "estimated_tokens": 90_000, "window_tokens": 100_000, "window_fraction": 0.8,
            "previously_overflowed": True,
        }
        return prove_overflow_is_the_agenda(exc, **{**args, **kw})

    assert proof() is size_fault
    # each of the other three conditions alone keeps it infra
    assert proof(previously_overflowed=False) is False        # the FIRST overflow
    assert proof(estimated_tokens=10_000) is False            # a fallback model's smaller window
    assert proof(window_tokens=None) is False                 # window unknown


def test_the_size_boundary_is_the_fraction_of_the_window():
    exc = _overflow("maximum context length", status=400)
    kw: dict[str, Any] = {"window_tokens": 1000, "window_fraction": 0.8,
                          "previously_overflowed": True}
    assert prove_overflow_is_the_agenda(exc, estimated_tokens=800, **kw) is True
    assert prove_overflow_is_the_agenda(exc, estimated_tokens=799, **kw) is False


def test_a_token_estimate_never_overstates_the_prompt():
    assert estimate_tokens_lower_bound("a" * 400, "b" * 400) == 200


@pytest.mark.parametrize("exc", [
    ContextWindowOverflowException("x"), PartialReplyError("cut"),
    type("MaxTokensReachedException", (Exception,), {})("cut"),
    RuntimeError("500"), TimeoutError(), ConnectionError("dropped"),
])
def test_no_sdk_class_is_the_agenda_by_itself(exc):
    assert classify_failure(exc) == "infra"
    assert classify_failure(AgendaError("proven")) == "agenda"


class _ClientError(Exception):
    """botocore's ClientError shape: the status lives in a response dict."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}


def _bedrock(status: int, message: str) -> ContextWindowOverflowException:
    exc = ContextWindowOverflowException(message)
    exc.__cause__ = _ClientError(status, message)
    return exc


BEDROCK_SHAPES = [
    ("bedrock 500 input context length exceeded",
     _bedrock(500, "input context length exceeded"), False),
    ("bedrock 413 input context length exceeded",
     _bedrock(413, "input context length exceeded"), False),
    ("bedrock 400 validation size fault",
     _bedrock(400, "input context length exceeded"), True),
    ("string-wrapped Error code 500", _overflow(
        "Error code: 500 - {'message': 'the context length was exceeded'}"), False),
    ("string-wrapped status 502", _overflow("status: 502 context length"), False),
    ("string-wrapped 400 stays a size fault", _overflow(
        "Error code: 400 - maximum context length is 8192 tokens"), True),
    ("a token count is not a status", _overflow(
        "maximum context length is 500 tokens, you sent 512 tokens"), True),
]


@pytest.mark.parametrize(
    ("name", "exc", "size_fault"), BEDROCK_SHAPES, ids=[s[0] for s in BEDROCK_SHAPES])
def test_transport_evidence_reads_a_botocore_dict_and_a_status_in_the_text(name, exc, size_fault):
    assert transport_evidence(exc) is (not size_fault)


# ---- through the real evaluator and runner --------------------------------------------------

def _causes(rig: Any) -> list[str]:
    return [o.cause for _, o in sorted(rig.stimuli.outcomes.items())]


async def _size_fault(agent: ScriptedAgent, tools: list[Any]) -> Any:
    raise _overflow("This model's maximum context length is 8192 tokens", status=400)


async def _tpm(agent: ScriptedAgent, tools: list[Any]) -> Any:
    raise _overflow("Error code: 413 - too many tokens per minute (TPM)", status=413)


async def _cut(agent: ScriptedAgent, tools: list[Any]) -> Any:
    raise PartialReplyError("the reply was cut at max_tokens")


#: A window the rig's tiny prompt fills: 40 tokens at 10% is 4 tokens.
BIG = {"model_window_tokens": 40, "overflow_window_fraction": 0.1}


async def test_the_first_overflow_is_infra_and_the_second_on_the_same_agenda_is_the_agendas():
    rig = _rig(agent=ScriptedAgent(script=[_size_fault]), **BIG)
    await rig.say()
    await _until_calls(rig, 2)
    assert _causes(rig) == ["infra", "agenda"]


async def test_five_proven_overflows_after_the_first_strike_and_a_provider_outage_never_does():
    rig = _rig(agent=ScriptedAgent(script=[_size_fault]), **BIG)
    await rig.say()
    await _until_calls(rig, 6)
    assert _causes(rig) == ["infra", "agenda", "agenda", "agenda", "agenda", "agenda"]

    down = _rig(agent=ScriptedAgent(script=[_tpm]), **BIG)
    await down.say()
    await _until_calls(down, 12)
    assert set(_causes(down)) == {"infra"}
    assert down.stimuli.retracted == set()


async def test_an_unknown_window_makes_every_overflow_infra():
    rig = _rig(agent=ScriptedAgent(script=[_size_fault]))
    await rig.say()
    await _until_calls(rig, 8)
    assert set(_causes(rig)) == {"infra"}
    assert rig.stimuli.retracted == set()


async def test_a_prompt_far_below_the_window_is_a_smaller_fallback_window_not_the_agenda():
    rig = _rig(agent=ScriptedAgent(script=[_size_fault]), model_window_tokens=10**9)
    await rig.say()
    await _until_calls(rig, 8)
    assert set(_causes(rig)) == {"infra"}


async def test_a_reply_cut_at_max_tokens_is_infra_however_often():
    rig = _rig(agent=ScriptedAgent(script=[_cut]), **BIG)
    await rig.say()
    await _until_calls(rig, 8)
    assert set(_causes(rig)) == {"infra"}
    assert rig.stimuli.retracted == set()


async def test_an_outage_between_two_size_faults_breaks_the_run():
    """size, outage, size, size: the outage clears the tracker (the two size
    faults are not consecutive), so the third session is a FIRST strike again
    and only the fourth, the second in a row, is the agenda's."""
    rig = _rig(agent=ScriptedAgent(script=[_size_fault, boom, _size_fault, _size_fault]), **BIG)
    await rig.say()
    await _until_calls(rig, 4)
    assert _causes(rig) == ["infra", "infra", "infra", "agenda"]


async def test_a_session_that_closed_forgets_the_earlier_overflow():
    """overflow on {1}; a session CLOSES (item 1 waits, returns as a carry);
    a new message makes the next agenda {1, 2}, which contains the one that
    overflowed. Without the close clearing the memory that overflow would be the
    second and strike; with it, it is a first."""
    rig = _rig(agent=ScriptedAgent(script=[_size_fault, ok, _size_fault]), **BIG)
    await rig.say()
    await _until_calls(rig, 2)
    await rig.say(cues=("datetime",))
    await _until_calls(rig, 3)
    assert rig.ends() == ["failed", "closed", "failed"]
    assert _causes(rig)[-1] == "infra"


async def test_a_provider_outage_between_two_size_faults_is_not_evidence():
    """A per-minute limit that arrives as an overflow must not count as the
    FIRST size fault: outage-shaped, then size, is still one size fault."""
    rig = _rig(agent=ScriptedAgent(script=[_tpm, _size_fault]), **BIG)
    await rig.say()
    await _until_calls(rig, 2)
    assert _causes(rig) == ["infra", "infra"]
