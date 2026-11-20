"""`composition_media._build_blob_store` owns the boto3 client it creates:

an `ensure_bucket` failure — or a cancellation through it — closes the
client rather than stranding its connection pool. The s3 client is faked
at the boto3 boundary; no RustFS is dialed.
"""

import asyncio
from typing import Any

import pytest

from nora.app import composition_media
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from tests.builders import override_config


@pytest.fixture
def rustfs_cfg(example_config, monkeypatch):
    monkeypatch.setenv("RUSTFS_ACCESS_KEY_TEST", "key")
    monkeypatch.setenv("RUSTFS_SECRET_KEY_TEST", "secret")
    return override_config(example_config, {
        "media.rustfs.enabled": True,
        "media.rustfs.endpoint": "http://rustfs.invalid:9000",
        "media.rustfs.access_key_env": "RUSTFS_ACCESS_KEY_TEST",
        "media.rustfs.secret_key_env": "RUSTFS_SECRET_KEY_TEST",
    })


@pytest.fixture
def closed_s3(monkeypatch):
    closed: list[Any] = []

    class FakeS3:
        def close(self):
            closed.append(self)

    monkeypatch.setattr(
        composition_media.boto3, "client", lambda *a, **kw: FakeS3())
    return closed


async def test_a_failed_ensure_bucket_closes_the_s3_client(
    rustfs_cfg, closed_s3, monkeypatch,
):
    """The boto3 client exists before `ensure_bucket` runs — the failure
    fallback must close it, not strand the connection pool."""
    def ensure_fails(self):
        raise OSError("bucket unreachable")
    monkeypatch.setattr(RustfsBlobStore, "ensure_bucket", ensure_fails)

    blobs = await composition_media._build_blob_store(
        rustfs_cfg, cipher=BlobCipher(bytes(range(32))))

    assert isinstance(blobs, FilesystemBlobStore)
    assert len(closed_s3) == 1


async def test_a_cancelled_ensure_bucket_closes_the_s3_client(
    rustfs_cfg, closed_s3, monkeypatch,
):
    """A cancellation inside `ensure_bucket` is a BaseException — the s3
    pool still closes, and the cancellation itself propagates."""
    def ensure_cancels(self):
        raise asyncio.CancelledError()
    monkeypatch.setattr(RustfsBlobStore, "ensure_bucket", ensure_cancels)

    with pytest.raises(asyncio.CancelledError):
        await composition_media._build_blob_store(
            rustfs_cfg, cipher=BlobCipher(bytes(range(32))))
    assert len(closed_s3) == 1
