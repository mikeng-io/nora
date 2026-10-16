"""Information parity of the Cognition prompt: the FORMAT changed, and nothing it
said was lost.

The oracle is the capture of the OLD format
(`tests/fixtures/cognition_render_pre3b_baseline.json`, never regenerated). The
cases are the ones that capture was made from
(`test_cognition_render_goldens.CASES`), rendered now through the NEW renderer.
Each side is read into FACTS, by what it is (the old by pattern over its lines,
the new by parsing it as XML, which is itself a property: every case renders
well-formed), and the facts are compared:

* every message id, author, time with its relative age, edit flag and cue;
* a reaction (its emoji, the message it is on, who wrote that message and its
  words), a deletion, a message whose words are withdrawn, Nora's own send (and its
  withdrawn or empty words), a Rollup row's sentence;
* an item's number, summary, room label, returning count and window;
* the count and range of what a budget left out, and how many of an item's stimuli
  could not be shown;
* the clock and the reads line, word for word;
* every note's id, title and description, and every event's id, title, status,
  when, venue and revision, under its room.

**Where the two may differ, and only there.** A case with a tight budget
(`budget-*`) cuts by what a row COSTS, and a row now costs its element, so the new
render may show fewer of the newest rows than the old did (never different ones,
never more): the rows it shows are a suffix of the old shown set, and what it shows
plus what it cut is exactly what the old showed plus cut. Every other case must
show every fact the old one did. The events of a room past `register_lines` were capped
silently in the old format; the new one says so (`not_listed`), which only adds a fact.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.unit.cognition_view import blocks
from tests.unit.test_cognition_render_goldens import CASES

BASELINE: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "cognition_render_pre3b_baseline.json")
    .read_text(encoding="utf-8"),
)

#: Cases whose budget decides what is shown (they name `RenderBudget(...)` or
#: spend the default budget over twelve chatty items).
TIGHT = {name for name in CASES if name.startswith("budget-")} | {
    "register-cut-at-its-character-budget",
}

#: Cases that are a FUNCTION's own output (the clock sentence, `ago`, the room's
#: label), not an agenda: those functions are unchanged, so the text is the same.
FUNCTION_CASES = {name for name in CASES if name.startswith("clock-")} | {
    "ago-boundaries", "room-label-and-key",
}

Row = tuple  # (kind, ...) — see `_old_row` and `_new_row`


@dataclass
class Item:
    index: int
    summary: str
    room: str
    returning: int
    window: str
    rows: list[Row] = field(default_factory=list)
    cut: int = 0
    cut_range: tuple[int, int] | None = None
    unreadable: int = 0


@dataclass
class Facts:
    clock: str | None = None
    reads: str | None = None
    items: list[Item] = field(default_factory=list)
    notes: list[tuple[str, str, str]] = field(default_factory=list)
    notes_not_listed: int = 0
    events: dict[str, list[tuple]] = field(default_factory=dict)
    events_not_listed: int = 0
    register_cut_said: bool = False


# --- the OLD format, read by pattern -----------------------------------------------

_WHEN = (
    r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2} )?\d{2}:\d{2} \([^)]*\)"
)
_CUES = r"(?: \[cues: ([^\]]*)\])?$"
_HEAD = re.compile(
    r"^(\d+)\. (.*) \[room («.*?»)\](?: \(((?:at|from) .*)\))?$",
)
_RETURNING = re.compile(r"^(.*?)(?: \(returning: left open by (\d+) earlier sessions?\))?$")
_DELETE = re.compile(
    rf"^  \[#(\d+)\] (.*?), ({_WHEN}): deleted by its author \(the words are withdrawn\){_CUES}",
)
_OWN = re.compile(rf"^  \[#(\d+)\] (.*?) \(you\), ({_WHEN}): (.*)$")
_MESSAGE = re.compile(
    rf"^  \[#(\d+)\] (.*?), ({_WHEN})( \(edited\))?: (.*?){_CUES}",
)
_REACTION = re.compile(
    rf"^  someone reacted (.*?) to \[#(\d+)\] (.*?)(?:: (.*?))?, ({_WHEN}){_CUES}",
)
_CUT = re.compile(r"^  \((\d+) earlier messages? not shown: #(\d+) to #(\d+)\)$")
_UNREADABLE = re.compile(r"^  \((one|\d+) of these could not be shown\)$")
_NOTE = re.compile(r'^- note (\S+) "(.*?)": (.*)$')
_NOTES_MORE = re.compile(r"^- \((\d+) more open notes not listed\)$")
_ROOM = re.compile(r"^Already recorded for room («.*»):$")
_EVENT = re.compile(
    r'^- event (\S+) "(.*?)", (\S+), (no time set|[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2} \d{4} '
    r"\d{2}:\d{2})(?:, at (.*?))?(?:, revision (.*))?$",
)


def _cues(raw: str | None) -> tuple[str, ...]:
    return tuple(raw.split(", ")) if raw else ()


def _old_row(line: str) -> Row:
    if m := _DELETE.match(line):
        return ("delete", int(m[1]), m[2], m[3], _cues(m[4]))
    if m := _OWN.match(line):
        words = None if m[4] == "(the words are withdrawn)" else (
            "" if m[4] == "(no words)" else m[4]
        )
        return ("own", int(m[1]), m[2], m[3], words)
    if m := _MESSAGE.match(line):
        if m[5] == "(the words are withdrawn)":
            return ("withdrawn", int(m[1]), m[2], m[3], bool(m[4]), _cues(m[6]))
        return ("message", int(m[1]), m[2], m[3], bool(m[4]), _cues(m[6]), m[5])
    if m := _REACTION.match(line):
        return ("reaction", m[1], int(m[2]), m[3], m[4] or "", m[5], _cues(m[6]))
    return ("rollup", line[2:])


def old_facts(text: str) -> Facts:
    facts = Facts()
    head, _, register = text.partition("\n\n")
    item: Item | None = None
    for line in head.split("\n") if head else []:
        if line.startswith("It is "):
            facts.clock = line
        elif line.startswith("Your read tools"):
            facts.reads = line
        elif m := _HEAD.match(line):
            summary = _RETURNING.match(m[2])
            assert summary is not None
            item = Item(
                int(m[1]), summary[1], m[3], int(summary[2] or 0), m[4] or "",
            )
            facts.items.append(item)
        elif item is not None and (m := _CUT.match(line)):
            item.cut, item.cut_range = int(m[1]), (int(m[2]), int(m[3]))
        elif item is not None and (m := _UNREADABLE.match(line)):
            item.unreadable = 1 if m[1] == "one" else int(m[1])
        elif item is not None and line.startswith("  "):
            item.rows.append(_old_row(line))
        elif item is not None and line.startswith("[#"):
            # the one row an allowance keeps was clipped WHOLE in the old format,
            # indent included, and `one_line` stripped the indent
            item.rows.append(_old_row("  " + line))
        elif item is not None and line.startswith("["):
            item.rows.append(("clipped",))     # cut through its own head: nothing to read
        else:
            raise AssertionError(f"an old line no pattern reads: {line!r}")
    room = ""
    for line in register.split("\n") if register else []:
        if line.startswith("Open notes of this group"):
            continue
        if m := _NOTE.match(line):
            facts.notes.append((m[1], m[2], m[3]))
        elif m := _NOTES_MORE.match(line):
            facts.notes_not_listed = int(m[1])
        elif m := _ROOM.match(line):
            room = m[1]
            facts.events[room] = []
        elif m := _EVENT.match(line):
            facts.events[room].append((m[1], m[2], m[3], m[4], m[5] or "", m[6] or ""))
        elif line == "- (more index lines not listed)":
            facts.register_cut_said = True
        else:
            raise AssertionError(f"an old register line no pattern reads: {line!r}")
    return facts


# --- the NEW format, read as XML ---------------------------------------------------


def _new_row(el: ET.Element) -> Row:
    a = el.attrib
    words = el.text or ""
    if el.tag == "rollup":
        return ("rollup", words)
    cues = _cues(a.get("cues"))
    n = int(a["n"])
    if "reaction" in a:
        return ("reaction", a["reaction"], n, a["from"], words, a["at"], cues)
    if "deleted" in a:
        assert "withdrawn" in a and not words
        return ("delete", n, a["from"], a["at"], cues)
    if "own" in a:
        return ("own", n, a["from"], a["at"], None if "withdrawn" in a else words)
    if "withdrawn" in a:
        assert not words
        return ("withdrawn", n, a["from"], a["at"], "edited" in a, cues)
    return ("message", n, a["from"], a["at"], "edited" in a, cues, words)


def new_facts(text: str) -> Facts:
    facts = Facts()
    for block in blocks(text):                   # raises on stray text outside a block
        if block.tag == "clock":
            facts.clock = (block.text or "").strip()
        elif block.tag == "reads":
            facts.reads = (block.text or "").strip()
        elif block.tag == "agenda":
            for item_el in block:
                assert item_el.tag == "item", item_el.tag
                a = item_el.attrib
                returning = re.fullmatch(
                    r"left open by (\d+) earlier sessions?", a.get("returning", ""),
                )
                if "returning" in a:
                    assert returning, a["returning"]
                facts.items.append(Item(
                    int(a["index"]), a["summary"], a["room"],
                    int(returning[1]) if returning else 0, a.get("window", ""),
                    [_new_row(el) for el in item_el], int(a.get("earlier_not_shown", 0)),
                    tuple(int(x) for x in a["earlier_range"].split(" to "))  # type: ignore[arg-type]
                    if "earlier_range" in a else None,
                    int(a.get("unreadable", 0)),
                ))
        elif block.tag == "note_index":
            a = block.attrib
            facts.notes += [(n.attrib["n"], n.attrib["title"], n.text or "") for n in block]
            facts.notes_not_listed = int(a.get("not_listed", 0))
        elif block.tag == "events":
            for group in block:
                assert group.tag == "room_events", group.tag
                facts.events[group.attrib["room"]] = [
                    (e.attrib["id"], e.text or "", e.attrib["status"], e.attrib["when"],
                     e.attrib.get("venue", ""), e.attrib.get("revision", ""))
                    for e in group
                ]
                facts.events_not_listed += int(group.attrib.get("not_listed", 0))
        else:
            raise AssertionError(f"a block the parity test does not know: {block.tag}")
    return facts


# --- the comparison ----------------------------------------------------------------


def _same_words(a: str | None, b: str | None) -> bool:
    if a == b:
        return True
    if a is None or b is None:
        return False
    return (a.endswith("…") and b.startswith(a[:-1])) or (
        b.endswith("…") and a.startswith(b[:-1])
    )


def _same_row(old: Row, new: Row) -> bool:
    if old == ("clipped",):
        return True
    if old[0] != new[0]:
        return False
    if old[0] == "rollup":      # an empty span left the old sentence a trailing space
        return old[1].strip() == new[1]
    # the row's words are its last field for a message and an own send, its
    # fifth for a reaction: a cut may differ in length, never in what it starts with
    index = {"message": 6, "own": 4, "reaction": 4}.get(old[0])
    if index is None:
        return old == new
    return (
        old[:index] + old[index + 1:] == new[:index] + new[index + 1:]
        and _same_words(old[index], new[index])
    )


@pytest.mark.parametrize("name", sorted(FUNCTION_CASES))
def test_the_functions_the_agenda_is_built_from_say_the_same(name: str):
    assert CASES[name]() == BASELINE[name]


def test_a_returning_item_says_how_many_looks_left_it_open_in_the_new_words():
    old, new = BASELINE["returning-wording"], CASES["returning-wording"]()
    assert re.sub(r"' \(returning: (.*)\)'", r"'\1'", old) == new
    assert "2: 'left open by 2 earlier sessions'" in new and "0: ''" in new


@pytest.mark.parametrize(
    "name", sorted(set(CASES) - FUNCTION_CASES - {"returning-wording"}),
)
def test_the_new_render_is_well_formed_and_loses_no_fact_of_the_old(name: str):
    old = old_facts(BASELINE[name])
    new = new_facts(CASES[name]())          # parses as XML, or this raises

    assert new.clock == old.clock
    assert new.reads == old.reads
    assert [(i.index, i.summary, i.room, i.returning, i.window) for i in new.items] == [
        (i.index, i.summary, i.room, i.returning, i.window) for i in old.items
    ]
    for o, n in zip(old.items, new.items, strict=True):
        assert n.unreadable == o.unreadable, (name, o.index)
        if name not in TIGHT:
            assert (o.cut, n.cut) == (0, 0), (name, o.index)
            assert len(o.rows) == len(n.rows), (name, o.index)
            assert all(_same_row(a, b) for a, b in zip(o.rows, n.rows, strict=True)), (
                name, o.index, o.rows, n.rows,
            )
            continue
        # a tight budget: what is shown plus what was cut is what the old showed
        # plus cut, the shown rows are the NEWEST of the old ones, and the oldest
        # id left out is the same
        assert len(n.rows) + n.cut == len(o.rows) + o.cut, (name, o.index)
        assert len(n.rows) <= len(o.rows) or o.cut == 0, (name, o.index)
        kept = o.rows[len(o.rows) - len(n.rows):] if n.rows else []
        assert all(_same_row(a, b) for a, b in zip(kept, n.rows, strict=True)), (
            name, o.index, kept, n.rows,
        )
        if o.cut_range and n.cut_range:
            assert n.cut_range[0] == o.cut_range[0], (name, o.index)
            assert n.cut_range[1] >= o.cut_range[1], (name, o.index)
        assert bool(o.cut) == bool(n.cut) or name == "budget-default-with-twelve-items"

    if name == "register-cut-at-its-character-budget":
        # the character cut: the same prefix of the notes, a count of the rest
        assert new.notes == old.notes[: len(new.notes)] or old.notes == new.notes[: len(old.notes)]
        assert len(new.notes) + new.notes_not_listed == 30
        assert old.register_cut_said and new.notes_not_listed > 0
    else:
        assert new.notes == old.notes
        assert new.notes_not_listed == old.notes_not_listed
    assert new.events == old.events
    if name == "register-note-and-event-line-caps":
        # the events cap was silent in the old format; the new one says so
        assert new.events_not_listed == 4
    elif name != "register-cut-at-its-character-budget":
        assert new.events_not_listed == 0


def test_the_baseline_is_the_one_step_3a_captured_and_every_case_has_one():
    assert set(CASES) == set(BASELINE)
    # the oracle is in the OLD format: bracketed ids and `[room ...]` heads
    assert "[#101] Ada, 14:50 (40 min ago): dinner friday at 8?" in BASELINE["one-message"]
    assert "[room «Room A»]" in BASELINE["one-message"]
    assert "<m " not in "".join(BASELINE.values())
