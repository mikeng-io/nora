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
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CADDY = ROOT / "site/Caddyfile"
PLACEHOLDER = "@@CSP_SCRIPT_HASHES@@"


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


def _script_src(policy: str) -> str:
    match = re.search(r"script-src ([^;]+)", policy)
    assert match, policy
    return match.group(1)


def test_the_baseline_and_404_script_src_take_hashes_not_unsafe_inline():
    """Any injected inline script runs under 'unsafe-inline'; hashes allow only the known ones."""
    baseline, _graph, not_found = _policies()
    for policy in (baseline, not_found):
        script = _script_src(policy)
        assert "'unsafe-inline'" not in script
        assert PLACEHOLDER in script
        assert "'wasm-unsafe-eval'" in script


def test_the_graph_page_keeps_its_own_wider_policy_on_purpose():
    _baseline, graph, _not_found = _policies()
    assert "'unsafe-inline'" in _script_src(graph)
    assert PLACEHOLDER not in graph


def test_the_image_bakes_the_hashes_into_the_caddyfile_it_ships():
    dockerfile = (ROOT / "site/Dockerfile").read_text()
    assert "csp-hashes.mjs Caddyfile.in Caddyfile site-dist wiki-dist" in dockerfile
    assert "COPY --from=csp /csp/Caddyfile /etc/caddy/Caddyfile" in dockerfile
    assert "COPY site/Caddyfile /etc/caddy/Caddyfile" not in dockerfile, (
        "the raw Caddyfile still holds the placeholder and would block every inline script"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_hash_generator_passes_its_own_tests():
    result = subprocess.run(  # noqa: S603 -- fixed repo script
        [shutil.which("node") or "node", "--test", "scripts/csp-hashes.test.mjs"],
        cwd=ROOT / "site",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
