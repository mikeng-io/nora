"""`BlobStore` over RustFS (an S3-compatible object store), keyed by the
sha256 of the content (DEC-136).

**Same port, same encryption as `FilesystemBlobStore`.** `BlobCipher` seals the
bytes before they leave this process (DEC-47); this module never writes
plaintext. The ciphertext's home is an S3 `PUT`/`GET`.

**No `<first2>/<rest>` sharding.** That layout exists in `filesystem.py` to keep
any one directory from accumulating every object; an object store's flat key
namespace needs none, so the key is the sha256 hex digest itself.

**boto3 in `asyncio.to_thread`, not aiobotocore.** The call volume does not
justify `aiobotocore`'s larger dependency chain, and the blocking-I/O-off-the-
event-loop shape matches `FilesystemBlobStore`'s worker-thread writes.

**`put` always uploads**, even for a key that already exists (no existence check,
unlike `FilesystemBlobStore`). A duplicate PUT to the same content-addressed key
is idempotent: same bytes, resealed under a fresh GCM nonce, which is safe for
identical plaintext (nonce reuse is only unsafe across different plaintexts
under one key). A HEAD-then-PUT check would add a race window to save a rare
re-upload.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from nora.infra.blobs.cipher import BlobCipher

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


class RustfsBlobStore:
    """Satisfies `nora.domain.ports.BlobStore` against a real `boto3` S3
    client and bucket (RustFS)."""

    def __init__(self, *, s3_client: S3Client, bucket: str, cipher: BlobCipher) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._cipher = cipher

    def ensure_bucket(self) -> None:
        """Idempotent bucket creation, called once at startup by the
        composition root — RustFS, like S3, has no implicit bucket. Blocking;
        the caller is responsible for running this off the event loop."""
        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self._bucket)

    async def put(self, *, data: bytes) -> str:
        """Store `data`; return its sha256 hex digest, which is its key.

        The key is the hash of the PLAINTEXT, computed before sealing — the
        same DEC-47 property `FilesystemBlobStore.put` keeps, so dedup and
        verifiability are unchanged by which backend is wired.
        """
        key = hashlib.sha256(data).hexdigest()
        sealed = self._cipher.seal(data)
        await asyncio.to_thread(self._put_object, key, sealed)
        return key

    def _put_object(self, key: str, sealed: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=sealed)

    async def get(self, *, key: str) -> bytes | None:
        """The bytes for `key`, or None if RustFS does not hold it.

        None means ABSENT, and only absent — the same distinction
        `FilesystemBlobStore.get`'s docstring draws. An unreachable bucket, a
        network failure, or a wrong-key decryption failure (`InvalidTag`) all
        RAISE rather than returning None, because none of those mean "there
        was never anything there".
        """
        _validate_key(key)
        sealed = await asyncio.to_thread(self._get_object, key)
        if sealed is None:
            return None
        return self._cipher.open(sealed)

    def _get_object(self, key: str) -> bytes | None:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
        except self._s3.exceptions.NoSuchKey:
            return None
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                return None
            raise
        return response["Body"].read()


def _validate_key(key: str) -> None:
    """The same refusal `FilesystemBlobStore._path_for` makes, for the same
    reason: `get` is the half of this port a retrieval tool would one day
    expose to a MODEL-supplied argument, and an unvalidated key becomes an S3
    key an attacker chose. A key is refused, not filtered, so anything other
    than the exact sha256 shape this store could have produced is structurally
    impossible to look up."""
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("blob key must be a lowercase sha256 hex digest")


__all__ = ["RustfsBlobStore"]
