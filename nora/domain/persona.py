"""Persona: one account per entity, kept as typed lines (design/unified-memory/45).

An account is who an entity is to Nora, in three parts: **identity** (who or
what it is), **character** (what it is like) and **hypotheses** (why it is
like that). Each line is one free-text claim; its part, kind, reasoning mode,
citations and state are typed and checked, and what it says is not
enumerated (45 §0: the structure is the container, the content is prose).

This module is the write's one judge. `plan_write` takes the account's held
lines, the ops a writer asks for and what that writer was shown, and returns
either every refusal (nothing is applied) or the exact rows to insert. The
store (`nora.infra.sql.persona`) runs it inside the write's own transaction,
so no row reaches `persona_line` without passing it.

The reasoning-mode rules are 45 §3's table, enforced here at write time:

- `told` (identity): the message it was said in, by `told_by`; an
  `instruction` only by its subject.
- `deduced` (identity, character): at least 2 premises (citations plus
  `draws_on` lines), none `abduced`, every premise line current.
- `induced` (character): at least 3 instances on at least 2 calendar days
  (HKT), or one Rollup aggregate key.
- `abduced` (hypothesis): at least one current `induced` trait it explains
  (`draws_on`), a prediction, its observable and a window end still ahead.

Precedence when two lines conflict (45 §3): told by the subject > told by
someone else > deduced > induced. A writer names the conflict
(`conflicts_with`); code decides who wins, both lines stay, and the lower is
`outranked`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal, Protocol

__all__ = [
    "ACTOR_IDENTITY_KINDS",
    "CAPS",
    "COLLECTIVE_ENTITY_KINDS",
    "COLLECTIVE_IDENTITY_KINDS",
    "DEDUCED_MIN_PREMISES",
    "ENTITY_KINDS",
    "HELD_STATES",
    "INDUCED_MIN_DAYS",
    "INDUCED_MIN_INSTANCES",
    "KINDS_BY_PART",
    "LINE_MAX_CHARS",
    "MODES",
    "MODES_BY_PART",
    "PARTS",
    "PERSONA_HKT",
    "PERSONA_OUTCOMES",
    "PERSONA_REFUSALS",
    "ROLLUP_AGGREGATE_KINDS",
    "STATES",
    "TRAIT_DECAY_MS",
    "VERDICTS",
    "Account",
    "AddOp",
    "KeepOp",
    "NewLine",
    "PersonaCitable",
    "PersonaLine",
    "PersonaOp",
    "PersonaPlan",
    "PersonaRefusal",
    "PersonaWriteResult",
    "PersonaWriter",
    "RetireOp",
    "ReviseOp",
    "Said",
    "handles_for",
    "parse_ops",
    "plan_write",
    "rank",
    "rollup_citation",
]

PARTS: tuple[str, ...] = ("identity", "character", "hypothesis")

#: The identity kinds an ACTOR's account holds (a human, Nora, an agent).
ACTOR_IDENTITY_KINDS = frozenset({"name", "role", "relationship", "attribute", "instruction"})
#: The identity kinds a COLLECTIVE's account holds (a room, the unit).
COLLECTIVE_IDENTITY_KINDS = frozenset({"name", "purpose", "norm"})

KINDS_BY_PART: Mapping[str, frozenset[str]] = {
    "identity": ACTOR_IDENTITY_KINDS | COLLECTIVE_IDENTITY_KINDS,
    "character": frozenset({"trait"}),
    "hypothesis": frozenset({"explanation"}),
}

MODES: tuple[str, ...] = ("told", "deduced", "induced", "abduced")

#: 45 §3's "allowed in" column, the one amended table for Persona.
MODES_BY_PART: Mapping[str, frozenset[str]] = {
    "identity": frozenset({"told", "deduced"}),
    "character": frozenset({"deduced", "induced"}),
    "hypothesis": frozenset({"abduced"}),
}

ENTITY_KINDS: tuple[str, ...] = ("human", "nora", "agent", "room", "unit")
COLLECTIVE_ENTITY_KINDS = frozenset({"room", "unit"})

#: Every state a line may be in (45 §1). `disputed` has one meaning: awaiting
#: weighing. `outranked` is a line that lost on precedence.
STATES: tuple[str, ...] = (
    "current", "disputed", "outranked", "hidden", "superseded", "retired", "refuted",
)
#: The states a line is HELD in: it occupies a cap slot and a writer may act
#: on it. The rest are terminal for this writer.
HELD_STATES = frozenset({"current", "disputed", "outranked"})

#: A hypothesis's verdict, set by a judge other than the writer (45 §3).
VERDICTS: tuple[str, ...] = ("open", "confirmed", "refuted", "expired", "unknown")

#: One claim, prose, at most this long (45 §1).
LINE_MAX_CHARS = 300

#: Current lines per part (45 §2). Starting values, to be sized on what the
#: shadow sessions produce (00 «Sequence» step 6). For `hypothesis` the count
#: is lines whose prediction window is still open, whatever their state.
CAPS: Mapping[str, int] = {"identity": 25, "character": 5, "hypothesis": 3}

#: A trait unsupported this long is retired at the next session (45 §2).
TRAIT_DECAY_MS = 60 * 24 * 3600 * 1000

DEDUCED_MIN_PREMISES = 2
INDUCED_MIN_INSTANCES = 3
INDUCED_MIN_DAYS = 2

#: The clock "2 calendar days" is counted on: Hong Kong's, fixed, never the
#: host's (45 §4 prerequisite 3).
PERSONA_HKT = timezone(timedelta(hours=8), "HKT")

#: The Rollup kinds whose aggregate key a line may cite (`rollup:<kind>:<subject>`).
#: `response` is cited per episode instead (`response:<send id>`).
ROLLUP_AGGREGATE_KINDS = frozenset({"colocation", "outing", "daybook", "tie"})

#: Every value `nora_persona_session_total{outcome}` may carry: `written` (a
#: session's write landed rows), `kept` (it settled with none), `refused` (one
#: call of a verb was refused), `unsettled` (it ended without settling, so the
#: batch is held) and `abandoned` (the third unsettled run of one batch).
PERSONA_OUTCOMES = frozenset({"written", "kept", "refused", "unsettled", "abandoned"})

#: Why a write was refused, as the `reason` the writer reads.
PERSONA_REFUSALS = frozenset({
    "malformed", "unknown_line", "line_twice", "part_kind", "mode_part",
    "empty", "too_long", "ids_in_text", "identifier", "coordinate",
    "uncited", "unresolved_citation", "line_in_citations", "unlisted_room",
    "told_needs_message", "told_by_missing", "told_by_not_speaker",
    "instruction_not_subject", "told_by_unexpected",
    "deduced_premises", "premise_abduced", "premise_not_current",
    "induced_instances", "induced_one_day",
    "abduced_prediction", "abduced_observable", "abduced_window", "abduced_window_closed",
    "abduced_explains_nothing", "prediction_unexpected", "draws_on_unexpected",
    "same_rank", "conflict_hypothesis", "over_cap", "retire_why",
})

_EPISODE = re.compile(r"response:(\d+)")
_ROLLUP = re.compile(r"rollup:([a-z]+):(\S+)")
_HANDLE = re.compile(r"P\d+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NUMBER = re.compile(r"(?<![\d.])\d{3,}(?![\d.])")


def rollup_citation(kind: str, subject: str) -> str:
    """How a line cites one Rollup aggregate: `rollup:<kind>:<subject>`, the
    subject as `nora.domain.rollup.Citation` spells it (`unit|a,b` for a tie)."""
    return f"rollup:{kind}:{subject}"


@dataclass(frozen=True, slots=True)
class Said:
    """One citable Record row or response episode, as the validator checks it
    (45 §4 prerequisite 1: `Citable` widened from an id to `(ts, person, room)`).

    `ts` is epoch ms; `person_id` is who said it (`None` for an episode, which is
    her send and what followed); `room` is the room's `primary_id`."""

    ts: int
    person_id: str | None
    room: str


@dataclass(frozen=True, slots=True)
class PersonaCitable:
    """What one writer may cite, built by CODE from what it handed the model.

    `messages` are Record rows by id, `episodes` Rollup response episodes by
    her send's id, `rollup_keys` the aggregate keys it was shown
    (`rollup_citation`). `listed_rooms` are the rooms the account's subject is
    listed in: evidence about a person counts only if said in one of them (35
    §3); `None` for an account with no such rule (a collective, Nora)."""

    messages: Mapping[int, Said] = field(default_factory=dict)
    episodes: Mapping[int, Said] = field(default_factory=dict)
    rollup_keys: frozenset[str] = frozenset()
    listed_rooms: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class Account:
    """The account a write is for: its key (`nora.domain.culture`'s key forms),
    what kind of entity it is, its unit, and its subject's person id (a human
    account's; `None` otherwise)."""

    key: str
    entity_kind: str
    unit: str
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class PersonaLine:
    """One row of `persona_line`. Append-only: a change is a new row whose
    `supersedes` names this one, so the account is the chain heads."""

    id: str
    persona_key: str
    part: str
    kind: str
    text: str
    mode: str
    citations: tuple[str, ...]
    state: str
    unit: str
    written_at: int
    written_by_session: str
    draws_on: tuple[str, ...] = ()
    told_by: str | None = None
    prediction: str | None = None
    prediction_observable: str | None = None
    prediction_window_end: int | None = None
    verdict: str | None = None
    verdict_by: str | None = None
    verdict_at: int | None = None
    supersedes: str | None = None
    support_count: int = 1
    last_supported_at: int | None = None
    exclude_persons: tuple[str, ...] = ()
    as_of: int | None = None


#: A row `plan_write` asks the store to insert; the same shape as a stored line.
NewLine = PersonaLine


@dataclass(frozen=True, slots=True)
class AddOp:
    part: str
    kind: str
    text: str
    mode: str
    citations: tuple[str, ...]
    draws_on: tuple[str, ...] = ()
    told_by: str | None = None
    prediction: str | None = None
    prediction_observable: str | None = None
    prediction_window_end: int | None = None
    conflicts_with: str | None = None
    op: Literal["add"] = "add"


@dataclass(frozen=True, slots=True)
class KeepOp:
    line: str
    citations: tuple[str, ...] = ()
    op: Literal["keep"] = "keep"


@dataclass(frozen=True, slots=True)
class ReviseOp:
    line: str
    citations: tuple[str, ...]
    text: str | None = None
    draws_on: tuple[str, ...] | None = None
    mode: str | None = None
    conflicts_with: str | None = None
    op: Literal["revise"] = "revise"


@dataclass(frozen=True, slots=True)
class RetireOp:
    line: str
    why: str
    op: Literal["retire"] = "retire"


PersonaOp = AddOp | KeepOp | ReviseOp | RetireOp


@dataclass(frozen=True, slots=True)
class PersonaRefusal:
    """One refused op: the `reason` (a `PERSONA_REFUSALS` member), a `detail`
    the writer can act on, and which op (its index in the call) it was."""

    reason: str
    detail: str
    op_index: int | None = None


@dataclass(frozen=True, slots=True)
class PersonaPlan:
    """What a write does when every op passed: the rows to insert, in order.
    `refusals` is non-empty exactly when nothing may be applied."""

    inserts: tuple[NewLine, ...] = ()
    refusals: tuple[PersonaRefusal, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


@dataclass(frozen=True, slots=True)
class PersonaWriteResult:
    """What a `persona_write` came to: `ok` (committed at `revision`, the new
    rows' ids in `written`), `refused` (nothing applied; every reason in
    `refusals`) or `conflict` (the account moved since the writer read it, or
    a racing writer superseded the same line; nothing applied)."""

    status: Literal["ok", "refused", "conflict"]
    revision: int | None
    refusals: tuple[PersonaRefusal, ...] = ()
    written: tuple[str, ...] = ()


class PersonaWriter(Protocol):
    """The one Persona write (`nora.infra.sql.persona.SqlPersonaStore`):
    judged by `plan_write` inside its own transaction, every text through the
    store's own egress screen."""

    async def persona_write(
        self, *, account: Account, ops: Sequence[PersonaOp], citable: PersonaCitable,
        now_ms: int, written_by_session: str,
        apply_decay: bool, expected_revision: int | None = None,
    ) -> PersonaWriteResult: ...


def rank(line: PersonaLine, *, subject: str | None) -> int | None:
    """A line's precedence (45 §3): told by the subject 3, told by someone
    else 2, deduced 1, induced 0. A hypothesis has none: it is a question,
    never a fact that outranks one."""
    if line.part == "hypothesis":
        return None
    if line.mode == "told":
        return 3 if subject is not None and line.told_by == subject else 2
    return {"deduced": 1, "induced": 0}.get(line.mode)


def handles_for(heads: Sequence[PersonaLine]) -> dict[str, str]:
    """`P1`, `P2`, ... for the HELD lines, part by part then oldest first: the
    handles a writer acts on (a line's id is never shown). Deterministic, so
    the reader that shows them and the write that resolves them agree."""
    held = [h for h in heads if h.state in HELD_STATES]
    held.sort(key=lambda h: (PARTS.index(h.part), h.written_at, h.id))
    return {f"P{i}": h.id for i, h in enumerate(held, start=1)}


# --- parsing ------------------------------------------------------------------


def _strs(value: object) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(v, (str, int)) for v in value):
        return tuple(str(v).strip() for v in value)
    return None


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _window_end(value: object) -> int | None:
    """A prediction window's end as a writer may give it: epoch ms, or a
    `YYYY-MM-DD` day, which is the END of that day in HKT. Raises
    `ValueError` on anything else, which `parse_ops` refuses as malformed."""
    if value is None:
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        return int(value)
    day = date.fromisoformat(str(value).strip())
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), PERSONA_HKT)
    return int(end.timestamp() * 1000) - 1


def parse_ops(raw: object) -> tuple[tuple[PersonaOp, ...], tuple[PersonaRefusal, ...]]:
    """The ops a writer sent, typed, or why each one could not be read. A
    malformed op refuses the call like any other refusal."""
    if not isinstance(raw, (list, tuple)) or not raw:
        return (), (PersonaRefusal("malformed", "send a non-empty list of ops"),)
    ops: list[PersonaOp] = []
    refusals: list[PersonaRefusal] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            refusals.append(PersonaRefusal("malformed", "each op is an object", i))
            continue
        kind = item.get("op")
        citations = _strs(item.get("citations"))
        draws_on = _strs(item.get("draws_on"))
        if citations is None or draws_on is None:
            refusals.append(PersonaRefusal(
                "malformed", "`citations` and `draws_on` are lists of strings", i))
            continue
        try:
            if kind == "add":
                window = _window_end(item.get("prediction_window_end"))
                ops.append(AddOp(
                    part=str(item["part"]), kind=str(item["kind"]), text=str(item["text"]),
                    mode=str(item["mode"]), citations=citations, draws_on=draws_on,
                    told_by=_opt_str(item.get("told_by")),
                    prediction=_opt_str(item.get("prediction")),
                    prediction_observable=_opt_str(item.get("prediction_observable")),
                    prediction_window_end=window,
                    conflicts_with=_opt_str(item.get("conflicts_with")),
                ))
            elif kind == "keep":
                ops.append(KeepOp(line=str(item["line"]), citations=citations))
            elif kind == "revise":
                ops.append(ReviseOp(
                    line=str(item["line"]), citations=citations,
                    text=None if item.get("text") is None else str(item["text"]),
                    draws_on=None if item.get("draws_on") is None else draws_on,
                    mode=_opt_str(item.get("mode")),
                    conflicts_with=_opt_str(item.get("conflicts_with")),
                ))
            elif kind == "retire":
                ops.append(RetireOp(line=str(item["line"]), why=str(item.get("why") or "")))
            else:
                refusals.append(PersonaRefusal(
                    "malformed", "`op` is one of add, keep, revise, retire", i))
        except (KeyError, TypeError, ValueError) as exc:
            refusals.append(PersonaRefusal("malformed", f"op is missing or mistypes {exc}", i))
    return tuple(ops), tuple(refusals)


# --- the judge ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Evidence:
    """A line's citations, resolved against what the writer was shown."""

    said: tuple[Said, ...]  # messages and episodes, with their time and room
    messages: tuple[tuple[int, Said], ...]
    rollup_keys: tuple[str, ...]


def _resolve(
    citations: Sequence[str], citable: PersonaCitable, handles: Mapping[str, str],
) -> _Evidence | PersonaRefusal:
    said: list[Said] = []
    messages: list[tuple[int, Said]] = []
    keys: list[str] = []
    for raw in dict.fromkeys(c.strip() for c in citations):
        if _HANDLE.fullmatch(raw) or _UUID.fullmatch(raw) or raw in handles.values():
            return PersonaRefusal(
                "line_in_citations", f"{raw!r} is a line; a line another rests on goes in "
                "`draws_on`, never in `citations`")
        episode = _EPISODE.fullmatch(raw)
        rollup = _ROLLUP.fullmatch(raw)
        if raw.isdigit() and int(raw) in citable.messages:
            s = citable.messages[int(raw)]
            said.append(s)
            messages.append((int(raw), s))
        elif episode is not None and int(episode.group(1)) in citable.episodes:
            said.append(citable.episodes[int(episode.group(1))])
        elif (rollup is not None and rollup.group(1) in ROLLUP_AGGREGATE_KINDS
              and raw in citable.rollup_keys):
            keys.append(raw)
        else:
            return PersonaRefusal(
                "unresolved_citation", f"{raw!r} is not something you were shown; cite a "
                "message's `n`, a `response:<n>` episode or a `rollup:<kind>:<subject>` key")
    if citable.listed_rooms is not None:
        for s in said:
            if s.room not in citable.listed_rooms:
                return PersonaRefusal(
                    "unlisted_room", "a citation was said in a room this person is not "
                    "listed in, so it is not evidence about them")
    return _Evidence(said=tuple(said), messages=tuple(messages), rollup_keys=tuple(keys))


def _hkt_day(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).astimezone(PERSONA_HKT).date()


def _check_line(
    line: PersonaLine, *, account: Account, evidence: _Evidence,
    premises: Sequence[PersonaLine], screen: Callable[[str], str | None],
    shown_ids: frozenset[int], now_ms: int,
) -> PersonaRefusal | None:
    """Every rule a whole line must meet, whatever op produced it."""
    collective = account.entity_kind in COLLECTIVE_ENTITY_KINDS
    if line.kind not in KINDS_BY_PART.get(line.part, frozenset()) or (
        line.part == "identity"
        and line.kind not in (COLLECTIVE_IDENTITY_KINDS if collective else ACTOR_IDENTITY_KINDS)
    ):
        return PersonaRefusal("part_kind", f"`{line.kind}` is not a kind of `{line.part}` "
                              f"for a {account.entity_kind}")
    if line.mode not in MODES_BY_PART[line.part]:
        allowed = ", ".join(sorted(MODES_BY_PART[line.part]))
        return PersonaRefusal("mode_part", f"a `{line.part}` line is {allowed}, "
                              f"never `{line.mode}`")
    text = line.text.strip()
    if not text:
        return PersonaRefusal("empty", "the line says nothing")
    if len(text) > LINE_MAX_CHARS:
        return PersonaRefusal("too_long", f"one claim in at most {LINE_MAX_CHARS} "
                              f"characters; this is {len(text)}")
    if any(int(n) in shown_ids for n in _NUMBER.findall(text)):
        return PersonaRefusal("ids_in_text", "message numbers go in `citations`, never in "
                              "the line's text")
    for value in (text, line.prediction or "", line.prediction_observable or ""):
        offence = screen(value)
        if offence is not None:
            return PersonaRefusal(offence, "the line carries an identifier or a place's "
                                  "coordinates; say it without them")
    if not line.citations:
        return PersonaRefusal("uncited", "cite what the line rests on")
    if line.mode != "told" and line.told_by is not None:
        return PersonaRefusal("told_by_unexpected", "`told_by` is for a told line only")
    if line.mode not in ("deduced", "abduced") and line.draws_on:
        return PersonaRefusal("draws_on_unexpected", "`draws_on` is for a deduced line's "
                              "premises or the traits a hypothesis explains")
    if line.mode != "abduced" and (
        line.prediction is not None or line.prediction_observable is not None
        or line.prediction_window_end is not None
    ):
        return PersonaRefusal("prediction_unexpected", "a prediction belongs to a hypothesis")

    if line.kind == "instruction" and (
        account.subject is None or line.told_by != account.subject
    ):
        # 35 §2: an instruction is told by its subject, never someone else's
        # and never deduced (a deduced line has no `told_by`).
        return PersonaRefusal("instruction_not_subject", "an instruction is told by "
                              "the person it is about, never by someone else or deduced")
    if line.mode == "told":
        if not evidence.messages or evidence.rollup_keys or (
            len(evidence.said) != len(evidence.messages)
        ):
            return PersonaRefusal("told_needs_message", "a told line cites the message it "
                                  "was said in, and nothing else")
        if line.told_by is None:
            return PersonaRefusal("told_by_missing", "say who told it (`told_by`)")
        if any(s.person_id != line.told_by for _, s in evidence.messages):
            return PersonaRefusal("told_by_not_speaker", "`told_by` is not who said the "
                                  "cited message")
    elif line.mode == "deduced":
        if any(p.mode == "abduced" for p in premises):
            return PersonaRefusal("premise_abduced", "a hypothesis is not a premise")
        if any(p.state != "current" for p in premises):
            return PersonaRefusal("premise_not_current", "every premise line must be current")
        count = len(evidence.said) + len(evidence.rollup_keys) + len(premises)
        if count < DEDUCED_MIN_PREMISES:
            return PersonaRefusal("deduced_premises", f"a deduction rests on at least "
                                  f"{DEDUCED_MIN_PREMISES} premises; it has {count}")
    elif line.mode == "induced":
        if not evidence.rollup_keys:
            if len(evidence.said) < INDUCED_MIN_INSTANCES:
                return PersonaRefusal(
                    "induced_instances", f"an induction rests on at least "
                    f"{INDUCED_MIN_INSTANCES} instances or one Rollup aggregate key; it has "
                    f"{len(evidence.said)}")
            if len({_hkt_day(s.ts) for s in evidence.said}) < INDUCED_MIN_DAYS:
                return PersonaRefusal("induced_one_day", "its instances fall on one day "
                                      "(HKT); a pattern needs at least two")
    elif line.mode == "abduced":
        if not line.prediction:
            return PersonaRefusal("abduced_prediction", "a hypothesis carries a prediction")
        if not line.prediction_observable:
            return PersonaRefusal("abduced_observable", "name what would be seen if the "
                                  "prediction holds (`prediction_observable`)")
        if line.prediction_window_end is None:
            return PersonaRefusal("abduced_window", "say when the prediction's window "
                                  "closes (`prediction_window_end`)")
        if line.prediction_window_end <= now_ms:
            return PersonaRefusal("abduced_window_closed", "the window must end after now")
        if not any(p.part == "character" and p.mode == "induced" and p.state == "current"
                   for p in premises):
            return PersonaRefusal("abduced_explains_nothing", "a hypothesis explains at "
                                  "least one current induced trait; link it in `draws_on`")
    return None


def plan_write(
    *, account: Account, heads: Sequence[PersonaLine], ops: Sequence[PersonaOp],
    citable: PersonaCitable, now_ms: int, written_by_session: str,
    screen: Callable[[str], str | None], apply_decay: bool,
    aliases: Mapping[str, str] | None = None,
) -> PersonaPlan:
    """Judge one write against the account's chain heads and return what to
    insert, or every refusal. Nothing is repaired for the writer: one refused
    op refuses the call.

    `screen` is the R-18/R-28 egress screen (`nora.agent.dream_screen.offence`
    in production), returning a refusal reason or `None`. `apply_decay` is a
    session's settle: held traits unsupported for `TRAIT_DECAY_MS` that this
    call neither keeps with new evidence nor revises are retired by code (a
    bare `keep` is not new evidence).
    `aliases` maps every row id of the account to its chain head's id, so a
    revision that does not restate `draws_on` keeps premises revised since."""
    aliases = aliases or {}
    handles = handles_for(heads)
    by_id = {h.id: h for h in heads}
    shown_ids = frozenset(citable.messages)
    refusals: list[PersonaRefusal] = []
    touched: dict[str, int] = {}
    # The simulated heads after the call: id -> line. Each op replaces entries.
    after: dict[str, PersonaLine] = {h.id: h for h in heads}
    inserts: list[NewLine] = []

    def held(handle: str, i: int) -> PersonaLine | PersonaRefusal:
        line_id = handles.get(handle)
        if line_id is None:
            return PersonaRefusal("unknown_line", f"{handle!r} is not a line you were shown", i)
        if line_id in touched:
            return PersonaRefusal("line_twice", f"{handle} is acted on twice in one call", i)
        return by_id[line_id]

    def premises_of(draws: Sequence[str], i: int) -> list[PersonaLine] | PersonaRefusal:
        out = []
        for handle in dict.fromkeys(draws):
            line_id = handles.get(handle)
            if line_id is None:
                return PersonaRefusal("unknown_line", f"{handle!r} in `draws_on` is not a "
                                      "line you were shown", i)
            if line_id in touched and line_id not in after:
                return PersonaRefusal("premise_not_current", f"{handle} changes in this "
                                      "call; rest on it in a later one", i)
            out.append(by_id[line_id])
        return out

    def successor(old: PersonaLine, **changes: object) -> PersonaLine:
        return replace(
            old, id=str(uuid.uuid4()), supersedes=old.id, written_at=now_ms,
            written_by_session=written_by_session, **changes,  # type: ignore[arg-type]
        )

    def support(
        old: PersonaLine | None, ev: _Evidence, cites: Sequence[str],
    ) -> tuple[int, int]:
        """`support_count` and `last_supported_at` after this evidence: a
        Rollup aggregate supports as of now, a row as of when it was said."""
        if old is None:
            stamps = [s.ts for s in ev.said] + ([now_ms] if ev.rollup_keys else [])
            return max(1, len(set(cites))), max(stamps, default=now_ms)
        # Only evidence the line did not already rest on supports it anew:
        # re-citing what it cited is not re-evidence (45 §2's decay).
        fresh = set(cites) - set(old.citations)
        stamps = [_stamp(c) for c in fresh]
        stamps.append(old.last_supported_at or old.written_at)
        return old.support_count + len(fresh), max(stamps)

    def _stamp(citation: str) -> int:
        episode = _EPISODE.fullmatch(citation)
        if citation.isdigit() and int(citation) in citable.messages:
            return citable.messages[int(citation)].ts
        if episode is not None and int(episode.group(1)) in citable.episodes:
            return citable.episodes[int(episode.group(1))].ts
        return now_ms  # a Rollup aggregate supports as of now

    for i, op in enumerate(ops):
        if isinstance(op, RetireOp):
            target = held(op.line, i)
            if isinstance(target, PersonaRefusal):
                refusals.append(target)
                continue
            if not op.why.strip():
                refusals.append(PersonaRefusal("retire_why", "say why it is retired", i))
                continue
            touched[target.id] = i
            new = successor(target, state="retired")
            inserts.append(new)
            after.pop(target.id)
            after[new.id] = new
            continue

        if isinstance(op, KeepOp):
            target = held(op.line, i)
            if isinstance(target, PersonaRefusal):
                refusals.append(target)
                continue
            touched[target.id] = i
            if not op.citations:
                continue  # affirmed with no new evidence: nothing to write
            ev = _resolve(op.citations, citable, handles)
            if isinstance(ev, PersonaRefusal):
                refusals.append(replace(ev, op_index=i))
                continue
            cites = tuple(dict.fromkeys((*target.citations, *op.citations)))
            count, last = support(target, ev, op.citations)
            new = successor(target, citations=cites, support_count=count,
                            last_supported_at=last)
            inserts.append(new)
            after.pop(target.id)
            after[new.id] = new
            continue

        # add or revise: a whole line to judge
        old: PersonaLine | None = None
        if isinstance(op, ReviseOp):
            got = held(op.line, i)
            if isinstance(got, PersonaRefusal):
                refusals.append(got)
                continue
            old = got
            touched[old.id] = i
            if not op.citations:
                refusals.append(PersonaRefusal("uncited", "a revision cites what it rests on", i))
                continue
            # Unrestated premises carry over, each followed to its chain head:
            # a premise kept or revised since is the same line under a new row.
            carried = {aliases.get(lid, lid) for lid in old.draws_on}
            draws_handles = op.draws_on if op.draws_on is not None else tuple(
                h for h, lid in handles.items() if lid in carried)
            cites = op.citations
            candidate_fields = {
                "text": op.text.strip() if op.text is not None else old.text,
                "mode": op.mode or old.mode,
                "citations": tuple(dict.fromkeys(cites)),
            }
        else:
            draws_handles = op.draws_on
            cites = op.citations
        prem = premises_of(draws_handles, i)
        if isinstance(prem, PersonaRefusal):
            refusals.append(prem)
            continue
        ev = _resolve(cites, citable, handles)
        if isinstance(ev, PersonaRefusal):
            refusals.append(replace(ev, op_index=i))
            continue
        count, last = support(old, ev, cites)
        if old is not None:
            candidate = successor(
                old, draws_on=tuple(p.id for p in prem), state="current",
                support_count=count, last_supported_at=last, **candidate_fields,
            )
            if candidate.mode != "told":
                candidate = replace(candidate, told_by=None)
            if old.state == "outranked" and op.conflicts_with is None:
                # Rewording a line that lost on precedence does not win it back;
                # only a named conflict, which code ranks, can.
                candidate = replace(candidate, state="outranked")
        elif isinstance(op, AddOp):
            candidate = PersonaLine(
                id=str(uuid.uuid4()), persona_key=account.key, part=op.part, kind=op.kind,
                text=op.text.strip(), mode=op.mode, citations=tuple(dict.fromkeys(cites)),
                state="current", unit=account.unit, written_at=now_ms,
                written_by_session=written_by_session, draws_on=tuple(p.id for p in prem),
                told_by=op.told_by, prediction=op.prediction,
                prediction_observable=op.prediction_observable,
                prediction_window_end=op.prediction_window_end,
                verdict="open" if op.mode == "abduced" else None,
                support_count=count, last_supported_at=last,
            )
        bad = _check_line(candidate, account=account, evidence=ev, premises=prem,
                          screen=screen, shown_ids=shown_ids, now_ms=now_ms)
        if bad is not None:
            refusals.append(replace(bad, op_index=i))
            continue
        conflict = op.conflicts_with
        if conflict is not None:
            rival = held(conflict, i)
            if isinstance(rival, PersonaRefusal):
                refusals.append(rival)
                continue
            mine, theirs = (rank(candidate, subject=account.subject),
                            rank(rival, subject=account.subject))
            if mine is None or theirs is None:
                refusals.append(PersonaRefusal(
                    "conflict_hypothesis", "a hypothesis is a question; it never conflicts "
                    "with a fact", i))
                continue
            if mine == theirs:
                refusals.append(PersonaRefusal(
                    "same_rank", f"{conflict} rests on the same footing; revise or retire "
                    "it instead of adding a rival", i))
                continue
            touched[rival.id] = i
            if mine > theirs:
                lowered = successor(rival, state="outranked")
                inserts.append(lowered)
                after.pop(rival.id)
                after[lowered.id] = lowered
            else:
                candidate = replace(candidate, state="outranked")
        inserts.append(candidate)
        if old is not None:
            after.pop(old.id)
        after[candidate.id] = candidate

    if refusals:
        return PersonaPlan(refusals=tuple(refusals))

    if apply_decay:
        decayed: set[str] = set()
        for line in list(after.values()):
            fresh_add = line.id not in by_id and line.supersedes is None
            if (line.part != "character" or line.state not in HELD_STATES or fresh_add
                    or (line.last_supported_at or line.written_at) >= now_ms - TRAIT_DECAY_MS):
                continue
            after.pop(line.id)
            if line.id in by_id:  # untouched: its successor retires it
                gone = successor(line, state="retired")
                decayed.add(line.id)
            else:  # rewritten in this call with no newer evidence: retire that row
                gone = replace(line, state="retired")
                inserts[inserts.index(line)] = gone
                decayed.add(line.supersedes or line.id)
            if line.id in by_id:
                inserts.append(gone)
            after[gone.id] = gone
        for line in inserts:
            if line.state in HELD_STATES and decayed & set(line.draws_on):
                refusals.append(PersonaRefusal(
                    "premise_not_current", "a line this call adds rests on a trait that "
                    "decays at this session; re-evidence the trait or rest on another"))

    for part, cap in CAPS.items():
        def counted(lines: Sequence[PersonaLine], part: str = part) -> int:
            if part == "hypothesis":
                # A hypothesis holds its slot until its window closes, whatever
                # its state or verdict (45 §2), so retiring one early frees nothing.
                return sum(1 for x in lines if x.part == part
                           and (x.prediction_window_end or 0) > now_ms)
            return sum(1 for x in lines if x.part == part and x.state in HELD_STATES)
        before_n, after_n = counted(heads), counted(list(after.values()))
        if after_n > cap and after_n > before_n:
            hint = ("wait for a window to close" if part == "hypothesis"
                    else "retire a line in the same call")
            refusals.append(PersonaRefusal(
                "over_cap", f"the {part} cap is {cap} lines and this call leaves {after_n}; "
                f"{hint}"))
    if refusals:
        return PersonaPlan(refusals=tuple(refusals))
    return PersonaPlan(inserts=tuple(inserts))
