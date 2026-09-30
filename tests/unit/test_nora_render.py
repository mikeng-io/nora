"""`nora-render` (deploy/computer/render/nora_render.py, design/sandbox-runtime
§5, S3a): the offline renderers, loaded from the file the image copies.

`mermaid` needs mermaid-cli and its Chromium, which only the image has; it was
exercised by building the image and running it with `--network=none` (the PR
says how). What is pinned here is what the gate can hold without them: a page
never carries a script the source wrote, a chart is drawn from its data and
refuses data that does not fit, a report inlines its chart and its local
pictures, and `map` fails closed (exit 3, nothing written) without the network
and the key it needs.
"""

from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

RENDER = Path(__file__).parents[2] / "deploy" / "computer" / "render" / "nora_render.py"
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63f8cfc0f01f0005000201a5b3a8f90000000049454e44ae426082"
)


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


def test_a_markdown_page_escapes_the_html_its_source_wrote(render, tmp_path):
    src = tmp_path / "notes.md"
    src.write_text("---\ntitle: 報告\n---\nHi <script>alert(1)</script> **there**\n")
    out = tmp_path / "out" / "notes.html"
    assert _run(render, "markdown", src, out) == 0
    page = out.read_text()
    assert "<script" not in page
    assert "&lt;script&gt;" in page
    assert "<title>報告</title>" in page
    assert "<strong>there</strong>" in page


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


def test_a_report_inlines_its_chart_and_its_local_picture(render, tmp_path):
    (tmp_path / "dot.png").write_bytes(PNG_1PX)
    src = tmp_path / "report.md"
    src.write_text(
        "# Power\n\n```chart\n"
        '{"type": "line", "x": ["1", "2"], "series": [{"name": "kWh", "values": [3, 4]}]}\n'
        "```\n\n![dot](dot.png)\n\n![far](https://example.com/x.png)\n"
    )
    out = tmp_path / "report.html"
    assert _run(render, "report", src, out) == 0
    page = out.read_text()
    assert "<svg" in page and "<polyline" in page
    assert 'src="data:image/png;base64,' in page
    # A remote picture is left as written, never fetched.
    assert 'src="https://example.com/x.png"' in page


def test_a_map_without_its_network_and_key_fails_closed(render, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    src = tmp_path / "m.json"
    src.write_text('{"points": [{"lat": 22.28, "lng": 114.19}]}')
    out = tmp_path / "m.png"
    assert _run(render, "map", src, out) == 3
    assert not out.exists()
    assert "maps.googleapis.com" in capsys.readouterr().err


def test_a_missing_input_is_an_input_error(render, tmp_path):
    assert _run(render, "markdown", tmp_path / "nope.md", tmp_path / "x.html") == 2
