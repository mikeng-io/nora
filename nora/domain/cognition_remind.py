"""`remind`'s pure checks: the
code-side act guards a Cognition session's reminder is held to before it is
written to the live reminder store. Each is a refusal with a named reason:
non-empty citations, the dedupe rule ("one pending act per (subject rows,
room)"), the room's `proactive` flag, "a value that is only abduced goes out as
a question", and the tag `remind` tool's own lead, horizon and pending ceiling
(`RemindContext`). The caller reads the state and hands it in; nothing here
reads. Disclosure is re-run when the reminder fires, by the fire path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

REMIND_VERBS: tuple[str, ...] = ("remind", "remind_move", "remind_cancel")
INTENTS: tuple[str, ...] = ("tell", "ask")


@dataclass(frozen=True, slots=True)
class PendingRemind:
    """One of Nora's own pending reminders, as the dedupe rule reads it."""

    reminder_ref: str
    platform: str
    conversation_id: str
    subject_rows: tuple[str, ...]
    #: The resolved shared-object `(kind, id)` the row was written against, and
    #: the action it named. `None`/"" for rows written before subject stamping:
    #: an unsubjected row can never conflict-match a resolved one.
    subject: tuple[str, str] | None = None
    action: str = ""


@dataclass(frozen=True, slots=True)
class RemindRefusal:
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RemindContext:
    """What the caller read for one `remind`, beyond the pending rows: the checks
    the LIVE `remind` tool makes (`nora.agent.tools`: the moment must be ahead, no
    nearer than `min_lead_seconds`, no further than `max_horizon_days`; Nora's own
    pending rows under `max_pending_per_requester`), from the same constants, plus
    two of the act guards (the room's `proactive` flag, an abduced value goes
    out as a question). Every field's default is the permissive reading, so a
    caller that reads none of it is unchanged; the composition reads all of it.
    No number here is ever shown to the model, and no cap is invented: the live
    path has no daily act cap, so none is checked."""

    #: The room's `proactive` flag (`Policy.proactive_allowed`): may Nora reach
    #: this room unasked at all.
    room_proactive: bool = True
    #: A cited row is a value that is only abduced: the act must be an `ask`.
    abduced_subject: bool = False
    #: The live ceiling (`ReminderCaps.max_pending_per_requester`, `None`: none)
    #: and how many pending rows Nora's own requester id already holds
    #: (`ReminderStore.count_pending`, a series counted as one).
    max_pending: int | None = None
    live_pending: int = 0
    #: The live lead and horizon, and the clock and the moment asked for, all in
    #: epoch milliseconds / seconds / days; any left `None` leaves the three time
    #: checks out.
    now_ms: int | None = None
    at_ms: int | None = None
    min_lead_seconds: int | None = None
    max_horizon_days: int | None = None


def check_remind_time(ctx: RemindContext) -> RemindRefusal | None:
    """The moment: ahead, no nearer than the lead, no further than the
    horizon. The live tool's own order and wording, without its numbers."""
    if ctx.now_ms is None or ctx.at_ms is None:
        return None
    if ctx.at_ms <= ctx.now_ms:
        return RemindRefusal(
            "past",
            "That moment has already gone. Work out the date again and give one "
            "exact moment ahead.",
        )
    if ctx.min_lead_seconds is not None and ctx.at_ms - ctx.now_ms < ctx.min_lead_seconds * 1000:
        return RemindRefusal(
            "too_soon", "That is near enough that it would be said now. Set it further out.",
        )
    if (
        ctx.max_horizon_days is not None
        and ctx.at_ms - ctx.now_ms > ctx.max_horizon_days * 86_400_000
    ):
        return RemindRefusal(
            "too_far", "That is further out than anything is held. Set it nearer the time.",
        )
    return None


def check_remind(
    *, subject_rows: Sequence[str], intent: str, platform: str, conversation_id: str,
    pending: Sequence[PendingRemind], context: RemindContext | None = None,
    subject: tuple[str, str] | None = None,
    extra_destinations: Sequence[tuple[str, str]] = (),
) -> RemindRefusal | None:
    """`remind(at, room, brief)`. `brief` is `(intent, subject_rows)` — never
    prose. The refusal reasons are `no_citations`,
    `bad_intent`, `room_not_proactive`, `abduced_must_ask`, `past`, `too_soon`,
    `too_far`, `duplicate_pending`, `conflict_other_room` and
    `pending_ceiling`; none of the details states a number (a model told of a
    cap declines the work).

    The dedupe rule when `subject` resolves is **same subject and same
    action**, independent of room and of the citation set — a pending row for
    this act at a destination the call does not cover is
    `conflict_other_room`, never silently a second row; a call that names it
    in `extra_destinations` has declared the multi-destination intent. A call
    whose every covered destination is already pending this act is
    `duplicate_pending`. An unresolved `subject` falls back to the older
    same-room citation-set rule (the only key such rows have); the caller is
    expected to have surfaced `unresolved` first."""
    if not subject_rows:
        return RemindRefusal("no_citations", "a reminder must cite at least one row")
    if intent not in INTENTS:
        return RemindRefusal("bad_intent", f"intent must be one of {INTENTS}")
    ctx = context or RemindContext()
    if not ctx.room_proactive:
        return RemindRefusal(
            "room_not_proactive", "you may not reach that room unasked",
        )
    if ctx.abduced_subject and intent != "ask":
        return RemindRefusal(
            "abduced_must_ask",
            "a row you cite is only your best explanation, not something anyone said: "
            "ask the room about it, do not tell it",
        )
    timing = check_remind_time(ctx)
    if timing is not None:
        return timing
    covered = {(platform, conversation_id), *extra_destinations}
    if subject is not None:
        conflicts = [
            p for p in pending
            if p.subject == subject and p.action == intent
        ]
        pending_dests = {(p.platform, p.conversation_id) for p in conflicts}
        uncovered = pending_dests - covered
        if uncovered:
            return RemindRefusal(
                "conflict_other_room",
                "the same act is already pending for "
                + ", ".join(sorted(f"{p}:{c}" for p, c in uncovered))
                + "; name that room too if you mean both, or move or cancel that one",
            )
        if pending_dests and covered <= pending_dests:
            return RemindRefusal(
                "duplicate_pending",
                "the same act is already pending for every room you named",
            )
    else:
        duplicate = any(
            p.platform == platform and p.conversation_id == conversation_id
            and set(p.subject_rows) == set(subject_rows)
            for p in pending
        )
        if duplicate:
            return RemindRefusal(
                "duplicate_pending", "an identical reminder is already pending for this room",
            )
    if ctx.max_pending is not None and ctx.live_pending >= ctx.max_pending:
        return RemindRefusal(
            "pending_ceiling",
            "You already have enough of your own waiting. Let some fire before "
            "deciding more.",
        )
    return None
