"""The site's two CSPs serve two different pages and must stay separate.

The baseline has to keep Pagefind's WASM and worker alive (`'wasm-unsafe-eval'`,
`worker-src 'self' blob:`). The OpenWiki graph page has its own meta CSP that
also runs, so the header for `/wiki/graph/*` must name the same jsDelivr script
and Google Fonts origins or the stricter of the two policies wins and the graph
renders an empty canvas. Replacing the baseline with a wildcard would widen
every other page; this test pins the narrow matcher and both policies.
"""

from __future__ import annotations

import re
from pathlib import Path

CADDY = Path(__file__).resolve().parents[2] / "site/Caddyfile"


def _policies() -> tuple[str, str, str]:
    source = CADDY.read_text()
    headers = re.findall(r'Content-Security-Policy "([^"]+)"', source)
    assert len(headers) == 3, headers  # baseline, graph, 404
    graph = re.search(
        r"@graph path /wiki/graph/\*\n\theader @graph >Content-Security-Policy \"([^\"]+)\"",
        source,
    )
    assert graph, "the graph path needs its own replacing header"
    return headers[0], graph.group(1), headers[-1]


def test_the_baseline_csp_keeps_pagefinds_wasm_and_worker():
    baseline, _graph, not_found = _policies()
    for policy in (baseline, not_found):
        assert "'wasm-unsafe-eval'" in policy
        assert "worker-src 'self' blob:" in policy


def test_the_graph_csp_names_only_the_origins_its_meta_csp_already_allows():
    _baseline, graph, _not_found = _policies()
    assert "https://cdn.jsdelivr.net" in graph
    assert "https://fonts.googleapis.com" in graph
    assert "https://fonts.gstatic.com" in graph
    assert "default-src 'none'" in graph
    # No blanket scheme wildcard: the only https origins are the three named
    # above.
    assert graph.count("https://") == 3
