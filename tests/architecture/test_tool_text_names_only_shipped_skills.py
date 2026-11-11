"""A tool's text may point the model at a skill only if that skill ships.

Text that tells her to follow a skill that does not exist sends her to load
something she cannot find. Every «the <name> skill» in a string of the tool
sources (descriptions and result notes alike; comments are not strings and do
not count) must name a skill in `nora/skills/`. ADR 0037 moved the tool bodies
from `nora/agent/tools.py` into each area's `adapters/tools*` — the scan reads
those plus the agent-side toolkit/runtime files.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from nora.skills.loader import SkillStore

ROOT = Path(__file__).resolve().parents[2]
POINTER = re.compile(r"\bthe\s+([a-z][a-z0-9-]*)\s+skill\b")


def _tool_sources() -> list[Path]:
    paths = [ROOT / "nora" / "agent" / "runtime_tools.py"]
    paths += sorted((ROOT / "nora" / "agent" / "toolkit").rglob("*.py"))
    for area in sorted((ROOT / "nora").iterdir()):
        adapters = area / "adapters"
        if not adapters.is_dir():
            continue
        paths += sorted(adapters.glob("tools.py"))
        paths += sorted((adapters / "tools").rglob("*.py"))
    return paths


def _strings() -> list[str]:
    return [
        n.value
        for path in _tool_sources()
        for n in ast.walk(ast.parse(path.read_text()))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


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
