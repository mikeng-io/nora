"""`nora.infra.social.__main__` — the bridge entrypoint's env parsing
(DEC-151). The unit overrides `NORA_SOCIAL_BRIDGE_HOST` with
`127.0.0.1,172.17.0.1` so Nora's container (private `nora` compose network,
NOT host networking) can reach the bridge at host.docker.internal:19826; the
default stays loopback-only because there is no auth on `/read` and the
allowlist is the boundary.
"""

from __future__ import annotations

import pytest

from nora.infra.social.__main__ import parse_bind_hosts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ["127.0.0.1"]),
        ("127.0.0.1", ["127.0.0.1"]),
        ("127.0.0.1,172.17.0.1", ["127.0.0.1", "172.17.0.1"]),
        (" 127.0.0.1 , 172.17.0.1 ", ["127.0.0.1", "172.17.0.1"]),
        (",,", ["127.0.0.1"]),          # all-empty → the loopback default
    ],
)
def test_parse_bind_hosts(raw: str, expected: list[str]) -> None:
    assert parse_bind_hosts(raw) == expected
