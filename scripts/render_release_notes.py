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
    for a valid one.

    «Not the first thing» applies WITHIN a fragment as well as across the
    directory, and the first cut of this function got that wrong: a bad
    `type` returned immediately, so a `details` fault on the same file
    stayed hidden until the type was fixed. That is the same fix-one-run-
    again loop `load_fragments` exists to end, one level down — and it was
    not hypothetical. Two of the fragments this change repaired
    (`gate-transport-measurement`, `retire-watchdog`) carried BOTH faults,
    so the run meant to report every problem on `master` reported eight
    where ten were waiting.
    """
    if not isinstance(data, dict):
        return [f"{path}: fragment must be a JSON object"]
    found: list[str] = []
    kind = data.get("type")
    if kind not in TYPES:
        found.append(f"{path}: type must be one of {', '.join(sorted(TYPES))}")
    if kind == "none":
        if not isinstance(data.get("reason"), str) or not data["reason"].strip():
            found.append(f"{path}: type 'none' requires a non-empty reason")
        # A `none` fragment renders NOTHING, so a NON-EMPTY `details` or
        # `limitations` on one is prose that silently never reaches the
        # release notes — the same class of loss this change exists to stop,
        # and the one a reader is least likely to catch, because the file
        # looks complete.
        #
        # The bar is emptiness, not absence. Eight `none` fragments on
        # `master` carry a `summary` and empty arrays, and nothing is lost by
        # that — banning the KEY would redden eight honest files to enforce a
        # preference, while a `none` carrying a real bullet is the actual
        # harm and no fragment in the tree does it. Measured before choosing
        # the rule: 8 of 20 `none` fragments carry the keys; 0 of 20 carry
        # content in them.
        carried = [
            f for f in ("details", "limitations") if data.get(f)
        ]
        if carried:
            found.append(
                f"{path}: type 'none' renders nothing, so a non-empty "
                f"{' and '.join(carried)} would never be read — move that "
                f"prose into `reason`, or give it a type that renders"
            )
        return found
    # Everything below holds for any RENDERING fragment, including one whose
    # `type` is already wrong — reporting the type and stopping is what hid
    # the second fault on two files.
    named = kind if kind in TYPES else "a rendering fragment"
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        found.append(f"{path}: {named} requires a non-empty summary")
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
    offending file, so a tree with eight malformed fragments reported one
    name; you fixed it, ran again, and got the next. Eight runs to learn
    what one run knew — which is how a gate teaches people to stop running
    it. Measured 2026-09-11 against `master`: eight fragments, carrying ten
    faults between them, and `make release-notes` had been reporting a
    single one of them.

    A file that cannot be READ is a problem about that file, not a reason to
    stop reading the rest — an undecodable byte or a permission error used to
    escape this loop and take every other finding with it, reporting a codec
    message with no path in it. Both are named and counted like any other.
    """
    fragments: list[dict[str, object]] = []
    problems: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path}: cannot be read: {exc}")
            continue
        try:
            data = json.loads(raw)
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
