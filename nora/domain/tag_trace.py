"""`TagTrace` — one completed tag turn, captured whole for the debrief to
fold and for retention to rebuild from.

`messages` is `agent.messages[baseline:]`, sanitised and capped by
`nora.agent.tag_trace_extract.extract_tag_trace` -- never semantically
filtered here or there. `prior` (the previous `Standing`) is deliberately NOT
a field: this type is built at the `nora/agent/` extraction boundary, which
has no storage access, so `prior` is the fold's second argument.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import TranscriptRow

VALID_OUTCOMES = frozenset({
    "send_reply",
    "recovered",
    "silent",
    "vetoed",
    "timeout",
    "llm_error",
})


@dataclass(frozen=True, slots=True)
class TagTrace:
    tag_id: str
    conversation_id: str
    messages: tuple[dict[str, Any], ...]
    transcript: tuple[TranscriptRow, ...]
    outcome: str
    created_at: int
    started_at: int | None = None
    occasion: str = "tag"
    platform: str = ""
    unit: str | None = None
    bound_rooms: tuple[str, ...] = ()
    requirements: dict[str, Any] = field(default_factory=dict)
    sent_message_ids: tuple[int, ...] = ()
    turn_id: int | None = field(default=None, compare=False)

    @property
    def handle(self) -> str | None:
        return f"t{self.turn_id}" if self.turn_id is not None else None

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(VALID_OUTCOMES)}, got "
                f"{self.outcome!r}"
            )


@dataclass(frozen=True, slots=True)
class FactItem:
    """One toolUse/toolResult pair pulled structurally out of a turn's
    Record — a projected fact with its provenance, never a raw transcript."""

    name: str
    call_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    requirements: tuple[Any, ...] = ()
    turn_id: int | None = None
    handle: str | None = None
    tag_id: str = ""
    occasion: str = ""
    created_at: int = 0
    #: (created_at, turn_id, message index, block index) — the ordering that
    #: makes a LATER call inside one trace newer than an earlier one.
    ordinal: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class TurnFacts:
    """The store's CURRENT-fact projection: items still open (their newest
    result is unresolved/refused/unavailable/unknown), capped AFTER the
    latest-per-key reduction, plus a separately bounded display read of the
    newest accepted conclusions."""

    open_items: tuple[FactItem, ...] = ()
    recent_items: tuple[FactItem, ...] = ()
    capped: bool = False


def tool_result_status(result: dict[str, Any] | None) -> str:
    """The status a recorded toolResult carries — `{json:{status}}` or a
    legitimate `{text: <json-string>}` payload; malformed/missing is
    `unknown`, never a guessed ok."""
    if not result:
        return "unknown"
    for block in result.get("content") or ():
        if isinstance(block, dict):
            if isinstance(block.get("json"), dict):
                return str(block["json"].get("status") or "unknown")
            if isinstance(block.get("text"), str):
                try:
                    payload = json.loads(block["text"])
                except (ValueError, TypeError):
                    return "unknown"
                if isinstance(payload, dict):
                    return str(payload.get("status") or "unknown")
                return "unknown"
    return "unknown"


_OPEN_STATUSES = frozenset({"unresolved", "refused", "unavailable", "unknown"})


def fact_key(item: FactItem) -> tuple[str, str, str]:
    """The identity a fact is reduced by — (name, subject, action); an
    unresolvable identity keeps its unique call identity so unrelated
    failures never coalesce."""
    subject = str(item.arguments.get("subject") or "")
    action = str(item.arguments.get("action") or "")
    if not subject or not action:
        return (item.name, "call", item.call_id)
    return (item.name, subject, action)


def reduce_turn_facts(
    items: Sequence[FactItem], *, open_cap: int, recent_cap: int,
    conclude_name: str = "decision_conclude",
) -> TurnFacts:
    """Latest-result-per-key over structurally extracted pairs: the newest
    item of each key decides — a later `ok` closes that key only, never a
    different one; a same-trace later result wins by its block ordinal.
    `open_cap` bounds CURRENT open identities (history already closed does
    not count); `recent_cap` bounds the display-only accepted tail."""
    latest: dict[tuple[str, str, str], FactItem] = {}
    for item in items:
        key = fact_key(item)
        prior = latest.get(key)
        if prior is None or item.ordinal > prior.ordinal:
            latest[key] = item
    open_items = sorted(
        (i for i in latest.values()
         if tool_result_status(i.result) in _OPEN_STATUSES),
        key=lambda i: i.ordinal, reverse=True,
    )
    capped = len(open_items) > open_cap
    recent = sorted(
        (i for i in latest.values()
         if i.name == conclude_name
         and tool_result_status(i.result) == "ok"),
        key=lambda i: i.ordinal, reverse=True,
    )[:recent_cap]
    return TurnFacts(
        open_items=tuple(open_items[:open_cap]),
        recent_items=tuple(recent),
        capped=capped,
    )
