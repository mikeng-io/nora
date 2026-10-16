"""The services on the wake-to-first-speech path are given no swap.

The first wake of a voice session after hours of idle pays seconds inside the
STT forward pass, against ~0.3s warm (see the comment on `stt` in
docker-compose.yml). A restart does not reproduce it, an idle 10 minutes does
not either, and norty swaps its idle containers out. A cgroup with
`memswap_limit == mem_limit` has no swap, so its loaded weights stay resident.
This reads the compose file as parsed YAML, like its sibling
`test_compose_gives_nora_no_container_control.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

#: The two services whose first call after an idle stretch is on the
#: wake-to-speech path and holds a loaded model. `voice-sidecar` is not here:
#: it streams audio continuously, so it is never idle long enough to page out.
NO_SWAP_SERVICES = ("stt", "voice-wake")

_UNITS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _bytes(value: object) -> int:
    text = str(value).strip().lower().removesuffix("b")
    if text[-1:] in _UNITS and text[-1:].isalpha():
        return int(float(text[:-1]) * _UNITS[text[-1]])
    return int(text)


def _service(name: str) -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"][name]


def test_the_services_are_found_so_the_checks_below_can_fail():
    """A positive control: the parse reaches both services."""
    for name in NO_SWAP_SERVICES:
        assert _service(name).get("container_name") == name


@pytest.mark.parametrize("name", NO_SWAP_SERVICES)
def test_a_voice_path_service_has_a_memory_limit_and_no_swap_beyond_it(name: str):
    service = _service(name)
    assert "mem_limit" in service, f"{name} has no mem_limit"
    assert "memswap_limit" in service, f"{name} may swap: no memswap_limit"
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"]), (
        f"{name}: memswap_limit must equal mem_limit (swap = memswap - mem = 0)"
    )


def test_the_stt_limit_leaves_room_for_both_engines():
    """sensevoice (~3.2GB) plus the lazily loaded noise-detection whisper
    (~3.5GB) must fit, or the limit trades slowness for an OOM kill."""
    assert _bytes(_service("stt")["mem_limit"]) >= 7 * 1024**3
