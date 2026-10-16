"""`BlobStore` over an ordinary directory: `<root>/<first2>/<rest>`, keyed by
the sha256 of the content.

**Why a filesystem behind a port.** No media volume has been observed, so an
object store would be sized and operated against no data. An object store is
also a CONTAINER on the INGEST WRITE PATH that can be DOWN: recording a message
can already fail on the record store and on the disk the bytes land on, and
another daemon adds a custody failure unrelated to either. Its lifecycle rules
are not needed: the one expiry there is (a story frame's bytes) is a pass over
the rows that name them (`nora.app.social_expiry`).

**The port keeps the choice reversible.** `nora.domain.ports.BlobStore` is
`put`/`get`/`delete`, S3's shape; swapping in S3 or RustFS is one constructor in the
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
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from nora.domain.ports import BlobDeletion
from nora.infra.blobs.cipher import BlobCipher

#: The suffix of a file `delete` has set aside while it decides.
_ASIDE = ".deleting"


class FilesystemBlobStore:
    """Satisfies `nora.domain.ports.BlobStore`."""

    def __init__(
        self, root: Path, *, cipher: BlobCipher, clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._cipher = cipher
        # What `delete` measures a file's idle time against (seconds, the
        # epoch `st_mtime` uses); a test moves it instead of sleeping.
        self._clock = clock
        self._recovered = False
        self._recovering = threading.Lock()

    def _recover(self) -> None:
        """Undo every deletion a crash interrupted, once, before this store's
        first read or write: `delete` renames a file aside before it decides,
        so a process killed in between leaves the bytes under a name `get`
        never reads. A file set aside whose blob is missing is put back (the
        deletion was never confirmed, so nothing says it is gone); one whose
        blob was written again meanwhile is removed."""
        with self._recovering:
            if self._recovered:
                return
            for aside in self._root.glob(f"??/.*{_ASIDE}"):
                blob = aside.with_name(aside.name[1:].split(".", 1)[0])
                if blob.exists():
                    aside.unlink(missing_ok=True)
                else:
                    os.replace(aside, blob)
            self._recovered = True

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
        Its modification time is set to now instead: that is the last `put`
        `delete` reads, so bytes a writer has just stored are not deleted
        before the row naming them is written.
        """
        key = hashlib.sha256(data).hexdigest()
        await asyncio.to_thread(self._write, key, data)
        return key

    def _write(self, key: str, data: bytes) -> None:
        self._recover()
        path = self._path_for(key)
        if path.exists():
            try:
                os.utime(path)
                return
            except FileNotFoundError:
                pass  # a deletion took it between the two calls: write it again
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
        self._recover()
        path = self._path_for(key)
        if not path.exists():
            return None
        # Opens the ciphertext (DEC-47). A wrong key or a tampered blob RAISES
        # (`InvalidTag`) rather than returning garbage — the same "unreadable is
        # not absence" distinction `get`'s docstring draws for a bad disk, made
        # here by GCM's authentication. Nothing on the ingest hot path calls
        # this; a future retrieval tool is its only caller.
        return self._cipher.open(path.read_bytes())

    async def delete(self, *, key: str, idle_seconds: float) -> BlobDeletion:
        """Drop one blob unless it was put within `idle_seconds`
        (`BlobStore.delete`).

        The mechanism only: which bytes may go is the caller's to decide, and
        one key can be named by many rows (a forwarded image, the computer
        audit), so a caller asks `nora.infra.sql.blob_refs.held_elsewhere`
        first. A key that is not a sha256 is refused, as `get` refuses it.

        **One step against `put`.** The file is first renamed aside, which is
        atomic, and its modification time read there: a `put` that touched
        it before the rename shows as recent and the file is put back; a
        `put` after the rename finds no file (or loses its `utime`) and
        writes a new one. Either way no `put` is lost. A crash while the file
        is aside is undone by `_recover` on the next process's first use.
        """
        return await asyncio.to_thread(self._delete, key, idle_seconds)

    def _delete(self, key: str, idle_seconds: float) -> BlobDeletion:
        self._recover()
        path = self._path_for(key)
        aside = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}{_ASIDE}")
        try:
            os.rename(path, aside)
        except FileNotFoundError:
            return "absent"
        if self._clock() - aside.stat().st_mtime < idle_seconds:
            if path.exists():
                aside.unlink()  # a `put` wrote the same bytes again meanwhile
            else:
                os.replace(aside, path)
            return "kept"
        aside.unlink()
        return "deleted"
