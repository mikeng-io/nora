"""`nora-render`: Smith's image renderers as one CLI on Nora's computer
Each makes a FILE, a picture, never a
page: a document people open is the document path's (ADR 0020), not this.

    nora-render chart   IN.json|IN.csv OUT.svg|OUT.png   a bar or line chart
    nora-render mermaid IN.mmd OUT.svg|OUT.png           a diagram

A chart is static SVG drawn here from the data (a PNG is that SVG rasterised
by librsvg), and a diagram is drawn by mermaid-cli in a local headless
Chromium: neither carries a live library, and both run offline on the
computer's deny-all network. A PNG is what goes out as a link or into chat;
an SVG is never a link. There is no map renderer: maps are the document
path's (ADR 0020).

Exit codes: 0 done; 2 the input is wrong (the message says what to fix);
3 a renderer needs a tool the computer does not have. Standard library only:
it runs on the image's python3.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

#: The series colours: the shell's ink first, then four that stay apart in
#: both light and dark (the page itself is greyscale; a chart needs to tell
#: its series apart).
PALETTE = ("#141414", "#2f6f62", "#b5651d", "#3b5b92", "#8a8a8a")

class InputError(Exception):
    """The input is wrong; the message says what to change (exit 2)."""


class MissingCapabilityError(Exception):
    """A renderer needs what the computer does not have (exit 3)."""


# -- mermaid -------------------------------------------------------------------


def render_mermaid(source: str, fmt: str) -> bytes:
    """Mermaid source to SVG (or PNG) with mermaid-cli, which draws in a
    local headless Chromium: no network."""
    mmdc = shutil.which("mmdc")
    if mmdc is None:
        raise MissingCapabilityError("mmdc (mermaid-cli) is not installed on this computer")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "diagram.mmd", Path(tmp) / f"diagram.{fmt}"
        config = Path(tmp) / "puppeteer.json"
        # The VM is the sandbox; Chromium's own needs user namespaces the
        # room's user does not have.
        config.write_text(json.dumps({"args": ["--no-sandbox"]}))
        src.write_text(source)
        ran = subprocess.run(  # noqa: S603 - the diagram is the input; the VM is the boundary
            [mmdc, "-i", str(src), "-o", str(out), "--puppeteerConfigFile", str(config),
             "--backgroundColor", "transparent", "--quiet"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if ran.returncode != 0 or not out.is_file():
            raise InputError("the diagram did not render: "
                             + (ran.stderr.strip()[-600:] or "mmdc gave no reason"))
        return out.read_bytes()


# -- charts ---------------------------------------------------------------------


def _json_spec(text: str, kind: str = "chart") -> dict[str, Any]:
    try:
        spec = json.loads(text)
    except ValueError as exc:
        raise InputError(f"the {kind} is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise InputError(f"a {kind} is one JSON object")
    return spec


def _chart_spec_csv(text: str, kind: str, title: str) -> dict[str, Any]:
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        raise InputError("a chart CSV needs a header row and at least two columns: "
                         "the labels, then one column per series")
    header, data = rows[0], rows[1:]
    series = []
    for col in range(1, len(header)):
        values = []
        for row in data:
            cell = row[col].strip() if col < len(row) else ""
            try:
                values.append(float(cell.replace(",", "")))
            except ValueError as exc:
                raise InputError(f"column {header[col]!r} has a value that is not a "
                                 f"number: {cell!r}") from exc
        series.append({"name": header[col], "values": values})
    return {"type": kind, "title": title, "x": [r[0] for r in data], "series": series,
            "x_label": header[0]}


def _nice_step(span: float) -> float:
    raw = span / 5 if span > 0 else 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    for step in (1, 2, 2.5, 5, 10):
        if raw <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def _fmt(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render_chart_svg(spec: dict[str, Any]) -> str:
    """A bar or line chart as static SVG: `{"type": "bar"|"line", "title",
    "x": [labels], "series": [{"name", "values": [numbers]}], "x_label",
    "y_label"}`."""
    kind = spec.get("type", "bar")
    if kind not in ("bar", "line"):
        raise InputError(f"chart type is bar or line, not {kind!r}")
    labels = [str(x) for x in spec.get("x") or []]
    series = spec.get("series") or []
    if not labels or not series or not all(isinstance(s, dict) for s in series):
        raise InputError("a chart needs x (the labels) and series ([{name, values}])")
    for s in series:
        values = s.get("values")
        if not isinstance(values, list) or len(values) != len(labels) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in values
        ):
            raise InputError(f"series {s.get('name')!r} needs one number per x label "
                             f"({len(labels)})")
    everything = [float(v) for s in series for v in s["values"]]
    low = min(0.0, min(everything))
    high = max(0.0, max(everything))
    step = _nice_step(high - low)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step if high > low else low + step
    width, height = 720, 400
    left, right, top, bottom = 64, 16, 48 if spec.get("title") else 20, 64
    plot_w, plot_h = width - left - right, height - top - bottom

    def y_at(value: float) -> float:
        return top + plot_h * (1 - (value - low) / (high - low))

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'role="img" font-family="system-ui, sans-serif" font-size="12">']
    if spec.get("title"):
        out.append(f'<text x="{left}" y="24" font-size="16" font-weight="600" '
                   f'fill="#141414">{html.escape(str(spec["title"]))}</text>')
    tick = low
    while tick <= high + step / 2:
        y = y_at(tick)
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="#dcdcdc"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" '
                   f'fill="#6b6b6b">{_fmt(tick)}</text>')
        tick += step
    band = plot_w / len(labels)
    for i, label in enumerate(labels):
        x = left + band * (i + 0.5)
        out.append(f'<text x="{x:.1f}" y="{top + plot_h + 18}" text-anchor="middle" '
                   f'fill="#6b6b6b">{html.escape(label)}</text>')
    zero = y_at(0.0)
    if kind == "bar":
        inner = band * 0.8
        bar_w = inner / len(series)
        for si, s in enumerate(series):
            colour = PALETTE[si % len(PALETTE)]
            for i, value in enumerate(s["values"]):
                x = left + band * i + band * 0.1 + bar_w * si
                y = y_at(float(value))
                out.append(f'<rect x="{x:.1f}" y="{min(y, zero):.1f}" width="{bar_w:.1f}" '
                           f'height="{abs(zero - y):.1f}" fill="{colour}">'
                           f'<title>{html.escape(str(s.get("name", "")))} '
                           f'{html.escape(labels[i])}: {_fmt(float(value))}</title></rect>')
    else:
        for si, s in enumerate(series):
            colour = PALETTE[si % len(PALETTE)]
            points = " ".join(
                f"{left + band * (i + 0.5):.1f},{y_at(float(v)):.1f}"
                for i, v in enumerate(s["values"])
            )
            out.append(f'<polyline points="{points}" fill="none" stroke="{colour}" '
                       f'stroke-width="2"/>')
            for i, v in enumerate(s["values"]):
                out.append(f'<circle cx="{left + band * (i + 0.5):.1f}" '
                           f'cy="{y_at(float(v)):.1f}" r="3" fill="{colour}">'
                           f'<title>{html.escape(labels[i])}: {_fmt(float(v))}</title>'
                           f'</circle>')
    out.append(f'<line x1="{left}" x2="{width - right}" y1="{zero:.1f}" y2="{zero:.1f}" '
               f'stroke="#141414"/>')
    if len(series) > 1 or series[0].get("name"):
        x = left
        for si, s in enumerate(series):
            name = html.escape(str(s.get("name", f"series {si + 1}")))
            out.append(f'<rect x="{x}" y="{height - 22}" width="10" height="10" '
                       f'fill="{PALETTE[si % len(PALETTE)]}"/>')
            out.append(f'<text x="{x + 14}" y="{height - 13}" fill="#141414">{name}</text>')
            x += 24 + 7 * len(name)
    if spec.get("x_label"):
        out.append(f'<text x="{width - right}" y="{top + plot_h + 36}" text-anchor="end" '
                   f'fill="#6b6b6b">{html.escape(str(spec["x_label"]))}</text>')
    if spec.get("y_label"):
        out.append(f'<text x="14" y="{top + plot_h / 2:.1f}" text-anchor="middle" '
                   f'fill="#6b6b6b" transform="rotate(-90 14 {top + plot_h / 2:.1f})">'
                   f'{html.escape(str(spec["y_label"]))}</text>')
    out.append("</svg>")
    return "".join(out)


def rasterise(svg: str) -> bytes:
    """An SVG drawn here as a PNG, by librsvg's `rsvg-convert` (in the
    image), offline."""
    rsvg = shutil.which("rsvg-convert")
    if rsvg is None:
        raise MissingCapabilityError("rsvg-convert (librsvg) is not installed on this computer")
    ran = subprocess.run(  # noqa: S603 - fixed argv; the SVG was drawn here
        [rsvg, "--format", "png", "--zoom", "2", "--background-color", "white"],
        input=svg.encode("utf-8"), capture_output=True, timeout=60, check=False,
    )
    if ran.returncode != 0 or not ran.stdout.startswith(b"\x89PNG"):
        raise MissingCapabilityError("the chart could not be rasterised: "
                                     + (ran.stderr.decode("utf-8", "replace").strip()[-300:]
                                        or "no reason"))
    return ran.stdout


def _write(out: Path, data: str | bytes) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        out.write_text(data, encoding="utf-8")
    else:
        out.write_bytes(data)


def run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="nora-render", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="kind", required=True)
    mermaid = sub.add_parser("mermaid")
    mermaid.add_argument("input", type=Path)
    mermaid.add_argument("output", type=Path)
    chart = sub.add_parser("chart")
    chart.add_argument("input", type=Path)
    chart.add_argument("output", type=Path)
    chart.add_argument("--type", choices=("bar", "line"), default="bar",
                       help="for a CSV input; a JSON spec names its own")
    chart.add_argument("--title", default="", help="for a CSV input")
    args = parser.parse_args(argv)
    src: Path = args.input
    out: Path = args.output
    try:
        if not src.is_file():
            raise InputError(f"no such input file: {src}")
        text = src.read_text(encoding="utf-8")
        suffix = out.suffix.lower()
        if args.kind == "mermaid":
            if suffix not in (".svg", ".png"):
                raise InputError("a diagram is written as OUT.svg or OUT.png")
            _write(out, render_mermaid(text, suffix[1:]))
        elif args.kind == "chart":
            spec = (_chart_spec_csv(text, args.type, args.title)
                    if src.suffix.lower() == ".csv" else _json_spec(text))
            if suffix == ".svg":
                _write(out, render_chart_svg(spec))
            elif suffix == ".png":
                _write(out, rasterise(render_chart_svg(spec)))
            else:
                raise InputError("a chart is written as OUT.svg or OUT.png")
    except InputError as exc:
        print(f"nora-render {args.kind}: {exc}", file=sys.stderr)
        return 2
    except MissingCapabilityError as exc:
        print(f"nora-render {args.kind}: {exc}", file=sys.stderr)
        return 3
    print(f"{out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
