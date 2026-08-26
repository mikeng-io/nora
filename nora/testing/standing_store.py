"""`InMemoryStandingStore` — the domain-only double for
`nora.infra.sql.standing.SqlStandingStore`, bound to it by
`tests/contract/test_standing_store_contract.py` so the two cannot drift.

**It refuses what its production sibling refuses**, which for this tier means
one thing and it is the ceiling: `Standing.__post_init__` raises on an
over-long body and `standing_body_fits_the_ceiling_check` raises on the same
input, so a fake that stored whatever it was handed would turn a missing
guarantee into a passing gate. It gets that for free by holding `Standing`
objects rather than dicts — which is the point of holding them.

The dict is keyed `(platform, conversation_id)`, the row's own primary key, so
"write twice, read once" answers the second write here for the same structural
reason `ON CONFLICT DO UPDATE` does in Postgres.
"""

from __future__ import annotations

from nora.domain.standing import Standing


class InMemoryStandingStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Standing] = {}

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Standing | None:
        return self._rows.get((platform, conversation_id))

    async def write(self, *, standing: Standing) -> None:
        self._rows[(standing.platform, standing.conversation_id)] = standing
