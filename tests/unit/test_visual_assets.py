"""Inventory rejects an omitted tracked image and a missing derivative source."""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import zlib

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


def test_png_metadata_removal_preserves_idat_and_decoded_pixels():
    image = Image.new("RGBA", (3, 2), (101, 91, 83, 192))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    original = stream.getvalue()
    metadata = b"SyntheticAuthor\x00synthetic metadata"
    kind = b"tEXt"
    chunk = (
        struct.pack(">I", len(metadata))
        + kind
        + metadata
        + struct.pack(">I", zlib.crc32(kind + metadata))
    )
    tagged = original[:33] + chunk + original[33:]
    cleaned = visual_assets.sanitize_png(tagged)
    # Exact original bytes, including compressed IDAT, survive unchanged.
    assert cleaned == original
    assert Image.open(io.BytesIO(cleaned)).tobytes() == image.tobytes()
