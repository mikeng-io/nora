"""`nora-render` (deploy/computer/render/nora_render.py, design/sandbox-runtime
§5, S3a): the image renderers, loaded from the file the image copies.

`mermaid` needs mermaid-cli and its Chromium, which only the image has; it was
exercised by building the image and running it with `--network=none` (the PR
says how). What is pinned here is what the gate can hold without them: a chart
is drawn from its data and refuses data that does not fit, nothing renders a
page (`markdown` and `report` are gone: documents are the document path's),
and `map` fails closed (exit 3, nothing written) without the network and the
key it needs.
"""

from __future__ import annotations

import importlib.util
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


def test_a_map_without_its_network_and_key_fails_closed(render, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    src = tmp_path / "m.json"
    src.write_text('{"points": [{"lat": 22.28, "lng": 114.19}]}')
    out = tmp_path / "m.png"
    assert _run(render, "map", src, out) == 3
    assert not out.exists()
    assert "maps.googleapis.com" in capsys.readouterr().err


def test_a_missing_input_is_an_input_error(render, tmp_path):
    assert _run(render, "chart", tmp_path / "nope.csv", tmp_path / "x.svg") == 2
