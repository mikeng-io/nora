"""`deploy/computer/README.md`'s token rotation is the procedure that works.

The README used to say a rotation is `systemctl --user restart nora-computer`.
That restarts computerd, and nora reads `NORA_COMPUTER_TOKEN` from the environment
when her CONTAINER is created, so after that one step every computer call was a
401 while `/v1/health` stayed green. The facts the procedure rests on are
checked here against the files that make them true, so a README that drifts from
them is a red test and not a 3 a.m. discovery.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "deploy" / "computer" / "README.md").read_text(encoding="utf-8")


def _section(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert match, f"no '## {title}' section"
    return match.group(1)


def test_nora_gets_the_token_from_the_environment_at_container_creation() -> None:
    """The fact the second step rests on: the token is an `environment:` entry on
    the nora service, so only a recreate (not a restart) changes what she holds."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    nora = compose["services"]["nora"]
    env = [e for e in nora["environment"] if str(e).startswith("NORA_COMPUTER_TOKEN=")]
    assert len(env) == 1
    assert "env_file" not in nora


def test_the_rotation_restarts_the_unit_and_then_recreates_nora() -> None:
    section = _section("Rotating the token")
    restart = section.index("systemctl --user restart nora-computer")
    recreate = section.index("docker compose up -d --force-recreate nora")
    assert restart < recreate
    assert "a restart keeps the old environment" in section


def test_the_rotation_verifies_the_old_token_refused_and_the_new_one_accepted() -> None:
    section = _section("Rotating the token")
    assert "401" in section and "200" in section
    assert "old token:" in section and "new token:" in section
    # By header file, not on a command line: neither token appears in `ps`.
    assert "-H @/tmp/h.old" in section and "-H @/tmp/h.new" in section
    assert "nora_computer_up" in section


def test_the_operating_table_no_longer_says_a_unit_restart_is_a_rotation() -> None:
    row = next(line for line in README.splitlines() if line.startswith("| token rotated"))
    assert "systemctl --user restart nora-computer" not in row
    assert "Rotating the token" in row


def test_the_alert_that_points_at_this_section_points_at_one_that_exists() -> None:
    rules = (ROOT / "deploy" / "prometheus" / "rules" / "nora.yml").read_text(encoding="utf-8")
    assert 'README.md "Rotating the token"' in rules
    assert "## Rotating the token" in README


def test_a_rotation_is_documented_as_an_audit_row_by_fingerprint() -> None:
    section = _section("Rotating the token")
    assert "token_rotation" in section and "fingerprints" in section
