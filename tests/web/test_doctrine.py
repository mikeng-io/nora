from __future__ import annotations

from nora.doctrine.loader import DoctrineStore
from nora.web.doctrine import build_doctrine_payload


def test_lists_every_tag_audience_section_in_doctrine_order(doctrine_store: DoctrineStore):
    payload = build_doctrine_payload(doctrine_store=doctrine_store)
    assert [section["name"] for section in payload] == [
        "identity", "voice", "silence", "conduct", "capability", "constraints",
    ]


def test_each_section_carries_only_the_reader_facing_fields(doctrine_store: DoctrineStore):
    payload = build_doctrine_payload(doctrine_store=doctrine_store)
    for section in payload:
        assert set(section) == {"name", "description", "version", "body"}
        assert isinstance(section["version"], int)
        assert section["body"]


def test_never_surfaces_internal_wiring_fields(doctrine_store: DoctrineStore):
    """`change_policy`, `authority_above/below`, and `not_here` are the
    doctrine engine's own scaffolding — a reader has no use for them and
    they must never reach the browser."""
    payload = build_doctrine_payload(doctrine_store=doctrine_store)
    dumped = str(payload)
    assert "operator-only" not in dumped
    assert "authority_above" not in dumped
