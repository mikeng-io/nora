"""In-memory Culture appends with the SQL store's parent and validation rules."""

from __future__ import annotations

from uuid import uuid4

from nora.domain.culture import CultureRevision

#: `nora.infra.sql.schema.CULTURE_BODY_CEILING_CHARS`, restated because
#: `nora.testing` may not import `nora.infra` (the fake must stand without the
#: adapter it doubles). The contract suite pins the two equal.
CULTURE_BODY_CEILING_CHARS = 32_000


class InMemoryCultureStore:
    def __init__(self) -> None:
        self._heads: dict[str, str] = {}

    async def append(
        self,
        *,
        key: str,
        body: str,
        written_at: int,
        established_by: str,
        instruction_sha256: str | None = None,
        model: str | None = None,
        citations: tuple[str, ...] = (),
    ) -> CultureRevision:
        if not key.strip(" "):
            raise ValueError("culture_has_a_key_check")
        if not body.strip(" ") or len(body) > CULTURE_BODY_CEILING_CHARS:
            raise ValueError(
                f"culture_revision body check: {len(body)} characters")
        if not established_by.strip(" "):
            raise ValueError("culture_revision_established_by_check")
        revision = CultureRevision(
            id=str(uuid4()), culture_key=key, body=body, written_at=written_at,
            established_by=established_by,
            parent_revision_id=self._heads.get(key),
            instruction_sha256=instruction_sha256, model=model,
            citations=tuple(citations),
        )
        self._heads[key] = revision.id
        return revision
