"""`nora-render` (deploy/computer/render/nora_render.py): the image renderers,
loaded from the file the image copies.

`mermaid` needs mermaid-cli and its Chromium, which only the image has, so it
is not exercised here. What is pinned is what the gate can hold without them: a
chart is drawn from its data and refuses data that does not fit, nothing
renders a page (documents are the document path's), there is no map renderer
(maps are the document path's), and a chart's PNG fails closed (exit 3, nothing
written) without librsvg.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

RENDER = Path(__file__).parents[2] / "deploy" / "computer" / "render" / "nora_render.py"


@pytest.fixture(scope="module")
def render():
    spec = importlib.util.spec_from_file_location("nora_render_under_test", RENDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(render, *argv: str | Path) -> int:
    return render.run([str(a) for a in argv])


@pytest.mark.parametrize("kind", ["markdown", "report"])
def test_no_renderer_makes_a_page(render, tmp_path, kind):
    src = tmp_path / "notes.md"
    src.write_text("# Notes\n")
    with pytest.raises(SystemExit):
        render.run([kind, str(src), str(tmp_path / "notes.html")])
    assert not (tmp_path / "notes.html").exists()


def test_a_chart_is_only_ever_a_picture(render, tmp_path):
    src = tmp_path / "c.json"
    src.write_text('{"x": ["a"], "series": [{"values": [1]}]}')
    assert _run(render, "chart", src, tmp_path / "c.html") == 2
    assert _run(render, "chart", src, tmp_path / "c.pdf") == 2
    assert not (tmp_path / "c.html").exists()


def test_a_chart_is_drawn_from_its_data(render, tmp_path):
    src = tmp_path / "use.csv"
    src.write_text("month,2025,2026\nJan,10,12\nFeb,-3,15\n")
    out = tmp_path / "use.svg"
    assert _run(render, "chart", src, out, "--title", "Use") == 0
    svg = ET.fromstring(out.read_text())  # noqa: S314 - an SVG this test just drew
    bars = svg.findall("{http://www.w3.org/2000/svg}rect")
    # Four data bars (2 months x 2 series) plus a legend swatch per series.
    assert len(bars) == 6
    titles = [t.text for t in svg.iter("{http://www.w3.org/2000/svg}title")]
    assert "2025 Feb: -3" in titles


@pytest.mark.parametrize("body", [
    '{"type": "pie", "x": ["a"], "series": [{"values": [1]}]}',
    '{"x": ["a", "b"], "series": [{"values": [1]}]}',
    '{"x": ["a"], "series": [{"values": ["one"]}]}',
    "not json",
])
def test_a_chart_refuses_data_that_does_not_fit(render, tmp_path, body):
    src = tmp_path / "c.json"
    src.write_text(body)
    out = tmp_path / "c.svg"
    assert _run(render, "chart", src, out) == 2
    assert not out.exists()


def test_there_is_no_map_renderer(render, tmp_path):
    """Maps are the document path's (ADR 0020); one here needed Google egress
    and a secret in the VM."""
    src = tmp_path / "m.json"
    src.write_text('{"points": [{"lat": 22.28, "lng": 114.19}]}')
    with pytest.raises(SystemExit):
        render.run(["map", str(src), str(tmp_path / "m.png")])
    assert not (tmp_path / "m.png").exists()


@pytest.mark.skipif(shutil.which("rsvg-convert") is None,
                    reason="no librsvg here; the computer image installs it")
def test_a_chart_can_be_a_png(render, tmp_path):
    src = tmp_path / "use.csv"
    src.write_text("month,kWh\nJan,10\nFeb,12\n")
    out = tmp_path / "use.png"
    assert _run(render, "chart", src, out) == 0
    assert out.read_bytes().startswith(b"\x89PNG")


def test_a_chart_png_without_librsvg_fails_closed(render, tmp_path, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    src = tmp_path / "use.csv"
    src.write_text("month,kWh\nJan,10\n")
    assert _run(render, "chart", src, tmp_path / "use.png") == 3
    assert not (tmp_path / "use.png").exists()


def test_a_missing_input_is_an_input_error(render, tmp_path):
    assert _run(render, "chart", tmp_path / "nope.csv", tmp_path / "x.svg") == 2
