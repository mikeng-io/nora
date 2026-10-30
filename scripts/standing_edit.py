"""Operator one-shot: hand-edit one room's Standing position.

An operator edit written through `SqlStandingStore.write` (the ordinary fold
path) would be recorded in the history as a fold, indistinguishable from the
model's own work. This script is the ONE other writer of that table, and it
differs from the fold in every way the fold must not:

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
   `'fold'` — the reason `standing_revision` has that column.

**The reverse race is NOT closed** (a fold can still overwrite an operator
edit). `write_if_unchanged` protects THIS script's
write from a fold that landed before it; it does nothing for a fold that
lands AFTER — a fold reads the prior, calls the model (seconds), then does
its own blind upsert with no compare-and-set at all, so an edit landing
inside that window is silently lost. This script narrows the window it can
be caught in by re-reading the head a few seconds after its own write and
warning if the row has moved again, but that is a best-effort check, not a
guarantee: a fold whose model call finishes after the recheck, or one
already past its own read when this write lands, is not caught by anything
here.

Default action is a DRY RUN: it reads the row, validates the new body, and
prints a unified diff — it writes nothing unless `--apply` is given. Refuses
(exit 1), always before any write, on: no such row, an empty body, a body
over `[standing] max_chars` (the operating cap the fold itself is held to —
this script uses the same one, not the database's much larger hard ceiling),
or a stale read. Never prompts — the operator supplies the finished text.

Usage, on the deploy host (`config.toml` and `DATABASE_URL` are the daemon's
own, so this edits the row the running process reads; `standing_edit.py` is
baked into the image at `/app/scripts/`, same as `check_version_stamp.py`):

    # Preview only — no write:
    docker compose exec -T nora uv run --no-sync python \\
        scripts/standing_edit.py --platform signal --conversation-id g1 \\
        --body-file /tmp/new-standing-body.txt

    # Apply, body piped through stdin so the operator never has to get the
    # body FILE itself onto the container's filesystem (`docker cp` is still
    # how the SCRIPT reaches the image if it predates a rebuild):
    cat /tmp/new-standing-body.txt | docker compose exec -T nora \\
        uv run --no-sync python scripts/standing_edit.py --platform signal \\
        --conversation-id g1 --body-file - --apply
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import difflib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nora.domain.standing import Standing
from nora.infra.sql.standing import SqlStandingStore
from nora.secret import credentials

CONFIG_PATH = Path("/app/config.toml")

#: How long to wait before re-reading the head after a write, looking for a
#: fold that landed on top of it. A few seconds, not a science: it is a
#: best-effort widening of the catch window, never a guarantee (see the
#: module docstring).
DEFAULT_RECHECK_DELAY_SECONDS = 5.0


class EditRefusedError(Exception):
    """The edit did not land; nothing is claimed and nothing was written."""


@dataclass(frozen=True)
class EditOutcome:
    """What `apply_operator_edit` did, and what it saw when it looked again."""

    before: Standing
    after: Standing
    #: The head as re-read `recheck_delay_seconds` after the write, or `None`
    #: if the recheck itself found no row at all (the room's `standing` row
    #: was deleted out from under this — recorded as a state, not swallowed).
    reread: Standing | None
    #: `True` when `reread` no longer matches what this script just wrote —
    #: the fold-overwrote-the-edit race the module docstring names. `False`
    #: is not a guarantee nothing happened; it means nothing had happened
    #: BY the time of the recheck.
    moved_again: bool


async def _read_or_refuse(
    store: SqlStandingStore, *, platform: str, conversation_id: str
) -> Standing:
    row = await store.read(platform=platform, conversation_id=conversation_id)
    if row is None:
        raise EditRefusedError(
            f"no standing row for platform={platform!r} "
            f"conversation_id={conversation_id!r} — nothing to edit"
        )
    return row


def _validate_body(new_body: str, *, max_chars: int) -> None:
    if not new_body.strip():
        raise EditRefusedError("the new body is empty")
    if len(new_body) > max_chars:
        raise EditRefusedError(
            f"the new body is {len(new_body)} characters, over "
            f"[standing] max_chars = {max_chars} — the same operating cap "
            "the fold itself is held to. This is not the database's hard "
            "ceiling (4000); an operator edit is not exempt from the cap a "
            "fold would be refused for exceeding."
        )


def render_diff(*, before_body: str, new_body: str) -> str:
    """A unified diff, so the operator sees exactly what changes before
    deciding to `--apply` — never the two full bodies side by side, which
    hides a one-word change in two paragraphs of unchanged text."""
    diff = difflib.unified_diff(
        before_body.splitlines(keepends=True),
        new_body.splitlines(keepends=True),
        fromfile="before", tofile="after",
    )
    rendered = "".join(diff)
    return rendered if rendered else "(no change)\n"


def _build_after(before: Standing, new_body: str, *, now_ms: int) -> Standing:
    """`before` with the new body and time and NOTHING else changed — a
    `replace`, not a field-by-field rebuild: a rebuild that omitted
    `working_set` would write an empty working set over whatever the room
    held, which `write_if_unchanged` refuses."""
    try:
        return dataclasses.replace(before, body=new_body, updated_at=now_ms)
    except ValueError as exc:
        raise EditRefusedError(str(exc)) from exc


async def preview_operator_edit(
    *,
    platform: str,
    conversation_id: str,
    new_body: str,
    store: SqlStandingStore,
    max_chars: int,
) -> tuple[Standing, str]:
    """Read-only: validates the body and returns `(before, diff)`. Writes
    nothing — this is what runs without `--apply`, and what `--apply` runs
    FIRST, so the two paths can never disagree about what would change."""
    before = await _read_or_refuse(store, platform=platform, conversation_id=conversation_id)
    _validate_body(new_body, max_chars=max_chars)
    return before, render_diff(before_body=before.body, new_body=new_body)


async def apply_operator_edit(
    *,
    platform: str,
    conversation_id: str,
    new_body: str,
    store: SqlStandingStore,
    now_ms: int,
    max_chars: int,
    recheck_delay_seconds: float = DEFAULT_RECHECK_DELAY_SECONDS,
) -> EditOutcome:
    """Read, validate, write through the compare-and-set path, then look
    again. Raises `EditRefusedError` for everything `preview_operator_edit`
    would, plus a stale-row refusal from `write_if_unchanged` itself — all of
    those leave the database exactly as it was. The recheck AFTER a
    successful write is not a refusal path: the write already landed, so
    this only reports whether it is still standing a few seconds later.
    """
    before = await _read_or_refuse(store, platform=platform, conversation_id=conversation_id)
    _validate_body(new_body, max_chars=max_chars)
    after = _build_after(before, new_body, now_ms=now_ms)
    applied = await store.write_if_unchanged(expected=before, standing=after)
    if not applied:
        raise EditRefusedError(
            "the row changed since it was read — a fold landed, or another "
            "edit ran, between the read and this write; re-run to read the "
            "current row and try again"
        )
    if recheck_delay_seconds > 0:
        await asyncio.sleep(recheck_delay_seconds)
    reread = await store.read(platform=platform, conversation_id=conversation_id)
    moved_again = (
        reread is None
        or reread.body != after.body
        or reread.updated_at != after.updated_at
        or reread.fold_count != after.fold_count
        or reread.last_folded_row_id != after.last_folded_row_id
    )
    return EditOutcome(before=before, after=after, reread=reread, moved_again=moved_again)


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
        help="UTF-8 file holding the new position, or '-' to read stdin",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument(
        "--apply", action="store_true",
        help="write the edit. Without it, this prints a diff and writes nothing.",
    )
    parser.add_argument(
        "--recheck-after", type=float, default=DEFAULT_RECHECK_DELAY_SECONDS,
        help=f"seconds to wait before re-reading the head after --apply "
             f"writes, looking for a fold that landed on top of it "
             f"(default {DEFAULT_RECHECK_DELAY_SECONDS})",
    )
    args = parser.parse_args(argv)

    if args.body is not None:
        new_body = args.body.strip()
    elif str(args.body_file) == "-":
        new_body = sys.stdin.read().strip()
    else:
        new_body = args.body_file.read_text(encoding="utf-8").strip()

    cfg = load(args.config, require_secrets=False)
    database_url = credentials().get(cfg.store.database_url_env, "")
    if not database_url:
        print(f"{cfg.store.database_url_env} is not set", file=sys.stderr)
        return 2

    engine = create_sql_engine(database_url)
    store = SqlStandingStore(engine)
    try:
        if not args.apply:
            _before, diff = await preview_operator_edit(
                platform=args.platform, conversation_id=args.conversation_id,
                new_body=new_body, store=store, max_chars=cfg.standing.max_chars,
            )
            print(f"DRY RUN — {args.platform}/{args.conversation_id}, nothing written")
            print(diff, end="")
            print("re-run with --apply to write this")
            return 0

        outcome = await apply_operator_edit(
            platform=args.platform, conversation_id=args.conversation_id,
            new_body=new_body, store=store, max_chars=cfg.standing.max_chars,
            now_ms=int(datetime.now(UTC).timestamp() * 1000),
            recheck_delay_seconds=args.recheck_after,
        )
    except EditRefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print(f"ok — {args.platform}/{args.conversation_id}, source=operator")
    print(render_diff(before_body=outcome.before.body, new_body=outcome.after.body), end="")
    if outcome.moved_again:
        after_diff = render_diff(
            before_body=outcome.after.body,
            new_body=outcome.reread.body if outcome.reread is not None else "(row deleted)",
        )
        print(
            f"WARNING: the row moved again within {args.recheck_after}s of this "
            "write — a fold likely landed on top of the operator edit "
            "(26-standing.md §9, \"a fold can still overwrite an operator "
            "edit\"). What changed since this script's own write:",
            file=sys.stderr,
        )
        print(after_diff, end="", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
