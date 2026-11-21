"""Validate the form and evidence of review proposals."""

from collections.abc import Collection, Mapping
from typing import Literal

from nora.memory.domain.admission_proposals import CreateClaim, PassContext, Proposal, RowFacts

ReviewRefusal = Literal[
    "grade_target", "grade_outside_part", "no_later_evidence", "self_assent",
    "grade_owned_by_judge",
]

_OUTCOMES = {
    "guess": ("confirmed", "refuted", "expired"),
    "lesson": ("holds", "weakened"),
}
_DUE_STATES = ("live", "awaiting_weighing")


def review_operation(proposal: Proposal) -> bool:
    return isinstance(proposal, CreateClaim)


def grade_target(proposal: CreateClaim) -> str | None:
    links = tuple(link for link in proposal.links if link.type == "grades")
    if len(links) == 1 and links[0].source == proposal.id:
        return links[0].target
    return None


def claim_evidence_refusal(
    proposal: CreateClaim, context: PassContext,
    rows: Mapping[str, RowFacts], shown: Collection[str], evidence: tuple[RowFacts, ...],
) -> tuple[Literal["occasion_part", "missing_evidence"] | ReviewRefusal, str] | None:
    if proposal.part == "grade":
        if context.occasion != "review":
            return "occasion_part", "part"
        return _grade_refusal(proposal, rows, shown, evidence)
    return None if evidence else ("missing_evidence", "citations")


def _grade_refusal(
    proposal: CreateClaim, rows: Mapping[str, RowFacts], shown: Collection[str],
    evidence: tuple[RowFacts, ...],
) -> tuple[ReviewRefusal, str] | None:
    target_id = grade_target(proposal)
    target = rows.get(target_id) if target_id is not None and target_id in shown else None
    if target is None or target.kind != "claim" or target.state not in _DUE_STATES:
        return "grade_target", "links"
    if target.part is None or target.part not in _OUTCOMES:
        return "grade_target", "links"
    if proposal.grade not in _OUTCOMES[target.part]:
        return "grade_outside_part", "grade"
    if not _has_later_evidence(target, proposal.grade, evidence):
        return "no_later_evidence", "citations"
    if proposal.grade == "confirmed" and all(
        row.kind == "item" and row.author == "nora" for row in evidence
    ):
        return "self_assent", "citations"
    return None


def _has_later_evidence(
    target: RowFacts, grade: str | None, evidence: tuple[RowFacts, ...],
) -> bool:
    start = target.asserted_at
    end = target.window_end
    if start is None:
        return False
    if target.part == "lesson":
        return any(
            row.table in ("intention_verdict", "lesson_tally")
            and row.asserted_at is not None and row.asserted_at > start for row in evidence
        )
    if end is None:
        return False
    if grade == "expired":
        return any(
            row.evidence_at is not None and row.evidence_at >= end
            and (row.basis == "computed" or row.table == "source_health") for row in evidence
        )
    return any(
        row.evidence_at is not None and start <= row.evidence_at <= end for row in evidence
    )
