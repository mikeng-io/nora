"""`RustfsBlobStore.delete` against a REAL RustFS (`rustfs_client`, the
`rustfs/rustfs` image `docker-compose.yml` deploys): deleted, absent, kept
while recently put, and never an object replaced between its `HEAD` and its
`DeleteObject`.

The last is the one the store cannot promise by itself: it sends the delete
`If-Match` the `ETag` the `HEAD` read, and only a server that refuses a stale
`If-Match` (412) makes that a guard. So the server's own answer is pinned too
(`test_the_server_refuses_a_delete_whose_etag_is_stale`): if a RustFS upgrade
stops honouring the header, that test says so before a race does.
"""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from nora.infra.blobs import RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher

BUCKET = "conditional-delete"
DATA = b"a story frame's bytes"


@pytest.fixture
def store(rustfs_client: Any) -> RustfsBlobStore:
    s = RustfsBlobStore(
        s3_client=rustfs_client, bucket=BUCKET, cipher=BlobCipher(bytes(range(32))),
    )
    s.ensure_bucket()
    return s


async def test_idle_bytes_are_deleted_then_absent(store: RustfsBlobStore) -> None:
    key = await store.put(data=DATA)
    assert await store.delete(key=key, idle_seconds=0) == "deleted"
    assert await store.get(key=key) is None
    assert await store.delete(key=key, idle_seconds=0) == "absent"


async def test_recently_put_bytes_are_kept(store: RustfsBlobStore) -> None:
    key = await store.put(data=DATA)
    assert await store.delete(key=key, idle_seconds=3600) == "kept"
    assert await store.get(key=key) == DATA


class _ReplacedBeforeDelete:
    """The S3 client, except that a writer replaces the object between the
    store's `HEAD` and its `DeleteObject`: a room `put` of the same bytes,
    which re-seals them under a fresh nonce, so the object's `ETag` changes."""

    def __init__(self, client: Any, replacement: bytes) -> None:
        self._client = client
        self._replacement = replacement

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def delete_object(self, **kwargs: Any) -> Any:
        self._client.put_object(
            Bucket=kwargs["Bucket"], Key=kwargs["Key"], Body=self._replacement,
        )
        return self._client.delete_object(**kwargs)


async def test_an_object_replaced_between_head_and_delete_is_not_deleted(
    rustfs_client: Any, store: RustfsBlobStore,
) -> None:
    key = await store.put(data=DATA)
    racing = RustfsBlobStore(
        s3_client=_ReplacedBeforeDelete(rustfs_client, BlobCipher(bytes(range(32))).seal(DATA)),
        bucket=BUCKET, cipher=BlobCipher(bytes(range(32))),
    )
    assert await racing.delete(key=key, idle_seconds=0) == "kept"
    assert await store.get(key=key) == DATA, "the writer's bytes survive"


def test_the_server_refuses_a_delete_whose_etag_is_stale(
    rustfs_client: Any, store: RustfsBlobStore,
) -> None:
    """The server half of the guard, with its control: a stale `If-Match` is
    refused and the object stays; the current one deletes it. (`store` makes
    the bucket.)"""
    rustfs_client.put_object(Bucket=BUCKET, Key="k", Body=b"one")
    stale = rustfs_client.head_object(Bucket=BUCKET, Key="k")["ETag"]
    rustfs_client.put_object(Bucket=BUCKET, Key="k", Body=b"two")

    with pytest.raises(ClientError) as refused:
        rustfs_client.delete_object(Bucket=BUCKET, Key="k", IfMatch=stale)
    assert refused.value.response["ResponseMetadata"]["HTTPStatusCode"] == 412
    assert rustfs_client.head_object(Bucket=BUCKET, Key="k")

    current = rustfs_client.head_object(Bucket=BUCKET, Key="k")["ETag"]
    rustfs_client.delete_object(Bucket=BUCKET, Key="k", IfMatch=current)
    with pytest.raises(ClientError):
        rustfs_client.head_object(Bucket=BUCKET, Key="k")
