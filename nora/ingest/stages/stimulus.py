"""Stage: stimulus — append one stimulus per recorded person-authored item.

**Per item, code appends one row to the unit's `stimulus` table with its cues,
and nothing else: no model**. It runs after the Record write it
raises a stimulus for — a message's audit row, an edit's `message_edit` row,
a reaction's or a delete's — so every stimulus names an item that exists
(`ref`).

**Nora's own items raise no stimulus** (the echo rule).
Her sends never pass through ingest (the parsers drop them, R-12), so this is
the second line and it is checked HERE, not assumed from upstream: an item
whose sender is one of her ids, or whose platform names no sender and whose
target is hers (an Instagram unsend or a Discord delete of her message), is
skipped.

**A person's reply to her message inside `reply_quiet_seconds` is recorded
and marked `waits`**: it is a real stimulus, consumed by the next wake
like any other, but it never starts one by itself. Two things count as a
reply here: a message whose quote names her, and a reaction on her message;
the window is measured from her message's time.

**Inline, bounded, never load-bearing.** One INSERT, like the audit write
before it, under `timeout_seconds`. Any failure in the stage — the write, a
cue pattern, a policy read — is logged and counted
(`nora_errors_total{stage="stimulus"}`) and ingest carries on: the item is
already Record, and a missing stimulus costs lateness (the next item's wake
covers the room), never the message. The Cognition session's wake evaluator is the consumer.

**Off unless `[cognition] enabled` is on.** `None` wiring — the default and
every offline/eval composition — makes the stage inert.

One public entry point: `run(...)`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nora.domain.cues import cues_of
from nora.domain.models import EditedMessage, NormalizedMessage
from nora.domain.ports import StimulusStore
from nora.domain.stimuli import MessageItem, NewStimulus

log = logging.getLogger("nora.ingest.stimulus")

#: The table each item kind lives in, which `ref` names it by. An edit names
#: the message it edits: `message_edit` rows are folded onto their message and
#: the edit's own time rides `at_ms`.
_REF_TABLE: dict[str, str] = {
    "message": "messages",
    "edit": "messages",
    "reaction": "message_reaction",
    "delete": "message_delete",
}


@dataclass(frozen=True)
class Wiring:
    """Everything the stage needs, present or absent as one object.

    `unit_of` is the SAME policy read every other unit-keyed writer makes
    (`policy.toml` `[[conversations]] unit`); `self_ids` is
    `AdapterRegistry.self_aliases()`, every id Nora answers to."""

    store: StimulusStore
    unit_of: Callable[[str, str], str | None]
    self_ids: frozenset[str]
    reply_quiet_seconds: float
    timeout_seconds: float = 2.0
    clock: Callable[[], float] = time.time


def _authored_by_nora(
    message: NormalizedMessage, target: EditedMessage | None, self_ids: frozenset[str],
) -> bool:
    if message.sender_id and message.sender_id in self_ids:
        return True
    return not message.sender_id and target is not None and target.is_nora


def _answers_nora_at(
    message: NormalizedMessage, item: MessageItem, target: EditedMessage | None,
    self_ids: frozenset[str],
) -> int | None:
    """The time of Nora's message this item answers, or None."""
    if item == "message" and message.quote is not None:
        if message.quote.author_id in self_ids and message.quote.quoted_ts > 0:
            return message.quote.quoted_ts
        return None
    if item == "reaction" and target is not None and target.is_nora:
        return target.ts
    return None


async def run(
    *,
    message: NormalizedMessage,
    item: MessageItem,
    row_id: int,
    person_id: str | None,
    wiring: Wiring,
    metrics: Any,
    target: EditedMessage | None = None,
) -> int | None:
    """Append the item's stimulus; return its id, or None when none was
    written (Nora's own item, a room with no unit, or a failed write).

    `row_id` is the id of the Record row the item was written as, or of the
    message an edit edits; `target` is the message a reaction, a delete or an
    edit touches."""
    # The WHOLE body is guarded, not only the write: a cue pattern, a policy
    # read or a malformed quote that raises here would otherwise propagate
    # into the pipeline and stop the memory feed and the gate for a message
    # that is already recorded. A stimulus is never worth a lost turn.
    try:
        return await _append(
            message=message, item=item, row_id=row_id, person_id=person_id,
            wiring=wiring, metrics=metrics, target=target,
        )
    except Exception:
        log.exception("stimulus stage failed; the item is recorded, its stimulus is not")
        metrics.inc("nora_errors_total", stage="stimulus", platform=message.platform)
        return None


async def _append(
    *, message: NormalizedMessage, item: MessageItem, row_id: int,
    person_id: str | None, wiring: Wiring, metrics: Any,
    target: EditedMessage | None,
) -> int | None:
    if _authored_by_nora(message, target, wiring.self_ids):
        return None
    unit = wiring.unit_of(message.platform, message.conversation_id)
    if not unit:
        # Scope admitted the room, so it is in policy.toml and has a unit
        # (`unit` defaults to "nora"). Reaching here is a wiring fault.
        log.warning(
            "stimulus: no unit for an admitted room (platform=%s); no stimulus",
            message.platform,
        )
        return None
    now_ms = int(wiring.clock() * 1000)
    cues = cues_of(message.body) if item in ("message", "edit") else ()
    answers = _answers_nora_at(message, item, target, wiring.self_ids)
    at_ms = message.timestamp if message.timestamp > 0 else now_ms
    waits = (
        answers is not None
        and at_ms - answers <= wiring.reply_quiet_seconds * 1000
    )
    stimulus = NewStimulus(
        unit=unit, kind="message", item=item, ref=f"{_REF_TABLE[item]}:{row_id}",
        at_ms=at_ms, platform=message.platform,
        conversation_id=message.conversation_id, person_id=person_id,
        cues=cues, waits=waits,
    )
    sid = await asyncio.wait_for(
        wiring.store.append(stimulus=stimulus, recorded_at_ms=now_ms),
        timeout=wiring.timeout_seconds,
    )
    for cue in cues or ("none",):
        metrics.inc("nora_stimulus_total", kind="message", cue=cue)
    return sid
