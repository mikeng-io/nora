"""`BlobStore` over an ordinary directory: `<root>/<first2>/<rest>`, keyed by
the sha256 of the content.

**Why a filesystem and not an object store — and why the port exists anyway.**

The operator asked about RustFS (an S3-compatible object store) for this. The
decision is a filesystem behind a port, and the argument is not "filesystems
are simpler". It is that **no media has arrived yet.** signal-cli's store on
this host holds `avatars/` and `data/` and has no `attachments/` directory at
all, so an object store would be sized, configured and operated against zero
observed data — the shape of decision this repo has already been bitten by
(`peer_card` returned `[]` for the life of the deployment because nobody could
tell an empty answer from a dead endpoint).

Two concrete costs, on top of that:

* An object store is a CONTAINER, on the INGEST WRITE PATH, that can be DOWN.
  Today the failure modes of recording a message are: SQLite, and the disk it
  is on. Adding a network round trip to a second daemon adds a way for custody
  to fail that has nothing to do with either.
* Its headline feature for this problem — lifecycle rules that expire objects —
  is one Nora does not need. She already runs a retention sweep for messages
  (`SqlAuditStore.prune_all`, called periodically by the runtime). Media expiry
  is that same loop plus one path: read the surviving rows' `media_json`,
  delete every blob no longer named. Buying a daemon for a feature that is a
  `for` loop over a directory is not a trade.

**The port is what makes this reversible.** `nora.domain.ports.BlobStore` is
`put`/`get`, which is exactly S3's shape; swapping in RustFS or S3 is one
constructor in the composition root, changing no stage, no schema and no
recorded key — because the key is the content hash, not a path this class
invented. So: when there IS evidence (a volume, a size distribution, a second
consumer), that swap is small and informed. Until then this is 40 lines with no
moving parts. **Please do not re-litigate this without the evidence; that is
what the port is for.**

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
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path


class FilesystemBlobStore:
    """Satisfies `nora.domain.ports.BlobStore`."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

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

        Idempotent by construction: the same bytes produce the same key and the
        same path, so a meme forwarded five times is written once. An existing
        blob is left alone rather than rewritten — the content cannot differ,
        and not rewriting means a concurrent reader can never see a truncated
        file that was previously complete.
        """
        key = hashlib.sha256(data).hexdigest()
        await asyncio.to_thread(self._write, key, data)
        return key

    def _write(self, key: str, data: bytes) -> None:
        path = self._path_for(key)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(data)
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
        return path.read_bytes()

    async def delete(self, *, key: str) -> bool:
        """Drop one blob. True if it was there.

        NOT on the `BlobStore` port, deliberately — `nora.domain.ports`'
        docstring says why, and it is the same argument that keeps `prune`
        off `AuditStore` while `SqlAuditStore.prune_all` exists. Retention for
        media is an OPERATOR DECISION that has not been made (DEC-45's open
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
