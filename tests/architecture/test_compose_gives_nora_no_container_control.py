"""The nora service holds no control over the host's Docker daemon.

Nothing in nora needs the host `docker.sock` mounted into the nora container,
nor the `nora-compute` network with its `compute-egress` proxy. Nora's
computer is one VM run by `deploy/computer/nora-computer` on the host, not by
anything in her own process (ADR 0019).

This reads `docker-compose.yml` as parsed YAML, not as text: a comment that
still names the socket is not a mount, and a mount written in the long syntax
(`type: bind, source: /var/run/docker.sock`) is one a grep for a string on a
`- /var/run/docker.sock:` line would miss.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

RETIRED_NETWORKS = {"nora-compute", "compute-egress"}


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _mount_sources(service: dict) -> list[str]:
    """Every volume's host-side source, short or long syntax."""
    sources: list[str] = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, str):
            sources.append(volume.split(":", 1)[0])
        elif isinstance(volume, dict):
            sources.append(str(volume.get("source", "")))
    return sources


def _network_names(service: dict) -> set[str]:
    networks = service.get("networks") or []
    return set(networks)


def test_the_nora_service_is_found_so_the_checks_below_can_fail():
    """A positive control: the parse reaches the service the rest is about."""
    assert "nora" in _compose()["services"]
    assert _mount_sources(_compose()["services"]["nora"]), "no volumes parsed for nora"


def test_no_service_mounts_a_docker_socket():
    """Every service, not only nora: nothing in this stack controls the daemon."""
    found = {
        name: [s for s in _mount_sources(service) if "docker.sock" in s]
        for name, service in _compose()["services"].items()
    }
    found = {k: v for k, v in found.items() if v}
    assert not found, f"services mounting the host Docker socket: {found}"


def test_the_nora_service_sets_no_docker_host():
    """A `DOCKER_HOST` would point her at a daemon (a socket proxy, tcp://)."""
    env = _compose()["services"]["nora"].get("environment") or []
    names = set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
    assert "DOCKER_HOST" not in names, "DOCKER_HOST is set in nora's environment"


def test_the_nora_service_is_not_in_the_docker_group():
    """`group_add` of the host's docker GID existed only to read that socket."""
    nora = _compose()["services"]["nora"]
    assert not nora.get("group_add"), f"group_add on nora: {nora.get('group_add')}"


def test_no_service_joins_the_retired_compute_networks():
    compose = _compose()
    joined = {
        name: sorted(_network_names(service) & RETIRED_NETWORKS)
        for name, service in compose["services"].items()
        if _network_names(service) & RETIRED_NETWORKS
    }
    assert not joined, f"services on a retired compute network: {joined}"


def test_the_retired_compute_networks_and_proxy_are_not_declared():
    compose = _compose()
    declared = RETIRED_NETWORKS & set(compose.get("networks") or {})
    assert not declared, f"retired networks still declared: {sorted(declared)}"
    assert "compute-egress" not in compose["services"], "the egress proxy is still a service"
