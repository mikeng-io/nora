"""The note body's schema, and the one function that holds a body to it
(design/dreamer-agent-loop/42-note-format.md §4).

A body is seven fixed English headings in a fixed order, one markdown list
item per line under them, and a line grammar per heading. Every line a model
writes ends in the ids of the messages it rests on; every name in it is a name
the input showed; every date is absolute. The content stays in the room's own
language: only the headings and the fixed tokens (`->`, `:`, `(as of …)`,
`(folded …)`, `no reason given`, `no date given`, `when`, the weekday
abbreviation) are keys, and code parses keys (§5).

**One writer calls `parse_note_body` so far**: note-candidates' `accept()`,
on a CREATE's body (design 42 S2, `nora.language.note_candidates`). S4a adds
that pass's update arm, S4b the tag path's `note_create`/`note_update`, S6
note-merge's fold.
It is called by each WRITER and never by `NoteRevision.__post_init__`: rows
written before the schema must still read back (§9).

**Refused, never repaired**, beyond whitespace (G1, decided 2026-09-25): a
repair that changes more than whitespace is code writing content the model
did not, and a silent repair makes the refusal rate unreadable. The repairs,
all of them: trailing whitespace, `\\r\\n`, blank lines (all dropped: the
normalised body has none, as §4.7's conformant body has none), `*`/`+`
bullets to `-`, heading case.

What it does not check, stated rather than discovered:

- that a cited message SUPPORTS its line. It checks that the message was
  shown, who wrote it and that it is not Nora's (§4.4: necessary, not
  sufficient);
- the carry-forward rule (§7, `dropped_citation`), which needs the head being
  revised and belongs to the update arm (S4a);
- `BODY_MAX_CHARS`, which the existing constructors in `nora.domain.notes`
  already refuse;
- relative time words («tomorrow», 聽日), which are counted, not refused, in
  v1 (G6), and counted where the metric lives (S2), not here;
- positions (`[#4]`): the tag path maps each to a row id BEFORE the body is
  stored (§4.3, G4), so a position reaching this function is uncited;
- whether a code-written section (`## From memory`, `## Folded`) was written
  by code. The writer knows who it is; this function does not.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

#: The headings, in their only order (§4.1). Spelled exactly; a heading's
#: case is the one thing about it that is repaired.
SECTIONS: tuple[str, ...] = (
    "Decided", "Open", "Owed", "Proposed", "Ruled out", "From memory", "Folded",
)

#: The two sections code writes and a model never does (§4.1): no citations,
#: their own line grammar.
CODE_WRITTEN: frozenset[str] = frozenset({"From memory", "Folded"})

#: The two sections every body carries, even empty (§4.1).
REQUIRED: tuple[str, ...] = ("Decided", "Open")

#: The sections whose lines end in `(<names>)`: who said it, which must be the
#: author of a message the line cites (§4.3).
ATTRIBUTED: frozenset[str] = frozenset({"Decided", "Proposed", "Ruled out"})

#: Every rule a refusal names (§4.5). One value per `NoteBodyError.rule`, so a
#: counter labelled by it (`nora_note_candidate_body_refused_total{rule}`, S2)
#: has a closed vocabulary.
RULES: frozenset[str] = frozenset({
    "unknown_section", "section_order", "missing_section", "not_a_line",
    "uncited", "unknown_id", "cites_nora", "misattributed", "unknown_owner",
    "bad_date",
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
_NAMED = re.compile(r"^(?P<content>.+) \((?P<names>[^()]*)\)$")
_FROM_MEMORY = re.compile(r"^(?P<what>.+) \(as of (?P<date>[^()]+)\)$")
_FOLDED = re.compile(r"^(?P<title>.+?): (?P<closing>.+) \(folded (?P<date>[^()]+)\)$")
_OWED_SPLIT = " -> "
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
    the tag path hands the refusal back (§4.5)."""

    def __init__(self, rule: str, message: str) -> None:
        if rule not in RULES:
            raise ValueError(f"{rule!r} is not a note-body rule; one of {sorted(RULES)}")
        super().__init__(f"{rule}: {message}")
        self.rule = rule


@dataclass(frozen=True, slots=True)
class ShownMessage:
    """One row the writing pass RENDERED for the model (§4.5, «rendered»).

    `sender` is the `from` exactly as rendered: the name a `(<names>)` slot
    must match. `person_id` is what code attaches for it; the model never
    writes one (§4.3).
    """

    id: int
    sender: str
    person_id: str | None = None
    is_nora: bool = False


@dataclass(frozen=True, slots=True)
class Shown:
    """What a write may cite and name (§4.5).

    - `rendered`: the rows this write put in front of the model. Author,
      owner and Nora checks run on these.
    - `carried`: the ids the head being revised already cites, as ids only.
      Accepted without a re-read: a row the audit pruner has since deleted
      must not turn into a refusal.
    - `roster`: the room's roster, roster name -> person_id. An `## Owed`
      owner must be one of its names.
    - `nora_names`: the names Nora goes by, never an `## Owed` owner (§4.3).
    """

    rendered: tuple[ShownMessage, ...] = ()
    carried: frozenset[int] = frozenset()
    roster: Mapping[str, str] = field(default_factory=dict)
    nora_names: frozenset[str] = frozenset()


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


#: `[<ids>]` anywhere in a text, read without the parser (§7).
_CITATION = re.compile(r"\[\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*\]")


def cited_ids_in(text: str) -> frozenset[int]:
    """Every id a text cites as `[<ids>]`, read with a pattern and NOT with
    `parse_note_body`, so it works on a head that does not parse (§7, §9): a
    legacy body carries none, a partly structured one carries some.

    What a revision may carry forward (`Shown.carried`) and what §7's
    carry-forward check will compare. A body with no citations answers the
    empty set, never an error.
    """
    return frozenset(
        int(i) for m in _CITATION.finditer(text) for i in m.group(1).split(",")
    )


def _canonical_heading(name: str) -> str | None:
    folded = name.casefold()
    return next((h for h in SECTIONS if h.casefold() == folded), None)


def _split(text: str) -> list[tuple[str, list[str]]]:
    """Headings and their raw list items, with the whitespace repairs applied.
    Refuses `unknown_section`, `section_order` and `not_a_line`."""
    sections: list[tuple[str, list[str]]] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            m = _HEADING.match(line)
            hashes, name = (m.group(1), m.group(2)) if m else ("", "")
            heading = _canonical_heading(name) if hashes == "##" else None
            if heading is None:
                raise NoteBodyError(
                    "unknown_section",
                    f"{line!r} is not one of the headings "
                    f"{', '.join('## ' + h for h in SECTIONS)}",
                )
            if sections and SECTIONS.index(heading) <= SECTIONS.index(sections[-1][0]):
                raise NoteBodyError(
                    "section_order",
                    f"## {heading} comes after ## {sections[-1][0]}; each heading "
                    f"appears once, in the order {', '.join(SECTIONS)}",
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


def _ids(item: str, heading: str) -> tuple[str, tuple[int, ...]]:
    m = _CITED.match(item)
    if m is None or not _IDS.match(m.group("ids")):
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
    return rest, tuple(int(i) for i in m.group("ids").split(","))


def _check_ids(
    ids: tuple[int, ...], item: str, rendered: Mapping[int, ShownMessage], carried: frozenset[int],
) -> None:
    for i in ids:
        row = rendered.get(i)
        if row is None:
            if i not in carried:
                raise NoteBodyError(
                    "unknown_id",
                    f"{item!r} cites [{i}], a message this write was not shown "
                    f"and the note did not already cite",
                )
        elif row.is_nora:
            raise NoteBodyError(
                "cites_nora",
                f"{item!r} cites [{i}], one of Nora's own messages; a note is "
                f"the group's, and what she owes is her Standing",
            )


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
    """The date in `(as of <date>)` / `(folded <date>)`: a date as §4.3
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

    rest, ids = _ids(item, heading)
    _check_ids(ids, item, rendered, shown.carried)

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
        return NoteLine(text=item, ids=ids, owner=owner, person_ids=(person_id,))

    if heading not in ATTRIBUTED:  # ## Open: `<question> [<ids>]`
        return NoteLine(text=item, ids=ids)

    m = _NAMED.match(rest)
    names = tuple(n.strip() for n in m.group("names").split(",")) if m else ()
    if not names or not all(names):
        raise NoteBodyError(
            "misattributed",
            f"under ## {heading}, {item!r} names nobody; it ends `(<names>) [<ids>]`",
        )
    content = m.group("content") if m is not None else rest
    if heading == "Ruled out" and ": " not in content:
        raise NoteBodyError(
            "not_a_line",
            f"under ## Ruled out, {item!r} is not `<option>: <reason> (<names>) [<ids>]`",
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
    return NoteLine(text=item, ids=ids, names=names, person_ids=person_ids)


def parse_note_body(text: str, *, shown: Shown, require_open: bool = False) -> NoteBody:
    """The body, held to §4, or `NoteBodyError` naming the rule it broke.

    `require_open` is note-candidates' create: the objective's own rule for a
    new entry is `committed` AND a non-empty `open`, so `## Open` holds at
    least one line there (§4.1). Elsewhere an empty `## Open` means «nothing
    left open as of this revision».
    """
    split = _split(text)
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
