"""A fetched web page, kept whole so `web_fetch` reads it in slices.

`web_fetch` materializes a page ONCE — the bytes are sealed into the shared
`BlobStore` (rustfs, content-addressed) and this row is the index entry:
whose conversation fetched it, the url, the blob key, the provenance and the
expiry. The model then pages the stored text (`offset`) or looks inside it
(`about`) without another provider call and without another egress.

The row lives in its own namespace on purpose: a page's words are a
stranger's (R-13), so fetched text never indexes into memory or documents —
the two stores stay disjoint by construction.

A row expires after `FETCHED_PAGE_TTL`; an expired row reads as a miss. Every
materialization purges the rows past their expiry and hands back the blob keys
nothing else names, which `web_fetch` then deletes through the blob store
(`BlobStore.delete`, which keeps bytes put within its grace window).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

#: How long a fetched page answers reads in its conversation. A conversation
#: is a short-lived context: within it, «the page» is the copy that was
#: fetched, which is also what makes a repeated fetch free.
FETCHED_PAGE_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class FetchedPage:
    """One conversation's materialized copy of one url."""

    conversation_id: str
    url: str
    blob_key: str
    fetched_at: datetime
    expires_at: datetime
    #: Provider-side facts the served slices keep: `provider`, `saved_copy`,
    #: `truncated` (the provider's own cut, not a serve-side clamp) and
    #: `images` (links only, for `media_fetch`).
    provenance: dict[str, Any]


class FetchedPageStore(Protocol):
    """The `fetched_pages` index, seen by `web_fetch`."""

    async def get(
        self, *, conversation_id: str, url: str, now: datetime
    ) -> FetchedPage | None:
        """The live row for `(conversation_id, url)`, or `None` — including
        for an expired row, which reads exactly as a miss."""
        ...

    async def put(self, *, page: FetchedPage) -> None:
        """Insert or replace the row for `(conversation_id, url)`."""
        ...

    async def purge_expired(self, *, now: datetime, limit: int = 20) -> list[str]:
        """Delete up to `limit` rows past their expiry and return the blob keys
        of those rows that no other row names any more — the bytes the caller
        may delete. A key still named elsewhere is not returned."""
        ...
