"""The touch path's stage — a reaction to, or a delete/unsend of, a recorded
message: find it, and record the touch beside it
(design/cognition-loop/20-observe.md §1, migration step 1).

Neither is a message (`nora.domain.models.Touch`), so neither becomes a
`messages` row, a transcript line, a Honcho feed or a turn. Each is a Record
item attached to the message it touches (`message_reaction`,
`message_delete`), because the withdrawal cascade (step 2c), the
cited-message cues and the tie need them.

A delete of a message on record also WITHDRAWS it: the touch stage hands the
target to the `DeleteWithdrawer` (`nora.app.delete_withdrawal`), which hides the
row and marks everything derived from it `unsupported` in one transaction, and
counts how that ended on `nora_delete_withdrawals_total`. The withdrawer is
optional wiring (`None` in an offline or eval composition, which then records the
delete and withdraws nothing, as before). A reaction withdraws nothing.

Four endings, each counted on `nora_ingest_outcomes_total`:

* **`<kind>_recorded`** — the target is on record and the row is written.
* **`<kind>_orphaned`** — nothing on record to attach it to (a message from
  before Nora joined, pruned, or never recorded, or one not recorded YET). A
  reaction is dropped, for `edit_target`'s reason: a touch of nothing cannot be
  attached, and a lookup that FAILED counts an error besides — «could not look»
  is not «not there». A DELETE is not dropped: with a withdrawer wired it is kept
  as an `orphan_delete` row (the durable record, written first, in its own
  transaction), which the pipeline applies the moment the message is audited and
  the start-up sweep resolves against what is stored. That one row covers a delete
  that overtook its message (Discord dispatches events as separate tasks and a
  photo is recorded after its custody fetch), one that arrives after a restart or
  a long hold, and one whose lookup failed.
* **`delete_refused`** — a delete on a platform where only a message's author
  can delete it (Signal's remote delete, WhatsApp's revoke), naming a message
  whose author is not the deleter: a malformed or forged event, refused as
  `edit_target` refuses an edit by someone else
  (`nora.domain.delete_authorship`: the platform id, the resolved person, or any
  id form the event gives for the deleter).
* **`audit_refused`** — the write failed; dropped and counted, never raised
  (this runs on the listener's await path, like the audit write).

The target is found by the lookup an edit uses. On Signal the target's
AUTHOR is preferred when two rows share a timestamp — a reaction names whose
message it is — not the reactor. Who acted is the platform's sender; where it
names nobody (an Instagram unsend, a Discord delete, which a moderator may
have made) the row keeps `sender_id = ""` AND `person_id = NULL`: the
platform did not say who, and the Record does not assert it. (The stimulus
stage still treats an unattributed delete of Nora's OWN message as hers, so
it raises no stimulus; that is a judgement about waking, not a Record claim.)
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.delete_authorship import (
    AUTHOR_ONLY_DELETES,
    DELETER_IDS_KEY,
    deleter_is_author,
    target_is_stale,
)
from nora.domain.models import EditedMessage, NormalizedMessage, Touch
from nora.domain.ports import DeleteWithdrawal, DeleteWithdrawer, RecordStore
from nora.persons import Persons

log = logging.getLogger("nora.ingest.touch")

#: The durable write of a held delete is tried this many times in all. A failure here is
#: usually a deadlock or serialization abort between concurrent writers, which a retry a
#: moment later passes (the eviction itself takes no waiting locks, but a unique-index wait
#: or the history trigger can still conflict); the pause is random, so two writers that
#: collided do not collide again in step. Exhausted, the delete is LOST (`orphan_lost`).
ORPHAN_WRITE_ATTEMPTS = 4
ORPHAN_RETRY_BASE_S = 0.05

__all__ = ["AUTHOR_ONLY_DELETES", "TouchEnding", "TouchResult", "run"]

TouchEnding = Literal[
    "reaction_recorded", "reaction_orphaned",
    "delete_recorded", "delete_orphaned", "delete_refused",
    "audit_refused",
]


@dataclass(frozen=True)
class TouchResult:
    """`row_id` set only when the touch was recorded; `target` whenever it
    was found."""

    ending: TouchEnding
    message: NormalizedMessage
    target: EditedMessage | None = None
    row_id: int | None = None
    person_id: str | None = None
    #: Set for a delete whose target was found and a withdrawer is wired.
    withdrawal: DeleteWithdrawal | None = None


async def run(
    *, message: NormalizedMessage, touch: Touch, audit: RecordStore,
    persons: Persons, metrics: Any, withdrawer: DeleteWithdrawer | None = None,
) -> TouchResult:
    """`touch` is `message.touch`, passed on its own so its presence is a
    type rather than a runtime check."""
    orphaned: TouchEnding = (
        "reaction_orphaned" if touch.kind == "reaction" else "delete_orphaned"
    )
    if message.timestamp <= 0:
        # A platform that gives no time for the touch: stamped on ARRIVAL,
        # seconds late, never wrong in kind (the edit path's fallback).
        message = dataclasses.replace(message, timestamp=int(time.time() * 1000))
    withdrawing = touch.kind == "delete" and withdrawer is not None
    person = persons.resolve(message.platform, message.sender_id) if message.sender_id else None
    person_id = person.person_id if person is not None else None
    target, looked = await _lookup(audit, message, touch, metrics)
    orphan_id: int | None = None
    if target is None and withdrawing:
        if looked and target_is_stale(
            platform=message.platform, handle=touch.target.message_id,
            sent_ts=touch.target.sent_ts, event_ts=message.timestamp,
        ):
            # Looked, and it is not there, and the target is older than
            # `ORPHAN_RACE_WINDOW_MS`: DROPPED, not held. That rests on an ASSUMPTION about
            # the ingest paths (a message reaches Nora within seconds of being sent), not
            # on a proof: a Discord RESUME replaying a long outage, or a Signal device with
            # a skewed clock, can still bring the message AFTER this delete, and it then
            # stays visible (see `nora.domain.delete_authorship`). A lookup that FAILED is
            # never taken for this: it is held below.
            metrics.inc(
                "nora_delete_withdrawals_total",
                platform=message.platform, outcome="orphan_stale_target",
            )
            return TouchResult(ending=orphaned, message=message)
        # THE DURABLE RECORD, FIRST, in its own transaction, then one more look:
        # the message may have been recorded between the first lookup and this
        # write, and the pipeline's check after a message's audit write has
        # already run. Written whether the target was absent or the lookup FAILED
        # («could not look» is not «not there»), so neither loses the delete.
        orphan_id = await _record_orphan(audit, message, touch, person_id, metrics)
        target, _ = await _lookup(audit, message, touch, metrics)
    if target is None:
        if withdrawing:
            metrics.inc(
                "nora_delete_withdrawals_total",
                platform=message.platform, outcome="not_stored",
            )
        return TouchResult(ending=orphaned, message=message)
    try:
        return await _record(
            message=message, touch=touch, target=target, person_id=person_id,
            audit=audit, metrics=metrics, withdrawer=withdrawer,
        )
    finally:
        if orphan_id is not None:
            # The delete found its message after all: the orphan has been applied
            # (or refused) here, and a row left would apply it a second time.
            await _discard_orphan(audit, orphan_id)


async def _lookup(
    audit: RecordStore, message: NormalizedMessage, touch: Touch, metrics: Any,
) -> tuple[EditedMessage | None, bool]:
    """The target and whether the store was actually read: `(None, False)` is a lookup
    that FAILED, `(None, True)` is «looked, not there»."""
    try:
        return await audit.find_edited(
            conversation_id=message.conversation_id, target=touch.target,
            sender_id=touch.target_author or message.sender_id,
        ), True
    except Exception:
        log.exception("%s target lookup failed", touch.kind)
        metrics.inc("nora_errors_total", stage="touch_target", platform=message.platform)
        return None, False


async def _record_orphan(
    audit: RecordStore, message: NormalizedMessage, touch: Touch,
    person_id: str | None, metrics: Any,
) -> int | None:
    if not touch.target.message_id and touch.target.sent_ts is None:
        return None             # it names nothing: nothing could ever match it
    recorded = None
    for attempt in range(ORPHAN_WRITE_ATTEMPTS):
        try:
            recorded = await audit.record_orphan_delete(
                message=message, touch=touch, person_id=person_id,
                now_ms=int(time.time() * 1000),
            )
            break
        except Exception:
            # Logged without a word of the message. A concurrent writer's deadlock or
            # serialization abort passes on a retry; a store that is down does not.
            log.exception(
                "an orphan delete could not be recorded (attempt %d of %d)",
                attempt + 1, ORPHAN_WRITE_ATTEMPTS,
            )
            if attempt + 1 < ORPHAN_WRITE_ATTEMPTS:
                await asyncio.sleep(
                    random.uniform(0, ORPHAN_RETRY_BASE_S * 2 ** attempt)  # noqa: S311  jitter
                )
    if recorded is None:
        # Dropped, counted, never raised: this delete is LOST. A leak-direction loss if
        # its message then arrives (it stays visible and is fed to memory).
        log.error("an orphan delete could not be recorded after %d attempts; it is lost",
                  ORPHAN_WRITE_ATTEMPTS)
        metrics.inc("nora_errors_total", stage="touch_orphan", platform=message.platform)
        metrics.inc(
            "nora_delete_withdrawals_total",
            platform=message.platform, outcome="orphan_lost",
        )
        return None
    if recorded.evicted:
        # A cap bound and held deletes of that scope went to make room (the oldest, or at
        # the room scope the heaviest contributor's oldest). This is a LEAK-direction loss
        # if an evicted delete's message then arrives (it stays visible and is fed to
        # memory): it takes a flood big enough to push a legitimate row out within its
        # seconds-to-minutes window, and it is only visible as this counter (alerted on:
        # `OrphanDeleteEvicted`, `deploy/prometheus/rules/nora.yml`). See
        # `nora.domain.delete_authorship` for the caps and what each scope takes.
        log.warning(
            "%d held deletes evicted to make room (%s)", recorded.evicted, message.platform,
        )
        metrics.inc(
            "nora_delete_withdrawals_total", recorded.evicted,
            platform=message.platform, outcome="orphan_evicted",
        )
    return recorded.id


async def _discard_orphan(audit: RecordStore, orphan_id: int) -> None:
    try:
        await audit.discard_orphan_delete(orphan_id=orphan_id)
    except Exception:
        # A row left behind is applied again, idempotently, and then purged.
        log.exception("orphan delete %d could not be discarded", orphan_id)


async def _record(
    *, message: NormalizedMessage, touch: Touch, target: EditedMessage,
    person_id: str | None, audit: RecordStore, metrics: Any,
    withdrawer: DeleteWithdrawer | None,
) -> TouchResult:
    if (
        touch.kind == "delete" and message.platform in AUTHOR_ONLY_DELETES
        and not deleter_is_author(
            author_sender_id=target.sender_id, author_person_id=target.person_id,
            deleter_sender_id=message.sender_id, deleter_person_id=person_id,
            deleter_ids=touch.actor_ids,
        )
    ):
        if withdrawer is not None:
            metrics.inc(
                "nora_delete_withdrawals_total",
                platform=message.platform, outcome="refused_not_author",
            )
        return TouchResult(ending="delete_refused", message=message, target=target)
    # THE RECORD FIRST, in its own transaction, then the hide. Whatever the hide
    # does (a cascade that raises rolls it back), the `message_delete` row is
    # already committed, so a withdrawal that failed is always findable: the
    # retry (`IngestPipeline._retry_withdrawal`) and the start-up sweep
    # (`delete_withdrawal.reconcile_deletes`) read it. If this write fails too,
    # the platform event is dropped (counted, logged without a word of the
    # message) and the hide below is still attempted: the withdrawal is what
    # honours the delete, the record is only evidence of it.
    row_id: int | None = None
    ending: TouchEnding = "reaction_recorded" if touch.kind == "reaction" else "delete_recorded"
    try:
        if touch.kind == "reaction":
            row_id = await audit.append_reaction(
                message_id=target.id, message=message, person_id=person_id,
            )
        else:
            row_id = await audit.append_delete(
                message_id=target.id, person_id=person_id,
                # The deleter's other id forms ride in the stored event
                # (`DELETER_IDS_KEY`): the authorship rule reads them back later (the
                # start-up sweep, `unforget`), so a record never depends on a
                # reconstructed event.
                message=dataclasses.replace(
                    message,
                    raw={**message.raw, DELETER_IDS_KEY: list(touch.actor_ids)},
                ),
            )
    except Exception:
        log.exception("%s append failed; dropping it", touch.kind)
        metrics.inc("nora_errors_total", stage="touch_record", platform=message.platform)
        ending = "audit_refused"
    withdrawal: DeleteWithdrawal | None = None
    if touch.kind == "delete" and withdrawer is not None:
        withdrawal = await _withdraw(
            withdrawer, target=target, platform=message.platform, metrics=metrics,
        )
    return TouchResult(
        ending=ending, message=message, target=target, row_id=row_id,
        person_id=person_id, withdrawal=withdrawal,
    )


async def _withdraw(
    withdrawer: DeleteWithdrawer, *, target: EditedMessage, platform: str, metrics: Any,
) -> DeleteWithdrawal:
    """Never raises (it runs on the listener's await path): a withdrawer that
    broke its own promise is `cascade_failed`, counted, and logged without a
    word of the message."""
    try:
        withdrawal = await withdrawer.withdraw(
            message_id=target.id, now_ms=int(time.time() * 1000),
        )
    except Exception:
        log.exception("delete withdrawal of message %d raised", target.id)
        withdrawal = DeleteWithdrawal(outcome="cascade_failed")
    metrics.inc(
        "nora_delete_withdrawals_total", platform=platform, outcome=withdrawal.outcome,
    )
    return withdrawal
