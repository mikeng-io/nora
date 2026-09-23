"""The operator one-shot that makes DEC-193's canonical self row exist.

`ingest_self` is the only writer of `provenance.role = "self"`, and it runs at
the same trust level as the doctrine and skill files (operator, on the deploy
host). These pin that it writes what the resolver reads, that it can mark a row
first-source-wins had already claimed, and that it refuses bytes the skill's
handle does not name, writing nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nora.domain.media import MediaObject
from nora.testing import FakeBlobStore, FakeMediaObjectStore
from scripts.ingest_self_asset import (
    SELF_PROVENANCE,
    IngestRefusedError,
    ingest_self,
    skill_self_handle,
)

DATA = b"a canonical picture"
SHA = hashlib.sha256(DATA).hexdigest()


async def test_ingest_writes_the_bytes_and_marks_the_row_self():
    media, blobs = FakeMediaObjectStore(), FakeBlobStore()
    row = await ingest_self(data=DATA, expected_sha256=SHA, blob_store=blobs,
                            media_store=media)
    assert blobs.blobs[SHA] == DATA
    assert row.sha256 == SHA
    assert row.source == "fetched"
    stored = await media.get(sha256=SHA)
    assert stored is not None
    assert stored.provenance["role"] == "self"
    assert stored.provenance["source"] == "operator-provided"
    assert stored.provenance["canonical"] is True


async def test_ingest_marks_a_row_that_already_existed():
    """Somebody once sent the same bytes into a room: first-source-wins kept
    that `inbound` row, so `save` alone would leave it unmarked and the handle
    dead. The merge upsert marks it; the birth record stays."""
    media = FakeMediaObjectStore([MediaObject(
        sha256=SHA, media_type="image/png", size=len(DATA), source="inbound", description="",
        provenance={"conversation_id": "g1"}, created_at_ms=5,
    )])
    await ingest_self(data=DATA, expected_sha256=SHA, blob_store=FakeBlobStore(),
                      media_store=media)
    stored = await media.get(sha256=SHA)
    assert stored is not None
    assert stored.provenance["role"] == "self"
    assert stored.provenance["conversation_id"] == "g1"
    assert stored.source == "inbound"


async def test_ingest_refuses_bytes_the_skill_does_not_name_and_writes_nothing():
    media, blobs = FakeMediaObjectStore(), FakeBlobStore()
    with pytest.raises(IngestRefusedError):
        await ingest_self(data=b"a different picture", expected_sha256=SHA,
                          blob_store=blobs, media_store=media)
    assert blobs.blobs == {}
    assert media.objs == {}


async def test_ingest_refuses_when_the_store_cannot_be_read_back():
    """An unreachable index answers `False`/`None`; that is a failed ingest,
    never a quiet success."""
    with pytest.raises(IngestRefusedError):
        await ingest_self(data=DATA, expected_sha256=SHA, blob_store=FakeBlobStore(),
                          media_store=FakeMediaObjectStore(fail=True))


async def test_ingest_is_idempotent():
    media, blobs = FakeMediaObjectStore(), FakeBlobStore()
    for _ in range(2):
        await ingest_self(data=DATA, expected_sha256=SHA, blob_store=blobs,
                          media_store=media)
    assert list(media.objs) == [SHA]
    assert media.objs[SHA].provenance == {**SELF_PROVENANCE}


def test_the_handle_is_read_from_the_skill(tmp_path: Path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(f"---\nname: x\n---\nYour picture: `[image {SHA}]`.\n")
    assert skill_self_handle(skill) == SHA


@pytest.mark.parametrize("body", ["no handle here", f"[image {SHA}] and [image {'b' * 64}]"])
def test_a_skill_with_no_single_handle_is_refused(tmp_path: Path, body: str):
    skill = tmp_path / "SKILL.md"
    skill.write_text(f"---\nname: x\n---\n{body}\n")
    with pytest.raises(IngestRefusedError):
        skill_self_handle(skill)
