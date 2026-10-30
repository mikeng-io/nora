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
from datetime import UTC, date, datetime

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
    is_nora: bool = False
    id: int = 0


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


# Shared with the confirmer: includes the moment and Nora's replies.
WINDOW_MESSAGES, WINDOW_MS = 30, 86_400_000
MAX_CANDIDATES = 20
SHORT_SPAN_MS = 15 * 60_000
ACK = re.compile(r"(?:k|ok|okay|好|得)[.!\uFF01。]*", re.I)
ARRIVAL = re.compile(r"\b(?:downstairs|arrived|visiting)\b|樓下|到咗|到喇|上嚟", re.I)
CANCEL = re.compile(r"\bcancel(?:led|ed)?\b|called off|\boff\b|取消|唔去", re.I)
PURCHASE = re.compile(r"\b(?:bought|booked|purchased)\b|買咗|訂咗", re.I)
TICKET = re.compile(r"\b(?:ticket|tickets|concert)\b|門票|演唱會", re.I)
ATTENDANCE = re.compile(r"\b(?:attended|went to|was there)\b|去咗|睇咗", re.I)
PLAN = re.compile(
    r"\b(?:dinner|lunch|meet|meeting|concert|party)\b|食飯|晚飯|午餐|見面|演唱會", re.I)
DAY_TIME = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow)\b|"
    r"\b(?:at|around) \d{1,2}\b|(?:星期|週|禮拜)[一二三四五六日天]|聽日|今日|\d{1,2}點", re.I)
OPTION = re.compile(r"[?\uFF1F]|\bor\b|定係|不如", re.I)
# ponytail: explicit topic vocabulary; extend with generated examples when recall is measured.
TOPICS = re.compile(r"\b(?:racing|cooking|photography|chess)\b|賽車|烹飪|攝影|象棋", re.I)
ITEM = re.compile(r'"[^"\n]{2,60}"|「[^」\n]{2,60}」|《[^》\n]{2,60}》')
LIKES_QUESTION = re.compile(
    # English needs its question mark; the Cantonese form is a question unpunctuated.
    r"\bwhat does .{1,40} like\b[^\n]*[?\uFF1F]|.{1,20}(?:鍾意|喜歡)(?:啲?咩|什麼)", re.I)


def _day(row: Moment) -> date:
    # Room-history day buckets are explicitly Hong Kong local days.
    return datetime.fromtimestamp((row.at_ms + 8 * 3_600_000) / 1000, UTC).date()


def _plan_tokens(body: str) -> set[str]:
    return {m.group().lower() for pattern in (PLAN, DAY_TIME) for m in pattern.finditer(body)}


def _scores(row: Moment, prior: list[Moment], history: list[Moment],
            later: list[Moment]) -> dict[int, int]:
    """Message evidence only; open notes/plans still require private confirmation.

    Cross-day evidence uses room history for 5/12, not extra model context. The
    bounded confirmer cannot establish those histories when absent from its window.
    """
    body = row.body.strip()
    recent = [m for m in prior if row.at_ms - m.at_ms <= SHORT_SPAN_MS]
    ack = bool(ACK.fullmatch(body))
    proposal = [m for m in recent if OPTION.search(m.body) and PLAN.search(m.body)
                and DAY_TIME.search(m.body)]
    shared_plan = _plan_tokens(body)
    cancellation = bool(CANCEL.search(body)) and any(
        PLAN.search(m.body) and shared_plan & _plan_tokens(m.body) for m in prior)
    visits = {_day(m) for m in [*history, row]
              if m.sender == row.sender and ARRIVAL.search(m.body)}
    topics = {m.group().lower() for m in TOPICS.finditer(body)}
    topic_days = {_day(m) for m in [*history, row] if m.sender == row.sender
                  and topics & {t.group().lower() for t in TOPICS.finditer(m.body)}}
    discussion = [m for m in [*recent, row] if PLAN.search(m.body)
                  and OPTION.search(m.body) and shared_plan & _plan_tokens(m.body)]
    # Three human speakers establish a group without guessing from a private room id.
    group = len({m.sender for m in [*prior, row] if not m.is_nora}) >= 3
    return {
        1: int(bool(ARRIVAL.search(body))),
        2: int(ack and not any(OPTION.search(m.body) or PLAN.search(m.body) for m in recent)),
        3: int(ack and bool(proposal) and proposal[-1].sender != row.sender),
        4: int(cancellation),
        5: len(visits) if ARRIVAL.search(body) and len(visits) >= 3 else 0,
        6: int(bool(PURCHASE.search(body) and TICKET.search(body)) and not any(
            m.sender == row.sender and ATTENDANCE.search(m.body) for m in later)),
        11: len(discussion) if (PLAN.search(body) and OPTION.search(body)
                               and len(discussion) >= 2
                               and len({m.sender for m in discussion}) >= 2) else 0,
        12: len(topic_days) if topics and ITEM.search(body) and len(topic_days) >= 2 else 0,
        13: int(group and bool(LIKES_QUESTION.search(body))),
    }


def candidates(rows: Iterable[Moment], aliases: Mapping[str, str], *,
               limit: bool = True) -> list[dict[str, object]]:
    rooms: dict[str, list[Moment]] = defaultdict(list)
    for row in rows:
        if row.room in aliases and re.fullmatch(r"room_[a-z][a-z0-9_]*", aliases[row.room]):
            rooms[row.room].append(row)
    output: list[dict[str, object]] = []
    for room, messages in sorted(rooms.items()):
        messages.sort(key=lambda m: (m.at_ms, m.id, m.sender, m.body))
        ranked: dict[int, list[tuple[int, dict[str, object]]]] = defaultdict(list)
        for i, row in enumerate(messages):
            # A timestamp cursor cannot select an earlier member of a tied batch.
            if row.is_nora or (i + 1 < len(messages) and messages[i + 1].at_ms == row.at_ms):
                continue
            prior = [m for m in messages[max(0, i - WINDOW_MESSAGES + 1):i]
                     if m.at_ms >= row.at_ms + 1 - WINDOW_MS]
            for scenario, strength in _scores(row, prior, messages[:i], messages[i + 1:]).items():
                if strength:
                    ranked[scenario].append((strength, {
                        "room": aliases[room], "before_ms": row.at_ms + 1, "message_id": row.id,
                        "scenario": scenario, "counts": {
                            "prior": len(prior),
                            "questions": sum(bool(re.search(r"[?\uFF1F]", m.body)) for m in prior),
                            "same_sender": sum(m.sender == row.sender for m in prior),
                            # The rule's own evidence: visit or topic days for 5/12,
                            # which the confirmer's 24h window cannot show.
                            "strength": strength}}))
        for scenario in sorted(ranked):
            output.extend(item for _, item in sorted(
                ranked[scenario], key=lambda pair: (-pair[0], int(str(pair[1]["before_ms"]))))
                          [:MAX_CANDIDATES if limit else None])
    return output


def wake_candidates(matches: list[dict[str, object]], wakes: Mapping[int, object],
                    aliases: Mapping[str, str]) -> list[dict[str, object]]:
    """Join heuristic moments to the recorded batch, never a timestamp proxy."""
    from nora.domain.cognition_ground import record_ref
    by_message: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for match in matches:
        by_message[(str(match["room"]), int(str(match["message_id"])))].append(match)
    ranked: dict[tuple[str, int], dict[int, int]] = defaultdict(dict)
    for wake_id, view in wakes.items():
        for stimulus in view.pending:
            ref = record_ref(stimulus)
            alias = aliases.get(stimulus.conversation_id)
            if ref is None or ref[0] != "messages" or alias is None:
                continue
            for match in by_message.get((alias, ref[1]), ()):
                key = (alias, int(match["scenario"]))
                strength = int(match["counts"]["strength"])
                ranked[key][wake_id] = max(ranked[key].get(wake_id, 0), strength)
    return [{"room": room, "scenario": scenario, "wake": wake, "strength": strength}
            for (room, scenario), scores in sorted(ranked.items())
            for wake, strength in sorted(
                scores.items(), key=lambda x: (-x[1], x[0]))[:MAX_CANDIDATES]]


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
                "SELECT conversation_id, ts, sender_id, coalesce(body, '') AS body, is_nora, id "
                "FROM messages ORDER BY conversation_id, ts, id"
            ))
            matches = candidates((Moment(str(r[0]), int(r[1]), str(r[2]), str(r[3]),
                                      bool(r[4]), int(r[5]))
                               for r in result), aliases, limit=False)
            wake_ids = list(await conn.scalars(text("SELECT id FROM cognition_wake ORDER BY id")))
        from nora.infra.sql.cognition_replay import recorded_wake
        wakes = {}
        for wake_id in wake_ids:
            try:
                _, wakes[wake_id] = await recorded_wake(engine, wake_id)
            except ValueError:
                continue  # incomplete recorded batch cannot be replayed
        return wake_candidates(matches, wakes, aliases)
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
