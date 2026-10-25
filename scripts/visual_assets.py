#!/usr/bin/env python3
"""Inventory and synchronize tracked visual assets; no model calls or video renders.

uv run python scripts/visual_assets.py check|report|sync
sync reuses existing icon, showreel and rig processors (see their prerequisites).
Optional --sources JSON maps repository master paths to local generated PNGs.
Full sync prerequisites (already used by those processors):
uv run --no-project --python 3.12 --with pillow --with numpy --with scipy \
    --with pymatting --with "rembg[cpu]" --with fonttools --with brotli \
    python scripts/visual_assets.py sync --sources /tmp/mini08-sources.json
The site needs npm ci and site/.cache/NotoSansHK-wght.ttf for OG typography.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import struct
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
            if parent not in paths or not (ROOT / parent).is_file():
                errors.append(f"{a['path']}: missing declared source {parent}")
        if not a["consumers"] or not a["provenance"]:
            errors.append(f"{a['path']}: missing consumer/provenance")
        if hashes and a.get("status") in {"pending-refresh", "needs-recapture"}:
            errors.append(f"pending Mini refresh: {a['path']}")
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
            "including app/site/GitHub README inputs, "
            "dynamic art registries, rig textures and retained screenshots. Build-generated "
            "Astro/Next images are derived at build time and are not tracked. Paths and "
            "public URLs stay stable."
        ),
        "",
        (
            "Current Mini design: **option08 Balanced, approved 2026-10-26**. Brown irises, "
            "visible sclera, pupils, restrained highlights and an upper lid line. No nose "
            "marker; one tiny mouth. Human Bauhinia stays unchanged. Closed-eye and "
            "noncharacter art is explicitly identified below."
        ),
        "",
        (
            "Run `check` to verify coverage, source existence, byte fingerprints and the "
            "runtime handle; `sync` to rebuild app copies, icons, OG previews, showreel "
            "images and the live rig using existing processors. These commands do not "
            "generate new artwork, ingest media, publish profiles, deploy or render videos. A "
            "source edit requires a sync before its derivatives can be accepted."
        ),
        "",
        (
            "Generation sources are retained in the repository at the declared master paths. "
            "Unmodified generated originals and their metadata remain in the operator's local "
            "archive. Source-family prompts record intent, not modeling or visual acceptance."
        ),
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


def sanitize_png(content: bytes) -> bytes:
    """Drop identifying ancillary chunks without decoding or re-encoding IDAT."""
    signature = b"\x89PNG\r\n\x1a\n"
    if not content.startswith(signature):
        raise ValueError("source is not a PNG")
    result = bytearray(signature)
    offset = len(signature)
    while offset < len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        end = offset + length + 12
        if end > len(content):
            raise ValueError("truncated PNG chunk")
        kind = content[offset + 4 : offset + 8]
        if kind not in {b"caBX", b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            result.extend(content[offset:end])
        offset = end
        if kind == b"IEND":
            break
    return bytes(result)


def import_sources(data: dict, mapping: dict[str, str]) -> None:
    """Map repository master paths to local generated PNGs; keep public paths."""
    assets = {a["path"]: a for a in data["assets"]}
    for target, source in mapping.items():
        if (
            target not in assets
            or assets[target]["recipe"] != "source"
            or assets[target]["form"] not in {"mini", "ui"}
        ):
            raise ValueError(f"not a declared source master: {target}")
        path = ROOT / target
        path.parent.mkdir(parents=True, exist_ok=True)
        assets[target]["status"] = "active"
        assets[target]["eyes"] = (
            "mini08"
            if assets[target]["eyes"] not in {"closed", "no-eyes"}
            else assets[target]["eyes"]
        )
        assets[target]["unchanged_reason"] = None
        assets[target]["provenance"] = (
            "Option08 refresh from parent-supplied generation map, 2026-10-26; "
            "original metadata retained locally."
        )
        if path.suffix == ".png":
            path.write_bytes(sanitize_png(Path(source).read_bytes()))
        else:
            with Image.open(source) as image:
                image.save(path, "WEBP", quality=90, method=6, alpha_quality=100, exact=True)
    # This pin describes repository bytes only; no live media ingest occurs.
    skill = ROOT / "nora/skills/image-generation/SKILL.md"
    skill.write_text(
        re.sub(
            r"\[image [0-9a-f]{64}\]",
            f"[image {digest(ROOT / 'assets/nora-canonical-self.png')}]",
            skill.read_text(),
        )
    )


def sync(data: dict) -> None:
    errors = validate(data, hashes=False)
    if errors:
        raise SystemExit("\n".join(errors))
    app = load_module("nora_app_icons", "app/scripts/make-icons.py")
    for a in data["assets"]:
        dst = ROOT / a["path"]
        if a["recipe"] == "copy":
            shutil.copyfile(ROOT / a["parents"][0], dst)
        elif a["recipe"] == "webp":
            Image.open(ROOT / a["parents"][0]).save(dst, "WEBP", quality=90, method=6)
        elif a["recipe"] == "app-icon-webp":
            app.bare_icon(512).save(
                dst, "WEBP", quality=90, method=6, exact=True, alpha_quality=100
            )
    app.main()
    load_module("nora_site_images", "site/scripts/make_images.py").main()
    rc = load_module("nora_showreel_art", "showreel/pipeline/process_art.py").main()
    if rc:
        raise SystemExit(rc)
    subprocess.run(  # noqa: S603 - fixed repository processor, no external arguments
        [sys.executable, str(ROOT / "site/scripts/make_rig.py"), "--debug"], cwd=ROOT, check=True
    )
    by_path = {a["path"]: a for a in data["assets"]}

    # Masters precede outputs in the dependency graph, regardless of list order.
    def refresh(entry: dict) -> None:
        if entry["recipe"] == "source":
            return
        for parent in entry["parents"]:
            refresh(by_path[parent])
        parents = [by_path[p] for p in entry["parents"]]
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
    parser.add_argument(
        "--sources", type=Path, help="JSON object: repository master path to local PNG"
    )
    args = parser.parse_args()
    data = json.loads(MANIFEST.read_text())
    if args.sources:
        if args.command != "sync":
            parser.error("--sources requires sync")
        import_sources(data, json.loads(args.sources.read_text()))
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
