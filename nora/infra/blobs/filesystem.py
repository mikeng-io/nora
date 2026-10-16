"""`BlobStore` over an ordinary directory: `<root>/<first2>/<rest>`, keyed by
the sha256 of the content.

**Why a filesystem behind a port.** No media volume has been observed, so an
object store would be sized and operated against no data. An object store is
also a CONTAINER on the INGEST WRITE PATH that can be DOWN: recording a message
can already fail on the record store and on the disk the bytes land on, and
another daemon adds a custody failure unrelated to either. Its lifecycle rules
are not needed: media expiry is the retention sweep (`SqlRecordStore.prune_all`)
plus one path: read the surviving rows' `media_json`, delete every blob no
longer named.

**The port keeps the choice reversible.** `nora.domain.ports.BlobStore` is
`put`/`get`, S3's shape; swapping in S3 or RustFS is one constructor in the
composition root, changing no stage, no schema and no recorded key, because the
key is the content hash, not a path this class invented.

Layout is git's, for git's reason: `<first2>/<rest>` keeps any one directory
from accumulating every object, which matters on ext4 well before it matters
anywhere else.

Writes are atomic — a temp file in the same directory, then `os.replace`. A
half-written blob under a hash that claims to describe it is the one corruption
this store can produce on its own, and it would be silent: the key would verify
against nothing until somebody read the bytes back. The same-directory
requirement is not incidental, `os.replace` is only atomic within a filesystem.

Every filesystem call runs in a worker thread (`asyncio.to_thread`). This store
sits on the ingest path, which is awaited by the SSE reader, and a 100 MiB
video is a real thing to write; blocking the event loop for it would stall
every other conversation, delivery and health check in the process.

**Encryption at rest (DEC-47).** The bytes on disk are ciphertext, never the
plaintext. `put` hashes the plaintext it is handed — that hash is the key, so
dedup and verifiability are unchanged — and then seals it under a `BlobCipher`
before writing. `get` reads and opens. Nothing on the ingest hot path decrypts:
the text model never reads bytes, so `open` runs only for a future retrieval
tool. See `cipher.py` for why the key is of the plaintext and how the nonce is
handled. The cipher is REQUIRED, not optional — there is no plaintext-on-disk
mode to fall into by accident (the operator's "never plaintext-by-accident").
Custody being switched off is a decision made ABOVE this store (the composition
root wires no store at all, and the ingest stage counts `not_configured`), not
a plaintext degradation inside it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from nora.infra.blobs.cipher import BlobCipher


class FilesystemBlobStore:
    """Satisfies `nora.domain.ports.BlobStore`."""

    def __init__(self, root: Path, *, cipher: BlobCipher) -> None:
        self._root = Path(root)
        self._cipher = cipher

    def _path_for(self, key: str) -> Path:
        """`<root>/<first2>/<rest>`, and only for a key this store could have
        produced.

        The key is validated as 64 lowercase hex characters rather than
        trusted, because `get` is the half of this port a retrieval tool would
        one day expose to a MODEL-supplied argument. A key is a path segment;
        an unvalidated one is a path traversal. Refusing anything that is not a
        sha256 makes that structurally impossible rather than filtered.
        """
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("blob key must be a lowercase sha256 hex digest")
        return self._root / key[:2] / key[2:]

    async def put(self, *, data: bytes) -> str:
        """Store `data`; return its sha256 hex digest, which is its key.

        The key is the hash of the PLAINTEXT `data`, computed before any
        encryption — that is what keeps dedup and verifiability true while the
        bytes on disk are ciphertext (DEC-47). The sealing (a fresh nonce, then
        AES-GCM) happens in the worker thread beside the write, so the CPU cost
        of encrypting a 100 MiB video does not block the event loop either.

        Idempotent by construction: the same bytes produce the same key and the
        same path, so repeated bytes are written once. An existing
        blob is left alone rather than rewritten — the content cannot differ,
        and not rewriting means both that a concurrent reader never sees a
        truncated file that was previously complete AND that the stored nonce is
        never reused to seal a second, different plaintext under the same key.
        """
        key = hashlib.sha256(data).hexdigest()
        await asyncio.to_thread(self._write, key, data)
        return key

    def _write(self, key: str, data: bytes) -> None:
        path = self._path_for(key)
        if path.exists():
            return
        sealed = self._cipher.seal(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(sealed)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    async def get(self, *, key: str) -> bytes | None:
        """The bytes for `key`, or None if this store does not hold it.

        None means ABSENT, and only absent. An unreadable-but-present blob
        (permissions, a bad disk) raises, because that is an operator problem
        that must not be reported as "there was never anything there" — the
        same distinction `Roster.available` and `MediaBytes.available` carry,
        made here by the exception rather than by a flag, since this side has
        no network and absence is genuinely unambiguous.
        """
        return await asyncio.to_thread(self._read, key)

    def _read(self, key: str) -> bytes | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        # Opens the ciphertext (DEC-47). A wrong key or a tampered blob RAISES
        # (`InvalidTag`) rather than returning garbage — the same "unreadable is
        # not absence" distinction `get`'s docstring draws for a bad disk, made
        # here by GCM's authentication. Nothing on the ingest hot path calls
        # this; a future retrieval tool is its only caller.
        return self._cipher.open(path.read_bytes())

    async def delete(self, *, key: str) -> bool:
        """Drop one blob. True if it was there.

        NOT on the `BlobStore` port, deliberately — `nora.domain.ports`'
        docstring says why, and it is the same argument that keeps `prune`
        off `RecordStore` while `SqlRecordStore.prune_all` exists. Retention for
        media is an OPERATOR DECISION that has not been made (DEC-43's open
        question); this is the mechanism that decision will need, present so
        that making it costs a policy and not an implementation.

        Content addressing is what makes the eventual sweep cheap: the live set
        is every hash named by a surviving row's `media_json`, and everything
        else under the root is garbage. No reference counting, because two rows
        naming the same blob name the same hash.
        """
        return await asyncio.to_thread(self._delete, key)

    def _delete(self, key: str) -> bool:
        path = self._path_for(key)
        if not path.exists():
            return False
        path.unlink()
        return True
