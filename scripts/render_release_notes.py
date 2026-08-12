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


def load_fragments(directory: Path) -> list[dict[str, object]]:
    fragments: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path}: fragment must be a JSON object")
        kind = data.get("type")
        if kind not in TYPES:
            raise ValueError(f"{path}: type must be one of {', '.join(sorted(TYPES))}")
        if kind == "none":
            if not isinstance(data.get("reason"), str) or not data["reason"].strip():
                raise ValueError(f"{path}: type 'none' requires a non-empty reason")
            fragments.append(data)
            continue
        if not isinstance(data.get("summary"), str) or not data["summary"].strip():
            raise ValueError(f"{path}: {kind} requires a non-empty summary")
        for field in ("details", "limitations"):
            value = data.get(field, [])
            valid_items = isinstance(value, list) and all(
                isinstance(item, str) and item.strip() for item in value
            )
            if not valid_items:
                raise ValueError(f"{path}: {field} must be an array of non-empty strings")
        fragments.append(data)
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
