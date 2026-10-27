"""Standing entries and operations; the paragraph remains during migration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from nora.domain.models import TranscriptRow

from nora.domain.lineage import Lineage, RenderRoom
from nora.domain.note_body import Shown
from nora.domain.standing_revise import Refusal

LedgerKind = Literal["decision", "commitment", "thread"]
LedgerScope = Literal["room", "unit"]


@dataclass(frozen=True)
class ThreadSpan:
    platform: str
    conversation_id: str
    first_id: int
    last_id: int
    first_at: int
    last_at: int


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    kind: LedgerKind
    unit: str
    scope: LedgerScope
    platform: str
    conversation_id: str
    revision: int
    status: str
    subject: str
    opened_at: int
    last_revised_at: int
    body: str = ""
    closed_at: int | None = None
    note_id: str | None = None
    owed_to: tuple[str, ...] = ()
    due_at: int | None = None
    note_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    message_ids: tuple[int, ...] = ()
    spans: tuple[ThreadSpan, ...] = ()
    last_moved_at: int | None = None
    lineage: Lineage = field(default_factory=Lineage)
    exclude_persons: tuple[str, ...] = ()
    reasoning: str = "deduced"
    confidence: str | None = None
    as_of: int | None = None


@dataclass(frozen=True)
class LedgerOp:
    op: Literal["open", "revise", "close", "touch"]
    kind: LedgerKind
    id: str | None = None
    base: int | None = None
    scope: LedgerScope | None = None
    subject: str | None = None
    sections: Mapping[str, str] = field(default_factory=dict)
    status: str | None = None
    outcome: str | None = None
    message_ids: tuple[int, ...] = ()
    note_id: str | None = None
    owed_to: tuple[str, ...] | None = None
    due_at: int | None = None
    note_ids: tuple[str, ...] | None = None
    decision_ids: tuple[str, ...] | None = None
    mode: str = "deduced"


@dataclass(frozen=True)
class LedgerResult:
    entry: LedgerEntry | None = None
    refusal: Refusal | None = None

    @property
    def status(self) -> str:
        return "refused" if self.refusal else "ok"


class StandingLedger(Protocol):
    async def read(
        self,
        *,
        unit: str,
        platform: str,
        conversation_id: str,
    ) -> tuple[LedgerEntry, ...]: ...

    async def span_rows(
        self,
        span: ThreadSpan,
        *,
        limit: int,
        before_ts: int | None = None,
    ) -> list[TranscriptRow]: ...

    async def history(self, *, unit: str, kind: LedgerKind, id: str) -> tuple[LedgerEntry, ...]: ...

    async def apply(
        self,
        op: LedgerOp,
        *,
        target: RenderRoom,
        shown: Shown,
        now_ms: int,
    ) -> LedgerResult: ...
