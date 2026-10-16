"""One suite, three `BlobStore`s (DEC-43, DEC-136): `put`, `get` and `delete`.

`FilesystemBlobStore` and `RustfsBlobStore` are both production, chosen by
`[media.rustfs].enabled`; `FakeBlobStore` is what the ingest tests run
against. Per CLAUDE.md a port gains no second implementation — including a
test fake — without a shared suite both pass, and the property that matters
most here is the one a lazy fake would break first: **the key is the hash of
the content.** Dedup, verifiability and the rendered `[image a3f2c1]` handle
all fall out of that single fact, and a fake handing out counters would make
all three untestable.

`RustfsBlobStore` runs against a REAL `rustfs/rustfs` container (the same
image `docker-compose.yml` deploys), not a mock or a MinIO stand-in — the
same "make the fake behave like the real server" posture `tests/conftest.py`
already takes for Postgres. `rustfs_client` (`tests/conftest.py`) starts that
container lazily, on the first test that asks for it, exactly like `pg_url`.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from nora.domain.ports import BlobStore
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.testing import FakeBlobStore

PAYLOAD = b"\x89PNG\r\n\x1a\n not really a png"


@pytest.fixture(params=["filesystem", "fake", "rustfs"])
def store(request, tmp_path) -> BlobStore:
    if request.param == "filesystem":
        # The production store encrypts at rest (DEC-47), so the contract fixture
        # gives it a key. Every port behaviour below — put returns the PLAINTEXT
        # hash, get round-trips, dedup, empty-vs-absent — must hold THROUGH the
        # encryption, which is exactly why these run against the encrypting store
        # and not a plaintext variant. The at-rest-ciphertext property itself is
        # FilesystemBlobStore-specific and lives in tests/unit/test_media_encryption.py,
        # the same split test_media_source_contract.py draws for its Signal-only wire shapes.
        return FilesystemBlobStore(tmp_path / "media", cipher=BlobCipher(bytes(range(32))))
    if request.param == "rustfs":
        # getfixturevalue, not a direct parameter, so rustfs_client's session
        # container is never started for the "filesystem"/"fake" runs — the
        # same laziness tests/conftest.py's pg_url docstring calls out.
        client = request.getfixturevalue("rustfs_client")
        store = RustfsBlobStore(
            s3_client=client, bucket="contract-test",
            cipher=BlobCipher(bytes(range(32))),
        )
        store.ensure_bucket()
        return store
    return FakeBlobStore()


async def test_put_returns_the_sha256_of_the_content(store):
    """Not "a key" — THE hash. A retrieval can verify a blob against the row
    that names it only because this is true of both implementations."""
    assert await store.put(data=PAYLOAD) == hashlib.sha256(PAYLOAD).hexdigest()


async def test_get_returns_exactly_what_was_put(store):
    key = await store.put(data=PAYLOAD)
    assert await store.get(key=key) == PAYLOAD


async def test_the_same_bytes_store_once(store):
    """The meme forwarded five times. Same key, and the second put must not
    corrupt or duplicate the first."""
    first = await store.put(data=PAYLOAD)
    second = await store.put(data=PAYLOAD)
    assert first == second
    assert await store.get(key=first) == PAYLOAD


async def test_different_bytes_get_different_keys(store):
    assert await store.put(data=b"a") != await store.put(data=b"b")


async def test_get_of_an_unheld_key_is_none_not_an_error(store):
    """None means ABSENT — the honest answer for a blob the operator's
    retention has already swept. It is a `bytes | None`, not a raise, because
    absence is the expected steady state once retention exists."""
    absent = hashlib.sha256(b"never stored").hexdigest()
    assert await store.get(key=absent) is None


async def test_empty_bytes_are_storable_and_are_not_absence(store):
    """A zero-byte attachment is a real thing that arrives. `get` returning
    `b""` must stay distinguishable from `get` returning None, or the store
    reproduces the `peer_card` ambiguity in a new place."""
    key = await store.put(data=b"")
    got = await store.get(key=key)
    assert got == b""
    assert got is not None


async def test_a_key_that_is_not_a_sha256_is_refused_not_resolved(store):
    """`get` is the half a retrieval tool would expose to a MODEL-supplied
    argument, and a key is a path segment. Both implementations refuse rather
    than filter, so traversal is structurally impossible."""
    for bad in ("../../etc/passwd", "", "ZZ" * 32, "abc"):
        with pytest.raises(ValueError):
            await store.get(key=bad)


async def test_delete_drops_idle_bytes_and_says_what_it_did(store):
    """A story frame's bytes go at their bound (cognition-loop/22 §5). After
    `deleted`, `get` is None; a second `delete` answers `absent`, so a
    deletion retried after a restart is safe and says the bytes are gone."""
    key = await store.put(data=PAYLOAD)
    other = await store.put(data=b"kept")
    assert await store.delete(key=key, idle_seconds=0) == "deleted"
    assert await store.get(key=key) is None
    assert await store.delete(key=key, idle_seconds=0) == "absent"
    assert await store.get(key=other) == b"kept"


async def test_bytes_put_within_the_idle_window_are_kept(store):
    key = await store.put(data=PAYLOAD)
    assert await store.delete(key=key, idle_seconds=3600) == "kept"
    assert await store.get(key=key) == PAYLOAD


async def test_putting_held_bytes_again_counts_as_a_put(store):
    """The race a deletion must not win: a writer stores bytes the store
    already holds, and has not yet written the row naming them. Its `put`
    must make the bytes recent, or a deletion in that gap takes them."""
    key = await store.put(data=PAYLOAD)
    await asyncio.sleep(1.5)
    assert await store.put(data=PAYLOAD) == key
    assert await store.delete(key=key, idle_seconds=1.0) == "kept"
    await asyncio.sleep(1.5)
    assert await store.delete(key=key, idle_seconds=1.0) == "deleted", "the control"


async def test_delete_refuses_a_key_that_is_not_a_sha256(store):
    for bad in ("../../etc/passwd", "", "ZZ" * 32, "abc"):
        with pytest.raises(ValueError):
            await store.delete(key=bad, idle_seconds=0)
