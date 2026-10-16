"""A rendered Cognition prompt, read the way the model reads it: as blocks.

The prompt is well-formed XML-shaped text (every value escaped, every row an
element), so tests read it by parsing instead of substring-matching, which also
checks that nothing a message or a name says breaks the markup. Test support
only: it holds no assertion of its own.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def _no_stray_text(element: ET.Element) -> None:
    """An element that holds other elements holds ONLY them: whatever sits before
    the first child or after any child is whitespace. A leaf (an `m`, a `note`, a
    clock) is text, and is left alone. This is what «the prompt is blocks and
    items and nothing else» means: a line of prose outside a block would be read
    by the model as the prompt's own words."""
    if len(element):
        assert not (element.text or "").strip(), (element.tag, element.text)
        for child in element:
            assert not (child.tail or "").strip(), (element.tag, child.tag, child.tail)
            _no_stray_text(child)


def blocks(prompt: str) -> list[ET.Element]:
    """Every top-level block of the prompt, in order. Raises if it is not
    well-formed (a finding: the prompt escapes what people typed) or if any text
    sits outside a block or between an element's children."""
    root = ET.fromstring(f"<root>{prompt}</root>")  # noqa: S314 - our own bytes, rooted before parse
    _no_stray_text(root)
    return list(root)


def named(prompt: str, tag: str) -> list[ET.Element]:
    return [b for b in blocks(prompt) if b.tag == tag]


def items(prompt: str) -> list[ET.Element]:
    """The `item` elements of the prompt's one `agenda` block."""
    return [item for agenda in named(prompt, "agenda") for item in agenda]


def events(prompt: str) -> list[ET.Element]:
    """The `room_events` groups of the prompt's one `events` block."""
    return [group for block in named(prompt, "events") for group in block]


def rows(item: ET.Element) -> list[ET.Element]:
    """An item's rows: its `m` messages and `rollup` facts, oldest first."""
    return list(item)


def ids(item: ET.Element) -> list[int]:
    """The `n` of each message row of an item, in order."""
    return [int(r.attrib["n"]) for r in item if r.tag == "m"]


def words(item: ET.Element) -> str:
    """Everything the item's rows say, one string, for a 'was this shown' check."""
    return "\n".join((r.text or "") for r in item)
