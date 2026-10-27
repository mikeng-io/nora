"""Honcho is reached on a private network, never over the host (DEC-259).

Honcho answers with no authentication and must not get any: the protection is
where it listens. Published on `0.0.0.0:8000`, it would let any container that
can name the host (`host.docker.internal:host-gateway`) and anything on the LAN
read and write Nora's memory. It publishes on loopback only, and nora reaches
it by name, `honcho:8000`, on the external network `honcho_memory`, which holds
Honcho's api and this one service.

Honcho's own compose file is outside this repository, so what this pins is the
half that is here: nora joins that network and nothing else does, the network
is declared external under the name the runbook creates, and the eval suite on
the host, which cannot resolve a container-network name, is rewritten to
loopback. The other half, that the port is in fact refused, is
`deploy/honcho-closed-probe.sh`, run on norty.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"

NETWORK_KEY = "honcho"
NETWORK_NAME = "honcho_memory"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _joined(service: dict) -> set[str]:
    networks = service.get("networks") or []
    return set(networks if isinstance(networks, list) else networks.keys())


def test_the_nora_service_joins_the_honcho_network() -> None:
    """The caller. Without this the container cannot resolve `honcho` and every
    memory call fails the moment Honcho stops publishing on every interface."""
    nora = _compose()["services"]["nora"]
    assert NETWORK_KEY in _joined(nora)
    # And it kept its own network: db, signal-cli and the rest are by name there.
    assert "nora" in _joined(nora)


def test_no_other_service_joins_the_honcho_network() -> None:
    """The point of a network of its own is that it holds two things. Another
    member is another route to an unauthenticated memory store."""
    members = sorted(
        name for name, service in _compose()["services"].items()
        if NETWORK_KEY in _joined(service)
    )
    assert members == ["nora"]


def test_the_network_is_external_and_named_as_the_runbook_creates_it() -> None:
    """Owned by neither stack, so taking either down never removes it, and the
    name is the one `docs/runbook.md` tells the operator to create."""
    declared = _compose()["networks"][NETWORK_KEY]
    assert declared["external"] is True
    assert declared["name"] == NETWORK_NAME
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    # INTERNAL, or joining it can move nora's default route.
    assert f"docker network create --internal {NETWORK_NAME}" in runbook
    assert f"docker network create {NETWORK_NAME}" not in runbook


def test_no_service_is_pointed_at_honcho_through_the_host_any_more() -> None:
    """`host.docker.internal:8000` is the route that stays open to every
    container; a compose environment line that names it brings the exposure
    back."""
    text = COMPOSE.read_text(encoding="utf-8")
    uncommented = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "host.docker.internal:8000" not in uncommented


def test_the_host_reaches_honcho_on_loopback_where_the_container_uses_its_name() -> None:
    """The eval suite runs on the host, outside any container, where `honcho`
    does not resolve. The deployment's `[honcho] base_url` is `http://honcho:8000`,
    and the suite must read it as loopback, exactly as it already reads
    `host.docker.internal`."""
    # By module path: `nora.subject.obtain` as an attribute is the function.
    obtain = importlib.import_module("nora.subject.obtain")
    assert obtain._from_host("http://honcho:8000") == "http://127.0.0.1:8000"
    assert obtain._from_host("http://host.docker.internal:8000") == "http://127.0.0.1:8000"
    # Not a substring rewrite: a host that merely contains the name is untouched.
    assert obtain._from_host("http://honcho.example.org:8000") == "http://honcho.example.org:8000"
    assert obtain._from_host("http://omniroute:20128/v1") == "http://omniroute:20128/v1"


def test_the_nora_deploy_skill_creates_the_network_before_any_compose_up() -> None:
    """Merging blocks every `compose up` that names nora until the external
    network exists, so the deploy procedure carries the idempotent check."""
    skill = (ROOT / ".agents" / "skills" / "nora-deploy" / "SKILL.md").read_text(encoding="utf-8")
    line = (f"docker network inspect {NETWORK_NAME} >/dev/null 2>&1 "
            f"|| docker network create --internal {NETWORK_NAME}")
    assert line in skill
    assert skill.index(line) < skill.index("up -d --force-recreate")


def test_the_rollback_puts_the_config_back_before_it_touches_the_compose_or_the_port() -> None:
    """Nora on an old compose has no `honcho` network, so a config still naming
    `honcho:8000` resolves nothing and every memory call fails. The runbook says
    so, and says the config goes back FIRST."""
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    rollback = runbook[runbook.index("**Rollback**, in this order"):]
    config_back = rollback.index('`[honcho] base_url` back to')
    port_back = rollback.index("publish `8000:8000` again")
    old_compose = rollback.index("recreate nora from an OLD compose")
    assert config_back < port_back < old_compose
    assert "every memory call fails" in rollback
