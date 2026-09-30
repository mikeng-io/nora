"""Comparing cognition's mirrored `remind` calls against the live proactive
pass's own decisions over the same window (migration step 3a-ii;
design/cognition-loop/30-cognition.md §2's "shadow before cut-over": every
reminder cognition writes is mirrored and held, never sent — this module is
how that mirror is READ, never how it acts).

**The match key is a note id or an event id, in one room.** The live proactive
pass writes one `reminders` row per thing it decides to raise, and that row's
payload names what it is about (`note_id`, and an `event_id` when the reminder
is event-anchored); a mirrored `remind` call cites its subject rows as
Register/Rollup/Record ids. Two acts MATCH when they share `(platform,
conversation_id)` and any id the proactive row names appears among the
mirrored act's subject rows. Ids are compared BARE: `note:<id>` and `<id>`
are the same id, so the comparison does not depend on which spelling a writer
chose (the payload stores the bare id; a subject row is typed).

**Three things cannot be counted the obvious way, and are reported apart
(`CompareResult`'s footnote fields), never folded into the three counts**:

- *A mirrored remind is a CHAIN* — `remind`, then any `remind_move` and
  `remind_cancel` (`resolve_chains`). Its state is the LATEST link's: a moved
  one is compared at its latest time (`moved`), and a cancelled one is not an
  act cognition would have made at all (`cancelled`), so it is counted in
  neither `matched` nor `cognition_only`.
- *A proactive row with neither `note_id` nor `event_id`* cannot be keyed and
  is listed `unkeyed`, not dropped and not called `proactive_only`: nothing
  says cognition did NOT cover it.
- *A mirrored remind whose subject rows name no note or event* (a Rollup or
  Record row only) can never match a proactive row by construction, so its
  `cognition_only` is not a disagreement; it is footnoted (`unkeyable`).

The counts stay conservative for a shadow-period read: they can undercount
matches; the footnotes say by how much.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MirroredAct:
    """One mirrored `remind` chain, at its LATEST state. `at_ms` is the time
    it was set for (after any move); `moved`/`cancelled` say how the chain
    went."""

    reminder_ref: str
    platform: str
    conversation_id: str
    subject_rows: tuple[str, ...]
    at_ms: int
    moved: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ProactiveAct:
    reminder_id: str
    platform: str
    conversation_id: str
    note_id: str = ""
    at_ms: int = 0
    event_id: str = ""

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(k for k in (bare(self.note_id), bare(self.event_id)) if k)


@dataclass(frozen=True, slots=True)
class MirrorRow:
    """One `cognition_remind_mirror` row, as `resolve_chains` reads it."""

    verb: str
    reminder_ref: str
    platform: str
    conversation_id: str
    subject_rows: tuple[str, ...]
    at_ms: int
    outcome: str = "recorded"


@dataclass(frozen=True, slots=True)
class CompareResult:
    matched: tuple[tuple[MirroredAct, ProactiveAct], ...]
    cognition_only: tuple[MirroredAct, ...]
    proactive_only: tuple[ProactiveAct, ...]
    #: Mirrored reminds cognition cancelled: not counted above.
    cancelled: tuple[MirroredAct, ...] = ()
    #: Proactive rows with neither a note nor an event id: not counted above.
    unkeyed: tuple[ProactiveAct, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            "matched": len(self.matched), "cognition_only": len(self.cognition_only),
            "proactive_only": len(self.proactive_only),
        }

    @property
    def moved(self) -> tuple[MirroredAct, ...]:
        """Counted mirrored reminds (matched or cognition-only) that were moved."""
        return tuple(
            m for m in (*(m for m, _ in self.matched), *self.cognition_only) if m.moved
        )

    @property
    def unkeyable(self) -> tuple[MirroredAct, ...]:
        """`cognition_only` reminds that name no note or event: they cannot
        match by construction."""
        return tuple(m for m in self.cognition_only if not _has_key(m))


def bare(row: str) -> str:
    """`note:<id>` / `event:<id>` / `<id>` -> `<id>`."""
    kind, sep, rest = row.partition(":")
    return rest if sep and kind in ("note", "event") else row


def _has_key(act: MirroredAct) -> bool:
    return any(
        ":" not in row or row.partition(":")[0] in ("note", "event")
        for row in act.subject_rows
    )


def resolve_chains(rows: Sequence[MirrorRow]) -> tuple[MirroredAct, ...]:
    """Each `remind` chain at its latest state. `rows` are in recorded order.
    A refused row changes nothing; a move after a cancel changes nothing; a
    move or cancel whose `remind` is not among `rows` is ignored (its chain
    began outside the window)."""
    acts: dict[str, MirroredAct] = {}
    for row in rows:
        if row.outcome != "recorded":
            continue
        if row.verb == "remind":
            acts[row.reminder_ref] = MirroredAct(
                reminder_ref=row.reminder_ref, platform=row.platform,
                conversation_id=row.conversation_id, subject_rows=row.subject_rows,
                at_ms=row.at_ms,
            )
            continue
        was = acts.get(row.reminder_ref)
        if was is None or was.cancelled:
            continue
        if row.verb == "remind_move":
            acts[row.reminder_ref] = MirroredAct(
                reminder_ref=was.reminder_ref, platform=was.platform,
                conversation_id=was.conversation_id, subject_rows=was.subject_rows,
                at_ms=row.at_ms, moved=True,
            )
        elif row.verb == "remind_cancel":
            acts[row.reminder_ref] = MirroredAct(
                reminder_ref=was.reminder_ref, platform=was.platform,
                conversation_id=was.conversation_id, subject_rows=was.subject_rows,
                at_ms=was.at_ms, moved=was.moved, cancelled=True,
            )
    return tuple(acts.values())


def compare(
    mirrored: tuple[MirroredAct, ...], proactive: tuple[ProactiveAct, ...],
) -> CompareResult:
    """Pure: no store, no clock — the caller reads both sides for the same
    window and hands them in. A proactive act matches AT MOST ONE mirrored
    act (first found, in the given order), so a run with duplicate mirrored
    subjects for one note never double-counts a single live decision."""
    cancelled = tuple(m for m in mirrored if m.cancelled)
    remaining = [m for m in mirrored if not m.cancelled]
    matched: list[tuple[MirroredAct, ProactiveAct]] = []
    proactive_only: list[ProactiveAct] = []
    unkeyed: list[ProactiveAct] = []
    for p in proactive:
        if not p.keys:
            unkeyed.append(p)
            continue
        hit = next(
            (m for m in remaining
             if m.platform == p.platform and m.conversation_id == p.conversation_id
             and p.keys & {bare(r) for r in m.subject_rows}),
            None,
        )
        if hit is None:
            proactive_only.append(p)
        else:
            matched.append((hit, p))
            remaining.remove(hit)
    return CompareResult(
        matched=tuple(matched), cognition_only=tuple(remaining),
        proactive_only=tuple(proactive_only), cancelled=cancelled,
        unkeyed=tuple(unkeyed),
    )
