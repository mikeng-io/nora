"""A tool's text may point the model at a skill only if that skill ships.

Text that tells her to follow a skill that does not exist sends her to load
something she cannot find. Every «the <name> skill» in a string of
`nora/agent/tools.py` (descriptions and result notes alike; comments are not
strings and do not count) must name a skill in `nora/skills/`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from nora.skills.loader import SkillStore

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "nora" / "agent" / "tools.py"
POINTER = re.compile(r"\bthe\s+([a-z][a-z0-9-]*)\s+skill\b")


def _strings() -> list[str]:
    tree = ast.parse(TOOLS.read_text())
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _pointers(texts: list[str]) -> set[str]:
    # Adjacent literals («"...the document " "skill."») are one string by then:
    # the parser concatenates them, so a wrapped note is read whole.
    return {m for text in texts for m in POINTER.findall(" ".join(text.split()))}


def test_every_skill_a_tool_points_at_ships():
    shipped = {s.name for s in SkillStore(ROOT / "nora" / "skills").available()}
    named = _pointers(_strings())
    assert named, "the scan found no pointer at all; it has stopped reading the tools"
    assert named <= shipped, f"tools point at skills that do not ship: {named - shipped}"


def test_the_scan_catches_a_pointer_to_a_missing_skill():
    assert _pointers(["For a document, follow the document\n  skill."]) == {"document"}
