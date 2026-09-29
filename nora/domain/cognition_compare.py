"""Comparing cognition's mirrored `remind` calls against the live proactive
pass's own decisions over the same window (migration step 3a-ii;
design/cognition-loop/30-cognition.md §2's "shadow before cut-over": every
reminder cognition writes is mirrored and held, never sent — this module is
how that mirror is READ, never how it acts).

**The match key is a note.** The live proactive pass writes one `reminders`
row per note it decides to raise (`nora.domain.reminders.PROACTIVE_KIND`,
`open_proactive_note` reads `payload["note_id"]`); a mirrored `remind` call
cites its subject rows as Register/Rollup/Record ids, one of which — for a
note-triggered reminder — is the note's own id. So two acts MATCH when they
share `(platform, conversation_id)` and the proactive act's `note_id`
appears among the mirrored act's `subject_rows`. This is an APPROXIMATION,
stated rather than hidden: a mirrored act whose subject rows never include
a note id (an event-anchored or a co-location-anchored `remind`) can never
match a proactive row by construction — the live pass raises notes only —
so every such mirrored act reports `cognition_only`, correctly, and the
comparison undercounts nothing on the proactive side; it can only ever
undercount matches on cognition's, which is the conservative direction for
a shadow-period read.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MirroredAct:
    reminder_ref: str
    platform: str
    conversation_id: str
    subject_rows: tuple[str, ...]
    at_ms: int


@dataclass(frozen=True, slots=True)
class ProactiveAct:
    reminder_id: str
    platform: str
    conversation_id: str
    note_id: str
    at_ms: int


@dataclass(frozen=True, slots=True)
class CompareResult:
    matched: tuple[tuple[MirroredAct, ProactiveAct], ...]
    cognition_only: tuple[MirroredAct, ...]
    proactive_only: tuple[ProactiveAct, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "matched": len(self.matched), "cognition_only": len(self.cognition_only),
            "proactive_only": len(self.proactive_only),
        }


def compare(
    mirrored: tuple[MirroredAct, ...], proactive: tuple[ProactiveAct, ...],
) -> CompareResult:
    """Pure: no store, no clock — the caller reads both sides for the same
    window and hands them in. A proactive act matches AT MOST ONE mirrored
    act (first found, in the given order), so a run with duplicate mirrored
    subjects for one note never double-counts a single live decision."""
    remaining = list(mirrored)
    matched: list[tuple[MirroredAct, ProactiveAct]] = []
    proactive_only: list[ProactiveAct] = []
    for p in proactive:
        hit = next(
            (m for m in remaining
             if m.platform == p.platform and m.conversation_id == p.conversation_id
             and p.note_id in m.subject_rows),
            None,
        )
        if hit is None:
            proactive_only.append(p)
        else:
            matched.append((hit, p))
            remaining.remove(hit)
    return CompareResult(
        matched=tuple(matched), cognition_only=tuple(remaining),
        proactive_only=tuple(proactive_only),
    )
