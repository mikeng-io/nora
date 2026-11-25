"""Inventory rejects an omitted tracked image and a missing derivative source."""

from __future__ import annotations

import shutil
import subprocess

from PIL import Image

from scripts import visual_assets


def test_inventory_measures_tracked_coverage_and_real_source_existence(tmp_path, monkeypatch):
    git = shutil.which("git")
    subprocess.run(  # noqa: S603 - synthetic paths in isolated test repository
        [git, "init", "-q", str(tmp_path)], check=True
    )
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2)).save(source)
    output = tmp_path / "output.webp"
    Image.open(source).save(output)
    subprocess.run(  # noqa: S603 - synthetic paths in isolated test repository
        [git, "-C", str(tmp_path), "add", "source.png", "output.webp"], check=True
    )
    monkeypatch.setattr(visual_assets, "ROOT", tmp_path)
    entries = [
        {
            "id": "source",
            "path": "source.png",
            "parents": [],
            "consumers": ["output.webp"],
            "provenance": "synthetic test",
        },
        {
            "id": "output",
            "path": "output.webp",
            "parents": ["source.png"],
            "consumers": ["test"],
            "provenance": "synthetic derivative",
        },
    ]
    for entry in entries:
        entry["sha256"] = visual_assets.digest(tmp_path / entry["path"])
    data = {"assets": entries, "historical_exports": []}
    assert visual_assets.validate(data) == []
    # A real new tracked image is absent from the inventory, not a mocked list.
    extra = tmp_path / "omitted.png"
    extra.write_bytes(source.read_bytes())
    subprocess.run(  # noqa: S603 - synthetic paths in isolated test repository
        [git, "-C", str(tmp_path), "add", "omitted.png"], check=True
    )
    assert "unlisted tracked image: omitted.png" in visual_assets.validate(data)
    source.unlink()
    assert any("missing declared source source.png" in e for e in visual_assets.validate(data))


def test_a_copy_that_differs_from_its_local_source_fails_the_check(tmp_path, monkeypatch):
    git = shutil.which("git")
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)  # noqa: S603 - isolated repo
    (tmp_path / "mark.svg").write_text("<svg/>")
    (tmp_path / "copy.svg").write_text("<svg/>")
    subprocess.run(  # noqa: S603 - synthetic paths in isolated test repository
        [git, "-C", str(tmp_path), "add", "mark.svg", "copy.svg"], check=True
    )
    monkeypatch.setattr(visual_assets, "ROOT", tmp_path)
    entries = [
        {
            "id": "mark",
            "path": "mark.svg",
            "parents": [],
            "recipe": "source",
            "consumers": ["copy.svg"],
            "provenance": "synthetic",
        },
        {
            "id": "copy",
            "path": "copy.svg",
            "parents": ["mark.svg"],
            "recipe": "copy",
            "consumers": ["test"],
            "provenance": "synthetic copy",
        },
    ]
    for entry in entries:
        entry["sha256"] = visual_assets.digest(tmp_path / entry["path"])
    data = {"assets": entries, "historical_exports": []}
    assert visual_assets.validate(data) == []
    # The source changes and its recorded hash is refreshed; the copy is now stale.
    (tmp_path / "mark.svg").write_text("<svg id='new'/>")
    entries[0]["sha256"] = visual_assets.digest(tmp_path / "mark.svg")
    assert "copy.svg: copy differs from mark.svg; run sync" in visual_assets.validate(data)
