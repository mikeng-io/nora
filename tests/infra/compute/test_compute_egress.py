"""`compute-egress`, the one way out of `nora-compute` (design/sandbox-runtime
§0, S0). Three things live in three files and must agree: the allowlist
(`EGRESS_ALLOWLIST`, which composition checks the live endpoints against at
boot), the proxy that enforces it (`deploy/compute-egress/squid.conf`), and
the network that makes the proxy the only exit (`docker-compose.yml`). These
tests read the real files, so a change to any one of them alone goes red."""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from nora.infra.compute.docker_backend import (
    EGRESS_ALLOWLIST,
    EGRESS_PROXY_URL,
    egress_refusal,
)

ROOT = Path(__file__).parents[3]
SQUID_CONF = ROOT / "deploy" / "compute-egress" / "squid.conf"


def _squid_allowlist() -> set[tuple[str, str, int, str]]:
    """Every `http_access allow` rule as (scheme, host, port, path prefix),
    expanded over EVERY value an acl lists (squid ORs them), so a second port
    or domain slipped onto an existing acl line shows up as an extra rule. A
    plain rule is http; a CONNECT rule is https with no path."""
    acls: dict[str, tuple[str, list[str]]] = {}
    rules: set[tuple[str, str, int, str]] = set()
    for line in SQUID_CONF.read_text().splitlines():
        words = line.split()
        if len(words) >= 4 and words[0] == "acl":
            acls[words[1]] = (words[2], words[3:])
        elif words[:2] == ["http_access", "allow"]:
            names = words[2:]
            kinds = {acls[n][0]: acls[n][1] for n in names if n in acls}
            connect = "CONNECT" in names and "!CONNECT" not in names
            paths = kinds.get("urlpath_regex", [""])
            for host, port, path in itertools.product(kinds["dstdomain"], kinds["port"], paths):
                rules.add((
                    "https" if connect else "http", host, int(port),
                    "" if connect else re.sub(r"^\^", "", path),
                ))
    return rules


def test_squid_conf_allows_exactly_the_allowlist():
    assert _squid_allowlist() == set(EGRESS_ALLOWLIST)


def test_squid_conf_ends_by_denying_everything_else():
    access = [line.split() for line in SQUID_CONF.read_text().splitlines()
              if line.startswith("http_access")]
    assert access[-1] == ["http_access", "deny", "all"]


def test_the_proxy_url_names_the_compose_service_and_squid_port():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    service = compose["services"]["compute-egress"]
    proxy = urlsplit(EGRESS_PROXY_URL)
    assert service["container_name"] == proxy.hostname
    assert f"http_port {proxy.port}" in SQUID_CONF.read_text().splitlines()
    assert "nora-compute" in service["networks"]


def test_nora_compute_has_no_route_but_the_proxy():
    """Internal alone left the gateway address on the bridge and the host's
    sshd answered on it (measured 2026-09-30); inhibit_ipv4 removes it."""
    network = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["networks"]["nora-compute"]
    assert network["internal"] is True
    assert network["driver_opts"]["com.docker.network.bridge.inhibit_ipv4"] == "true"


@pytest.mark.parametrize("url", [
    "http://host.docker.internal:20128/v1/chat/completions",
    "http://host.docker.internal:4318/v1/traces",
    "https://maps.googleapis.com/maps/api/staticmap",
])
def test_allowed_destinations_are_not_refused(url):
    assert egress_refusal(url) is None


@pytest.mark.parametrize("url", [
    "http://host.docker.internal:8000/v3/workspaces/list",   # Honcho, the §0 read
    "http://host.docker.internal:20128/dashboard",           # OmniRoute's admin side
    "http://host.docker.internal:4318/v1/metrics",
    "http://localhost:20128/v1/chat/completions",            # right port, wrong host
    "https://maps.googleapis.com:8443/",
    "http://maps.googleapis.com/maps/api/staticmap",         # plain http is not allowed
])
def test_everything_else_is_refused(url):
    assert egress_refusal(url) is not None
