"""`nora-render`: Smith's renderers as one CLI on Nora's computer
(design/sandbox-runtime §5, §6; S3a).

    nora-render markdown IN.md  OUT.html   markdown to a page
    nora-render report   IN.md  OUT.html   markdown plus ```mermaid and
                                           ```chart blocks and local images,
                                           all inlined: one self-contained page
    nora-render mermaid  IN.mmd OUT.svg    a diagram (OUT.png for a picture)
    nora-render chart    IN.json|IN.csv OUT.svg|OUT.html   a bar or line chart
    nora-render map      IN.json OUT.png|OUT.html          a static map

Every page is Smith's Mono page shell (`page_shell.py`) with no script in it:
markdown is rendered with raw HTML ESCAPED (the source is model-written), a
diagram and a chart are static SVG drawn here, never a live library.

The computer's network is deny-all. Everything above runs offline except
`map`, which needs `maps.googleapis.com` (Geocoding for an address, Static
Maps for the picture) and `GOOGLE_MAPS_API_KEY`, which the VM only sees as an
sbx placeholder the proxy fills in on requests to that host. Without both it
exits 3 and says so; nothing else is attempted.

Exit codes: 0 done; 2 the input is wrong (the message says what to fix);
3 a renderer needs something the computer does not have (the network, a key,
the diagram tool).
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_shell import page_shell

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
MAPS_HOST = "maps.googleapis.com"

#: The series colours: the shell's ink first, then four that stay apart in
#: both light and dark (the page itself is greyscale; a chart needs to tell
#: its series apart).
PALETTE = ("#141414", "#2f6f62", "#b5651d", "#3b5b92", "#8a8a8a")

#: Extra page CSS: diagrams and charts scale to the column.
_FIGURE_CSS = (
    "<style>.section svg, .section img { max-width: 100%; height: auto; }"
    " figure { margin: 0; } figcaption { color: var(--muted); font-size: .9rem; }"
    "</style>"
)


class InputError(Exception):
    """The input is wrong; the message says what to change (exit 2)."""


class MissingCapabilityError(Exception):
    """A renderer needs what the computer does not have (exit 3)."""


# -- markdown ----------------------------------------------------------------

_FORMATTER = HtmlFormatter(nowrap=True, cssclass="highlight")
_CODE_STYLE = f"<style>{_FORMATTER.get_style_defs('.highlight')}</style>"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _highlight(code: str, lang: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    return f'<pre class="highlight"><code>{highlight(code, lexer, _FORMATTER)}</code></pre>'


def _markdown(*, fences: dict[str, Any] | None = None, base: Path | None = None) -> MarkdownIt:
    # `html: False`: raw HTML in the (model-written) source is escaped, never
    # rendered, so no script reaches a page (Smith's Global Constraint 1).
    md = MarkdownIt("commonmark", {"html": False, "highlight": _highlight})
    md.enable(["table", "strikethrough"])
    if fences:
        default_fence = md.renderer.rules["fence"]

        def fence(_self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
            token = tokens[idx]
            lang = token.info.strip().split(" ", 1)[0].lower()
            if lang in fences:
                return fences[lang](token.content)
            return default_fence(tokens, idx, options, env)

        md.add_render_rule("fence", fence)
    if base is not None:
        default_image = md.renderer.rules["image"]

        def image(_self, tokens, idx, options, env):  # type: ignore[no-untyped-def]
            token = tokens[idx]
            src = str(token.attrGet("src") or "")
            inlined = _inline_image(src, base)
            if inlined is not None:
                token.attrSet("src", inlined)
            return default_image(tokens, idx, options, env)

        md.add_render_rule("image", image)
    return md


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    found = _FRONT_MATTER.match(text)
    if not found:
        return {}, text
    meta: dict[str, str] = {}
    for line in found.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[found.end():]


def _title(meta: dict[str, str], text: str, fallback: str) -> str:
    if meta.get("title"):
        return meta["title"]
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _accent(meta: dict[str, str]) -> str | None:
    accent = meta.get("accent") or None
    if accent is not None and not re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent):
        raise InputError(f"accent must be a hex colour like #2f6f62, got {accent!r}")
    return accent


def _inline_image(src: str, base: Path) -> str | None:
    """A local picture as a `data:` URI, so the page carries it. A remote or
    missing one is left as written."""
    if not src or re.match(r"^[a-z][a-z0-9+.-]*:", src, re.IGNORECASE):
        return None
    path = (base / urllib.parse.unquote(src)).resolve()
    if not path.is_file():
        return None
    media_type = mimetypes.guess_type(path.name)[0] or ""
    if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp",
                          "image/svg+xml"):
        return None
    return f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def render_markdown_page(text: str, *, fallback_title: str) -> str:
    meta, body = _split_front_matter(text)
    return page_shell(
        title=_title(meta, body, fallback_title),
        sections=[_CODE_STYLE + _markdown().render(body)],
        accent=_accent(meta),
    )


def render_report_page(text: str, *, fallback_title: str, base: Path) -> str:
    """Markdown with its ```mermaid and ```chart blocks drawn as inline SVG
    and its local pictures inlined: one file that opens anywhere. A block
    that cannot be drawn stops the render (exit 2 or 3): a report that
    silently drops a figure is worse than one that says it failed."""
    meta, body = _split_front_matter(text)
    fences = {
        "mermaid": lambda source: f"<figure>{render_mermaid(source, 'svg').decode()}</figure>",
        "chart": lambda source: f"<figure>{render_chart_svg(_json_spec(source))}</figure>",
    }
    return page_shell(
        title=_title(meta, body, fallback_title),
        sections=[_FIGURE_CSS + _CODE_STYLE + _markdown(fences=fences, base=base).render(body)],
        accent=_accent(meta),
    )


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


# -- maps (network) -----------------------------------------------------------------


def _get(url: str, params: dict[str, str]) -> bytes:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise MissingCapabilityError(
            f"map needs GOOGLE_MAPS_API_KEY and network access to {MAPS_HOST}, "
            "which this computer does not have"
        )
    query = urllib.parse.urlencode({**params, "key": key})
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=20) as response:  # noqa: S310
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MissingCapabilityError(
            f"map could not reach {MAPS_HOST} ({type(exc).__name__}): the computer's "
            "network does not allow it"
        ) from exc


def _geocode(address: str) -> tuple[float, float]:
    reply = json.loads(_get(GEOCODE_URL, {"address": address}))
    if reply.get("status") != "OK" or not reply.get("results"):
        raise InputError(f"could not find {address!r} on the map ({reply.get('status')})")
    location = reply["results"][0]["geometry"]["location"]
    return float(location["lat"]), float(location["lng"])


def render_map_png(spec: dict[str, Any]) -> bytes:
    """`{"title", "points": [{"lat", "lng"} or {"address"}, ...]}` to a
    static map picture, fetched once and embedded, never a live map."""
    points = spec.get("points")
    if not isinstance(points, list) or not points:
        raise InputError("a map needs points: [{lat, lng} or {address}]")
    coords = []
    for point in points:
        if not isinstance(point, dict):
            raise InputError("each point is {lat, lng} or {address}")
        if "lat" in point and "lng" in point:
            coords.append((float(point["lat"]), float(point["lng"])))
        elif isinstance(point.get("address"), str):
            coords.append(_geocode(point["address"]))
        else:
            raise InputError("each point is {lat, lng} or {address}")
    markers = "|".join(f"{lat:.6f},{lng:.6f}" for lat, lng in coords)
    return _get(STATIC_MAPS_URL, {"size": "640x400", "scale": "2", "markers": markers})


# -- the CLI --------------------------------------------------------------------------


def _write(out: Path, data: str | bytes) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        out.write_text(data, encoding="utf-8")
    else:
        out.write_bytes(data)


def _page(title: str, fragment: str) -> str:
    return page_shell(title=title, sections=[_FIGURE_CSS + fragment])


def run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="nora-render", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("markdown", "report", "mermaid", "map"):
        cmd = sub.add_parser(kind)
        cmd.add_argument("input", type=Path)
        cmd.add_argument("output", type=Path)
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
        if args.kind == "markdown":
            _write(out, render_markdown_page(text, fallback_title=src.stem))
        elif args.kind == "report":
            _write(out, render_report_page(text, fallback_title=src.stem,
                                           base=src.resolve().parent))
        elif args.kind == "mermaid":
            if suffix not in (".svg", ".png"):
                raise InputError("a diagram is written as OUT.svg or OUT.png")
            _write(out, render_mermaid(text, suffix[1:]))
        elif args.kind == "chart":
            spec = (_chart_spec_csv(text, args.type, args.title)
                    if src.suffix.lower() == ".csv" else _json_spec(text))
            svg = render_chart_svg(spec)
            if suffix == ".svg":
                _write(out, svg)
            elif suffix == ".html":
                _write(out, _page(str(spec.get("title") or src.stem), svg))
            else:
                raise InputError("a chart is written as OUT.svg or OUT.html")
        else:
            spec = _json_spec(text, "map")
            picture = render_map_png(spec)
            if suffix == ".png":
                _write(out, picture)
            elif suffix == ".html":
                alt = html.escape(str(spec.get("title") or "map"))
                encoded = base64.b64encode(picture).decode("ascii")
                _write(out, _page(str(spec.get("title") or src.stem),
                                  f'<img src="data:image/png;base64,{encoded}" alt="{alt}"/>'))
            else:
                raise InputError("a map is written as OUT.png or OUT.html")
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
