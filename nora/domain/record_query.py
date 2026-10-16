"""One message of the Record as `record_query` returns it.

A Record read, not a derived one: what was said, in which room, with the
edits, reactions and deletes recorded against it (DEC-233). The
lineage of every hit is `message:<its room>`; which rooms a turn may read is
decided before the query by `nora.agent.lineage`, never after it.

Framework-free, per `nora/domain/`'s rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.models import TranscriptRow

__all__ = ["RECORD_QUERY_MAX", "RecordHit", "RecordQuery", "RecordRefHit"]

#: The most hits one call returns. A read that answers "what was said over
#: this stretch" hands back verbatim group text, so it is bounded the way
#: `load_transcript` is; more is the next call with a narrower window.
RECORD_QUERY_MAX = 30


@dataclass(frozen=True, slots=True)
class RecordQuery:
    """What to read: these rooms, `[start_ms, end_ms)`, optionally one
    person's messages, optionally messages containing `text`."""

    rooms: tuple[tuple[str, str], ...]
    start_ms: int
    end_ms: int
    person_id: str | None = None
    text: str | None = None
    limit: int = RECORD_QUERY_MAX


@dataclass(frozen=True, slots=True)
class RecordHit:
    """One recorded message: its room, the row as a transcript reads it (the
    newest edit folded in, what it first said on `row.edit`), its reactions as
    `(emoji, how many people)`, and whether its author deleted or unsent it."""

    platform: str
    conversation_id: str
    row: TranscriptRow
    reactions: tuple[tuple[str, int], ...] = ()
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class RecordRefHit:
    """The message a stimulus's `ref` names, for the Cognition session's
    grounding (`nora.domain.cognition_ground`): the hit, and for a reaction the
    emoji it left. A reaction and a delete are rows of their own tables; the hit
    is the MESSAGE they touch."""

    table: str
    row_id: int
    hit: RecordHit
    emoji: str = ""
