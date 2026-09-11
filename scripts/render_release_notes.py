"""Render unreleased JSON fragments into the next annotated version-tag note."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "release-fragments" / "unreleased"
TYPES = frozenset({"feature", "fix", "change", "security", "none"})


def _problems(path: Path, data: object) -> list[str]:
    """Everything wrong with ONE fragment, not the first thing. Returns `[]`
    for a valid one."""
    if not isinstance(data, dict):
        return [f"{path}: fragment must be a JSON object"]
    found: list[str] = []
    kind = data.get("type")
    if kind not in TYPES:
        return [f"{path}: type must be one of {', '.join(sorted(TYPES))}"]
    if kind == "none":
        if not isinstance(data.get("reason"), str) or not data["reason"].strip():
            found.append(f"{path}: type 'none' requires a non-empty reason")
        return found
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        found.append(f"{path}: {kind} requires a non-empty summary")
    for field in ("details", "limitations"):
        value = data.get(field, [])
        valid_items = isinstance(value, list) and all(
            isinstance(item, str) and item.strip() for item in value
        )
        if not valid_items:
            found.append(
                f"{path}: {field} must be an array of non-empty strings"
                + (" (it is a single string — wrap it, or split it into the "
                   "sentences it already is)" if isinstance(value, str) else "")
            )
    return found


def load_fragments(directory: Path) -> list[dict[str, object]]:
    """Every fragment, or a `ValueError` naming EVERY bad one.

    ALL OF THEM, NOT THE FIRST (issue #172). This raised on the first
    offending file, so a tree with seven malformed fragments reported one
    name; you fixed it, ran again, and got the next. Seven runs to learn
    what one run knew — which is how a gate teaches people to stop running
    it. Measured 2026-09-11: seven fragments on `master` wrote `details` as a
    string, and `make release-notes` had been reporting a single one of them.
    """
    fragments: list[dict[str, object]] = []
    problems: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path}: invalid JSON: {exc.msg}")
            continue
        found = _problems(path, data)
        if found:
            problems.extend(found)
            continue
        fragments.append(cast(dict[str, object], data))
    if problems:
        raise ValueError(
            f"{len(problems)} release-fragment problem(s):\n  "
            + "\n  ".join(problems)
        )
    return fragments


def render(fragments: list[dict[str, object]]) -> str:
    visible = [fragment for fragment in fragments if fragment["type"] != "none"]
    if not visible:
        return ""
    lines: list[str] = []
    for title, kinds in (("New", {"feature"}), ("Changed", {"change", "fix", "security"})):
        chosen = [fragment for fragment in visible if fragment["type"] in kinds]
        if not chosen:
            continue
        lines.append(f"{title}:")
        for fragment in chosen:
            lines.append(f"- {fragment['summary']}")
            details = cast(list[str], fragment.get("details", []))
            lines.extend(f"  - {detail}" for detail in details)
    limitations = [
        item for fragment in visible for item in cast(list[str], fragment.get("limitations", []))
    ]
    if limitations:
        lines.append("Limitations:")
        lines.extend(f"- {item}" for item in limitations)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        fragments = load_fragments(args.dir)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not fragments:
        print(f"{args.dir}: no release fragments", file=sys.stderr)
        return 2
    print(render(fragments), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
