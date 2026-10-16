"""One Rollup row as `rollup_read` returns it.

A Rollup row is computed by code from the Record, so it carries no
model text: a kind, the subject and interval a note cites it by
(`nora.domain.rollup.Citation`), the lineage it rests on, and a few typed
fields. Which rows a room may be shown is decided AFTER the read by
`nora.agent.lineage` (a row's lineage is per row, so the row is what passes
or fails), and the unit boundary is decided BEFORE it: `RollupReadQuery.unit`
is the calling room's own unit (DEC-231).

Coordinates are never a field (R-28): a stay or a co-location names its place
by the words the substrate has for it, never by where it is.

Framework-free, per `nora/domain/`'s rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from nora.domain.lineage import Lineage

__all__ = [
    "ROLLUP_READ_KINDS",
    "ROLLUP_READ_MAX",
    "RollupKind",
    "RollupRead",
    "RollupReadQuery",
    "RollupReadRow",
]

RollupKind = Literal["stay", "trip", "colocation", "outing", "daybook", "tie"]

#: The rollups `rollup_read` reads. The aggregates that answer «where
#: does X usually go» (place, occupancy, rhythm, corridor) stay behind
#: `routine_read`, which forecasts from them; `response` is the Lessons
#: adaptor's, not a session's.
ROLLUP_READ_KINDS: tuple[str, ...] = ("stay", "trip", "colocation", "outing", "daybook", "tie")

#: The most rows one call returns: a bounded read, like `record_query`'s.
#: More is the next call with a narrower interval.
ROLLUP_READ_MAX = 40


@dataclass(frozen=True, slots=True)
class RollupReadQuery:
    """What to read from the CURRENT build: rows of `kind` in `unit` whose
    valid interval overlaps `[start_ms, end_ms)` (the overlap rule a citation
    resolves by), involving `subjects` (canonical subject ids; every
    one of them when `match == "all"`, any of them when `"any"`)."""

    kind: str
    unit: str
    subjects: tuple[str, ...]
    start_ms: int
    end_ms: int
    match: Literal["any", "all"] = "any"
    limit: int = ROLLUP_READ_MAX


@dataclass(frozen=True, slots=True)
class RollupReadRow:
    """One row: `subject` is the citation subject exactly as
    `nora.domain.rollup.Citation` documents it (the person for a person-scoped
    kind, `unit|key` for a unit kind); `[valid_from_ms, valid_to_ms]` its
    interval; `fields` the typed values, with no coordinate among them."""

    kind: str
    subject: str
    valid_from_ms: int
    valid_to_ms: int
    lineage: Lineage = field(default=Lineage(), compare=False)
    fields: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class RollupRead:
    """The rows, oldest first, whether more matched, and the build they came
    from (`build_id` and `through_ms`, the Record time it has read up to).
    `build_id is None` is «no build is current», which the tool answers
    `unavailable`, never as an empty interval."""

    rows: tuple[RollupReadRow, ...]
    more: bool
    build_id: int | None
    through_ms: int | None
