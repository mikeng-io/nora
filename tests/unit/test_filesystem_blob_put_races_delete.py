"""`FilesystemBlobStore`: a `put` of held bytes and a `delete` are one step
against each other. A `put` that finds the file and then loses it to a
deletion before refreshing it writes the bytes again rather than trusting a
file that is gone; and bytes a crashed deletion left set aside are put back
on the next process's first use."""

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


def _set_aside(store_root: Path, key: str) -> Path:
    """What `delete` leaves when the process dies after its rename."""
    blob = store_root / key[:2] / key[2:]
    aside = blob.with_name(f".{blob.name}.4242.{'ab' * 16}.deleting")
    os.rename(blob, aside)
    return aside


async def test_bytes_set_aside_by_a_crashed_deletion_are_put_back(tmp_path: Path) -> None:
    key = await FilesystemBlobStore(tmp_path, cipher=BlobCipher(bytes(range(32)))).put(data=DATA)
    aside = _set_aside(tmp_path, key)

    restarted = FilesystemBlobStore(tmp_path, cipher=BlobCipher(bytes(range(32))))
    assert await restarted.get(key=key) == DATA
    assert not aside.exists()


async def test_a_set_aside_copy_of_bytes_written_again_is_removed(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path, cipher=BlobCipher(bytes(range(32))))
    key = await store.put(data=DATA)
    aside = _set_aside(tmp_path, key)
    (tmp_path / key[:2] / key[2:]).write_bytes(aside.read_bytes())  # stored again since

    restarted = FilesystemBlobStore(tmp_path, cipher=BlobCipher(bytes(range(32))))
    assert await restarted.delete(key=key, idle_seconds=0) == "deleted"
    assert not aside.exists()
    assert list((tmp_path / key[:2]).iterdir()) == []
