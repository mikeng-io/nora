#!/usr/bin/env python3
"""Inventory and synchronize the images this repository ships; no model calls.

uv run python scripts/visual_assets.py check|report|sync
Her art's masters live in the private nora-assets repository. A
copy here names its source as `nora-assets:<path>@<commit>`; refreshing it is a
PR that copies the new file in. sync rebuilds only what is made here: the
portal's icons from the emblem (app/scripts/make-icons.py, needs rsvg-convert).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "assets/visual-assets.json"
IMAGE_SUFFIXES = {
    ".png",
    ".webp",
    ".jpg",
    ".jpeg",
    ".svg",
    ".ico",
    ".gif",
    ".avif",
    ".bmp",
    ".tif",
    ".tiff",
}


EXTERNAL = "nora-assets:"  # a master kept in the private art repository


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load processor: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(data: dict, *, hashes: bool = True) -> list[str]:
    tracked = (
        subprocess.check_output(  # noqa: S603 - fixed read-only git command
            [shutil.which("git") or "/usr/bin/git", "ls-files", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    images = {p for p in tracked if Path(p).suffix.lower() in IMAGE_SUFFIXES}
    entries = data["assets"]
    paths = {a["path"] for a in entries}
    errors = [f"unlisted tracked image: {p}" for p in sorted(images - paths)]
    if len(paths) != len(entries) or len({a["id"] for a in entries}) != len(entries):
        errors.append("duplicate asset path or stable id")
    for a in entries:
        path = ROOT / a["path"]
        if not path.is_file():
            errors.append(f"missing asset: {a['path']}")
            continue
        for parent in a["parents"]:
            if parent.startswith(EXTERNAL):
                continue
            if parent not in paths or not (ROOT / parent).is_file():
                errors.append(f"{a['path']}: missing declared source {parent}")
        if not a["consumers"] or not a["provenance"]:
            errors.append(f"{a['path']}: missing consumer/provenance")
        if hashes and a.get("status") in {"pending-refresh", "needs-recapture"}:
            errors.append(f"pending Mini refresh: {a['path']}")
        local = [p for p in a["parents"] if not p.startswith(EXTERNAL)]
        copy_of = ROOT / local[0] if a.get("recipe") == "copy" and local else None
        if hashes and copy_of and copy_of.is_file() and digest(path) != digest(copy_of):
            errors.append(f"{a['path']}: copy differs from {local[0]}; run sync")
        if hashes and a.get("sha256") != digest(path):
            errors.append(f"changed bytes; run sync after reviewing source: {a['path']}")
    exports = {e["path"] for e in data["historical_exports"]}
    for p in sorted({p for p in tracked if p.endswith(".mp4")} - exports):
        errors.append(f"unlisted historical video: {p}")
    for p in sorted(exports):
        if not (ROOT / p).is_file():
            errors.append(f"missing historical video: {p}")
    # The runtime pin must name exactly the bytes shipped as the canonical source.
    canonical = ROOT / "assets/nora-canonical-self.png"
    if canonical.exists():
        handle = f"[image {digest(canonical)}]"
        if handle not in (ROOT / "nora/skills/image-generation/SKILL.md").read_text():
            errors.append("runtime self handle differs from sanitized canonical source")
    return errors


def report(data: dict) -> str:
    lines = [
        "# Nora visual assets",
        "",
        "Generated from `visual-assets.json` by `uv run python scripts/visual_assets.py report`.",
        "",
        (
            "All tracked repository images and newly declared source artwork are listed, "
            "including the portal's art and icons, the README's images and retained "
            "screenshots. Her art's masters are in the private nora-assets repository; a "
            "copy names its source as `nora-assets:<path>@<commit>`. Build-generated Next "
            "images are derived at build time and are not tracked."
        ),
        "",
        (
            "Run `check` to verify coverage, source existence, byte fingerprints and the "
            "runtime handle; `sync` to rebuild the portal's icons. These commands do not "
            "generate new artwork, ingest media, publish profiles or deploy."
        ),
        "",
        ("Her art here is all rights reserved (LICENSE); MIT covers the rest of the repository."),
        "",
        "## Verification boundaries",
        "",
    ]
    lines += [
        f"- **{key.replace('_', ' ')}:** {value}"
        for key, value in data.get("verification", {}).items()
    ]
    lines += [
        "",
        (
            "Status `active` means the file is the current repository input/output; it does "
            "not mean deployment or browser acceptance. Per-file visual review remains "
            "pending wherever stated below. Consumers distinguish imports, renderer use, "
            "processor inputs and documentation references. An app copy never inherits "
            "the site's consumers merely because its basename matches."
        ),
        "",
        "## Image inventory",
        "",
        (
            "| Asset (stable ID in manifest) | Form / role / status | Eyes | Source / recipe "
            "| Consumers | Provenance / unchanged reason |"
        ),
        "|---|---|---|---|---|---|",
    ]
    for a in data["assets"]:
        link = f"[`{a['path']}`](../{a['path']})<br>ID: `{a['id']}`"
        parents = ", ".join(f"`{p}`" for p in a["parents"]) or "master / no parent"
        consumers = "<br>".join(f"`{c}`" for c in a["consumers"])
        note = a["provenance"] + (" " + a["unchanged_reason"] if a.get("unchanged_reason") else "")
        if a.get("visual_review"):
            note += " **" + a["visual_review"] + "**"
        lines.append(
            f"| {link} | {a['form']} / {a['role']} / {a['status']} | "
            f"{a['eyes']} | {parents}; `{a['recipe']}` | {consumers} | {note} |"
        )
    lines += [
        "",
        "## Source to derivative outputs",
        "",
        "References with recipe `source` are generated artwork, not deterministic copies.",
        "",
    ]
    for source in data["assets"]:
        if source["recipe"] != "source":
            continue
        outputs = [
            a["path"]
            for a in data["assets"]
            if source["path"] in a["parents"] and a["recipe"] != "source"
        ]
        references = [
            a["path"]
            for a in data["assets"]
            if source["path"] in a["parents"] and a["recipe"] == "source"
        ]
        if outputs:
            lines += ["- `" + source["path"] + "` → " + ", ".join(f"`{p}`" for p in outputs)]
        if references:
            lines += [
                "- `"
                + source["path"]
                + "` → generated reference artwork: "
                + ", ".join(f"`{p}`" for p in references)
            ]
    groups = {}
    for a in data["assets"]:
        groups.setdefault(a.get("sha256"), []).append(a["path"])
    lines += [
        "",
        "## Identical outputs",
        "",
        "Duplicate files remain declared build outputs with a single source.",
    ]
    for sha, paths in groups.items():
        if sha and len(paths) > 1:
            lines += ["", "- " + ", ".join(f"`{p}`" for p in paths)]
    lines += [
        "",
        "## Historical video exports",
        "",
        (
            "These are retained exports; they can show the prior eye design. No video "
            "rerender is part of this image synchronization."
        ),
        "",
    ]
    lines += [f"- `{a['path']}`: {a['reason']}" for a in data["historical_exports"]]
    return "\n".join(lines) + "\n"


def sync(data: dict) -> None:
    errors = validate(data, hashes=False)
    if errors:
        raise SystemExit("\n".join(errors))
    for a in data["assets"]:
        local = [p for p in a["parents"] if not p.startswith(EXTERNAL)]
        if a["recipe"] == "copy" and local:
            shutil.copyfile(ROOT / local[0], ROOT / a["path"])
    load_module("nora_app_icons", "app/scripts/make-icons.py").main()
    by_path = {a["path"]: a for a in data["assets"]}

    # Masters precede outputs in the dependency graph, regardless of list order.
    def refresh(entry: dict) -> None:
        local = [p for p in entry["parents"] if not p.startswith(EXTERNAL)]
        if entry["recipe"] == "source" or not local:
            return
        for parent in local:
            refresh(by_path[parent])
        parents = [by_path[p] for p in local]
        entry["status"] = (
            "pending-refresh"
            if any(p["status"] == "pending-refresh" for p in parents)
            else "active"
        )
        if entry["role"] not in {"rig-plate", "rig-texture"}:
            entry["eyes"] = parents[0]["eyes"]

    for a in data["assets"]:
        refresh(a)
        a["sha256"] = digest(ROOT / a["path"])
        if Path(a["path"]).suffix != ".svg":
            with Image.open(ROOT / a["path"]) as im:
                a["size"] = list(im.size)
                a["mode"] = im.mode
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "report", "sync"])
    args = parser.parse_args()
    data = json.loads(MANIFEST.read_text())
    if args.command == "sync":
        sync(data)
    rendered = report(data)
    readme = ROOT / "assets/README.md"
    if args.command != "check":
        readme.write_text(rendered)
    errors = validate(data)
    if not readme.exists() or readme.read_text() != rendered:
        errors.append("assets/README.md differs from manifest; run report")
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(
            f"Byte/coverage checks passed: {len(data['assets'])} images and "
            f"{len(data['historical_exports'])} historical videos; "
            "visual/runtime acceptance is separate (see inventory)"
        )
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
