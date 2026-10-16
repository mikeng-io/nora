"""`RustfsBlobStore.delete` ages an object from the LATEST moment its put
could have been. `HEAD` returns `LastModified` in whole seconds, rounded down
(measured against the `rustfs/rustfs` container: microsecond 0 on every
put), so aging from the stamp itself reads bytes as up to a second older than
they are, and deletes them before their window ends.

A stub S3 client and an injected clock: no container, no sleep.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nora.infra.blobs import RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher

KEY = "a" * 64
STAMP = datetime(2026, 10, 17, 4, 0, 0, tzinfo=UTC)


class _S3:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key in self.deleted:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"LastModified": STAMP, "ETag": '"e"'}

    def delete_object(self, *, Bucket: str, Key: str, IfMatch: str) -> None:
        self.deleted.append(Key)


def _store(now: datetime) -> tuple[RustfsBlobStore, _S3]:
    s3 = _S3()
    return RustfsBlobStore(
        s3_client=s3, bucket="b", cipher=BlobCipher(bytes(range(32))), clock=lambda: now,
    ), s3


async def test_bytes_still_inside_their_window_are_kept_whatever_the_stamp_rounded_off():
    # Put at 04:00:00.9 (stamped 04:00:00); 1.5 s later they are 0.6 s old.
    store, s3 = _store(STAMP + timedelta(seconds=1.5))
    assert await store.delete(key=KEY, idle_seconds=1.0) == "kept"
    assert s3.deleted == []


async def test_bytes_past_their_window_plus_the_stamps_resolution_are_deleted():
    store, s3 = _store(STAMP + timedelta(seconds=2.01))
    assert await store.delete(key=KEY, idle_seconds=1.0) == "deleted"
    assert s3.deleted == [KEY]
