"""The site's CSP allows only the inline scripts the build hashed.

The Caddyfile holds a placeholder that the image build replaces with the
SHA-256 of every inline script; 'unsafe-inline' would let any injected script
run. The site compiles no WebAssembly and starts no worker, so the policy
names neither.
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


def _policies() -> list[str]:
    headers = re.findall(r'Content-Security-Policy "([^"]+)"', CADDY.read_text())
    assert len(headers) == 2, headers  # baseline, 404
    return headers


def _script_src(policy: str) -> str:
    match = re.search(r"script-src ([^;]+)", policy)
    assert match, policy
    return match.group(1)


def test_the_baseline_and_404_script_src_take_hashes_not_unsafe_inline():
    for policy in _policies():
        script = _script_src(policy)
        assert "'unsafe-inline'" not in script
        assert PLACEHOLDER in script
        assert "'wasm-unsafe-eval'" not in script
        assert "worker-src" not in policy


def test_the_retired_wiki_redirects_home():
    assert "@wiki path /wiki /wiki/*\n\tredir @wiki / 308" in CADDY.read_text()


def test_the_image_bakes_the_hashes_into_the_caddyfile_it_ships():
    dockerfile = (ROOT / "site/Dockerfile").read_text()
    assert "csp-hashes.mjs Caddyfile.in Caddyfile site-dist" in dockerfile
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
