#!/usr/bin/env python3
"""Lifecycle audit events for Nora's computer (design/sandbox-runtime §12, DEC-262).

`nora-computer` runs on the deploy host as the operator, outside the nora
container and outside the VM. §12 requires a row for each VM create, recreate,
computerd restart, token rotation and policy-log read, and until now there was
none: the audit covered only calls the tools made. This is the operator-side
writer, the smallest honest one:

- **A host-side spool is the first record.** Each event is one line appended to
  `~/.local/state/nora-computer/lifecycle-events.jsonl` before anything else
  happens, so an event survives Postgres being down at boot (the unit starts
  before the db is healthy more often than not).
- **Postgres is the record of truth.** `sql` turns the spool into INSERTs into
  `computer_events` (actor `operator`, a value `ck_computer_events_actor` already
  allows, so no migration), `ON CONFLICT (operation_id, phase) DO NOTHING`, so
  replaying the whole spool is idempotent and a sync can be run any time. The
  table's append-only triggers are untouched: an INSERT that conflicts is skipped
  before any UPDATE could happen, and nothing here ever updates or deletes.
- **Never the token.** A rotation is recorded as the first 12 hex characters of
  the token's SHA-256, enough to tell two tokens apart and to show a rotation
  happened, not enough to recover one.

stdlib only: it runs under the host's `python3` with no virtualenv.

    lifecycle_event.py record --spool FILE --event NAME [--status ok|failed]
                              [--name VM] [--detail key=value ...]
    lifecycle_event.py sql    --spool FILE
    lifecycle_event.py fingerprint            (reads the token from stdin)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The `tool` of every row this writes; the agent tools' own names are
#: `computer_run` and so on, so `where tool = 'computer_vm'` is the operator-side
#: trail.
TOOL = "computer_vm"
ACTOR = "operator"

#: What can happen to the machine. A closed set: a typo in the script would
#: otherwise be a new kind of event nobody queries.
EVENTS: frozenset[str] = frozenset({
    "create", "recreate", "destroy", "computerd_start", "token_rotation",
    "policy_log_read", "egress_apply", "backup", "restore",
})
STATUSES: frozenset[str] = frozenset({"ok", "failed"})

FINGERPRINT_CHARS = 12


def fingerprint(token: str) -> str:
    """A token's identity for the audit: a truncated digest, never the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def make_event(
    event: str, status: str = "ok", *, name: str = "", detail: Mapping[str, str] | None = None,
    now: datetime | None = None, actor_host: str = "",
) -> dict[str, Any]:
    if event not in EVENTS:
        raise ValueError(f"unknown lifecycle event {event!r}")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "id": str(uuid.uuid4()),
        "operation_id": str(uuid.uuid4()),
        "at": moment.isoformat(),
        "event": event,
        "status": status,
        "name": _clean(name),
        "host": _clean(actor_host),
        "detail": {_clean(k): _clean(v) for k, v in (detail or {}).items()},
    }


def _clean(text: str) -> str:
    """No NUL: JSON would carry it as `\\u0000`, which Postgres' jsonb refuses,
    and one such line in the spool would fail every later sync (psql stops at the
    first error) and park the whole trail behind it. Stripped at the source."""
    return text.replace("\x00", "")


def _quote(text: str) -> str:
    """A SQL string literal. Single quotes doubled and NULs refused: psql reads
    this on stdin with standard_conforming_strings on, so a backslash is only a
    backslash. The values are the script's own words, not user text, and this
    still does not trust them."""
    if "\x00" in text:
        raise ValueError("a NUL cannot be stored in a text column")
    return "'" + text.replace("'", "''") + "'"


def insert_sql(event: Mapping[str, Any]) -> str:
    """One idempotent INSERT of `event` into `computer_events`.

    `phase` is `finished` with `started_at = finished_at = at`: a lifecycle step
    is recorded once it has happened, so there is no `started` row to orphan.
    `created_at` is when it HAPPENED, so a row replayed from the spool days
    later still sorts where it belongs."""
    when = _quote(str(event["at"]))
    request = json.dumps(
        {"event": event["event"], "name": event["name"], "host": event["host"]},
        sort_keys=True,
    )
    result = json.dumps({"status": event["status"], **event["detail"]}, sort_keys=True)
    return (
        "INSERT INTO computer_events (id, operation_id, phase, created_at, started_at, "
        "finished_at, actor, requester_person_id, platform, conversation_id, "
        "conversation_type, tag_id, scope, tool, path, trace_id, span_id, status, "
        "request, result, output_ref, session_id) VALUES ("
        f"{_quote(str(event['id']))}::uuid, {_quote(str(event['operation_id']))}::uuid, "
        f"'finished', {when}::timestamptz, {when}::timestamptz, {when}::timestamptz, "
        f"{_quote(ACTOR)}, NULL, '', '', '', '', '', {_quote(TOOL)}, 'direct', '', '', "
        f"{_quote(str(event['status']))}, {_quote(request)}::jsonb, {_quote(result)}::jsonb, "
        "'{}'::jsonb, NULL) ON CONFLICT (operation_id, phase) DO NOTHING;"
    )


def read_spool(path: Path) -> list[dict[str, Any]]:
    """Every event in the spool. A line that is not an event is an ERROR, not
    skipped: a spool that silently drops a line is an audit that does."""
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: not a lifecycle event ({exc})") from exc
    return events


def _parse_detail(pairs: list[str]) -> dict[str, str]:
    detail: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--detail wants key=value, got {pair!r}")
        detail[key] = value
    return detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--spool", type=Path, required=True)
    rec.add_argument("--event", required=True, choices=sorted(EVENTS))
    rec.add_argument("--status", default="ok", choices=sorted(STATUSES))
    rec.add_argument("--name", default="")
    rec.add_argument("--host", default="")
    rec.add_argument("--detail", action="append", default=[])
    sql = sub.add_parser("sql")
    sql.add_argument("--spool", type=Path, required=True)
    sub.add_parser("fingerprint")
    args = parser.parse_args(argv)

    if args.command == "fingerprint":
        print(fingerprint(sys.stdin.read().strip()))
        return 0
    if args.command == "record":
        event = make_event(
            args.event, args.status, name=args.name, detail=_parse_detail(args.detail),
            actor_host=args.host,
        )
        args.spool.parent.mkdir(parents=True, exist_ok=True)
        # One short line, O_APPEND: two writers interleave whole lines, never
        # halves of one.
        with args.spool.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        print(event["id"])
        return 0
    for event in read_spool(args.spool):
        print(insert_sql(event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
