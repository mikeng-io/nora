"""The note tier's back-stop for duplicates — `NoteMergeAdaptor`
(design/dreamer-agent-loop/41 §7.3-7.8, DEC-202).

WHAT IT IS FOR
--------------
Two open notes asking the same open question become one. `note-candidates`
and the tag path are the front gate (an `ambiguous` candidate stays pending,
`note_create` refuses an open title); this is what folds what gets through.
It is not the curator: the curator judges each note alone against its own
room's evidence and never puts two side by side, and folding a second,
unrelated question into its prompt would widen its input to every open note
(41 §7.3).

WHAT IT READS, AND WHEN IT READS AT ALL
---------------------------------------
Unit-keyed, watermark-free, tools-less. Every tick is a cheap store read and
nothing else unless the notebook CHANGED since the last rendered pass: an open
note created after the stamp, or any revision written after it whose
`established_by` is not this adaptor's own (`dream:note-merge:*`). Revisions,
not heads, because a later revision would hide an earlier one — the gap in the
proactive pass's own change check (41 §7.4).

The stamp is a `dream_schedule_state` row per unit and per mode
(`note-merge:pass:shadow`, `note-merge:pass:on`), so it survives restarts and a
flip from `shadow` to `on` starts a fresh clock (`PASS_CLOCK_NAME`). It is
written with the RENDER instant — the moment `inputs` read the notebook — as
the last write of `commit`, and never on a pass that
rendered nothing or that the runtime reported `unavailable` (the runtime does
not call `commit` then). A stamp taken at commit time would never see a note
written while the model was thinking.

When something changed, the model is shown EVERY open note of the unit — the
changed ones are marked — because a changed note has to be compared against
every other. Each carries its age, when it was last added to, its anchor read
against today, and the room it was born in: the judgement is about whether two
notes are one open question, and «the same people, the same day» is exactly
what it must not mistake for that.

ONE SAMPLE, AND WHY THE CODE FAILS TOWARD KEEPING NOTES APART
------------------------------------------------------------
The runtime UNIONS samples, so with `samples > 1` a group any one sample named
would be folded: the composition root pins `samples = 1`. Past that, code
refuses what it can decide (`NOTE_MERGE_OUTCOMES`): an ordinal this pass did
not show, a group of one, a note named by two groups (every such group refused
whole — no transitive closure), a group spanning rooms, and a group in which no
note changed since the last pass. The last one is the same argument as the
one-sample pin, over time: a pair judged apart once would otherwise be re-asked
every pass, and a union of passes is a union of samples.

A missed fold costs a duplicate line, which is visible. A wrong fold puts two
questions in one note — kept, readable and un-retirable (41 §7.7), but a
mistake a reader has to undo.

WHAT A FOLD WRITES
------------------
`merge_mode = "shadow"` writes nothing to notes: each fold that passes every
check is recorded as a `note_merge_decision` row (`would_merge`) with both
titles and the model's reason, for the operator to label. `"on"` folds through
`SqlNoteStore.merge_into`, one duplicate at a time into the OLDEST note of the
group, stopping at the first failure: the survivor gains one revision
(`nora.domain.notes.merged_body`) and the LATEST anchor of the two with its
`revisit_at` recomputed, and the duplicate is retired `retired-merged` naming
it, in one transaction that compare-and-swaps both heads. Every refusal is
recorded too, as a row where it names a pair and on the counter always.

Ordinal HANDLES in the prompt and the envelope, never ids: the Dreamer's
screen drops any item carrying a uuid (issue #63).
"""

from __future__ import annotations

import collections
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import ClassVar, Literal, Protocol, get_args
from zoneinfo import ZoneInfo

from nora.domain.blocks import ContextBlock
from nora.domain.notes import (
    BODY_MAX_CHARS,
    MERGE_REASON_MAX_CHARS,
    NOTE_MERGE_MODES,
    Note,
    NoteMergeDecision,
    NoteRevision,
    StaleNoteHeadError,
    default_revisit_at,
    latest_anchor,
    merged_body,
)
from nora.domain.ports import Proposal
from nora.domain.prompt import age, escape
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.notes.merge")

ADAPTOR_NAME = "note-merge"

#: The `dream_schedule_state` rows this adaptor keeps per unit: when a pass
#: last RENDERED (41 §7.4), ONE ROW PER MODE — `note-merge:pass:shadow` and
#: `note-merge:pass:on` (`pass_clock_name`).
#:
#: Per mode because `not_changed` refuses a group nobody touched since the
#: last pass, and a pass in `shadow` judges without folding. One shared clock
#: meant a pair `shadow` recorded as `would_merge` was refused `not_changed`
#: for ever once the operator flipped to `on` — the fold the shadow run was
#: there to vouch for could never happen (review of #495). A fresh clock on the
#: flip makes the first `on` pass see every open note as new, so every pair is
#: reconsidered once, by the mode that writes.
PASS_CLOCK_NAME = "note-merge:pass"  # noqa: S105 — a table row name, not a secret


def pass_clock_name(mode: str) -> str:
    """The pass-clock row for one mode — see `PASS_CLOCK_NAME`."""
    return f"{PASS_CLOCK_NAME}:{mode}"

#: The `established_by` prefix `DreamerRuntime` stamps on this adaptor's own
#: proposals (`dream:<adaptor>:<key>:<ms>`). A revision carrying it is the
#: pass's own fold and never a reason to run again.
OWN_REVISION_PREFIX = f"dream:{ADAPTOR_NAME}:"

#: How much of a body one note shows. Stated in the render when it cuts, so
#: the model knows it is reading the start of a longer entry.
DEFAULT_BODY_EXCERPT_CHARS = 600

#: How many awaited collaborator CALL SITES `inputs` and `commit` hold between
#: them — what `DreamerRuntime` multiplies by `[dreamer]
#: store_timeout_seconds` (issue #60). `inputs`: the pass clock, `standing`,
#: and per open note `revision_chain`. `commit`: per fold one `read` site
#: (walked over the two notes), `merge_into`, `record_merge_decision`, and the
#: pass clock's stamp. `tests/architecture/test_dream_store_calls_are_declared.py`
#: counts them off this module's AST.
STORE_CALLS_PER_TICK = 7

#: `nora_note_merge_total{outcome}`: the per-pair outcomes a decision row can
#: carry, plus three that name no pair and so are counted only.
MergeOutcome = Literal[
    "would_merge", "merged", "stale", "too_long", "not_open", "overlap",
    "cross_room", "not_changed",
    # -- counted, never a row: there is no (survivor, duplicate) to name ----
    "unknown_ordinal",  # the group named a handle this pass did not show
    "singleton",        # the group named fewer than two distinct notes
    "malformed",        # the item was not a list of handles and a reason
]
MERGE_TOTAL_OUTCOMES: frozenset[str] = frozenset(get_args(MergeOutcome))

#: `nora_note_merge_pass_total{reason}`: whether a tick rendered.
PassReason = Literal["changed", "unchanged", "nothing_open"]
PASS_REASONS: frozenset[str] = frozenset(get_args(PassReason))

#: `nora_note_merge_proactive_total{outcome}`: what `SqlNoteStore.merge_into`'s
#: reminders step (issue #158 residue) did to either note's open proactive
#: row, one call per event via `on_reminder_outcome` — see that method's
#: docstring for what each value means.
ProactiveReminderOutcome = Literal[
    "repointed", "collision_repointed", "collision_kept_survivor", "moved",
    "skipped_firing_collision",
]
PROACTIVE_REMINDER_OUTCOMES: frozenset[str] = frozenset(
    get_args(ProactiveReminderOutcome)
)

MergeMode = Literal["shadow", "on"]


class _NoteStore(Protocol):
    """`SqlNoteStore`, seen from here. `merge_into` and
    `record_merge_decision` are store verbs, not `DreamSink`'s (41 §7.6)."""

    async def standing(self, *, unit: str) -> tuple[Note, ...]: ...

    async def revision_chain(
        self, *, unit: str, note_id: str,
    ) -> tuple[NoteRevision, ...]: ...

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...

    async def merge_into(
        self, *, unit: str, survivor_id: str, survivor_head: str,
        duplicate_id: str, duplicate_head: str, body: str,
        anchor_date: int | None, revisit_at: int, reset_strikes: bool,
        established_by: str, now_ms: int,
        instruction_sha256: str | None = None, model: str | None = None,
        on_reminder_outcome: Callable[[str], None] | None = None,
    ) -> NoteRevision: ...

    async def record_merge_decision(self, *, decision: NoteMergeDecision) -> None: ...


class _PassClock(Protocol):
    """`nora.domain.ports.DreamScheduleClock`'s two verbs."""

    async def last_fired(self, *, adaptor_name: str, key: str) -> int | None: ...

    async def record_fire(self, *, adaptor_name: str, key: str, at_ms: int) -> None: ...


@dataclass(frozen=True)
class _Group:
    """One envelope item: the handles the model says are one open question,
    and why. Resolved against `_Shown` in `commit`, where the whole answer is
    in view — overlap is a property of the answer, not of one item."""

    handles: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class _Shown:
    """What one render put in front of the model, kept per unit between
    `inputs` and `commit`."""

    #: The instant `inputs` read the notebook — the stamp `commit` writes.
    render_ms: int
    by_handle: Mapping[str, str]
    notes: Mapping[str, Note]
    #: note id -> the head revision the model was shown.
    heads: Mapping[str, NoteRevision]
    changed: frozenset[str]


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


class NoteMergeAdaptor:
    """One `DreamAdaptor` — see the module docstring."""

    name = ADAPTOR_NAME
    key_kind = "unit"
    uses_watermark = False
    objective = "note-merge"
    envelope_key = "groups"
    #: Tools-less: everything it judges is known before the call and is
    #: rendered in (41 §7.3).
    tools: frozenset[str] = frozenset()
    store_calls_per_tick = STORE_CALLS_PER_TICK

    _VOCABULARIES: ClassVar[dict[str, tuple[str, frozenset[str]]]] = {
        "nora_note_merge_total": ("outcome", MERGE_TOTAL_OUTCOMES),
        "nora_note_merge_pass_total": ("reason", PASS_REASONS),
        "nora_note_merge_proactive_total": (
            "outcome", PROACTIVE_REMINDER_OUTCOMES,
        ),
    }

    def __init__(
        self,
        *,
        notes: _NoteStore,
        clock_store: _PassClock,
        mode: MergeMode,
        timezone: ZoneInfo,
        label_for: Callable[[str], str | None] = lambda _conversation: None,
        metrics: Metrics | None = None,
        clock: Callable[[], int] = _now_ms,
        body_excerpt_chars: int = DEFAULT_BODY_EXCERPT_CHARS,
    ) -> None:
        if mode not in NOTE_MERGE_MODES:
            raise ValueError(
                f"note-merge mode {mode!r} is not one of {NOTE_MERGE_MODES}; "
                f"`off` is not a mode of a built adaptor, it is not building one"
            )
        self._notes = notes
        self._clock_store = clock_store
        self._mode = mode
        self._tz = timezone
        self._label_for = label_for
        self._metrics = metrics
        self._clock = clock
        self._pass_clock = pass_clock_name(mode)
        self._excerpt = body_excerpt_chars
        self._shown: dict[str, _Shown] = {}

    def _inc(self, name: str, **labels: str) -> None:
        label, allowed = self._VOCABULARIES[name]
        if labels.get(label) not in allowed:
            raise ValueError(
                f"{name}{{{label}={labels.get(label)!r}}} is not in this "
                f"metric's vocabulary {sorted(allowed)}"
            )
        if self._metrics is not None:
            self._metrics.inc(name, 1.0, **labels)

    # -- inputs ---------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[object]) -> str:
        """The unit's open notes, side by side — or `""` when nothing changed
        since the last rendered pass, which is the runtime's fire condition
        (no model call on a blank prompt).

        `batch` is always empty (`uses_watermark = False`)."""
        render_ms = self._clock()
        self._shown.pop(key, None)
        last = await self._clock_store.last_fired(
            adaptor_name=self._pass_clock, key=key,
        )
        open_notes = await self._notes.standing(unit=key)
        if len(open_notes) < 2:
            self._inc("nora_note_merge_pass_total", reason="nothing_open")
            return ""
        chains: dict[str, tuple[NoteRevision, ...]] = {}
        for note in open_notes:
            chains[note.id] = await self._notes.revision_chain(
                unit=key, note_id=note.id,
            )
        changed = frozenset(
            note.id for note in open_notes
            if _changed_since(note, chains[note.id], last_ms=last)
        )
        if not changed:
            self._inc("nora_note_merge_pass_total", reason="unchanged")
            return ""
        self._inc("nora_note_merge_pass_total", reason="changed")

        now = datetime.fromtimestamp(render_ms / 1000, tz=self._tz)
        today = now.date()
        rooms = _room_names(open_notes, self._label_for)
        by_handle: dict[str, str] = {}
        heads: dict[str, NoteRevision] = {}
        parts = [_today_block(today)]
        for n, note in enumerate(open_notes, start=1):
            handle = str(n)
            by_handle[handle] = note.id
            chain = chains[note.id]
            head = next((r for r in chain if r.id == note.head_revision_id), None)
            if head is None:
                # A head the chain does not hold is a row this pass cannot
                # read whole; it is left out rather than judged on nothing.
                log.warning(
                    "note-merge: note %s has no readable head; not shown",
                    note.id,
                )
                del by_handle[handle]
                continue
            heads[note.id] = head
            parts.append(_note_block(
                handle, note, head,
                room=rooms[note.established_in], now=now, today=today,
                changed=(
                    None if note.id not in changed
                    else "new" if last is None or note.created_at > last
                    else "revised"
                ),
                excerpt=self._excerpt,
            ))
        self._shown[key] = _Shown(
            render_ms=render_ms, by_handle=by_handle,
            notes={note.id: note for note in open_notes if note.id in heads},
            heads=heads, changed=changed,
        )
        prompt = "\n".join(parts)
        if self._metrics is not None:
            # The prompt's size in characters. Its size in TOKENS is the
            # model call's own usage, on the pass's Langfuse trace — this
            # adaptor never sees it, so it records what it does see.
            self._metrics.observe("nora_note_merge_prompt_chars", len(prompt))
        return prompt

    # -- accept ---------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]],
    ) -> Sequence[object]:
        """Typed parse into `_Group`. A malformed item is dropped and counted,
        never guessed at."""
        out: list[object] = []
        for item in items:
            handles = item.get("notes")
            why = item.get("why", "")
            if (
                not isinstance(handles, list)
                or not all(isinstance(h, str | int) and not isinstance(h, bool)
                           for h in handles)
                or not isinstance(why, str)
            ):
                self._inc("nora_note_merge_total", outcome="malformed")
                log.warning("note-merge dropped a malformed group")
                continue
            out.append(_Group(
                handles=tuple(str(h).strip() for h in handles), why=why.strip(),
            ))
        return tuple(out)

    # -- commit ---------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> int:
        """Refuse what code can decide, fold (or, in `shadow`, record) the
        rest, then stamp the pass clock with the RENDER instant, last.

        Returns notes retired by a fold — 0 in `shadow`, always."""
        shown = self._shown.pop(key, None)
        if shown is None:
            # Nothing was rendered, so nothing was judged and there is no
            # instant to stamp.
            return 0
        provenance = proposals[0] if proposals else None
        resolved: list[tuple[tuple[str, ...], str]] = []
        for proposal in proposals:
            group = proposal.payload
            if not isinstance(group, _Group):
                continue
            found = [shown.by_handle.get(h) for h in group.handles]
            if any(i is None for i in found):
                self._inc("nora_note_merge_total", outcome="unknown_ordinal")
                continue
            distinct = tuple(dict.fromkeys(i for i in found if i is not None))
            if len(distinct) < 2:
                self._inc("nora_note_merge_total", outcome="singleton")
                continue
            resolved.append((distinct, group.why))

        # OVERLAP is a property of the whole answer: a note named by two
        # groups refuses every group that names it. No transitive closure.
        named = collections.Counter(i for ids, _ in resolved for i in ids)
        written = 0
        for ids, why in resolved:
            members = sorted(
                (shown.notes[i] for i in ids),
                key=lambda n: (n.created_at, n.id),
            )
            survivor, duplicates = members[0], members[1:]
            refusal: str | None = None
            if any(named[i] > 1 for i in ids):
                refusal = "overlap"
            elif len({n.established_in for n in members}) > 1:
                refusal = "cross_room"
            elif not any(i in shown.changed for i in ids):
                refusal = "not_changed"
            if refusal is not None:
                for duplicate in duplicates:
                    await self._record(
                        key, refusal, survivor, duplicate, why, provenance,
                        now_ms=now_ms,
                    )
                continue
            written += await self._fold(
                key, survivor, duplicates, why, shown=shown,
                provenance=provenance, now_ms=now_ms,
            )

        # LAST, and with the RENDER instant (41 §7.4): a note written while
        # the model was thinking is newer than this stamp and is seen next
        # pass. A commit cancelled before here leaves the stamp where it was,
        # so the same change is judged again rather than lost.
        await self._clock_store.record_fire(
            adaptor_name=self._pass_clock, key=key, at_ms=shown.render_ms,
        )
        return written

    async def _fold(
        self, key: str, survivor: Note, duplicates: Sequence[Note], why: str,
        *, shown: _Shown, provenance: Proposal | None, now_ms: int,
    ) -> int:
        """Fold `duplicates` into `survivor` one at a time, each its own
        transaction, stopping at the first that does not land (41 §7.7)."""
        folded = 0
        survivor_head = shown.heads[survivor.id]
        # The survivor's activity AS SHOWN — after a first fold its head is
        # this pass's own revision, which says nothing about the room.
        shown_written_at = survivor_head.written_at
        survivor_note = survivor
        # In `shadow` nothing is written, so the next duplicate's `too_long`
        # check reads the body the previous fold WOULD have left.
        survivor_body = survivor_head.body
        for duplicate in duplicates:
            current: dict[str, tuple[Note, NoteRevision] | None] = {}
            for note_id in (survivor.id, duplicate.id):
                current[note_id] = await self._notes.read(unit=key, note_id=note_id)
            s_now, d_now = current[survivor.id], current[duplicate.id]
            if (
                s_now is None or d_now is None
                or s_now[0].retired_at is not None
                or d_now[0].retired_at is not None
            ):
                await self._record(
                    key, "not_open", survivor, duplicate, why, provenance,
                    now_ms=now_ms,
                )
                return folded
            if (
                s_now[1].id != survivor_head.id
                or d_now[1].id != shown.heads[duplicate.id].id
            ):
                await self._record(
                    key, "stale", survivor, duplicate, why, provenance,
                    now_ms=now_ms,
                )
                return folded
            d_note, d_head = d_now
            body = merged_body(
                survivor_body=survivor_body, duplicate=d_note,
                duplicate_body=d_head.body,
            )
            if len(body) > BODY_MAX_CHARS:
                await self._record(
                    key, "too_long", survivor, duplicate, why, provenance,
                    now_ms=now_ms,
                )
                return folded
            anchor = latest_anchor(survivor_note.anchor_date, d_note.anchor_date)
            if self._mode == "shadow":
                await self._record(
                    key, "would_merge", survivor, duplicate, why, provenance,
                    now_ms=now_ms,
                )
                survivor_body = body
                survivor_note = replace(survivor_note, anchor_date=anchor)
                continue
            try:
                survivor_head = await self._notes.merge_into(
                    unit=key, survivor_id=survivor.id,
                    survivor_head=survivor_head.id,
                    duplicate_id=duplicate.id, duplicate_head=d_head.id,
                    body=body, anchor_date=anchor,
                    revisit_at=default_revisit_at(
                        anchor_date=anchor, now_ms=now_ms,
                    ),
                    # A duplicate the room added to more recently than the
                    # survivor is evidence the room is still talking about
                    # it; an older one leaves the survivor's strikes (§7.7).
                    reset_strikes=d_head.written_at > shown_written_at,
                    established_by=_established_by(provenance, key, now_ms),
                    now_ms=now_ms,
                    instruction_sha256=(
                        provenance.instruction_sha256 if provenance else None
                    ),
                    model=provenance.model if provenance else None,
                    on_reminder_outcome=lambda outcome: self._inc(
                        "nora_note_merge_proactive_total", outcome=outcome,
                    ),
                )
            except StaleNoteHeadError:
                await self._record(
                    key, "stale", survivor, duplicate, why, provenance,
                    now_ms=now_ms,
                )
                return folded
            survivor_body = body
            survivor_note = replace(survivor_note, anchor_date=anchor)
            await self._record(
                key, "merged", survivor, duplicate, why, provenance,
                now_ms=now_ms,
            )
            folded += 1
        return folded

    async def _record(
        self, key: str, outcome: str, survivor: Note, duplicate: Note,
        why: str, provenance: Proposal | None, *, now_ms: int,
    ) -> None:
        """One pair's outcome: the counter, always, and the decision row."""
        self._inc("nora_note_merge_total", outcome=outcome)
        log.info(
            "note-merge %s: %s -> %s (unit=%s, mode=%s)",
            outcome, duplicate.id, survivor.id, key, self._mode,
        )
        await self._notes.record_merge_decision(decision=NoteMergeDecision(
            id=str(uuid.uuid4()), unit=key, mode=self._mode, outcome=outcome,
            survivor_id=survivor.id, duplicate_id=duplicate.id,
            survivor_title=survivor.title, duplicate_title=duplicate.title,
            reason=why[:MERGE_REASON_MAX_CHARS],
            established_by=_established_by(provenance, key, now_ms),
            ts=now_ms,
        ))


def _established_by(provenance: Proposal | None, key: str, now_ms: int) -> str:
    """The run's own stamp when there is one — it is what makes a fold's
    revision recognisably this adaptor's (`OWN_REVISION_PREFIX`)."""
    if provenance is not None:
        return provenance.established_by
    return f"{OWN_REVISION_PREFIX}{key}:{now_ms}"


def _changed_since(
    note: Note, chain: Sequence[NoteRevision], *, last_ms: int | None,
) -> bool:
    """41 §7.4's change predicate. `None` — no pass has ever rendered for this
    unit — makes every open note new."""
    if last_ms is None or note.created_at > last_ms:
        return True
    return any(
        r.written_at > last_ms and not r.established_by.startswith(OWN_REVISION_PREFIX)
        for r in chain
    )


def _room_names(
    notes: Sequence[Note], label_for: Callable[[str], str | None],
) -> dict[str, str]:
    """What each birth room is called in this render: the operator's label,
    or `room N` — distinct per room, so two unlabelled rooms never read as
    one. Never the conversation id (R-18)."""
    out: dict[str, str] = {}
    unnamed = 0
    for note in notes:
        room = note.established_in
        if room in out:
            continue
        label = label_for(room)
        if label:
            out[room] = label
        else:
            unnamed += 1
            out[room] = f"room {unnamed}"
    return out


def _as_date(yyyymmdd: int) -> date | None:
    try:
        return date(yyyymmdd // 10000, (yyyymmdd // 100) % 100, yyyymmdd % 100)
    except ValueError:
        return None


def _today_block(today: date) -> str:
    return ContextBlock(name="today", body=escape(f"{today:%Y-%m-%d %a}")).render()


def _anchor_attrs(anchor_date: int | None, today: date) -> tuple[tuple[str, str], ...]:
    if anchor_date is None:
        return ()
    day = _as_date(anchor_date)
    if day is None:
        return (("anchor", str(anchor_date)),)
    delta = (day - today).days
    reading = (
        "today" if delta == 0
        else "tomorrow" if delta == 1
        else "yesterday" if delta == -1
        else f"in {delta} days" if delta > 0
        else f"{-delta} days ago"
    )
    return (("anchor", f"{day:%Y-%m-%d %a}"), ("anchor_is", reading))


def _note_block(
    handle: str, note: Note, head: NoteRevision, *, room: str, now: datetime,
    today: date, changed: str | None, excerpt: int,
) -> str:
    """One open note. Every value is escaped (R-13): titles and bodies are
    model-authored from what the group typed."""
    attrs: tuple[tuple[str, str], ...] = (
        ("id", handle),
        ("title", note.title),
        ("room", room),
        ("written", age(note.created_at, now)),
        ("revised", age(head.written_at, now)),
        *_anchor_attrs(note.anchor_date, today),
    )
    if changed is not None:
        attrs += (("changed", changed),)
    body = head.body.strip()
    if len(body) > excerpt:
        shown_body = (
            f"body (its first {excerpt} characters; it runs longer):\n"
            f"{escape(body[:excerpt])}"
        )
    else:
        shown_body = f"body:\n{escape(body)}"
    return ContextBlock(
        name="open_note", attrs=attrs,
        body=(
            f"about: {escape(note.description)}\n"
            f"undecided: {escape(note.uncertainty)}\n"
            f"settled when: {escape(note.closing_condition)}\n"
            f"{shown_body}"
        ),
    ).render()
