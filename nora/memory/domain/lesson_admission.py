"""Pure evidence, form and budget checks for a graph lesson."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nora.domain.doctrine_check import LawRule
from nora.domain.lessons import (
    LESSON_BODY_MAX_CHARS,
    LESSON_BODY_MIN_CHARS,
    LESSON_MAX_ACTIVE,
    LESSON_MIN_EPISODES,
    LESSON_UNIT_MAX_CHARS,
    echoes,
)
from nora.memory.domain.admission_proposals import Link, PersonAuthor, RowFacts

LessonFormRefusal = Literal[
    "lesson_not_about_nora", "thin_evidence", "echo", "too_short", "too_long",
]
LessonInventoryRefusal = Literal["echoes_doctrine", "echoes_lesson", "cap", "relearn"]


@dataclass(frozen=True, slots=True)
class LessonInventory:
    live: tuple[tuple[str, str], ...] = ()
    operator_retirements: tuple[datetime, ...] = ()


def lesson_form(
    body: object,
    evidence: tuple[RowFacts, ...],
    links: tuple[Link, ...],
    rows: Mapping[str, RowFacts],
    id_: str,
) -> tuple[LessonFormRefusal, str] | None:
    for link in links:
        target = rows.get(link.target)
        if (link.type == "about" and link.basis == "inferred" and link.source == id_
                and target is not None and target.pointer_kind == "nora"
                and target.pointer_ref == "nora" and target.state == "admitted"):
            break
    else:
        return "lesson_not_about_nora", "about"

    if all(row.author == "nora" for row in evidence):
        return "echo", "citations"
    verdicts = {row.id for row in evidence
                if row.table == "intention_verdict" and row.verdict_ending != "unknown"}
    # An offhand remark aimed at Nora passes this floor. Its content is judged separately.
    correction = any(
        row.aimed_at_nora and isinstance(row.author, PersonAuthor) and row.author.id != "nora"
        for row in evidence
    )
    if len(verdicts) < LESSON_MIN_EPISODES and not correction:
        return "thin_evidence", "citations"
    if not isinstance(body, str) or len(body.strip()) < LESSON_BODY_MIN_CHARS:
        return "too_short", "content"
    if len(body) > LESSON_BODY_MAX_CHARS:
        return "too_long", "content"
    return None


def lesson_inventory_refusal(
    body: str, inventory: LessonInventory, law: Sequence[LawRule], replacing: str | None,
    newest_evidence: datetime | None,
) -> LessonInventoryRefusal | None:
    if any(echoes(body, rule.text) for rule in law):
        return "echoes_doctrine"
    others = [text for id_, text in inventory.live if id_ != replacing]
    if any(echoes(body, text) for text in others):
        return "echoes_lesson"
    if replacing is None and inventory.operator_retirements and (
        newest_evidence is None or newest_evidence <= max(inventory.operator_retirements)
    ):
        return "relearn"
    if len(others) >= LESSON_MAX_ACTIVE or (
        sum(map(len, others)) + len(body) > LESSON_UNIT_MAX_CHARS
    ):
        return "cap"
    return None
