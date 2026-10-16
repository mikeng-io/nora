"""Failure taxonomy (DEC-4).

R-5's rule is "never speak on failure", and it is absolute for failures that mean
Nora is broken. DEC-4 carves out exactly one exception: a tool the user explicitly
asked her to use. Staying silent there reads as being ignored, which is a worse
failure than admitting the tool failed. `disclosable` is that distinction, typed.
"""

from __future__ import annotations

from enum import Enum


class FailureClass(Enum):
    INFRASTRUCTURE = "infrastructure"   # LLM error, timeout, adapter down -> silent
    GUARD_VETO = "guard_veto"           # ReplyGuard refused -> silent
    REQUESTED_TOOL = "requested_tool"   # a tool the user asked for failed -> disclosable

    @property
    def disclosable(self) -> bool:
        return self is FailureClass.REQUESTED_TOOL


class NoraError(Exception):
    """Base for Nora's own failures, carrying how the failure should be surfaced.

    Defaults to INFRASTRUCTURE so an uncategorised failure defaults to silence —
    speaking on an unknown failure is the outcome R-5 exists to prevent.
    """

    def __init__(
        self, message: str, *, failure_class: FailureClass = FailureClass.INFRASTRUCTURE
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class


class PartialReplyError(NoraError):
    """The model was cut off at `max_tokens` AFTER writing real prose.

    Strands raises `MaxTokensReachedException` once the partial message is
    already appended to `agent.messages` — the words are
    recoverable, they just never left the loop. This error carries them so the
    pipeline can deliver them instead of staying silent (R-5's silence is for
    failures where Nora is broken; here she wrote an answer and the harness
    dropped it).

    `partial_text` is the model's own words, extracted from the last message's
    text blocks — never a canned line. Empty when the cut-off message carried
    only tool-use blocks (nothing to salvage).
    """

    def __init__(self, partial_text: str) -> None:
        super().__init__("model cut off at max_tokens with a partial reply")
        self.partial_text = partial_text


class AccountNotRecordableError(NoraError):
    """The session ended without settling its entity: nothing it wrote can
    enter the chain, and it did not close the entity unchanged.

    **Raised so the BATCH IS NOT CONSUMED.** `DreamerRuntime.run` reports an
    adaptor raising out of `accept` as `unavailable` (logged at WARNING, without
    a stack trace: it is an expected outcome, not a fault), and the dream loop holds
    the watermark on `unavailable` alone, so the evidence this session read is
    read again on the next wake instead of being spent on nothing. It is
    `unavailable` rather than `nothing_found` for R-27's reason: «there was
    nothing here» is not what happened; «I could not record this» is.

    A run of them is bounded (`EntitySessions._reject`, DEC-280), so a batch a
    model keeps failing to settle is not re-read for ever.
    """
