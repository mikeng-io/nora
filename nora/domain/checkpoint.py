"""The job CHECKPOINT: a sub-agent worker's working state, kept apart from what code knows.

A long job needs a place to keep its place: what is done, what is in hand, what is next and
what it decided. That is Standing's shape and Standing's failure (a model's fold drops an open
plan, and what the fold says about what exists is the model's word). The checkpoint differs on
the two points that failure turns on:

- it is TYPED and written by the worker itself, through one tool with four capped lists, and
  each call REPLACES the whole of it (no merge, so nothing stale survives a correction);
- it is never the record of what EXISTS. Code keeps the hard facts outside it, from tool
  results: the documents a `document_publish` returned `ok` for, the files exported with
  `to="keep"`. A checkpoint that names an output code does not have is the worker's words and
  nothing more (`nora.language.checkpoint_render` renders the two apart).

`Checkpoint` is the worker's four lists and when it wrote them. `Outputs` is code's. Both are
persisted on the job row (`compute_jobs.checkpoint` / `checkpoint_at`): the four lists under
their own keys, and the kept files under `kept_files`, a key only code writes (the tool has no
argument for it).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Items per list, and characters per item: past either, `checkpoint_update` is `bad_arguments`
#: and nothing is replaced. A checkpoint is a place-keeper, not a log.
MAX_ITEMS = 8
MAX_ITEM_CHARS = 160

#: The worker's four lists, in the order every renderer writes them.
SECTIONS: tuple[str, ...] = ("done", "doing", "next", "decisions")

#: Files kept (exported `to="keep"`) that a row remembers, each cut to this on one line.
MAX_KEPT_FILES = 40
MAX_KEPT_CHARS = 200


@dataclass(frozen=True)
class Checkpoint:
    """What the worker last wrote about its own progress: its words, unchecked."""

    done: tuple[str, ...] = ()
    doing: tuple[str, ...] = ()
    next: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    at: datetime | None = None

    def section(self, name: str) -> tuple[str, ...]:
        return getattr(self, name)  # type: ignore[no-any-return]

    @property
    def empty(self) -> bool:
        return not any(self.section(s) for s in SECTIONS)


@dataclass(frozen=True)
class Outputs:
    """What code recorded, never what the worker said: `documents` are the ids a successful
    `document_publish` returned (with a title only where a reader could look one up),
    `kept_files` the workspace paths a successful `computer_export` to `keep` stored, and
    `rounds` the model calls so far (shown to Nora, never to the worker)."""

    documents: tuple[tuple[str, str], ...] = ()
    kept_files: tuple[str, ...] = ()
    rounds: int | None = None

    @property
    def empty(self) -> bool:
        return not self.documents and not self.kept_files and not self.rounds


def parse_lists(**raw: object) -> tuple[dict[str, tuple[str, ...]], str]:
    """The worker's four lists, normalised, or `({}, problem)`. A list is a real list of
    strings (a bare string would otherwise split into characters); blank items are dropped,
    each item is one line, and past `MAX_ITEMS` items or `MAX_ITEM_CHARS` characters the whole
    call is refused rather than cut: a silently shortened plan is a plan the worker did not
    write."""
    out: dict[str, tuple[str, ...]] = {}
    for name in SECTIONS:
        value = raw.get(name)
        if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
            return {}, f"`{name}` must be a list of strings"
        items = tuple(one for one in (" ".join(i.split()) for i in value) if one)
        if len(items) > MAX_ITEMS:
            return {}, f"`{name}` has too many items; shorten the list"
        for item in items:
            if len(item) > MAX_ITEM_CHARS:
                return {}, f"an item in `{name}` is too long; shorten it"
        out[name] = items
    return out, ""


def row_payload(checkpoint: Checkpoint | None, kept_files: tuple[str, ...]) -> dict[str, Any]:
    """The checkpoint as JSON-shaped data for `compute_jobs.checkpoint`: the worker's lists
    (lists, never tuples, so the row reads back the same from either store) and, written by
    code, the kept files."""
    out: dict[str, Any] = {s: list(checkpoint.section(s)) for s in SECTIONS} if checkpoint else {}
    out["kept_files"] = [f[:MAX_KEPT_CHARS] for f in kept_files[:MAX_KEPT_FILES]]
    return out


def read_row(
    payload: Mapping[str, Any] | None, at: datetime | None,
) -> tuple[Checkpoint | None, tuple[str, ...]]:
    """`(the worker's checkpoint, the kept files)` from a row. A row with no checkpoint
    (`None`) or one holding junk reads as nothing, never as an error."""
    if not isinstance(payload, Mapping):
        return None, ()
    kept_raw = payload.get("kept_files")
    kept = tuple(str(f) for f in kept_raw) if isinstance(kept_raw, list) else ()
    lists: dict[str, tuple[str, ...]] = {}
    for name in SECTIONS:
        raw = payload.get(name)
        lists[name] = tuple(str(i) for i in raw) if isinstance(raw, list) else ()
    checkpoint = Checkpoint(at=at, **lists)
    return (None if checkpoint.empty else checkpoint), kept
