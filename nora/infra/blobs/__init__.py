"""Content-addressed byte custody — the `BlobStore` port's filesystem
implementation.

Its own package rather than a module under `infra/sql/` for the reason
`infra/`'s docstring gives about `sql` and `honcho`: the contracts are
different. A failed audit append drops the message; a failed blob write leaves
the message recorded with an honest placeholder and no custody claim.

May import: stdlib, `nora.domain`. Not `nora.obs` (layers contract) — metrics
are injected at the call site, not here; this store counts nothing and
therefore needs nothing injected.
"""

from nora.infra.blobs.filesystem import FilesystemBlobStore

__all__ = ["FilesystemBlobStore"]
