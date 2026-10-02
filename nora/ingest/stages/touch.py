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
  before Nora joined, pruned, or never recorded). Dropped, for
  `edit_target`'s reason: a touch of nothing cannot be attached, and a
  lookup that FAILED counts an error besides — «could not look» is not «not
  there».
* **`delete_refused`** — a delete on a platform where only a message's author
  can delete it (Signal's remote delete), naming a message whose author is
  not the deleter: a malformed or forged event, refused as
  `edit_target` refuses an edit by someone else. Authorship is the platform
  id first, the resolved person second (one person, two id forms).
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

import dataclasses
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.models import EditedMessage, NormalizedMessage, Touch
from nora.domain.ports import DeleteWithdrawal, DeleteWithdrawer, RecordStore
from nora.persons import Persons

log = logging.getLogger("nora.ingest.touch")

TouchEnding = Literal[
    "reaction_recorded", "reaction_orphaned",
    "delete_recorded", "delete_orphaned", "delete_refused",
    "audit_refused",
]

#: Platforms where only a message's author can delete it for everyone, so a
#: delete naming someone else's message is not a member's action: Signal's remote
#: delete and WhatsApp's revoke. On WhatsApp the platform enforces nothing the
#: server can refuse (end-to-end encrypted: the bridge forwards any REVOKE), and
#: the event carries the REVOKER (`sender_*`, the envelope's sender), so the check
#: is ours; a group admin revoking someone else's message cannot be verified
#: without group metadata Python does not hold and is REFUSED (`refused_not_author`:
#: the message stays visible). Discord's gateway and Instagram's unsend name no
#: deleter at all (`sender_id == ""`), so there is nothing to compare; both are
#: server-authenticated events and are honoured as the platform reports them.
AUTHOR_ONLY_DELETES: frozenset[str] = frozenset({"signal", "whatsapp"})


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


@dataclass(frozen=True)
class _Held:
    until: float
    message: NormalizedMessage
    touch: Touch
    #: Who the platform says deleted it, resolved when it arrived: the author
    #: check on a platform where only the author can delete.
    person_id: str | None


class PendingDeletes:
    """Deletes whose target was not on record when they arrived, held for a
    short while so the message, when it IS recorded, is withdrawn at once.

    A delete can overtake the message it names: Discord dispatches each event as
    its own task, and a message with an attachment is only recorded after its
    custody fetch, so a photo sent and unsent within seconds is deleted before it
    is stored. Without this the late row is recorded visible and nothing ever
    withdraws it. Bounded in size and in time and held in this process only: a
    restart inside the window loses the hold (nothing durable names it: a
    `message_delete` row needs a target), and a delete that outlives `ttl_seconds`
    unmatched is `hold_expired`, counted by `on_expired`, never `not_stored`.

    **A hold matches the message it names, and only it.** The same platform, the
    same room, and the platform handle (never an empty one: a platform that gave
    the message no handle must not match every message that has none) or Signal's
    sent-timestamp. And on a platform where only the author can delete
    (`AUTHOR_ONLY_DELETES`) the arriving message must be the deleter's own: two
    members posting in one millisecond are two messages, and one of them must not
    consume the other's delete."""

    def __init__(
        self, *, ttl_seconds: float = 300.0, max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
        on_expired: Callable[[NormalizedMessage], None] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._on_expired = on_expired
        self._held: OrderedDict[int, _Held] = OrderedDict()
        self._next = 0

    def add(self, message: NormalizedMessage, touch: Touch, *, person_id: str | None) -> int:
        self._expire()
        self._next += 1
        self._held[self._next] = _Held(self._clock() + self._ttl, message, touch, person_id)
        while len(self._held) > self._max:
            _, dropped = self._held.popitem(last=False)
            self._lost(dropped)
        return self._next

    def discard(self, entry: int) -> bool:
        """True when this call removed it (nobody had taken it)."""
        return self._held.pop(entry, None) is not None

    def matches(self, message: NormalizedMessage, *, person_id: str | None) -> bool:
        """Whether a held delete names `message`, without taking it."""
        return bool(self._matching(message, person_id))

    def take(
        self, message: NormalizedMessage, *, person_id: str | None,
    ) -> list[tuple[NormalizedMessage, Touch]]:
        """Every held delete that names `message`, removed."""
        out: list[tuple[NormalizedMessage, Touch]] = []
        for entry in self._matching(message, person_id):
            held = self._held.pop(entry)
            out.append((held.message, held.touch))
        return out

    def _matching(self, message: NormalizedMessage, person_id: str | None) -> list[int]:
        self._expire()
        author_only = message.platform in AUTHOR_ONLY_DELETES
        found: list[int] = []
        for entry, held in self._held.items():
            delete = held.message
            if delete.platform != message.platform or (
                delete.conversation_id != message.conversation_id
            ):
                continue
            target = held.touch.target
            by_handle = (
                bool(target.message_id) and bool(message.message_id)
                and target.message_id == message.message_id
            )
            by_ts = target.sent_ts is not None and target.sent_ts == message.timestamp
            if not (by_handle or by_ts):
                continue
            if author_only and not (
                (bool(delete.sender_id) and delete.sender_id == message.sender_id)
                or (held.person_id is not None and held.person_id == person_id)
            ):
                continue
            found.append(entry)
        return found

    def _expire(self) -> None:
        now = self._clock()
        for entry, held in list(self._held.items()):
            if held.until <= now:
                del self._held[entry]
                self._lost(held)

    def _lost(self, held: _Held) -> None:
        if self._on_expired is not None:
            self._on_expired(held.message)


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
    try:
        target = await audit.find_edited(
            conversation_id=message.conversation_id, target=touch.target,
            sender_id=touch.target_author or message.sender_id,
        )
    except Exception:
        log.exception("%s target lookup failed; dropping it", touch.kind)
        metrics.inc("nora_errors_total", stage="touch_target", platform=message.platform)
        return TouchResult(ending=orphaned, message=message)
    if target is None:
        if touch.kind == "delete" and withdrawer is not None:
            metrics.inc(
                "nora_delete_withdrawals_total",
                platform=message.platform, outcome="not_stored",
            )
        return TouchResult(ending=orphaned, message=message)

    person = persons.resolve(message.platform, message.sender_id) if message.sender_id else None
    person_id = person.person_id if person is not None else None
    if (
        touch.kind == "delete" and message.platform in AUTHOR_ONLY_DELETES
        and not _same_author(message.sender_id, person_id, target)
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
                message_id=target.id, message=message, person_id=person_id,
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


def _same_author(sender_id: str, person_id: str | None, target: EditedMessage) -> bool:
    """`edit_target._same_author`'s rule: the same platform id, or the same
    resolved person (a Signal sender seen by phone once and by ACI once)."""
    if sender_id and sender_id == target.sender_id:
        return True
    return person_id is not None and person_id == target.person_id
