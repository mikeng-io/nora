"""Subject drive boundaries over Nora's canonical private evidence and SQL history."""

from __future__ import annotations

import sys
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from nora.infra.sql.engine import create_sql_engine
from nora.subject.effects import (
    effect_boundary,
    effect_scope,
    observed_effects,
    prepare_effect_reads,
    read_effects,
)
from nora.subject.eval_evidence import EvidenceError, current


class SubjectEffects:
    def __init__(self, subject: Any, engine: Any, evidence: Any) -> None:
        self.subject, self.engine, self.evidence = subject, engine, evidence
        self.scope = effect_scope(subject)
        self.case_id = evidence.case_id
        if self.case_id is None:
            raise EvidenceError("canonical_case_unavailable")
        self.records: list[dict[str, Any]] = []

    @property
    def latest(self) -> dict[str, Any]:
        if not self.records or not observed_effects(self.records[-1]):
            raise EvidenceError("canonical_lifecycle_unavailable")
        return self.records[-1]

    def receipt(self) -> dict[str, Any]:
        return {"status": "observed" if self.records else "unavailable",
                "run_id": self.evidence.run_id, "case_id": self.case_id, "drives": self.records}

    async def drive(self, port: Any, case: Any, *, now_ms: int) -> Any:
        boundary = await effect_boundary(self.engine)
        cursor = self.evidence.cursor()
        try:
            return await port.drive(self.subject, case, now_ms=now_ms)
        finally:
            execution_error = sys.exception()
            try:
                effects = await read_effects(self.engine, since=boundary,
                    unit=self.scope.unit, rooms=self.scope.rooms,
                    profile_keys=self.scope.profile_keys, events=self.evidence.since(cursor))
                if not observed_effects(effects):
                    raise EvidenceError("canonical_lifecycle_unavailable")
            except Exception as exc:
                self.evidence.record("subject_effects", drive_index=len(self.records),
                    status="unavailable", cause="canonical_lifecycle_unavailable",
                    error_type=type(exc).__name__,
                    frames=[{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                            for frame in traceback.extract_tb(exc.__traceback__)],
                    execution_error=type(execution_error).__name__ if execution_error else None)
                raise EvidenceError("canonical_lifecycle_unavailable") from exc
            if execution_error is not None:
                effects = {**effects, "status": "partial", "cause": "subject_execution_unsettled",
                           "error_type": type(execution_error).__name__}
            self.evidence.record("subject_effects", drive_index=len(self.records), effects=effects)
            self.records.append(effects)


@asynccontextmanager
async def subject_effects(subject: Any) -> AsyncIterator[SubjectEffects]:
    """Own the read pool inside the same case's disposable SQL/services lifetime."""
    evidence = current()
    if evidence is None:
        raise EvidenceError("canonical_capture_unavailable")
    engine = create_sql_engine(subject.database_url)
    try:
        await prepare_effect_reads(engine, subject.environment)
        yield SubjectEffects(subject, engine, evidence)
    finally:
        await engine.dispose()
