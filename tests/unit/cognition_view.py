"""A rendered Cognition prompt, read the way the model reads it: as blocks.

Step 3b of `design/context-assembly/04-every-occasion.md` made the prompt well-formed
XML-shaped text (every value escaped, every row an element), so the tests that
used to substring-match a hand-written line read it by parsing instead, which is
also a check that nothing a message or a name says broke the markup. Test support
only: it holds no assertion of its own.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def blocks(prompt: str) -> list[ET.Element]:
    """Every top-level block of the prompt, in order. Raises if it is not
    well-formed, which is a finding: the prompt escapes what people typed."""
    return list(ET.fromstring(f"<root>{prompt}</root>"))  # noqa: S314 - our own bytes, rooted before parse


def named(prompt: str, tag: str) -> list[ET.Element]:
    return [b for b in blocks(prompt) if b.tag == tag]


def items(prompt: str) -> list[ET.Element]:
    return named(prompt, "agenda_item")


def rows(item: ET.Element) -> list[ET.Element]:
    """An item's rows: its `m` messages and `rollup` facts, oldest first."""
    return list(item)


def ids(item: ET.Element) -> list[int]:
    """The `n` of each message row of an item, in order."""
    return [int(r.attrib["n"]) for r in item if r.tag == "m"]


def words(item: ET.Element) -> str:
    """Everything the item's rows say, one string, for a 'was this shown' check."""
    return "\n".join((r.text or "") for r in item)
