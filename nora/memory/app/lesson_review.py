"""Apply the unit inventory and the independent law check to a lesson proposal."""

from datetime import UTC, datetime

from nora.domain.doctrine_check import Tension
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_ports import AdmissionStore, LessonChecker, TensionWriter
from nora.memory.domain.admission_proposals import PassContext
from nora.memory.domain.lesson_admission import lesson_inventory_refusal


class LessonReview:
    def __init__(self, checker: LessonChecker, tensions: TensionWriter) -> None:
        self._checker = checker
        self._tensions = tensions

    async def review(
        self, store: AdmissionStore, body: str, citations: tuple[str, ...],
        base: str | None, evidence_at: datetime | None, context: PassContext,
    ) -> Refusal | None:
        inventory = await store.lesson_inventory(context.unit)
        try:
            law = self._checker.rules()
        except Exception:  # noqa: BLE001 - unclassifiable law fails closed
            return Refusal("check_unavailable", "content")
        if reason := lesson_inventory_refusal(body, inventory, law, base, evidence_at):
            return Refusal(reason, "content")
        try:
            verdict = await self._checker.judge(body)
        except Exception:  # noqa: BLE001 - checker errors fail closed
            return Refusal("check_unavailable", "content")
        if verdict.outcome == "consistent":
            return None
        if verdict.outcome != "conflict" or verdict.rule is None:
            return Refusal("check_unavailable", "content")
        rule = verdict.rule
        await self._tensions.record(Tension(
            unit=context.unit, source="lesson", doctrine_file=rule.file,
            doctrine_line=rule.line, doctrine_text=rule.text, would_have_said=body,
            citations=tuple(dict.fromkeys(citations)), why=verdict.why,
            written_at=int(datetime.now(UTC).timestamp() * 1000), established_by=context.pass_id,
            instruction_sha256=context.rev_instruction or None, model=context.model or None,
            check_model=self._checker.model,
        ))
        return Refusal("loosens_law", "content")
