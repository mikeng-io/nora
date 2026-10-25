"""Record message readings: structured statements, never assertions of truth."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

VERSION = 1


def event_time(expression: str | None, assertion_ms: int) -> dict[str, str | None]:
    """Only unambiguous ISO dates/intervals and day offsets; never guess a date.

    # ponytail: other date phrases retain their wording as unknown; extend this
    # resolver against labelled date cases when its measured coverage needs it.
    """
    start = end = None
    if expression:
        offsets = {
            "today": 0,
            "tomorrow": 1,
            "yesterday": -1,
            "今日": 0,
            "今天": 0,
            "明天": 1,
            "聽日": 1,
            "昨日": -1,
            "尋日": -1,
            "昨天": -1,
        }
        if expression.lower() in offsets:
            reference = datetime.fromtimestamp(assertion_ms / 1000, UTC)
            start = end = (
                reference.astimezone(ZoneInfo("Asia/Hong_Kong")).date()
                + timedelta(days=offsets[expression.lower()])
            ).isoformat()
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}(/\d{4}-\d{2}-\d{2})?", expression):
            try:
                dates = [date.fromisoformat(s) for s in expression.split("/")]
                if dates[0] <= dates[-1]:
                    start, end = dates[0].isoformat(), dates[-1].isoformat()
            except ValueError:
                pass
    return {
        "start": start,
        "end": end,
        "expression": expression,
        "status": "resolved" if start else "unknown",
    }


def validate_claims(output: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind every claim to batch members and exact supporting source spans.

    Context can resolve a reference, but cannot be cited as a new batch claim.
    Semantic paraphrase accuracy still needs model acceptance measurement.
    """
    sources = {r["id"]: r for r in snapshot["messages"]}
    known = {
        p
        for r in snapshot["messages"] + snapshot["context"]
        for p in [r["person_id"], *r["mentions"]]
        if p
    }
    claims = []
    for claim in output["claims"]:
        ids = set(claim["source_ids"])
        if not ids <= sources.keys():
            raise ValueError("citation outside batch")
        if len(ids) != len(claim["source_ids"]):
            raise ValueError("duplicate citation")
        if {e["source_id"] for e in claim["evidence"]} != ids:
            raise ValueError("evidence does not cover citations")
        for evidence in claim["evidence"]:
            if evidence["quote"] not in sources[evidence["source_id"]]["body"]:
                raise ValueError("evidence absent from source")
        if any(s["person_id"] is not None and s["person_id"] not in known for s in claim["about"]):
            raise ValueError("unknown resolved subject")
        time_source = sources.get(claim["happened_at"]["source_id"])
        if claim["happened_at"]["source_id"] not in ids or time_source is None:
            raise ValueError("event reference outside citations")
        expression = claim["happened_at"]["expression"]
        if expression and expression not in time_source["body"]:
            raise ValueError("event expression absent from source")
        if claim["where"] and not any(claim["where"] in sources[i]["body"] for i in ids):
            raise ValueError("place absent from source")
        content = dict(claim)
        content["happened_at"] = {
            **event_time(expression, time_source["ts"]),
            "reference_source_id": time_source["id"],
            "reference_asserted_at_ms": time_source["ts"],
        }
        content["assertions"] = [
            {
                "source_id": i,
                "asserted_at_ms": sources[i]["ts"],
                "person_id": sources[i]["person_id"],
            }
            for i in sorted(ids)
        ]
        claims.append(content)
    return claims
