"""The note body's schema, and the one function that holds a body to it.

A body is seven fixed English headings in a fixed order, one markdown list
item per line under them, and a line grammar per heading. Every line a model
writes ends in the ids of the messages it rests on; every name in it is a name
the input showed; every date is absolute. The content stays in the room's own
language: only the headings and the fixed tokens (`->`, `:`, `(as of …)`,
`(folded …)`, `no reason given`, `no date given`, `when`, the weekday
abbreviation) are keys, and code parses keys.

**Writers that call `parse_note_body`**: the tag path's `note_create`/`note_revise`
and the Cognition session's `note_create`/`note_revise`/`note_merge`
(`nora.app.cognition_session`). It is called by each WRITER and never by
`NoteRevision.__post_init__`: a stored body that predates the schema must still
read back.

**Refused, never repaired**, beyond whitespace: a
repair that changes more than whitespace is code writing content the model
did not, and a silent repair makes the refusal rate unreadable. The repairs,
all of them: trailing whitespace, `\\r\\n`, blank lines (all dropped: the
normalised body has none, as a conformant body has none), `*`/`+`
bullets to `-`, heading case. One repair writes content:
an attributed line's `(<names>)` is completed from the rendered sender when
only one person can have said it (left out with a single cited sender, or a
first name exactly one cited sender extends), because who wrote a message is
code's fact, not the model's.

What it does not check, stated rather than discovered:

- that a cited message SUPPORTS its line. It checks that the message was
  shown, who wrote it and that it is not Nora's (necessary, not
  sufficient);
- the carry-forward rule (`dropped_citation`), which needs the head being
  revised and belongs to the update arm;
- `BODY_MAX_CHARS`, which the existing constructors in `nora.domain.notes`
  already refuse;
- relative time words («tomorrow», 聽日), which are counted, not refused, and
  counted where the metric lives, not here;
- positions (`[#4]`): the tag path maps each to a row id BEFORE the body is
  stored, so a position reaching this function is uncited;
- whether a code-written section (`## From memory`, `## Folded`) was written
  by code. The writer knows who it is; this function does not.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: The headings, in their only order. Spelled exactly; a heading's
#: case is the one thing about it that is repaired.
SECTIONS: tuple[str, ...] = (
    "Decided", "Open", "Owed", "Proposed", "Ruled out", "From memory", "Folded",
)

#: The two sections code writes and a model never does: no citations,
#: their own line grammar.
CODE_WRITTEN: frozenset[str] = frozenset({"From memory", "Folded"})

#: The two sections every body carries, even empty.
REQUIRED: tuple[str, ...] = ("Decided", "Open")

#: The sections whose lines end in `(<names>)`: who said it, which must be the
#: author of a message the line cites.
ATTRIBUTED: frozenset[str] = frozenset({"Decided", "Proposed", "Ruled out"})

#: Every rule a refusal names. One value per `NoteBodyError.rule`, so a
#: counter labelled by it (`nora_cognition_refused_total{verb, rule}`) has a
#: closed vocabulary.
RULES: frozenset[str] = frozenset({
    "unknown_section", "section_order", "missing_section", "not_a_line",
    "uncited", "unknown_id", "cites_nora", "misattributed", "unknown_owner",
    "bad_date", "unknown_rollup_key", "unknown_turn",
    # A memo body's `## Superseded` written by the model (`nora.domain.memo`):
    # code renders it from the revision chain.
    "code_written",
})

_WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAY_WORDS: frozenset[str] = frozenset(
    w.casefold() for w in (
        *_WEEKDAYS, "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday", "Tues", "Thur", "Thurs",
    )
)

_HEADING = re.compile(r"^(#+)\s*(.*?)\s*$")
_BULLET = re.compile(r"^[-*+] (.*)$")
_CITED = re.compile(r"^(?P<rest>.*?)\s*\[(?P<ids>[^\[\]]*)\]$")
_IDS = re.compile(r"^\s*[0-9]+(\s*,\s*[0-9]+)*\s*$")
#: A Rollup key cited inside a line's trailing brackets: `rollup(<kind>|<subject>|
#: <from>/<to>)`, the `(kind, subject, interval)` `rollup_read` shows a row by.
#: A group holds message ids, keys, or both.
_ROLLUP_KEY = re.compile(r"rollup\(([^()\[\]]*)\)")
_TURN_KEY = re.compile(r"turn\((t[0-9]+)\)")
_TURN_TAIL = re.compile(r"^(?P<rest>.+?)\s+(?P<ids>turn\(t[0-9]+\))$")
_IDS_OR_KEYS = re.compile(r"^\s*(?:[0-9]+|K)(?:\s*,\s*(?:[0-9]+|K))*\s*$")
_NAMED = re.compile(r"^(?P<content>.+) \((?P<names>[^()]*)\)$")
_FROM_MEMORY = re.compile(r"^(?P<what>.+) \(as of (?P<date>[^()]+)\)$")
_FOLDED = re.compile(r"^(?P<title>.+?): (?P<closing>.+) \(folded (?P<date>[^()]+)\)$")
_OWED_SPLIT = " -> "
#: A sender a `(<names>)` slot cannot hold: `_NAMED` splits on the comma and
#: takes no parenthesis, so such a name is never filled in by code.
_UNHOLDABLE_NAME = re.compile(r"[,()]")
#: A date-shaped run anywhere in the body, loose on purpose so a malformed one
#: (`2026-9-27`) is found and refused rather than skipped.
_DATE_LIKE = re.compile(
    r"(?<![0-9])([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?![0-9])(?:[ ]+([A-Za-z]+))?"
)
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DUE = re.compile(
    r"^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})(?: (?P<weekday>[A-Za-z]{3}))?"
    r"(?: (?P<time>[0-9]{2}:[0-9]{2}))?$"
)
_TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class NoteBodyError(ValueError):
    """A body that breaks the schema. `rule` is one of `RULES`; the message is
    a sentence naming the rule and the line, which is what a model reads when
    the tag path hands the refusal back."""

    def __init__(self, rule: str, message: str) -> None:
        if rule not in RULES:
            raise ValueError(f"{rule!r} is not a note-body rule; one of {sorted(RULES)}")
        super().__init__(f"{rule}: {message}")
        self.rule = rule


@dataclass(frozen=True, slots=True)
class ShownMessage:
    """One row the writing pass RENDERED for the model.

    `sender` is the `from` exactly as rendered: the name a `(<names>)` slot
    must match. `person_id` is what code attaches for it; the model never
    writes one.
    """

    id: int
    sender: str
    person_id: str | None = None
    is_nora: bool = False
    turn_handle: str | None = None


#: The names Nora goes by in a note body, ONE value for the tag path and the
#: Cognition session. It is the literal `_speaker` prints for her
#: own rows (`nora.agent.tools`: `"Nora"`, not `[doctrine].display_name`), so it
#: is the name a row's sender carries on both paths; a deployment that renames
#: her changes that one literal, in one place.
NORA_NAMES: frozenset[str] = frozenset({"Nora"})


@dataclass(frozen=True, slots=True)
class Shown:
    """What a write may cite and name.

    - `rendered`: the rows this write put in front of the model. Author,
      owner and Nora checks run on these.
    - `carried`: the ids the head being revised already cites, as ids only.
      Accepted without a re-read: a row the audit pruner has since deleted
      must not turn into a refusal.
    - `roster`: the room's roster, roster name -> person_id. An `## Owed`
      owner must be one of its names.
    - `nora_names`: the names Nora goes by, never an `## Owed` owner.
    - `rollup_keys`: the Rollup rows this write read, each as `rollup_key(...)`;
      a line may cite only these inside `rollup(...)` («shown
      or fetched»).
    """

    rendered: tuple[ShownMessage, ...] = ()
    carried: frozenset[int] = frozenset()
    roster: Mapping[str, str] = field(default_factory=dict)
    nora_names: frozenset[str] = frozenset()
    rollup_keys: frozenset[str] = frozenset()
    turn_handles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class NoteLine:
    """One list item, parsed. `text` is the line as normalised, without its
    `- `. `names` is `(<names>)` on an attributed line, `owner` the `## Owed`
    owner; `person_ids` is what code resolved them to, never model output."""

    text: str
    ids: tuple[int, ...] = ()
    names: tuple[str, ...] = ()
    owner: str | None = None
    person_ids: tuple[str, ...] = ()
    rollup_keys: tuple[str, ...] = ()
    turn_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NoteSection:
    heading: str
    lines: tuple[NoteLine, ...] = ()


@dataclass(frozen=True, slots=True)
class NoteBody:
    """A body that holds to the schema. `text` is the normalised form, which
    is what a writer stores; `sections` are in schema order."""

    text: str
    sections: tuple[NoteSection, ...]

    def section(self, heading: str) -> NoteSection | None:
        return next((s for s in self.sections if s.heading == heading), None)

    @property
    def cited_ids(self) -> frozenset[int]:
        return frozenset(i for s in self.sections for ln in s.lines for i in ln.ids)

    @property
    def person_ids(self) -> frozenset[str]:
        return frozenset(p for s in self.sections for ln in s.lines for p in ln.person_ids)


#: `[<ids>]` anywhere in a text, read without the parser: every bracket
#: group, which `cited_ids_in` then holds to ids-or-Rollup-keys.
_BRACKET_GROUP = re.compile(r"\[([^\[\]]*)\]")


def cited_ids_in(text: str) -> frozenset[int]:
    """Every id a text cites as `[<ids>]`, read with a pattern and NOT with
    `parse_note_body`, so it works on a head that does not parse: a
    legacy body carries none, a partly structured one carries some.

    What a revision may carry forward (`Shown.carried`) and what the
    carry-forward check compares. A body with no citations answers the
    empty set, never an error.
    """
    ids: set[int] = set()
    for group in _BRACKET_GROUP.findall(text):
        group = _TURN_KEY.sub("K", group)
        # A group may hold ids, Rollup keys or both (`[4812, rollup(...)]`): a key
        # carries commas of its own, so it is blanked before the ids are split.
        shape = _ROLLUP_KEY.sub("K", group)
        if _IDS_OR_KEYS.match(shape):
            ids.update(int(t) for t in shape.split(",") if t.strip().isdigit())
    return frozenset(ids)


def rollup_keys_in(text: str) -> frozenset[str]:
    """Every Rollup key a text cites as `rollup(<kind>|<subject>|<from>/<to>)`
    inside a bracket group (`[12, rollup(...)]`), read with a pattern and NOT
    with `parse_note_body`, for `cited_ids_in`'s reason: it works on a head that
    does not parse. What the `citation` edge and the lineage of a note that rests
    on a Rollup row are computed from."""
    return frozenset(
        key.strip() for group in _BRACKET_GROUP.findall(text)
        for key in _ROLLUP_KEY.findall(group) if key.strip()
    )


def citations_in(text: str) -> tuple[str, ...]:
    """What a stored body cites, as a revision's `citations` column holds it:
    `nora.domain.memo.citations_of`'s form and order (per line its ids, then
    its Rollup keys; ids as digits, keys as `rollup(...)`; each once).

    Read off each line's TRAILING bracket group only, the one group
    `parse_note_body` checks against what the write was shown, so on a body
    that parsed this is exactly `citations_of` of the parse, and a bracket
    inside a line's text is never a citation. Read with a pattern rather than
    the parser, so a stored body that predates the schema answers too."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        m = _CITED.match(line.strip()) or _TURN_TAIL.match(line.strip())
        if m is None:
            continue
        group = m.group("ids")
        shape = _TURN_KEY.sub("K", _ROLLUP_KEY.sub("K", group))
        if not _IDS_OR_KEYS.match(shape):
            continue
        for token in shape.split(","):
            if token.strip().isdigit():
                seen.setdefault(str(int(token)), None)
        for key in _ROLLUP_KEY.findall(group):
            if key.strip():
                seen.setdefault(f"rollup({key.strip()})", None)
        for handle in _TURN_KEY.findall(group):
            seen.setdefault(f"turn({handle})", None)
    return tuple(seen)


#: The rules that are about a body's FORM, as against its evidence: what
#: `in_schema_form` holds a stored body to. The rest (`unknown_id`,
#: `cites_nora`, `misattributed`, `unknown_owner`) need the rows the writer was
#: shown, which a reader of a stored body does not have.
FORM_RULES: frozenset[str] = frozenset({
    "unknown_section", "section_order", "missing_section", "not_a_line",
    "uncited", "bad_date",
})


class _AnyName(dict[str, str]):
    """A roster every name is on: `in_schema_form` reads form, not owners."""

    def get(self, key: str, default: str | None = None) -> str:  # type: ignore[override]
        return ""


def in_schema_form(text: str) -> bool:
    """Whether a STORED body is in the notebook's sections (its `form`): it parses under the schema
    when every id it cites counts as carried and every owner as a roster name. A legacy prose body
    is not; a schema body is, whoever wrote it. Never a refusal: this is what a reader is told it is
    looking at, and the validator runs on write, never on read."""
    shown = Shown(carried=cited_ids_in(text), roster=_AnyName())
    try:
        parse_note_body(text, shown=shown)
    except NoteBodyError as broke:
        return broke.rule not in FORM_RULES
    return True


def dropped_citations(head: str, body: str) -> tuple[frozenset[int], ...]:
    """The carry-forward check: the citation sets of the HEAD's lines that
    no line of the new `body` cites in full, in the head's order. Empty means
    every line's evidence survives the restatement.

    A restatement may rewrite a line, move it between sections or merge two
    lines into one citing both; it may not make a line's evidence vanish.
    Both sides are read LINE BY LINE WITH A PATTERN, never the parser, so the
    check binds to any `[<ids>]` a head carries, parsed or not: a legacy
    head with no citations makes it vacuous, and that one revision is held to
    form only.
    """
    new_lines = [cited_ids_in(line) for line in body.splitlines()]
    dropped: list[frozenset[int]] = []
    for line in head.splitlines():
        ids = cited_ids_in(line)
        if ids and not any(ids <= new for new in new_lines):
            dropped.append(ids)
    return tuple(dropped)


#: A citation group as the tag path writes it: positions (`#4`), bare ids,
#: or both, comma-separated, in square brackets.
_POSITIONAL = re.compile(r"\[(\s*#?[0-9]+(?:\s*,\s*#?[0-9]+)*\s*)\]")


def resolve_positions(
    text: str, *, rows: Sequence[int], carried: frozenset[int],
) -> str:
    """The tag path's body with every `[#n]` position mapped to the row id
    her transcript showed at that position, so a stored
    body holds only ids.

    `rows` is the transcript's row ids in the order the prefill numbered
    them, 1..N. A position outside 1..N, or a BARE id the note does not
    already cite (`carried`), is `unknown_id`: she cites what she sees, and a
    bare id is one only a body she read can have handed her.
    """
    def _one(token: str, group: str) -> str:
        token = token.strip()
        if token.startswith("#"):
            n = int(token[1:])
            if not 1 <= n <= len(rows):
                raise NoteBodyError(
                    "unknown_id",
                    f"[{group.strip()}] cites #{n}, and the transcript you were "
                    f"shown runs #1 to #{len(rows)}",
                )
            return str(rows[n - 1])
        if int(token) not in carried:
            raise NoteBodyError(
                "unknown_id",
                f"[{group.strip()}] cites {token}, which is not a transcript "
                f"position; cite a message as #n",
            )
        return token

    def _group(m: re.Match[str]) -> str:
        return "[" + ", ".join(_one(t, m.group(1)) for t in m.group(1).split(",")) + "]"

    return _POSITIONAL.sub(_group, text)


def _canonical_heading(name: str, schema: tuple[str, ...] = SECTIONS) -> str | None:
    folded = name.casefold()
    return next((h for h in schema if h.casefold() == folded), None)


def split_sections(
    text: str, *, schema: tuple[str, ...] = SECTIONS,
) -> list[tuple[str, list[str]]]:
    """Headings and their raw list items, with the whitespace repairs applied,
    held to `schema`'s headings in `schema`'s order (a note's `SECTIONS` by
    default; a memo body passes its own, `nora.domain.memo`). Refuses
    `unknown_section`, `section_order` and `not_a_line`."""
    sections: list[tuple[str, list[str]]] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            m = _HEADING.match(line)
            hashes, name = (m.group(1), m.group(2)) if m else ("", "")
            heading = _canonical_heading(name, schema) if hashes == "##" else None
            if heading is None:
                raise NoteBodyError(
                    "unknown_section",
                    f"{line!r} is not one of the headings "
                    f"{', '.join('## ' + h for h in schema)}",
                )
            if sections and schema.index(heading) <= schema.index(sections[-1][0]):
                raise NoteBodyError(
                    "section_order",
                    f"## {heading} comes after ## {sections[-1][0]}; each heading "
                    f"appears once, in the order {', '.join(schema)}",
                )
            sections.append((heading, []))
            continue
        bullet = _BULLET.match(line)
        if bullet is None or not sections:
            where = "before any heading" if not sections else "outside a list item"
            if line[:1].isspace() and _BULLET.match(line.lstrip()):
                where = "a nested list item"
            raise NoteBodyError(
                "not_a_line",
                f"{line.strip()!r} is {where}; every line is one `- ` list item under a heading",
            )
        item = bullet.group(1).strip()
        if _BULLET.match(item):
            raise NoteBodyError(
                "not_a_line", f"{line.strip()!r} is a nested list item; one `- ` per line",
            )
        if not item:
            raise NoteBodyError("not_a_line", f"an empty list item under ## {sections[-1][0]}")
        sections[-1][1].append(item)
    return sections


def rollup_key(kind: str, subject: str, start: str, end: str) -> str:
    """The text a note cites a Rollup row by, inside `rollup(...)`: the same
    `(kind, subject, interval)` `rollup_read` prints for the row (`kind`,
    `subject`, `from`, `to`)."""
    return f"{kind}|{subject}|{start}/{end}"


def _ids(item: str, heading: str) -> tuple[
    str, tuple[int, ...], tuple[str, ...], tuple[str, ...],
]:
    m = _CITED.match(item) or _TURN_TAIL.match(item)
    group = "" if m is None else m.group("ids")
    keys = tuple(k.strip() for k in _ROLLUP_KEY.findall(group))
    turns = tuple(_TURN_KEY.findall(group))
    shape = _TURN_KEY.sub("K", _ROLLUP_KEY.sub("K", group))
    ok = _IDS.match(group) if not keys and not turns else _IDS_OR_KEYS.match(shape)
    if m is None or not ok:
        raise NoteBodyError(
            "uncited",
            f"under ## {heading}, {item!r} does not end in the ids of the "
            f"messages it rests on, as `[<id>, <id>]`",
        )
    rest = m.group("rest").strip()
    if not rest:
        raise NoteBodyError(
            "not_a_line", f"under ## {heading}, {item!r} says nothing before its ids",
        )
    ids = tuple(int(t) for t in shape.split(",") if t.strip().isdigit())
    return rest, ids, keys, turns


def check_turn_citations(turns: Sequence[str], shown: Shown) -> None:
    known = shown.turn_handles | frozenset(
        m.turn_handle for m in shown.rendered if m.turn_handle is not None
    )
    for handle in turns:
        if handle not in known:
            raise NoteBodyError(
                "unknown_turn", "the cited turn was not shown or run by this writer",
            )


def _check_ids(
    ids: tuple[int, ...], item: str, rendered: Mapping[int, ShownMessage], carried: frozenset[int],
    keys: tuple[str, ...] = (), known_keys: frozenset[str] = frozenset(),
    *, may_cite_nora: bool = False,
) -> None:
    for key in keys:
        if key not in known_keys:
            raise NoteBodyError(
                "unknown_rollup_key",
                f"{item!r} cites rollup({key}), a Rollup row this write did not read; "
                f"cite the `kind`, `subject`, `from` and `to` of a row rollup_read returned",
            )
    for i in ids:
        row = rendered.get(i)
        if row is None:
            if i not in carried:
                raise NoteBodyError(
                    "unknown_id",
                    f"{item!r} cites [{i}], a message this write was not shown "
                    f"and the note did not already cite",
                )
        elif row.is_nora and not may_cite_nora:
            raise NoteBodyError(
                "cites_nora",
                f"{item!r} cites [{i}], one of Nora's own messages; a note is "
                f"the group's, and what she owes is her Standing",
            )


_ROLLUP_CITATION = re.compile(r"^rollup\(([^()\[\]]+)\)$")


def check_citations(
    cited: Sequence[str], shown: Shown, *, what: str,
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """The citations of a write that has no body to carry them (an event verb):
    `cited` are message ids as digits and Rollup rows as
    `rollup(kind|subject|from/to)`, exactly the two forms a note's brackets
    take, held to the same `Shown` the same way: an id the write was shown or
    fetched (or already carried), not one of Nora's own, a Rollup row it read.
    Returns `(message ids, rollup keys)` in the order given, each once.
    Raises `NoteBodyError` (`uncited` for none or a token that is neither
    form, else `_check_ids`'s own) with `what` naming the write in the message."""
    ids: list[int] = []
    keys: list[str] = []
    for raw in cited:
        token = raw.strip()
        if token.isdigit():
            if int(token) not in ids:
                ids.append(int(token))
            continue
        m = _ROLLUP_CITATION.match(token)
        if m is None:
            raise NoteBodyError(
                "uncited",
                f"{what} cites {token!r}, which is neither a message id (digits) nor "
                f"rollup(kind|subject|from/to)",
            )
        if m.group(1).strip() not in keys:
            keys.append(m.group(1).strip())
    if not ids and not keys:
        raise NoteBodyError(
            "uncited", f"{what} cites nothing; give the ids of the messages it rests on, "
            f"or the Rollup rows you read",
        )
    _check_ids(
        tuple(ids), what, {m.id: m for m in shown.rendered}, shown.carried,
        tuple(keys), shown.rollup_keys,
    )
    return tuple(ids), tuple(keys)


def _check_date(date: str, weekday: str | None, item: str) -> None:
    if not _DATE.match(date):
        raise NoteBodyError("bad_date", f"{date!r} in {item!r} is not a YYYY-MM-DD date")
    try:
        day = _dt.date.fromisoformat(date)
    except ValueError:
        raise NoteBodyError("bad_date", f"{date!r} in {item!r} is not a real day") from None
    if weekday is None:
        return
    # One comparison covers both a wrong day and a weekday not written as
    # the three English letters (`Saturday`, `sat`): neither equals `Sat`.
    if _WEEKDAYS[day.weekday()] != weekday:
        raise NoteBodyError(
            "bad_date", f"{date} is a {_WEEKDAYS[day.weekday()]}, not a {weekday} ({item!r})",
        )


def _check_stamp(stamp: str, item: str) -> None:
    """The date in `(as of <date>)` / `(folded <date>)`: a date as the schema
    writes every date, optionally with its weekday."""
    date, _, weekday = stamp.partition(" ")
    _check_date(date, weekday or None, item)


def _check_dates(item: str) -> None:
    """Every date-shaped run in a line is a real YYYY-MM-DD day, and a
    weekday word right after it is that day's, as three letters."""
    for m in _DATE_LIKE.finditer(item):
        word = m.group(4)
        weekday = word if word is not None and word.casefold() in _WEEKDAY_WORDS else None
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        _check_date(date, weekday, item)


def _check_due(due: str, item: str) -> None:
    if due == "no date given" or (due.startswith("when ") and due[5:].strip()):
        return
    m = _DUE.match(due)
    if m is None:
        raise NoteBodyError(
            "bad_date",
            f"the due of {item!r} is {due!r}; a due is a YYYY-MM-DD date, "
            f"optionally with its weekday and an HH:MM time, `when <event>`, "
            f"or `no date given`",
        )
    _check_date(m.group("date"), m.group("weekday"), item)
    if m.group("time") is not None and not _TIME.match(m.group("time")):
        raise NoteBodyError(
            "bad_date", f"{m.group('time')} in {item!r} is not a 24-hour HH:MM time",
        )


def _full_sender(name: str, senders: tuple[str, ...]) -> str:
    """`name` as the one cited sender it is the first word of (`Tyler` for a
    rendered `Tyler Cortez`); unchanged when it is a sender already, or when
    no sender, or more than one, starts with it."""
    if name in senders:
        return name
    matches = [s for s in senders if s.split(" ", 1)[0] == name]
    return matches[0] if len(matches) == 1 else name


def cited_line(
    heading: str, item: str, shown: Shown, *, may_cite_nora: bool = False,
) -> NoteLine:
    """One `<claim> [<ids>]` line under `heading`, held to the grammar every
    Register line shares: absolute dates, ending in the ids and
    Rollup keys it rests on, each one this write was shown or carries. A line
    may cite one of Nora's own messages only where `may_cite_nora` says so (a
    memo, which may keep what she found or said; never a note). The memo
    body's parser (`nora.domain.memo.parse_memo_body`) is its caller."""
    _check_dates(item)
    _rest, ids, keys, turns = _ids(item, heading)
    check_turn_citations(turns, shown)
    _check_ids(
        ids, item, {m.id: m for m in shown.rendered}, shown.carried, keys,
        shown.rollup_keys, may_cite_nora=may_cite_nora,
    )
    return NoteLine(text=item, ids=ids, rollup_keys=keys, turn_handles=turns)


def _line(
    heading: str, item: str, shown: Shown, rendered: Mapping[int, ShownMessage],
) -> NoteLine:
    _check_dates(item)
    if heading == "From memory":
        m = _FROM_MEMORY.match(item)
        if m is None:
            raise NoteBodyError(
                "not_a_line", f"under ## From memory, {item!r} is not `<what> (as of <date>)`",
            )
        _check_stamp(m.group("date").strip(), item)
        return NoteLine(text=item)
    if heading == "Folded":
        m = _FOLDED.match(item)
        if m is None:
            raise NoteBodyError(
                "not_a_line",
                f"under ## Folded, {item!r} is not `<title>: <closing condition> (folded <date>)`",
            )
        _check_stamp(m.group("date").strip(), item)
        return NoteLine(text=item)

    rest, ids, keys, turns = _ids(item, heading)
    check_turn_citations(turns, shown)
    _check_ids(ids, item, rendered, shown.carried, keys, shown.rollup_keys)

    if heading == "Owed":
        parts = [p.strip() for p in rest.split(_OWED_SPLIT)]
        if len(parts) != 3 or not all(parts):
            raise NoteBodyError(
                "not_a_line", f"under ## Owed, {item!r} is not `<name> -> <what> -> <due> [<ids>]`",
            )
        owner, _what, due = parts
        if owner in shown.nora_names:
            raise NoteBodyError(
                "unknown_owner",
                f"the owner of {item!r} is Nora; what she owes is her Standing, never a note's",
            )
        person_id = shown.roster.get(owner)
        if person_id is None:
            raise NoteBodyError(
                "unknown_owner",
                f"the owner of {item!r}, {owner!r}, is not a roster name of this room",
            )
        _check_due(due, item)
        return NoteLine(
            text=item, ids=ids, owner=owner, person_ids=(person_id,), rollup_keys=keys,
            turn_handles=turns,
        )

    if heading not in ATTRIBUTED:  # ## Open: `<question> [<ids>]`
        return NoteLine(text=item, ids=ids, rollup_keys=keys, turn_handles=turns)

    m = _NAMED.match(rest)
    names = tuple(n.strip() for n in m.group("names").split(",")) if m else ()
    content = m.group("content") if m is not None else rest
    # Who said it, when only one person can have: a line that leaves `(<names>)`
    # out while every rendered message it cites is ONE sender's, or writes a
    # first name exactly one cited sender extends, is completed from the
    # rendered `from`. Several cited
    # senders stay a refusal that lists them: who said a line is the model's
    # judgement, not every author it cites. A sender a `(<names>)` slot cannot
    # hold (a comma or a parenthesis) is never filled in.
    cited = [rendered[i] for i in ids if i in rendered and not rendered[i].is_nora]
    senders = tuple(dict.fromkeys(s.sender for s in cited))
    holdable = tuple(s for s in senders if not _UNHOLDABLE_NAME.search(s))
    completed = False
    if not names or not all(names):
        if len(senders) == 1 and holdable == senders:
            names, completed = senders, True
        elif senders:
            raise NoteBodyError(
                "misattributed",
                f"under ## {heading}, {item!r} names nobody; it ends `(<names>) [<ids>]`, "
                f"naming who said it among: {', '.join(senders)}",
            )
    elif holdable:
        expanded = tuple(dict.fromkeys(_full_sender(n, holdable) for n in names))
        if expanded != names:
            names, completed = expanded, True
    if not names or not all(names):
        raise NoteBodyError(
            "misattributed",
            f"under ## {heading}, {item!r} names nobody; it ends `(<names>) [<ids>]`",
        )
    if completed:
        cite = _CITED.match(item)
        item = f"{content} ({', '.join(names)}) [{cite.group('ids') if cite else ''}]"
    if heading == "Ruled out" and ": " not in content:
        raise NoteBodyError(
            "not_a_line",
            f"under ## Ruled out, {item!r} is not `<option>: <reason> (<names>) [<ids>]`",
        )
    if turns and not ids:
        raise NoteBodyError("misattributed", "a turn record does not say who said a line")
    if keys and not ids:
        raise NoteBodyError(
            "misattributed",
            f"under ## {heading}, {item!r} names who said it but cites only a Rollup row; "
            f"a Rollup row says who was where, never who said what: cite the message",
        )
    authors = {rendered[i].sender: rendered[i].person_id for i in ids if i in rendered}
    # A name is checked against the evidence this write rendered. Only a line
    # citing CARRIED ids alone may name an author this write was never shown,
    # taken as the head carried it; one rendered id is enough to check
    # against. Nora is never an author, carried or not.
    unverifiable = not authors
    for name in names:
        if name in shown.nora_names:
            raise NoteBodyError(
                "misattributed", f"{item!r} names Nora; a note records what the group said",
            )
        if name not in authors and not unverifiable:
            raise NoteBodyError(
                "misattributed",
                f"{item!r} names {name!r}, who wrote none of the messages it cites",
            )
    person_ids = tuple(p for n in names if (p := authors.get(n)) is not None)
    return NoteLine(text=item, ids=ids, names=names, person_ids=person_ids,
                    rollup_keys=keys, turn_handles=turns)


def parse_note_body(text: str, *, shown: Shown, require_open: bool = False) -> NoteBody:
    """The body, held to the schema, or `NoteBodyError` naming the rule it broke.

    `require_open` is a create's (and a merge's): the objective's own rule for a
    new entry is `committed` AND a non-empty `open`, so `## Open` holds at
    least one line there. Elsewhere an empty `## Open` means «nothing
    left open as of this revision».
    """
    split = split_sections(text)
    present = dict(split)
    missing = [h for h in REQUIRED if h not in present]
    if missing:
        raise NoteBodyError(
            "missing_section", f"## {missing[0]} is missing; it is always present, even empty",
        )
    if not present.get("Decided") and not present.get("Open"):
        raise NoteBodyError(
            "missing_section",
            "## Decided and ## Open are both empty; together they hold at least one line",
        )
    if require_open and not present.get("Open"):
        raise NoteBodyError(
            "missing_section", "## Open is empty; a new note holds at least one open corner",
        )

    rendered = {m.id: m for m in shown.rendered}
    sections = tuple(
        NoteSection(heading=h, lines=tuple(_line(h, item, shown, rendered) for item in items))
        for h, items in split
    )
    normalised = "\n".join(
        "\n".join((f"## {s.heading}", *(f"- {ln.text}" for ln in s.lines))) for s in sections
    )
    return NoteBody(text=normalised, sections=sections)
