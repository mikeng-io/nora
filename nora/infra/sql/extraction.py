"""Shared Record source-row readers and the claim writer.

No retrieval API and no index projection. `nora.infra.sql.comprehend.readings`
persists Cognition step-one statements through `write_claims`; `source_rows`
and `link_replies` render the frozen snapshot its validator binds citations to.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import EXTRACTION_CLAIM as CLAIM
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_EDIT, MESSAGES


async def source_rows(
    conn: AsyncConnection, ids: list[int], *, include_reply: bool = False,
    as_of_ms: int | None = None,
) -> list[dict[str, Any]]:
    if not ids:
        return []
    # Locks serialize publication with hide/edit/delete. Invalidation triggers
    # take the same source row lock before retiring the dependent extraction.
    rows = (
        (
            await conn.execute(
                sa.select(MESSAGES)
                .where(
                    MESSAGES.c.id.in_(ids),
                    MESSAGES.c.hidden_at.is_(None),
                    sa.or_(MESSAGES.c.is_nora == 0, MESSAGES.c.delivery_status == "sent"),
                    ~sa.exists().where(MESSAGE_DELETE.c.message_id == MESSAGES.c.id),
                )
                .order_by(MESSAGES.c.id)
                .with_for_update(read=True)
            )
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        edit = (
            (
                await conn.execute(
                    sa.select(MESSAGE_EDIT)
                    .where(
                        MESSAGE_EDIT.c.message_id == row["id"],
                        *([MESSAGE_EDIT.c.ts <= as_of_ms] if as_of_ms is not None else []),
                    )
                    .order_by(MESSAGE_EDIT.c.id.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        result.append(
            {
                "id": row["id"],
                "person_id": row["person_id"],
                "ts": edit["ts"] if edit is not None and as_of_ms is not None else row["ts"],
                **({"quoted_origin_ts": row["ts"]} if as_of_ms is not None else {}),
                "body": edit["body"] if edit else row["body"],
                "mentions": json.loads(
                    (edit["mention_persons_json"] if edit else row["mention_persons_json"]) or "[]"
                ),
                "revision": edit["id"] if edit else None,
                "is_nora": bool(row["is_nora"]),
                **({"quoted_ts": row["quoted_ts"]} if include_reply else {}),
            }
        )
    return result


def link_replies(messages: list[dict[str, Any]], context: list[dict[str, Any]]) -> None:
    targets: dict[int, list[int]] = {}
    for row in messages + context:
        targets.setdefault(row.get("quoted_origin_ts", row["ts"]), []).append(row["id"])
    for row in messages:
        matches = targets.get(row["quoted_ts"], [])
        row["reply_to"] = matches[0] if len(matches) == 1 else None


async def write_claims(
    conn: AsyncConnection, *, extraction_id: int, unit: str,
    platform: str, origin_room: str, claims: list[dict[str, Any]],
    start_ordinal: int = 0,
) -> None:
    """The shared Record claim writer, after the caller's span/admission checks."""
    for ordinal, content in enumerate(claims, start=start_ordinal):
        message_ids = content["source_ids"]
        if "source_keys" in content:
            # Step one's numeric span-check slots can name attachments/social
            # rows. Only real message ids enter the message invalidation column.
            message_ids = [int(key[1]) for slot in content["source_ids"]
                           if (key := content["source_keys"].get(str(slot)))
                           and key[0] == "message"]
        await conn.execute(sa.insert(CLAIM).values(
            extraction_id=extraction_id, ordinal=ordinal, unit=unit, platform=platform,
            origin_room=origin_room, source_ids=message_ids, content=content,
        ))
