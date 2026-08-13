"""`ArtifactIngest` (Nora Drop spec §7a) — this is a shape/behaviour test of
`FakeArtifactIngest` (`nora/testing/fakes.py`) alone. The cross-implementation
behavioral contract against the real adapter, `nora.infra.drop.DropArtifactIngest`,
lives in `tests/contract/test_artifact_ingest_contract.py` — see that file
for the shared suite both implementations are held to.
"""

from __future__ import annotations

import pytest

from nora.domain.ports import ArtifactIngest, ArtifactRegistration
from nora.testing import FakeArtifactIngest


def test_fake_artifact_ingest_satisfies_the_port():
    """`isinstance` against a `runtime_checkable` Protocol checks method names
    only (`test_runtime_checkable_protocols_only_check_method_names` makes the
    same point for `MemoryStore`) — this is the name-level floor every other
    port's fake gets in `tests/contract/test_port_conformance.py`, placed here
    instead because `ArtifactIngest` has no contract suite to live beside."""
    assert isinstance(FakeArtifactIngest(), ArtifactIngest)


async def test_register_returns_a_distinct_id_and_url_per_call():
    """Two artifacts registered in the same turn must not collide — each gets
    its own id and, per `ArtifactRegistration`'s docstring, a URL bundled with
    it in the same call rather than requiring a second round trip."""
    ingest = FakeArtifactIngest()

    first = await ingest.register(data=b"one", title="First", media_type="text/plain")
    second = await ingest.register(data=b"two", title="Second", media_type="text/plain")

    assert isinstance(first, ArtifactRegistration)
    assert isinstance(second, ArtifactRegistration)
    assert first.artifact_id and first.url
    assert second.artifact_id and second.url
    assert first.artifact_id != second.artifact_id
    assert first.url != second.url


async def test_register_records_what_it_was_given():
    """`FakeArtifactIngest.artifacts` keeps the full call, not just what
    `ArtifactRegistration` hands back — so a test can assert on
    `title`/`media_type`/`expires_at` too, the way `FakeRecordStore.rows`
    keeps the full row rather than only its own return value."""
    ingest = FakeArtifactIngest()

    reg = await ingest.register(
        data=b"payload", title="Report", media_type="application/pdf",
        expires_at=1_786_320_000_000,
    )

    stored = ingest.artifacts[reg.artifact_id]
    assert stored["data"] == b"payload"
    assert stored["title"] == "Report"
    assert stored["media_type"] == "application/pdf"
    assert stored["expires_at"] == 1_786_320_000_000


async def test_register_defaults_expires_at_to_none():
    """`expires_at=None` means "apply whatever default the implementation
    has" (the port's own docstring — Nora Drop spec §8 leaves the retention
    default undecided), not "never expires"; the fake must not invent one."""
    ingest = FakeArtifactIngest()

    reg = await ingest.register(data=b"x", title="X", media_type="text/plain")

    assert ingest.artifacts[reg.artifact_id]["expires_at"] is None


async def test_register_raises_when_drop_is_unreachable():
    """`fail=True` simulates `drop`'s internal listener being down — the
    failure mode `ArtifactIngest.register`'s docstring documents raising for,
    mirroring `FakeBlobStore.fail_on_put`. A future Compute tool has to be
    testable turning this into R-27's `unavailable`."""
    ingest = FakeArtifactIngest(fail=True)

    with pytest.raises(ConnectionError):
        await ingest.register(data=b"x", title="X", media_type="text/plain")
