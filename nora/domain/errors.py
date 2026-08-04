"""Failure taxonomy (spec DEC-4).

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
