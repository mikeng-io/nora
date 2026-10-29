"""Count the tools Nora calls on VOICE turns, against text turns in the same rooms.

The measurement behind `NativeTool.hot_on` (DEC-256): which tools a spoken
turn reaches for often enough that a `load_tools` hop on every one of them
costs more than keeping their schemas resident on voice turns. It is the
measured half of that convention; the declared half is the `hot_on` field
beside each tool's entry in `nora.agent.tools.NATIVE_TOOLS`. Re-run it before
adding or removing a tool from an occasion, and put its output in the PR.

Read-only (`create_readonly_sql_engine`: the server refuses a write). Prints
AGGREGATES only: tool names and counts, never a message body, a sender or a
conversation id, so the output can go in a PR (`AGENTS.md`, Never list).

How a turn is found. A voice utterance is a `messages` row whose `raw_json`
carries `"voice": true` (`nora.web.voice` is the only writer of that flag). Its
turn is the first `tag_trace` row in the same conversation that completed
within `--match-seconds` of it AND whose recorded model messages contain the
utterance's opening characters; a turn found by time alone is not counted.
Text turns are every other `tag_trace` row in the conversations that had a
voice turn, over the same window. Tool calls are the `toolUse` blocks in the
turn's recorded model messages (Strands' own shape).

Run on the deploy host, through the container's venv (the script need not be
in the image; it is read from stdin):

    ssh norty 'cd ~/workspace/nora && docker compose exec -T nora \
        /app/.venv/bin/python - --days 30' < scripts/measure_voice_tool_calls.py

Small samples lie. Every count is printed beside its n and the window it
covers; a rate over a dozen turns is a direction, not a share.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime

import sqlalchemy as sa

from nora.agent.surface import DISCORD_ONLY_TOOLS
from nora.infra.sql.engine import create_readonly_sql_engine
from nora.secret import credentials

#: Characters of the utterance that must appear in the turn's recorded model
#: messages for the match to count. Short enough to survive the prompt's own
#: wrapping, long enough that two utterances in one room rarely share it.
_SNIPPET = 12

_VOICE = sa.text("""
    select id, conversation_id, ts, body from messages
    where raw_json is not null and raw_json::jsonb ->> 'voice' = 'true'
      and ts >= :since
    order by ts
""")

_TURNS = sa.text("""
    select tag_id, conversation_id, created_at, messages from tag_trace
    where conversation_id = any(:convs) and created_at >= :since
    order by created_at
""")


def _tool_uses(messages: list[dict]) -> list[tuple[str, dict]]:
    uses = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            use = block.get("toolUse") if isinstance(block, dict) else None
            if isinstance(use, dict):
                uses.append((str(use.get("name", "")), use.get("input") or {}))
    return uses


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def _report(label: str, turns: list[list[tuple[str, dict]]], group: frozenset[str]) -> None:
    n = len(turns)
    per_tool: Counter[str] = Counter()
    loaded: Counter[str] = Counter()
    in_group = hopped = direct = 0
    for uses in turns:
        per_tool.update({name for name, _ in uses})
        fetched: set[str] = set()
        for name, args in uses:
            if name == "load_tools":
                loaded.update(args.get("names") or [])
                fetched.update(args.get("names") or [])
        called = {name for name, _ in uses} & group
        if called:
            in_group += 1
            # A `load_tools` hop for the group, or a group tool called with no
            # hop at all (resident before deferral, or `ShelfLoader` loading it
            # on the spot). The two together are every turn that called one.
            if fetched & group:
                hopped += 1
            else:
                direct += 1
    print(f"\n{label}: n={n} turns")
    print(f"  calling any of the group: {in_group}/{n} "
          f"(paid a load_tools hop for it: {hopped}; called it without one: {direct})")
    print("  tool                      turns calling it")
    for name, count in per_tool.most_common():
        print(f"  {name:<25} {count:>3}/{n}")
    if loaded:
        print("  load_tools fetched        times")
        for name, count in loaded.most_common():
            print(f"  {name:<25} {count:>3}")


async def main() -> int:
    parser = argparse.ArgumentParser(prog="measure_voice_tool_calls")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--match-seconds", type=int, default=180)
    parser.add_argument("--json", action="store_true", help="also print the counts as JSON")
    parser.add_argument(
        "--group", default=",".join(sorted(DISCORD_ONLY_TOOLS)),
        help="comma-separated tools counted together (default: the music verbs)",
    )
    parser.add_argument(
        "--tag-ids", action="store_true",
        help="print `voice|text <tag_id> <created_at ms> group|-` per turn instead of the "
             "report, for joining to Langfuse ON the host (ids only, no content)",
    )
    parser.add_argument(
        "--from-date", default="",
        help="YYYY-MM-DD (UTC): read from this date instead of --days, e.g. the "
             "day `[llm] defer_tools` went on, so every turn counted ran deferred",
    )
    args = parser.parse_args()
    group = frozenset(n for n in args.group.split(",") if n)

    url = credentials().get("NORA_DATABASE_URL", credentials().get("DATABASE_URL", ""))
    if not url:
        print("NORA_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
        return 2
    since = int(time.time() * 1000) - args.days * 86_400_000
    if args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=UTC)
        since = int(start.timestamp() * 1000)
    engine = create_readonly_sql_engine(url)
    try:
        async with engine.connect() as conn:
            voice = (await conn.execute(_VOICE, {"since": since})).all()
            convs = sorted({row.conversation_id for row in voice})
            rows = []
            if convs:
                rows = (await conn.execute(_TURNS, {"convs": convs, "since": since})).all()
    finally:
        await engine.dispose()

    voice_turns: dict[str, list[tuple[str, dict]]] = {}
    unmatched = 0
    for utterance in voice:
        snippet = (utterance.body or "").strip()[:_SNIPPET]
        match = None
        for turn in rows:
            if turn.conversation_id != utterance.conversation_id or turn.tag_id in voice_turns:
                continue
            if not utterance.ts <= turn.created_at <= utterance.ts + args.match_seconds * 1000:
                continue
            if snippet and snippet in json.dumps(turn.messages, ensure_ascii=False):
                match = turn
                break
        if match is None:
            unmatched += 1
            continue
        voice_turns[match.tag_id] = _tool_uses(match.messages)

    if args.tag_ids:
        for t in rows:
            called = {name for name, _ in _tool_uses(t.messages)} & group
            print("voice" if t.tag_id in voice_turns else "text", t.tag_id, t.created_at,
                  "group" if called else "-")
        return 0
    text_turns = [_tool_uses(t.messages) for t in rows if t.tag_id not in voice_turns]
    first = min((t.created_at for t in rows), default=since)
    last = max((t.created_at for t in rows), default=since)
    asked = f"from {args.from_date}" if args.from_date else f"last {args.days} days"
    print(f"window: {asked}; turns read {_stamp(first)} .. {_stamp(last)} UTC")
    print(f"voice utterances: {len(voice)}; matched to a turn: {len(voice_turns)}; "
          f"unmatched (no reply turn, or not found): {unmatched}")
    print(f"rooms with a voice turn: {len(convs)}")
    _report("VOICE turns", list(voice_turns.values()), group)
    _report("TEXT turns in the same rooms", text_turns, group)
    if args.json:
        print(json.dumps({
            "days": args.days, "voice_turns": len(voice_turns), "text_turns": len(text_turns),
            "voice_tools": Counter(n for u in voice_turns.values() for n in {x for x, _ in u}),
            "text_tools": Counter(n for u in text_turns for n in {x for x, _ in u}),
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
