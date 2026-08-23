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
"""

from nora.infra.blobs.filesystem import FilesystemBlobStore
from nora.infra.blobs.rustfs import RustfsBlobStore

__all__ = ["FilesystemBlobStore", "RustfsBlobStore"]
