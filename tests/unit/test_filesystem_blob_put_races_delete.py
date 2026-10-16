"""`FilesystemBlobStore`: a `put` of held bytes and a `delete` are one step
against each other. A `put` that finds the file and then loses it to a
deletion before refreshing it writes the bytes again rather than trusting a
file that is gone."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nora.infra.blobs import FilesystemBlobStore, filesystem
from nora.infra.blobs.cipher import BlobCipher

DATA = b"a story frame also sent to a room"


async def test_a_put_that_loses_the_file_to_a_deletion_writes_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FilesystemBlobStore(tmp_path, cipher=BlobCipher(bytes(range(32))))
    key = await store.put(data=DATA)
    real_utime = os.utime

    def deleted_in_between(path: Path, *args: object) -> None:
        Path(path).unlink()  # the deletion's rename lands between exists() and utime
        real_utime(path, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(filesystem.os, "utime", deleted_in_between)
    assert await store.put(data=DATA) == key
    monkeypatch.undo()

    assert await store.get(key=key) == DATA
    assert await store.delete(key=key, idle_seconds=60) == "kept", "the new file is recent"
