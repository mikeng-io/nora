"""`render_mermaid` — one of the three genuinely deterministic skills (this
plan's own Architecture note): Mermaid diagram source in, static SVG out,
rendered SERVER-SIDE (inside this sandbox, at job time) via `mermaid-cli`
(`mmdc`), never shipped as live `mermaid.js` in the artifact itself. Nora
Compute spec §6/§8: self-contained by default, no client JS required to view
the result.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


class MermaidRenderError(Exception):
    """`mmdc` refused the input -- malformed diagram source, not a transport
    or environment failure. The caller (Smith's own agent loop) should treat
    this as "try a different diagram" or "fall back to text", not retry
    blindly."""


def render_mermaid(diagram_source: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "diagram.mmd"
        output_path = Path(tmp) / "diagram.svg"
        # `mmdc` launches its own Chromium under Puppeteer to render, and
        # this container runs it as root with no unprivileged-user-namespace
        # setup for Chromium's own sandbox to use -- Chromium refuses to
        # start at all under those conditions unless told not to sandbox
        # itself. `--puppeteerConfigFile` is the documented way to pass
        # Puppeteer launch args (confirmed against the installed
        # `@mermaid-js/mermaid-cli`'s `mmdc --help`) through to the browser
        # launch, rather than a CLI flag `mmdc` itself exposes.
        puppeteer_config_path = Path(tmp) / "puppeteer-config.json"
        puppeteer_config_path.write_text(json.dumps({"args": ["--no-sandbox"]}))
        input_path.write_text(diagram_source)

        result = subprocess.run(  # noqa: S603
            # S603/S607: this call running `mmdc` against LLM-authored
            # `diagram_source` by design IS this function's whole job -- the
            # untrusted-input containment boundary is the Docker sandbox
            # this process runs inside, not a check at this call site.
            # `"mmdc"` (a partial path) deliberately resolves via this
            # image's own `PATH`, matching agent.py's own `"python3"` call.
            [  # noqa: S607
                "mmdc",
                "-i", str(input_path),
                "-o", str(output_path),
                "--outputFormat", "svg",
                "--puppeteerConfigFile", str(puppeteer_config_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or "mmdc failed with no output"
            raise MermaidRenderError(error)
        return output_path.read_text()
