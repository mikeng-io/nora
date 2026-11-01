"""Profile: one account per entity, kept as typed lines (design/unified-memory/45).

An account is who an entity is to Nora, in three parts: **facts** (who or
what it is), **traits** (what it is like) and **guesses** (why it is
like that). Identity carries named typed values (75); traits and explanations
carry prose slots (47).
The part, kind, reasoning mode,
citations and state are typed and checked, and what it says is not
enumerated (45 §0: the structure is the container, the content is prose).

This module is the write's one judge. `plan_write` takes the account's held
lines, the ops a writer asks for and what that writer was shown, and returns
either every refusal (nothing is applied) or the exact rows to insert. The
store (`nora.infra.sql.profile`) runs it inside the write's own transaction,
so no row reaches `profile_line` without passing it.

The reasoning-mode rules are 45 §3's table, enforced here at write time:

- `told` (facts): the message it was said in, by `told_by`; an
  `instruction` only by its subject.
- `deduced` (facts, traits): at least 2 premises (citations plus
  `draws_on` lines), none `abduced`, every premise line current.
- `induced` (traits): at least 3 instances on at least 2 calendar days
  (HKT), resolved from distinct occasion roots; an aggregate is no exemption.

- `abduced` (guess): at least one current `induced` trait it explains
  (`draws_on`), a prediction, its observable and a window end still ahead.

A member's social item (`social:<network>:<item id>`) is evidence about its
author only, on the author's own account: a `deduced` premise or an `induced`
instance (on its `taken_at` day), never what a `told` line cites.

Precedence when two lines conflict (45 §3): told by the subject > told by
someone else > deduced > induced. A writer names the conflict
(`conflicts_with`); code decides who wins, both lines stay, and the lower is
`outranked`.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal, Protocol

from nora.domain.memory_item import TRAIT_SLOTS, MemoryItem, content_text, is_location_fact

__all__ = [
    "ACTOR_IDENTITY_KINDS",
    "CAPS",
    "COLLECTIVE_ENTITY_KINDS",
    "COLLECTIVE_IDENTITY_KINDS",
    "DEDUCED_MIN_PREMISES",
    "DISPUTE_KINDS",
    "ENTITY_KINDS",
    "HELD_STATES",
    "INDUCED_MIN_DAYS",
    "INDUCED_MIN_INSTANCES",
    "KINDS_BY_PART",
    "LINE_MAX_CHARS",
    "MODES",
    "MODES_BY_PART",
    "PARTS",
    "PROFILE_HKT",
    "PROFILE_OUTCOMES",
    "PROFILE_REFUSALS",
    "ROLLUP_AGGREGATE_KINDS",
    "STATES",
    "TRAIT_DECAY_MS",
    "VERDICTS",
    "Account",
    "AddOp",
    "DisputeResult",
    "KeepOp",
    "NewLine",
    "ProfileCitable",
    "ProfileDispute",
    "ProfileLine",
    "ProfileOp",
    "ProfilePlan",
    "ProfileRefusal",
    "ProfileView",
    "ProfileWriteResult",
    "ProfileWriter",
    "RetireOp",
    "ReviseOp",
    "Said",
    "countable_observable",
    "handles_for",
    "match_disputed_line",
    "parse_ops",
    "plan_write",
    "rank",
    "rollup_citation",
]

PARTS: tuple[str, ...] = ("facts", "traits", "guesses")

#: The identity kinds an ACTOR's account holds (a human, Nora, an agent).
ACTOR_IDENTITY_KINDS = frozenset({"name", "role", "relationship", "attribute", "instruction"})
#: The identity kinds a COLLECTIVE's account holds (a room, the unit).
COLLECTIVE_IDENTITY_KINDS = frozenset({"name", "purpose", "norm"})

KINDS_BY_PART: Mapping[str, frozenset[str]] = {
    "facts": ACTOR_IDENTITY_KINDS | COLLECTIVE_IDENTITY_KINDS,
    "traits": frozenset({"trait", *TRAIT_SLOTS}),
    "guesses": frozenset({"explanation"}),
}

MODES: tuple[str, ...] = ("told", "deduced", "induced", "abduced")

#: 45 §3's "allowed in" column, the one amended table for Profile.
MODES_BY_PART: Mapping[str, frozenset[str]] = {
    "facts": frozenset({"told", "deduced"}),
    "traits": frozenset({"deduced", "induced"}),
    "guesses": frozenset({"abduced"}),
}

ENTITY_KINDS: tuple[str, ...] = ("human", "nora", "agent", "room", "unit")
COLLECTIVE_ENTITY_KINDS = frozenset({"room", "unit"})

#: Every state a line may be in (45 §1). `disputed` has one meaning: awaiting
#: weighing. `outranked` is a line that lost on precedence.
STATES: tuple[str, ...] = (
    "current",
    "disputed",
    "outranked",
    "hidden",
    "superseded",
    "retired",
    "refuted",
)
#: The states a line is HELD in: it occupies a cap slot and a writer may act
#: on it. The rest are terminal for this writer.
HELD_STATES = frozenset({"current", "disputed", "outranked"})

#: A guess's verdict, set by a judge other than the writer (45 §3).
VERDICTS: tuple[str, ...] = ("open", "confirmed", "refuted", "expired", "unknown")

#: One claim, prose, at most this long (45 §1).
LINE_MAX_CHARS = 300

#: Current lines per part (45 §2). Starting values, to be sized on what the
#: shadow sessions produce (00 «Sequence» step 6). For `guess` the count
#: is lines whose prediction window is still open, whatever their state.
CAPS: Mapping[str, int] = {"traits": 9, "guesses": 3}

#: A trait unsupported this long is retired at the next session (45 §2).
TRAIT_DECAY_MS = 60 * 24 * 3600 * 1000

DEDUCED_MIN_PREMISES = 2
INDUCED_MIN_INSTANCES = 3
INDUCED_MIN_DAYS = 2

#: The clock "2 calendar days" is counted on: Hong Kong's, fixed, never the
#: host's (45 §4 prerequisite 3).
PROFILE_HKT = timezone(timedelta(hours=8), "HKT")

#: The Rollup kinds whose aggregate key a line may cite (`rollup:<kind>:<subject>`).
#: `response` is cited per episode instead (`response:<send id>`).
ROLLUP_AGGREGATE_KINDS = frozenset({"colocation", "outing", "daybook", "tie"})

#: Every value `nora_profile_session_total{outcome}` may carry: `written` (a
#: session's write landed rows), `kept` (it settled with none), `refused` (one
#: call of a verb was refused), `unsettled` (it ended without settling, so the
#: batch is held) and `abandoned` (the third unsettled run of one batch).
PROFILE_OUTCOMES = frozenset({"written", "kept", "refused", "unsettled", "abandoned"})

#: How a dispute was raised (35 §2): `dispute` («that's wrong») or `forget`
#: («forget that»). Either from the line's subject hides the line at once; a
#: `forget` from anyone else is refused, not weighed.
DISPUTE_KINDS: tuple[str, ...] = ("dispute", "forget")

#: Why a write was refused, as the `reason` the writer reads.
PROFILE_REFUSALS = frozenset(
    {
        "malformed",
        "unknown_line",
        "unknown_premise",
        "line_twice",
        "part_kind",
        "mode_part",
        "empty",
        "too_long",
        "ids_in_text",
        "identifier",
        "coordinate",
        "uncited",
        "unresolved_citation",
        "line_in_citations",
        "unlisted_room",
        "told_needs_message",
        "told_by_missing",
        "told_by_not_speaker",
        "instruction_not_subject",
        "told_by_unexpected",
        "deduced_premises",
        "premise_abduced",
        "premise_not_current",
        "induced_instances",
        "induced_one_day",
        "social_not_author",
        "abduced_prediction",
        "abduced_observable",
        "abduced_window",
        "abduced_window_closed",
        "abduced_explains_nothing",
        "prediction_unexpected",
        "draws_on_unexpected",
        "same_rank",
        "conflict_guess",
        "over_cap",
        "retire_why",
        "forget_not_subject",
        "verdict_not_open",
        "verdict_window_open",
        "invalid_fact",
        "content_slots",
        "support_unavailable",
    }
)

_EPISODE = re.compile(r"response:(\d+)")
_ROLLUP = re.compile(r"rollup:([a-z]+):(\S+)")
#: `social:<network>:<item id>`, `nora.domain.culture.social_citation`'s form.
_SOCIAL = re.compile(r"social:([a-z]+):(\S+)")
_HANDLE = re.compile(r"P\d+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NUMBER = re.compile(r"(?<![\d.])\d{3,}(?![\d.])")


def rollup_citation(kind: str, subject: str) -> str:
    """How a line cites one Rollup aggregate: `rollup:<kind>:<subject>`, the
    subject as `nora.domain.rollup.Citation` spells it (`unit|a,b` for a tie)."""
    return f"rollup:{kind}:{subject}"


@dataclass(frozen=True, slots=True)
class Said:
    """One citable Record row, response episode or social item, as the
    validator checks it (45 §4 prerequisite 1: `Citable` widened from an id to
    `(ts, person, room)`).

    `ts` is epoch ms (a social item's `taken_at`); `person_id` is who said it
    (`None` for an episode, which is her send and what followed; a social
    item's author); `room` is the room's `primary_id`, `None` for a social
    item, which was published, not said in a room."""

    ts: int
    person_id: str | None
    room: str | None
    occasion: str | None = None
    happened_at: int | None = None
    medium: str = "chat"
    location: bool = False


@dataclass(frozen=True, slots=True)
class ProfileCitable:
    """What one writer may cite, built by CODE from what it handed the model.

    `messages` are Record rows by id, `episodes` Rollup response episodes by
    her send's id, `rollup_keys` the aggregate keys it was shown
    (`rollup_citation`), `social` the subject's social items it was handed, by
    `network:item_id` (room `None`). `listed_rooms` are the rooms the account's
    subject is listed in: evidence about a person counts only if said in one of
    them (35 §3); `None` for an account with no such rule (a collective, Nora)."""

    messages: Mapping[int, Said] = field(default_factory=dict)
    episodes: Mapping[int, Said] = field(default_factory=dict)
    rollup_keys: frozenset[str] = frozenset()
    social: Mapping[str, Said] = field(default_factory=dict)
    listed_rooms: frozenset[str] | None = None
    # Explicit Record roots of widened non-location Rollup inputs. Persisted
    # alongside the aggregate citation so another projection is not a witness.
    rollup_roots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    rollup_as_of: Mapping[str, int] = field(default_factory=dict)
    interpretation_only: frozenset[int] = frozenset()
    sources: Mapping[str, Said] = field(default_factory=dict)
    social_subjects: frozenset[str] = frozenset()
    #: Admitted memory-graph relation ids this writer may rest a line on
    #: (27 §9: a line `draws_on` the relations it generalises, spelled
    #: `relation:<id>` in `draws_on`). Declared by the caller; an undeclared
    #: ref is `unknown_premise`. A graph relation is a PREMISE like a held
    #: line, never independent evidence — the line still cites Record/Rollup
    #: sources for its own support.
    relations: frozenset[str] = frozenset()


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
class ProfileLine:
    """One row of `profile_line`. Append-only: a change is a new row whose
    `supersedes` names this one, so the account is the chain heads."""

    id: str
    profile_key: str
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
    fact_key: str | None = None
    fact_value: object = None
    valid_from: int | None = None
    valid_until: int | None = None
    content: Mapping[str, str] = field(default_factory=dict)
    location: bool = False
    source_medium: tuple[str, ...] = ()
    strength: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Nullable/defaulted migration fields do not reclassify old history.
        # The typed location slot remains authoritative on every read.
        if is_location_fact(self.fact_key, self.fact_value):
            object.__setattr__(self, "location", True)

    @property
    def location_subjects(self) -> tuple[str, ...]:
        from nora.domain.culture import split_culture_key
        _, _, subject = split_culture_key(self.profile_key)
        if subject is not None:
            return (subject,)
        subjects = self.strength.get("location_subjects")
        return (tuple(p for p in subjects if isinstance(p, str))
                if isinstance(subjects, (list, tuple)) else ())

    @property
    def memory_item(self) -> MemoryItem:
        return MemoryItem(
            subject=self.profile_key, part=self.part, kind=self.fact_key or self.kind,
            content=({"fact_key": self.fact_key, "fact_value": self.fact_value}
                     if self.part == "facts" else self.content),
            mode=self.mode, citations=self.citations,
            said_at=self.as_of, happened_at=(self.last_supported_at
                if self.part == "traits" else None),
            source_medium=self.source_medium, location=self.location, strength=self.strength,
        )



#: A row `plan_write` asks the store to insert; the same shape as a stored line.
NewLine = ProfileLine


@dataclass(frozen=True, slots=True)
class ProfileView:
    """One entity's account as a prompt renders it (45 §5): who the block is
    of, the account's chain heads, and the AUTHORED identity lines — the
    `identity.toml` names and accounts — which are rendered, never written
    (45 §3: an authored line carries no mode).

    `entity` is `nora`, `room`, `unit` or `person`; `of` is how the block
    names the entity by id (person_id, room id, unit id or "nora").
    `lines` holds every chain head, whatever its state: which states render
    is the renderer's rule, not the reader's, so a view is the same thing the
    write path read. A line whose `exclude_persons` names somebody the
    rendering room lists is dropped by the assembler BEFORE the view is built,
    never by the renderer guessing."""

    entity: str
    of: str
    lines: tuple[ProfileLine, ...] = ()
    authored: tuple[str, ...] = ()


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
    fact_key: str | None = None
    fact_value: object = None
    valid_from: int | None = None
    valid_until: int | None = None
    content: Mapping[str, str] | None = None
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
    fact_key: str | None = None
    fact_value: object = None
    valid_from: int | None = None
    valid_until: int | None = None
    content: Mapping[str, str] | None = None
    op: Literal["revise"] = "revise"


@dataclass(frozen=True, slots=True)
class RetireOp:
    line: str
    why: str
    op: Literal["retire"] = "retire"


ProfileOp = AddOp | KeepOp | ReviseOp | RetireOp


@dataclass(frozen=True, slots=True)
class ProfileRefusal:
    """One refused op: the `reason` (a `PROFILE_REFUSALS` member), a `detail`
    the writer can act on, and which op (its index in the call) it was."""

    reason: str
    detail: str
    op_index: int | None = None


@dataclass(frozen=True, slots=True)
class ProfilePlan:
    """What a write does when every op passed: the rows to insert, in order.
    `refusals` is non-empty exactly when nothing may be applied."""

    inserts: tuple[NewLine, ...] = ()
    refusals: tuple[ProfileRefusal, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


@dataclass(frozen=True, slots=True)
class ProfileWriteResult:
    """What a `profile_write` came to: `ok` (committed at `revision`, the new
    rows' ids in `written`), `refused` (nothing applied; every reason in
    `refusals`) or `conflict` (the account moved since the writer read it, or
    a racing writer superseded the same line; nothing applied)."""

    status: Literal["ok", "refused", "conflict"]
    revision: int | None
    refusals: tuple[ProfileRefusal, ...] = ()
    written: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileDispute:
    """One `profile_dispute` row (35 §2): a dispute of an account's line is a
    Record item — who disputed, the claim as they put it, and the message they
    said it in. `line_id` is the held line the claim matched, or `None` when
    none matched unambiguously (the row still wakes the account's session,
    which weighs it there)."""

    id: str
    profile_key: str
    line_id: str | None
    disputed_by: str
    kind: str
    claim: str
    message_id: int
    unit: str
    written_at: int


@dataclass(frozen=True, slots=True)
class DisputeResult:
    """What a `record_dispute` came to. `effect`: `hidden` (the subject's own
    dispute or «forget that»: the line is hidden at once, 35 §2 rule 2),
    `disputed` (anyone else's: the line is marked, awaiting weighing), or
    `recorded` (no held line matched the claim, so only the row landed)."""

    status: Literal["ok", "refused", "conflict"]
    dispute_id: str | None = None
    effect: Literal["hidden", "disputed", "recorded"] | None = None
    refusal: ProfileRefusal | None = None


class ProfileWriter(Protocol):
    """The one Profile write (`nora.infra.sql.profile.SqlProfileStore`):
    judged by `plan_write` inside its own transaction, every text through the
    store's own egress screen. `record_dispute` is the turn's other door (35
    §2): a dispute or «forget that», matched and ruled on inside its own
    transaction the same way."""

    async def account_state(self, *, key: str) -> tuple[int, int | None]: ...

    async def account_heads(self, *, key: str) -> tuple[ProfileLine, ...]: ...

    async def profile_write(
        self, *, account: Account, ops: Sequence[ProfileOp], citable: ProfileCitable,
        now_ms: int, written_by_session: str,
        apply_decay: bool, expected_revision: int | None = None,
    ) -> ProfileWriteResult: ...

    async def record_dispute(
        self, *, account: Account, disputed_by: str, claim: str, kind: str,
        message_id: int, now_ms: int,
    ) -> DisputeResult: ...


def rank(line: ProfileLine, *, subject: str | None) -> int | None:
    """A line's precedence (45 §3): told by the subject 3, told by someone
    else 2, deduced 1, induced 0. A guess has none: it is a question,
    never a fact that outranks one."""
    if line.part == "guesses":
        return None
    if line.mode == "told":
        return 3 if subject is not None and line.told_by == subject else 2
    return {"deduced": 1, "induced": 0}.get(line.mode)


def handles_for(heads: Sequence[ProfileLine]) -> dict[str, str]:
    """`P1`, `P2`, ... for the HELD lines, part by part then oldest first: the
    handles a writer acts on (a line's id is never shown). Deterministic, so
    the reader that shows them and the write that resolves them agree."""
    held = [h for h in heads if h.state in HELD_STATES]
    held.sort(key=lambda h: (PARTS.index(h.part), h.written_at, h.id))
    return {f"P{i}": h.id for i, h in enumerate(held, start=1)}


#: A dispute's claim shorter than this matches a line only word for word:
#: a short quote matches too easily, and hiding the WRONG line is worse than
#: weighing an unmatched dispute at the next session.
DISPUTE_MATCH_MIN_CHARS = 12


def _normalise_claim(text: str) -> str:
    return " ".join(text.split()).casefold()


def match_disputed_line(claim: str, heads: Sequence[ProfileLine]) -> ProfileLine | None:
    """The one HELD line a dispute's claim names, or None.

    Exact after normalisation (case-folded, whitespace collapsed), else a
    unique containment either way. Zero matches and several matches are both
    None — the dispute is recorded and the account's session weighs it, and
    no line is hidden or marked on a guess."""
    wanted = _normalise_claim(claim)
    if not wanted:
        return None
    held = [h for h in heads if h.state in HELD_STATES]
    exact = [h for h in held if _normalise_claim(h.text) == wanted]
    if exact:
        return exact[0] if len(exact) == 1 else None
    if len(wanted) < DISPUTE_MATCH_MIN_CHARS:
        return None
    contained = [
        h for h in held
        if wanted in (text := _normalise_claim(h.text)) or text in wanted
    ]
    return contained[0] if len(contained) == 1 else None


def countable_observable(prediction_observable: str | None) -> str | None:
    """The Rollup key a guess's observable IS, when code can count it (45
    §3: «a count over a Rollup key where it is countable, else a described
    event»). The observable must be exactly one `rollup:<kind>:<subject>`
    citation; anything prose is the judge model's, never parsed out of it."""
    if prediction_observable is None:
        return None
    match = _ROLLUP.fullmatch(prediction_observable.strip())
    if match is None or match.group(1) not in ROLLUP_AGGREGATE_KINDS:
        return None
    return match.group(0)


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
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), PROFILE_HKT)
    return int(end.timestamp() * 1000) - 1


def parse_ops(raw: object) -> tuple[tuple[ProfileOp, ...], tuple[ProfileRefusal, ...]]:
    """The ops a writer sent, typed, or why each one could not be read. A
    malformed op refuses the call like any other refusal."""
    if not isinstance(raw, (list, tuple)) or not raw:
        return (), (ProfileRefusal("malformed", "send a non-empty list of ops"),)
    ops: list[ProfileOp] = []
    refusals: list[ProfileRefusal] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            refusals.append(ProfileRefusal("malformed", "each op is an object", i))
            continue
        kind = item.get("op")
        citations = _strs(item.get("citations"))
        draws_on = _strs(item.get("draws_on"))
        if citations is None or draws_on is None:
            refusals.append(
                ProfileRefusal("malformed", "`citations` and `draws_on` are lists of strings", i)
            )
            continue
        try:
            if kind == "add":
                window = _window_end(item.get("prediction_window_end"))
                ops.append(
                    AddOp(
                        part=str(item["part"]),
                        kind=str(item["kind"]),
                        text=str(item.get("text", "")),
                        mode=str(item["mode"]),
                        citations=citations,
                        draws_on=draws_on,
                        told_by=_opt_str(item.get("told_by")),
                        prediction=_opt_str(item.get("prediction")),
                        prediction_observable=_opt_str(item.get("prediction_observable")),
                        prediction_window_end=window,
                        conflicts_with=_opt_str(item.get("conflicts_with")),
                        fact_key=_opt_str(item.get("fact_key")),
                        fact_value=item.get("fact_value"),
                        valid_from=item.get("valid_from"),
                        valid_until=item.get("valid_until"),
                        content=item.get("content"),
                    )
                )
            elif kind == "keep":
                ops.append(KeepOp(line=str(item["line"]), citations=citations))
            elif kind == "revise":
                ops.append(
                    ReviseOp(
                        line=str(item["line"]),
                        citations=citations,
                        text=None if item.get("text") is None else str(item["text"]),
                        draws_on=None if item.get("draws_on") is None else draws_on,
                        mode=_opt_str(item.get("mode")),
                        conflicts_with=_opt_str(item.get("conflicts_with")),
                        fact_key=_opt_str(item.get("fact_key")),
                        fact_value=item.get("fact_value"),
                        valid_from=item.get("valid_from"),
                        valid_until=item.get("valid_until"),
                        content=item.get("content"),
                    )
                )
            elif kind == "retire":
                ops.append(RetireOp(line=str(item["line"]), why=str(item.get("why") or "")))
            else:
                refusals.append(
                    ProfileRefusal("malformed", "`op` is one of add, keep, revise, retire", i)
                )
        except (KeyError, TypeError, ValueError) as exc:
            refusals.append(ProfileRefusal("malformed", f"op is missing or mistypes {exc}", i))
    return tuple(ops), tuple(refusals)


# --- the judge ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Evidence:
    """A line's citations, resolved against what the writer was shown."""

    said: tuple[Said, ...]  # messages, episodes and social items, with their time
    messages: tuple[tuple[int, Said], ...]
    rollup_keys: tuple[str, ...]
    unrooted_rollups: int = 0


def _resolve(
    citations: Sequence[str], citable: ProfileCitable, handles: Mapping[str, str],
    *, subject: str | None,
) -> _Evidence | ProfileRefusal:
    said: list[Said] = []
    messages: list[tuple[int, Said]] = []
    items: list[Said] = []
    keys: list[str] = []
    expanded = list(citations)
    for cite in citations:
        expanded.extend(citable.rollup_roots.get(cite, ()))
    for raw in dict.fromkeys(c.strip() for c in expanded):
        if _HANDLE.fullmatch(raw) or _UUID.fullmatch(raw) or raw in handles.values():
            return ProfileRefusal(
                "line_in_citations", f"{raw!r} is a line; a line another rests on goes in "
                "`draws_on`, never in `citations`")
        if raw in citable.sources:
            said.append(citable.sources[raw])
            continue
        episode = _EPISODE.fullmatch(raw)
        rollup = _ROLLUP.fullmatch(raw)
        social = _SOCIAL.fullmatch(raw)
        if raw.isdigit() and int(raw) in citable.messages:
            s = citable.messages[int(raw)]
            said.append(s)
            if int(raw) not in citable.interpretation_only:
                messages.append((int(raw), s))
        elif episode is not None and int(episode.group(1)) in citable.episodes:
            if raw not in citable.rollup_roots:
                said.append(citable.episodes[int(episode.group(1))])
        elif (rollup is not None and rollup.group(1) in ROLLUP_AGGREGATE_KINDS
              and raw in citable.rollup_keys):
            keys.append(raw)
        elif (social is not None
              and f"{social.group(1)}:{social.group(2)}" in citable.social):
            s = citable.social[f"{social.group(1)}:{social.group(2)}"]
            items.append(s)
        else:
            return ProfileRefusal(
                "unresolved_citation", f"{raw!r} is not something you were shown; cite a "
                "message's `n`, a `response:<n>` episode, a `rollup:<kind>:<subject>` key "
                "or a `social:<network>:<item id>` item")
    for s in items:
        # An item has no room, so 35 §3's listed-room rule reads: it is
        # evidence about its author alone (never a person it tags or
        # mentions), and only where the author is listed in a room of this
        # unit.
        if ((subject is None and s.person_id not in citable.social_subjects)
                or (subject is not None and s.person_id != subject)):
            return ProfileRefusal(
                "social_not_author", "a social item is evidence only about the person who "
                "posted it; cite it on their account, never on anyone else's")
        if not citable.listed_rooms:
            return ProfileRefusal(
                "unlisted_room", "this person is listed in no room of this unit, so their "
                "social items are not evidence here")
    if any(not isinstance(s.ts, int) or (s.happened_at is not None
           and not isinstance(s.happened_at, int)) for s in (*said, *items)):
        return ProfileRefusal("support_unavailable", "citation dating is unavailable")
    if citable.listed_rooms is not None:
        for s in said:
            if (s.room not in citable.listed_rooms
                    and not (s.medium == "presence"
                             and (subject is None or s.person_id == subject)
                             and citable.listed_rooms)):
                return ProfileRefusal(
                    "unlisted_room", "a citation was said in a room this person is not "
                    "listed in, so it is not evidence about them")
    return _Evidence(said=tuple(said + items), messages=tuple(messages), rollup_keys=tuple(keys),
                     unrooted_rollups=sum(k not in citable.rollup_roots for k in keys))


def _occasions(said: Sequence[Said]) -> dict[str, Said]:
    """One earliest known event/assertion time per underlying occasion."""
    roots: dict[str, Said] = {}
    for source in said:
        key = source.occasion or f"{source.room}:{source.ts}"
        previous = roots.get(key)
        stamp = source.happened_at if source.happened_at is not None else source.ts
        if previous is None or stamp < (
            previous.happened_at if previous.happened_at is not None else previous.ts
        ):
            roots[key] = source
    return roots


def _hkt_day(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).astimezone(PROFILE_HKT).date()


def _check_told(evidence: _Evidence, told_by: str | None) -> ProfileRefusal | None:
    """A told line can only gain messages by the person who told it."""
    # A social item is its author's published words, but not a message
    # said to anyone in a room, so it is in `said` and refused here.
    if not evidence.messages or evidence.rollup_keys or (
        len(evidence.said) != len(evidence.messages)
    ):
        return ProfileRefusal("told_needs_message", "a told line cites the message it "
                              "was said in, and nothing else")
    if told_by is None:
        return ProfileRefusal("told_by_missing", "say who told it (`told_by`)")
    if any(s.person_id != told_by for _, s in evidence.messages):
        return ProfileRefusal("told_by_not_speaker", "`told_by` is not who said the "
                              "cited message")
    return None


def _check_line(
    line: ProfileLine,
    *,
    account: Account,
    evidence: _Evidence,
    premises: Sequence[ProfileLine],
    screen: Callable[[str], str | None],
    shown_ids: frozenset[int],
    shown_items: frozenset[str],
    now_ms: int,
    relation_premises: int = 0,
) -> ProfileRefusal | None:
    """Every rule a whole line must meet, whatever op produced it."""
    collective = account.entity_kind in COLLECTIVE_ENTITY_KINDS
    if line.kind not in KINDS_BY_PART.get(line.part, frozenset()) or (
        line.part == "facts"
        and line.kind not in (COLLECTIVE_IDENTITY_KINDS if collective else ACTOR_IDENTITY_KINDS)
    ):
        return ProfileRefusal("part_kind", f"`{line.kind}` is not a kind of `{line.part}` "
                              f"for a {account.entity_kind}")
    if collective and line.kind == "skill":
        return ProfileRefusal("part_kind", "skill applies only to actors")
    if line.kind in TRAIT_SLOTS or line.part == "guesses":
        required = (("statement", "context", *TRAIT_SLOTS[line.kind])
                    if line.kind in TRAIT_SLOTS else ("proposed_cause", "alternative"))
        allowed_slots = set(required) | ({"exceptions"} if line.part == "traits" else set())
        if not isinstance(line.content, Mapping) or set(line.content) - allowed_slots or any(
            not isinstance(line.content.get(k), str) or not line.content[k].strip()
            for k in required
        ) or any(not isinstance(v, str) or not v.strip() for v in line.content.values()):
            return ProfileRefusal("content_slots", "required non-empty prose slots: " +
                                  ", ".join(required) + "; explicit unknown is allowed")
    if line.mode not in MODES_BY_PART[line.part]:
        allowed = ", ".join(sorted(MODES_BY_PART[line.part]))
        return ProfileRefusal("mode_part", f"a `{line.part}` line is {allowed}, "
                              f"never `{line.mode}`")
    text = line.text.strip()
    if not text:
        return ProfileRefusal("empty", "the line says nothing")
    if len(text) > LINE_MAX_CHARS:
        return ProfileRefusal("too_long", f"one claim in at most {LINE_MAX_CHARS} "
                              f"characters; this is {len(text)}")
    if line.part == "facts":
        try:
            validate_fact(
                line.fact_key,
                line.fact_value,
                kind=line.kind,
                collective=collective,
                valid_from=line.valid_from,
                valid_until=line.valid_until,
            )
        except ValueError as exc:
            return ProfileRefusal("invalid_fact", str(exc))
    elif line.fact_key is not None or line.fact_value is not None:
        return ProfileRefusal("invalid_fact", "typed facts belong to facts")
    if any(int(n) in shown_ids or n in shown_items for n in _NUMBER.findall(text)):
        return ProfileRefusal("ids_in_text", "message and item numbers go in `citations`, "
                              "never in the line's text")
    for value in (
        text,
        format_fact(line.fact_key, line.fact_value),
        line.prediction or "",
        line.prediction_observable or "",
    ):
        offence = screen(value)
        if offence is not None:
            return ProfileRefusal(
                offence,
                "the line carries an identifier or a place's coordinates; say it without them",
            )
    if not line.citations:
        return ProfileRefusal("uncited", "cite what the line rests on")
    if line.mode != "told" and line.told_by is not None:
        return ProfileRefusal("told_by_unexpected", "`told_by` is for a told line only")
    if line.mode not in ("deduced", "abduced") and line.draws_on:
        return ProfileRefusal("draws_on_unexpected", "`draws_on` is for a deduced line's "
                              "premises or the traits a guess explains")
    if line.mode != "abduced" and (
        line.prediction is not None or line.prediction_observable is not None
        or line.prediction_window_end is not None
    ):
        return ProfileRefusal("prediction_unexpected", "a prediction belongs to a guess")

    if line.fact_key == "attribute" and account.entity_kind != "human":
        return ProfileRefusal("invalid_fact", "fallback attributes belong to humans")
    if line.fact_key in ("gender", "sex", "attribute") and line.mode != "told":
        return ProfileRefusal("invalid_fact", "this field requires an explicit statement")
    if line.kind == "instruction" and (
        account.subject is None or line.told_by != account.subject
    ):
        # 35 §2: an instruction is told by its subject, never someone else's
        # and never deduced (a deduced line has no `told_by`).
        return ProfileRefusal("instruction_not_subject", "an instruction is told by "
                              "the person it is about, never by someone else or deduced")
    if line.mode == "told":
        bad = _check_told(evidence, line.told_by)
        if bad is not None:
            return bad
    elif line.mode == "deduced":
        if any(p.mode == "abduced" for p in premises):
            return ProfileRefusal("premise_abduced", "a guess is not a premise")
        if any(p.state != "current" for p in premises):
            return ProfileRefusal("premise_not_current", "every premise line must be current")
        count = (len(evidence.said) + evidence.unrooted_rollups
                 + len(premises) + relation_premises)
        if count < DEDUCED_MIN_PREMISES:
            return ProfileRefusal("deduced_premises", f"a deduction rests on at least "
                                  f"{DEDUCED_MIN_PREMISES} premises; it has {count}")
    elif line.mode == "abduced":
        if not line.prediction:
            return ProfileRefusal("abduced_prediction", "a guess carries a prediction")
        if not line.prediction_observable:
            return ProfileRefusal("abduced_observable", "name what would be seen if the "
                                  "prediction holds (`prediction_observable`)")
        if line.prediction_window_end is None:
            return ProfileRefusal("abduced_window", "say when the prediction's window "
                                  "closes (`prediction_window_end`)")
        if line.prediction_window_end <= now_ms:
            return ProfileRefusal("abduced_window_closed", "the window must end after now")
        if not any(p.part == "traits" and p.mode == "induced" and p.state == "current"
                   for p in premises):
            return ProfileRefusal("abduced_explains_nothing", "a guess explains at "
                                  "least one current induced trait; link it in `draws_on`")
    if line.part == "traits":
        if evidence.unrooted_rollups or not evidence.said:
            return ProfileRefusal("support_unavailable",
                                  "Rollup support needs dated occasion roots")
        occasions = _occasions(evidence.said)
        if len(occasions) < INDUCED_MIN_INSTANCES:
            return ProfileRefusal("induced_instances", "a trait needs three distinct occasions")
        if len({_hkt_day(s.happened_at if s.happened_at is not None else s.ts)
                for s in occasions.values()}) < INDUCED_MIN_DAYS:
            return ProfileRefusal("induced_one_day", "a trait needs two HKT days")

    return None


def plan_write(
    *,
    account: Account,
    heads: Sequence[ProfileLine],
    ops: Sequence[ProfileOp],
    citable: ProfileCitable,
    now_ms: int,
    written_by_session: str,
    screen: Callable[[str], str | None],
    apply_decay: bool,
    aliases: Mapping[str, str] | None = None,
) -> ProfilePlan:
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
    # An item id may be composite (a story frame's `<digits>_<digits>`): each
    # numeric part counts on its own, as in `culture.check_revision`.
    shown_items = frozenset(
        part for key in citable.social
        for part in re.findall(r"\d{3,}", key.partition(":")[2])
    )
    refusals: list[ProfileRefusal] = []
    touched: dict[str, int] = {}
    # The simulated heads after the call: id -> line. Each op replaces entries.
    after: dict[str, ProfileLine] = {h.id: h for h in heads}
    inserts: list[NewLine] = []

    def held(handle: str, i: int) -> ProfileLine | ProfileRefusal:
        line_id = handles.get(handle)
        if line_id is None:
            return ProfileRefusal("unknown_line", f"{handle!r} is not a line you were shown", i)
        if line_id in touched:
            return ProfileRefusal("line_twice", f"{handle} is acted on twice in one call", i)
        return by_id[line_id]

    def premises_of(
        draws: Sequence[str], i: int,
    ) -> tuple[list[ProfileLine], list[str]] | ProfileRefusal:
        """Resolve `draws_on` handles to held lines, and `relation:<id>` refs
        to the writer's declared admitted relations. Returns the held lines
        and the relation ids."""
        lines = []
        rels = []
        for handle in dict.fromkeys(draws):
            if handle.startswith("relation:"):
                relation_id = handle[len("relation:"):]
                if relation_id not in citable.relations:
                    return ProfileRefusal(
                        "unknown_premise", f"{handle!r} is not a relation you "
                        "were shown and admitted", i)
                rels.append(relation_id)
                continue
            line_id = handles.get(handle)
            if line_id is None:
                return ProfileRefusal("unknown_line", f"{handle!r} in `draws_on` is not a "
                                      "line you were shown", i)
            if line_id in touched and line_id not in after:
                return ProfileRefusal("premise_not_current", f"{handle} changes in this "
                                      "call; rest on it in a later one", i)
            lines.append(by_id[line_id])
        return lines, rels

    def successor(old: ProfileLine, **changes: object) -> ProfileLine:
        return replace(
            old, id=str(uuid.uuid4()), supersedes=old.id, written_at=now_ms,
            written_by_session=written_by_session, **changes,  # type: ignore[arg-type]
        )

    def location_subjects(ev: _Evidence, held: Sequence[ProfileLine] = ()) -> list[str]:
        spatial = tuple(s for s in ev.said if s.location) or ev.said
        return sorted({s.person_id for s in spatial if s.person_id is not None}
                      | {p for line in held if line.location for p in line.location_subjects})

    def support(
        old: ProfileLine | None, ev: _Evidence, cites: Sequence[str],
    ) -> tuple[int, int]:
        """`support_count` and `last_supported_at` after this evidence: a
        resolved root supports as of its evidence time, never its rebuild time."""
        def roots(refs: Sequence[str]) -> set[str]:
            return {root for ref in refs for root in citable.rollup_roots.get(ref, (ref,))}
        supplied = roots(cites)
        if old is None:
            return max(1, len(supplied)), max((_stamp(c) for c in supplied), default=now_ms)
        # Expanded roots were persisted with the old aggregate. Expanding its
        # key against TODAY's projection would falsely treat new roots as old.
        old_roots = {c for c in old.citations if c not in citable.rollup_roots}
        if not old_roots:
            old_roots = roots(old.citations)  # legacy unexpanded aggregate: no guessed freshness
        fresh = supplied - old_roots
        stamps = [_stamp(c) for c in fresh]
        stamps.append(old.last_supported_at or old.written_at)
        return old.support_count + len(fresh), max(stamps)

    def _stamp(citation: str) -> int:
        if citation in citable.sources:
            source = citable.sources[citation]
            return source.happened_at if source.happened_at is not None else source.ts
        episode = _EPISODE.fullmatch(citation)
        social = _SOCIAL.fullmatch(citation)
        if citation.isdigit() and int(citation) in citable.messages:
            return citable.messages[int(citation)].ts
        if episode is not None and int(episode.group(1)) in citable.episodes:
            return citable.episodes[int(episode.group(1))].ts
        if social is not None and f"{social.group(1)}:{social.group(2)}" in citable.social:
            return citable.social[f"{social.group(1)}:{social.group(2)}"].ts
        return citable.rollup_as_of.get(citation, now_ms)

    def rooted(cites: Sequence[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*cites, *(r for c in cites
            for r in citable.rollup_roots.get(c, ())))))

    for i, op in enumerate(ops):
        if isinstance(op, RetireOp):
            target = held(op.line, i)
            if isinstance(target, ProfileRefusal):
                refusals.append(target)
                continue
            if not op.why.strip():
                refusals.append(ProfileRefusal("retire_why", "say why it is retired", i))
                continue
            touched[target.id] = i
            new = successor(target, state="retired")
            inserts.append(new)
            after.pop(target.id)
            after[new.id] = new
            continue

        if isinstance(op, KeepOp):
            target = held(op.line, i)
            if isinstance(target, ProfileRefusal):
                refusals.append(target)
                continue
            touched[target.id] = i
            if not op.citations:
                continue  # affirmed with no new evidence: nothing to write
            ev = _resolve(op.citations, citable, handles, subject=account.subject)
            if isinstance(ev, ProfileRefusal):
                refusals.append(replace(ev, op_index=i))
                continue
            if target.mode == "told":
                bad = _check_told(ev, target.told_by)
                if bad is not None:
                    refusals.append(replace(bad, op_index=i))
                    continue
            cites = rooted((*target.citations, *op.citations))
            count, last = support(target, ev, op.citations)
            people = location_subjects(ev, (target,))
            new = successor(target, citations=cites, support_count=count, last_supported_at=last,
                location=target.location or any(s.location for s in ev.said),
                strength={**target.strength, "location_subjects": people},
                source_medium=tuple(sorted(set(target.source_medium)
                                           | {s.medium for s in ev.said})))
            if target.part == "traits" and target.content:
                previous = target.strength.get("roots")
                if not isinstance(previous, Mapping):
                    refusals.append(ProfileRefusal("support_unavailable",
                        "re-evidence this legacy trait before adding support", i))
                    continue
                roots = dict(previous)
                for root, source in _occasions(ev.said).items():
                    timestamp = (source.happened_at if source.happened_at is not None
                                 else source.ts)
                    earlier = roots.get(root)
                    roots[root] = min(earlier, timestamp) if isinstance(earlier, int) else timestamp
                if ev.unrooted_rollups:
                    refusals.append(ProfileRefusal("support_unavailable",
                        "Rollup support needs dated occasion roots", i))
                    continue
                times = [t for t in roots.values() if isinstance(t, int)]
                new = replace(new, support_count=len(roots), last_supported_at=max(times),
                    strength={"occasions": len(roots), "days": len({_hkt_day(t) for t in times}),
                              "first": min(times), "last": max(times), "roots": roots,
                              "location_subjects": people})
            inserts.append(new)
            after.pop(target.id)
            after[new.id] = new
            continue

        # add or revise: a whole line to judge
        old: ProfileLine | None = None
        if isinstance(op, ReviseOp):
            got = held(op.line, i)
            if isinstance(got, ProfileRefusal):
                refusals.append(got)
                continue
            old = got
            touched[old.id] = i
            if not op.citations:
                refusals.append(ProfileRefusal("uncited", "a revision cites what it rests on", i))
                continue
            # Unrestated premises carry over, each followed to its chain head:
            # a premise kept or revised since is the same line under a new row.
            carried = {aliases.get(lid, lid) for lid in old.draws_on}
            draws_handles = (
                op.draws_on
                if op.draws_on is not None
                else tuple(h for h, lid in handles.items() if lid in carried)
                + tuple(c for c in carried if c.startswith("relation:"))
            )
            cites = op.citations
            candidate_fields = {
                "text": op.text.strip() if op.text is not None else old.text,
                "content": op.content if op.content is not None else old.content,
                "mode": op.mode or old.mode,
                "citations": rooted(cites),
                "fact_key": op.fact_key if op.fact_key is not None else old.fact_key,
                "fact_value": op.fact_value if op.fact_key is not None else old.fact_value,
                "valid_from": op.valid_from if op.fact_key is not None else old.valid_from,
                "valid_until": op.valid_until if op.fact_key is not None else old.valid_until,
            }
        else:
            draws_handles = op.draws_on
            cites = op.citations
        resolved = premises_of(draws_handles, i)
        if isinstance(resolved, ProfileRefusal):
            refusals.append(resolved)
            continue
        prem, prem_rels = resolved
        ev = _resolve(cites, citable, handles, subject=account.subject)
        if isinstance(ev, ProfileRefusal):
            refusals.append(replace(ev, op_index=i))
            continue
        count, last = support(old, ev, cites)
        if old is not None:
            candidate = successor(
                old,
                draws_on=tuple(p.id for p in prem)
                + tuple(f"relation:{r}" for r in prem_rels),
                state="current",
                as_of=max((x.ts for x in ev.said), default=None),
                support_count=count,
                last_supported_at=last,
                **candidate_fields,
            )
            if candidate.mode != "told":
                candidate = replace(candidate, told_by=None)
            if old.state == "outranked" and op.conflicts_with is None:
                # Rewording a line that lost on precedence does not win it back;
                # only a named conflict, which code ranks, can.
                candidate = replace(candidate, state="outranked")
        elif isinstance(op, AddOp):
            candidate = ProfileLine(
                id=str(uuid.uuid4()),
                profile_key=account.key,
                part=op.part,
                content=op.content if op.content is not None else {},
                kind=op.kind,
                text=op.text.strip(),
                mode=op.mode,
                citations=rooted(cites),
                state="current",
                unit=account.unit,
                written_at=now_ms,
                written_by_session=written_by_session,
                draws_on=tuple(p.id for p in prem)
                + tuple(f"relation:{r}" for r in prem_rels),
                told_by=op.told_by,
                prediction=op.prediction,
                prediction_observable=op.prediction_observable,
                prediction_window_end=op.prediction_window_end,
                verdict="open" if op.mode == "abduced" else None,
                support_count=count,
                last_supported_at=last,
                fact_key=op.fact_key,
                fact_value=op.fact_value,
                valid_from=op.valid_from,
                valid_until=op.valid_until,
                as_of=max((x.ts for x in ev.said), default=None),
            )
        sources = _occasions(ev.said)
        if candidate.part == "traits":
            earlier = old.strength.get("roots") if old is not None else None
            if isinstance(earlier, Mapping):
                for root, source in sources.items():
                    known = earlier.get(root)
                    actual = source.happened_at if source.happened_at is not None else source.ts
                    if isinstance(known, int) and known < actual:
                        sources[root] = replace(source, happened_at=known)
            ev = replace(ev, said=tuple(sources.values()))
        stamps = [s.happened_at if s.happened_at is not None else s.ts
                  for s in sources.values()]
        if not isinstance(candidate.content, Mapping) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in candidate.content.items()
        ):
            refusals.append(ProfileRefusal(
                "content_slots", "content is a prose slot object", i))
            continue
        if candidate.content:
            candidate = replace(candidate, text=content_text(candidate.content))
        candidate = replace(candidate,
            location=(candidate.location or candidate.fact_key == "residence"
                      or any(s.location for s in ev.said) or any(p.location for p in prem)
                      or any(candidate.content.get(k, "").lower() not in
                             ("", "unknown", "unspecified", "in the group", "online")
                             for k in ("setting", "where"))),
            support_count=len(sources) if candidate.part == "traits" else count,
            last_supported_at=max(stamps, default=last),
            source_medium=tuple(sorted({s.medium for s in ev.said})),
            strength={"occasions": len(sources), "days": len({_hkt_day(t) for t in stamps}),
                      "first": min(stamps, default=None), "last": max(stamps, default=None),
                      "location_subjects": location_subjects(
                          ev, (*prem, *((old,) if old is not None else ()))),
                      "roots": {key: s.happened_at if s.happened_at is not None else s.ts
                                for key, s in sources.items()}},
        )
        bad = _check_line(
            candidate,
            account=account,
            evidence=ev,
            premises=prem,
            relation_premises=len(prem_rels),
            screen=screen,
            shown_ids=shown_ids,
            shown_items=shown_items,
            now_ms=now_ms,
        )
        if bad is not None:
            refusals.append(replace(bad, op_index=i))
            continue
        # Rewording a typed fact is not another assertion of it. Preserve its
        # grounding and support; actual corroboration belongs in an explicit keep.
        # Premise/state changes still take the normal evidence-bearing path.
        if (old is not None and old.part == "facts" and old.fact_key is not None
                and old.state == candidate.state == "current"
                and op.conflicts_with is None
                and all(getattr(candidate, field) == getattr(old, field) for field in (
                    "fact_key", "fact_value", "valid_from", "valid_until", "mode", "told_by"))
                # JSON booleans and numbers are different facts (True == 1 in Python).
                and (candidate.fact_key != "attribute" or (
                     isinstance(candidate.fact_value, dict) and isinstance(old.fact_value, dict)
                     and isinstance(candidate.fact_value["value"], bool)
                     == isinstance(old.fact_value["value"], bool)))
                and set(candidate.draws_on) == {aliases.get(lid, lid) for lid in old.draws_on}):
            candidate = replace(
                candidate, citations=tuple(dict.fromkeys((*old.citations, *candidate.citations))),
                as_of=old.as_of, support_count=old.support_count,
                last_supported_at=old.last_supported_at,
            )
        if (old is not None and candidate.part == "facts" and old.as_of is not None
                and candidate.as_of is not None and candidate.as_of < old.as_of):
            refusals.append(ProfileRefusal(
                "invalid_fact", "older evidence cannot replace a newer assertion; "
                "add the historical claim instead", i))
            continue
        # The live shadow produced old claims as current facts. Keep their
        # history, but require explicit correction rather than rival singletons.
        if candidate.part == "facts" and candidate.fact_key in SINGLETON_FACTS:
            rivals = [h for h in after.values() if h.state == "current"
                      and h.id != (old.id if old is not None else None)
                      and h.fact_key == candidate.fact_key]
            named_rival = handles.get(op.conflicts_with or "")
            if any(h.id != named_rival for h in rivals):
                if all(candidate.as_of is not None and h.as_of is not None
                       and candidate.as_of < h.as_of for h in rivals):
                    candidate = replace(candidate, state="retired")
                else:
                    refusals.append(ProfileRefusal(
                        "invalid_fact", "revise the held field or name its conflict; "
                        "do not add competing current singleton values", i))
                    continue
        conflict = op.conflicts_with
        if conflict is not None:
            rival = held(conflict, i)
            if isinstance(rival, ProfileRefusal):
                refusals.append(rival)
                continue
            mine, theirs = (
                rank(candidate, subject=account.subject),
                rank(rival, subject=account.subject),
            )
            if mine is None or theirs is None:
                refusals.append(
                    ProfileRefusal(
                        "conflict_guess",
                        "a guess is a question; it never conflicts with a fact",
                        i,
                    )
                )
                continue
            if mine == theirs:
                refusals.append(
                    ProfileRefusal(
                        "same_rank",
                        f"{conflict} rests on the same footing; revise or retire "
                        "it instead of adding a rival",
                        i,
                    )
                )
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
        return ProfilePlan(refusals=tuple(refusals))

    if apply_decay:
        decayed: set[str] = set()
        for line in list(after.values()):
            fresh_add = line.id not in by_id and line.supersedes is None
            horizon_closed = (line.kind == "goal" and isinstance(line.valid_until, int)
                              and line.valid_until <= now_ms)
            if (line.part != "traits" or line.state not in HELD_STATES
                    or (fresh_add and not horizon_closed)
                    or (not horizon_closed
                        and (line.last_supported_at or line.written_at) >= now_ms - (
                        3 * TRAIT_DECAY_MS if line.kind in ("skill", "value")
                        else TRAIT_DECAY_MS))):
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
                refusals.append(ProfileRefusal(
                    "premise_not_current", "a line this call adds rests on a trait that "
                    "decays at this session; re-evidence the trait or rest on another"))

    for part, cap in CAPS.items():
        def counted(lines: Sequence[ProfileLine], part: str = part) -> int:
            if part == "guesses":
                # A guess holds its slot until its window closes, whatever
                # its state or verdict (45 §2), so retiring one early frees nothing.
                return sum(1 for x in lines if x.part == part
                           and (x.prediction_window_end or 0) > now_ms)
            return sum(1 for x in lines if x.part == part and x.state in HELD_STATES)
        before_n, after_n = counted(heads), counted(list(after.values()))
        if after_n > cap and after_n > before_n:
            hint = ("wait for a window to close" if part == "guesses"
                    else "retire a line in the same call")
            refusals.append(ProfileRefusal(
                "over_cap", f"the {part} cap is {cap} lines and this call leaves {after_n}; "
                f"{hint}"))
    if refusals:
        return ProfilePlan(refusals=tuple(refusals))
    return ProfilePlan(inserts=tuple(inserts))


SINGLETON_FACTS = frozenset({
    "name", "gender", "sex", "birthday", "residence", "languages",
    "occupation", "dietary_restrictions", "food_preferences",
})

FACT_KINDS = {
    "name": "name",
    "role": "role",
    "relationship": "relationship",
    "attribute": "attribute",
    "gender": "attribute",
    "sex": "attribute",
    "birthday": "attribute",
    "residence": "attribute",
    "languages": "attribute",
    "occupation": "attribute",
    "dietary_restrictions": "attribute",
    "food_preferences": "attribute",
    "instruction": "instruction",
    "purpose": "purpose",
    "norm": "norm",
}
_LIST_FACTS = frozenset({"languages", "dietary_restrictions", "food_preferences"})


def validate_fact(
    key: str | None,
    value: object,
    *,
    kind: str,
    collective: bool,
    valid_from: int | None = None,
    valid_until: int | None = None,
) -> None:
    """The single typed Identity contract. Absence is unknown; [] is explicit none.

    Evidence decides whether a claim is stable, never a prose parser. Legacy
    rows stay readable as history but new identity writes must name a value.
    """
    if key not in FACT_KINDS or FACT_KINDS[key] != kind:
        raise ValueError("fact_key must name a factual field matching kind")
    if collective and key not in COLLECTIVE_IDENTITY_KINDS:
        raise ValueError("a collective holds name, purpose or norm")
    if not collective and key in {"purpose", "norm"}:
        raise ValueError("purpose and norm belong to collectives")
    for stamp in (valid_from, valid_until):
        if stamp is not None and (type(stamp) is not int):
            raise ValueError("effective times are nullable epoch milliseconds")
        if stamp is not None:
            try:
                datetime.fromtimestamp(stamp / 1000, UTC)
            except (ValueError, OverflowError, OSError) as exc:
                raise ValueError("effective time is outside the supported calendar") from exc
    if valid_from is not None and valid_until is not None and valid_until <= valid_from:
        raise ValueError("valid_until must follow valid_from")
    if key == "attribute":
        if not isinstance(value, dict) or set(value) not in (
            {"name", "value"}, {"name", "value", "unit"}
        ):
            raise ValueError("attribute needs name, value and optional unit only")
        name = value["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError("attribute name is a nonempty normalized label")
        normalized = re.sub(r"[\s-]+", "_", name.strip().lower())
        if normalized in FACT_KINDS:
            raise ValueError("use the dedicated fact_key and its typed value")
        if name != normalized:
            raise ValueError("normalize attribute name to lowercase with underscores")
        body = value["value"]
        if normalized in {"age", "height"} and not (
            (type(body) is int or (type(body) is float and math.isfinite(body)))
            and isinstance(value.get("unit"), str) and value["unit"].strip()
        ):
            raise ValueError("age and height require a numeric value and source unit")
        if not (
            (isinstance(body, str) and bool(body.strip()))
            or type(body) in (bool, int)
            or (type(body) is float and math.isfinite(body))
            or (isinstance(body, list) and bool(body)
                and all(isinstance(x, str) and x.strip() for x in body))
        ):
            raise ValueError("attribute value is a nonempty string, finite number, boolean "
                             "or nonempty list of nonempty strings")
        if "unit" in value and (not isinstance(value["unit"], str) or not value["unit"].strip()):
            raise ValueError("attribute unit is a nonempty string")
        if len(format_fact(key, value)) > LINE_MAX_CHARS:
            raise ValueError("formatted fact exceeds the line budget")
    elif key in _LIST_FACTS:
        if not isinstance(value, list) or any(
            not isinstance(x, str) or not x.strip() for x in value
        ):
            raise ValueError("this field is a list of nonempty strings, [] means explicit none")
    elif key == "birthday":
        if (
            not isinstance(value, dict)
            or set(value) not in ({"month", "day"}, {"month", "day", "year"})
            or any(type(x) is not int for x in value.values())
        ):
            raise ValueError("birthday is month/day and optional year, all integers")
        try:
            date(value.get("year", 2000), value["month"], value["day"])
        except ValueError as exc:
            raise ValueError("birthday must be a calendar date") from exc
    elif key == "relationship":
        if (
            not isinstance(value, dict)
            or set(value) not in ({"relation", "person_id"}, {"relation", "unresolved_name"})
            or any(not isinstance(x, str) or not x.strip() for x in value.values())
        ):
            raise ValueError("relationship needs relation and person_id OR unresolved_name")
    elif not isinstance(value, str) or not value.strip():
        raise ValueError("this field is a nonempty string")
    if len(format_fact(key, value).partition(": ")[2]) > LINE_MAX_CHARS:
        raise ValueError("formatted fact exceeds the line budget")


def format_fact(
    key: str | None,
    value: object,
    *,
    name_of: Callable[[str], str] | None = None,
    as_of: int | None = None, valid_from: int | None = None, valid_until: int | None = None,
) -> str:
    """Canonical value for every authored surface; resolved ids never render raw."""
    if key is None:
        return ""
    if key == "attribute" and isinstance(value, dict):
        key = value["name"]
        raw = value["value"]
        body = (", ".join(raw) if isinstance(raw, list) else
                str(raw).lower() if isinstance(raw, bool) else str(raw))
        if "unit" in value:
            body += f" {value['unit']}"
    elif key == "birthday" and isinstance(value, dict):
        year = f"{value['year']:04d}-" if "year" in value else ""
        body = f"{year}{value['month']:02d}-{value['day']:02d}"
    elif key == "relationship" and isinstance(value, dict):
        who = (
            (name_of(value["person_id"]) if name_of is not None else "someone")
            if ("person_id" in value)
            else f"{value.get('unresolved_name', 'someone')} (unresolved)"
        )
        body = f"{value.get('relation', '')}: {who}"
    elif isinstance(value, list):
        body = ", ".join(value) if value else "none (explicit)"
    else:
        body = str(value)
    text = f"{key.replace('_', ' ')}: {body}"
    def stamp(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, UTC).isoformat()
    if as_of is not None:
        text += f" (asserted {stamp(as_of)})"
    if valid_from is not None or valid_until is not None:
        start = stamp(valid_from) if valid_from is not None else "unknown"
        end = stamp(valid_until) if valid_until is not None else "unknown"
        text += f" (effective {start} to {end})"
    return text


def identity_text(line: ProfileLine, *, name_of: Callable[[str], str] | None = None) -> str:
    """Typed truth, carrying source/effective time; legacy prose is never promoted."""
    if line.part != "facts":
        return content_text(line.content) if line.content else line.text
    if line.fact_key is None:
        return ""
    return format_fact(line.fact_key, line.fact_value, name_of=name_of,
                       as_of=line.as_of, valid_from=line.valid_from, valid_until=line.valid_until)
