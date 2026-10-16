"""Content-addressed byte custody — the `BlobStore` port's two implementations
(DEC-43 filesystem, DEC-136 RustFS).

Its own package rather than a module under `infra/sql/` for the reason
`infra/`'s docstring gives about `sql` and `honcho`: the contracts are
different. A failed audit append drops the message; a failed blob write leaves
the message recorded with an honest placeholder and no custody claim.

May import: stdlib, `cryptography`, `botocore` (RustFS's error types only —
the `boto3` client itself is built at the composition root and handed in),
`nora.domain`. Not `nora.obs` (layers contract) — metrics are injected at the
call site, not here; neither store counts anything and therefore needs
nothing injected.

ONE KIND OF BLOB IS DELETED: a story frame's bytes, at its bound
(`nora.app.social_expiry`), and only when no other row names them. Every
other blob is kept. Beyond media custody, the computer audit trail
references blobs from Postgres (design/sandbox-runtime §12):
`computer_events.output_ref` (a run's full stdout/stderr, a read's full
content) and `computer_events.request` (a write's full content), each as
`{"key": ..., "sha256": ...}`. A deletion that treated a blob no media row
names as garbage would silently hollow out that audit record, so every
deletion asks `nora.infra.sql.blob_refs.held_elsewhere`, which reads every
column that names a key.
"""

from nora.infra.blobs.filesystem import FilesystemBlobStore
from nora.infra.blobs.rustfs import RustfsBlobStore

__all__ = ["FilesystemBlobStore", "RustfsBlobStore"]
