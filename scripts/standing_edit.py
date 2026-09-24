"""Operator one-shot: hand-edit one room's Standing position.

design/dreamer-agent-loop/26-standing.md, dated section 2026-09-25. On
2026-09-24 an operator-approved manual purge edited two `standing` rows
through `SqlStandingStore.write` — the ordinary fold path — and the history
now records those edits as folds, indistinguishable from the model's own
work. This script is the ONE other writer of that table, and it differs from
the fold in every way the fold must not:

1. **Compare-and-set, not a blind upsert.** The fold's `write` never refuses —
   it has no "someone else changed it" recovery and was never designed to
   need one. This script reads the row, then writes through
   `SqlStandingStore.write_if_unchanged`, which refuses (writes nothing) if
   the row is not EXACTLY what was read: a fold that lands between this
   script's read and its write must not be silently clobbered by a stale
   edit.
2. **`fold_count` and the row-id watermark pass through unchanged.** An
   operator correcting the body did not run a fold and must not claim to have
   read any further into the conversation than the last fold did — those two
   fields ride the read value straight back.
3. **The revision this write appends carries `source='operator'`**, never
   `'fold'` — the entire reason `standing_revision` gained that column.

Usage, on the deploy host (`config.toml` and `DATABASE_URL` are the daemon's
own, so this edits the row the running process reads):

    docker compose exec -T nora uv run --no-sync python \\
        scripts/standing_edit.py --platform signal --conversation-id g1 \\
        --body-file /tmp/new-standing-body.txt

Prints the old and new body and refuses (exit 1) with no write on any of:
no such row, an empty or over-ceiling body, or a stale read. Never prompts —
the operator supplies the finished text; there is no partial-edit mode.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from nora.domain.standing import Standing
from nora.infra.sql.standing import SqlStandingStore

CONFIG_PATH = Path("/app/config.toml")


class EditRefusedError(Exception):
    """The edit did not land; nothing is claimed and nothing was written."""


async def edit_standing(
    *,
    platform: str,
    conversation_id: str,
    new_body: str,
    store: SqlStandingStore,
    now_ms: int,
) -> tuple[Standing, Standing]:
    """Read the current position, then write `new_body` over it as an
    operator edit. Returns `(before, after)`. Raises `EditRefusedError` when
    there is no row to edit or the compare-and-set refuses a stale read —
    both are refusals, never a partial write.

    `fold_count` and `last_folded_row_id` are carried from `before` — see the
    module docstring point 2 — so the only field this function's caller
    actually changes is `body`, plus `updated_at`, which records when the
    operator wrote it (the same meaning it carries for a fold: the instant
    THIS position was taken).
    """
    before = await store.read(platform=platform, conversation_id=conversation_id)
    if before is None:
        raise EditRefusedError(
            f"no standing row for platform={platform!r} "
            f"conversation_id={conversation_id!r} — nothing to edit"
        )
    try:
        after = Standing(
            platform=platform,
            conversation_id=conversation_id,
            body=new_body,
            fold_count=before.fold_count,
            updated_at=now_ms,
            last_folded_row_id=before.last_folded_row_id,
        )
    except ValueError as exc:
        raise EditRefusedError(str(exc)) from exc
    applied = await store.write_if_unchanged(expected=before, standing=after)
    if not applied:
        raise EditRefusedError(
            "the row changed since it was read — a fold landed, or another "
            "edit ran, between the read and this write; re-run to read the "
            "current row and try again"
        )
    return before, after


async def _main(argv: list[str]) -> int:
    from nora.config import load
    from nora.infra.sql.engine import create_sql_engine

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True)
    parser.add_argument("--conversation-id", required=True)
    body_group = parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument(
        "--body", help="the new position, inline (prefer --body-file for "
        "anything long or non-ASCII, to avoid shell quoting)",
    )
    body_group.add_argument(
        "--body-file", type=Path,
        help="UTF-8 file holding the new position",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)

    new_body = (
        args.body if args.body is not None
        else args.body_file.read_text(encoding="utf-8")
    ).strip()

    cfg = load(args.config, require_secrets=False)
    database_url = os.environ.get(cfg.store.database_url_env, "")
    if not database_url:
        print(f"{cfg.store.database_url_env} is not set", file=sys.stderr)
        return 2

    engine = create_sql_engine(database_url)
    try:
        before, after = await edit_standing(
            platform=args.platform,
            conversation_id=args.conversation_id,
            new_body=new_body,
            store=SqlStandingStore(engine),
            now_ms=int(datetime.now(UTC).timestamp() * 1000),
        )
    except EditRefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print(f"ok — {args.platform}/{args.conversation_id}, source=operator")
    print(f"before: {before.body}")
    print(f"after:  {after.body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
