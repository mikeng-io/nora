"""Private, read-only candidate search; output is pseudonymised counts only.

Heuristics find shapes, never establish the scenario's truth. Confirm privately.
Run: uv run python -m scripts.find_cognition_anchors --source-url "$EVAL_SOURCE_URL"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from evals import pseudonyms


@dataclass(frozen=True)
class Moment:
    room: str
    at_ms: int
    sender: str
    body: str


def clone_url(value: str) -> str:
    url = make_url(value)
    if (url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_eval")
            or {k.lower() for k in url.query} & {"host", "port", "database", "dbname", "service"}):
        raise ValueError("requires a PostgreSQL *_eval clone without routing overrides")
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def room_aliases() -> dict[str, str]:
    # Only an explicit public room token may leave the host. Never fall back
    # to a real id, nor generate an irreversible hash of one.
    rules, _ = pseudonyms._active()
    return {real: alias for alias, real in rules.items()
            if re.fullmatch(r"room_[a-z][a-z0-9_]*", alias)}


def candidates(rows: Iterable[Moment], aliases: Mapping[str, str]) -> list[dict[str, object]]:
    rooms: dict[str, list[Moment]] = defaultdict(list)
    for row in rows:
        if row.room in aliases:
            rooms[row.room].append(row)
    output = []
    for room, messages in sorted(rooms.items()):
        messages.sort(key=lambda m: m.at_ms)
        for i, row in enumerate(messages):
            prior = [m for m in messages[max(0, i - 30):i] if row.at_ms - m.at_ms <= 86_400_000]
            body = row.body.strip().lower()
            short = 0 < len(body) <= 3
            questions = sum("?" in m.body or "\uFF1F" in m.body for m in prior)
            same_sender = sum(m.sender == row.sender for m in prior)
            shapes = {
                1: bool(re.search(r"downstairs|arrived|樓下|到咗", body)),
                2: short and not prior,
                3: short and questions > 0,
                4: bool(re.search(r"cancel|called off|取消|唔去", body)) and bool(prior),
                5: same_sender >= 2,
                6: bool(re.search(r"ticket|bought|門票|買咗", body)),
                8: len(prior) >= 2,  # room context for a private relation/admission check
                10: len(body) > 3 and not questions,
                11: questions >= 2,
                12: same_sender >= 2,
                13: bool(re.search(r"like|鍾意|喜歡", body)) and ("?" in body or "\uFF1F" in body),
            }
            for scenario, matches in shapes.items():
                if matches:
                    output.append({"room": aliases[room], "before_ms": row.at_ms + 1,
                                   "scenario": scenario, "counts": {
                                       "prior": len(prior), "questions": questions,
                                       "same_sender": same_sender}})
    return output


async def scan(source_url: str) -> list[dict[str, object]]:
    url = clone_url(source_url)  # refuse before making a connection
    aliases = room_aliases()
    if not aliases:
        raise ValueError("private pseudonym map needs room_* aliases")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            actual = await conn.scalar(text("SELECT current_database()"))
            if not str(actual).endswith("_eval"):
                raise ValueError("connected database is not an eval clone")
            result = await conn.execute(text(
                "SELECT conversation_id, ts, sender_id, coalesce(body, '') AS body "
                "FROM messages WHERE is_nora = 0 ORDER BY conversation_id, ts, id"
            ))
            return candidates((Moment(str(r[0]), int(r[1]), str(r[2]), str(r[3]))
                               for r in result), aliases)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()
    try:
        found = asyncio.run(scan(args.source_url))
    except Exception:  # noqa: BLE001 -- DB errors may contain credentials or private rows
        parser.exit(2, "anchor search unavailable; check clone URL, map and schema privately\n")
    for row in found:
        print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
