#!/usr/bin/env python3
"""Lifecycle audit events for Nora's computer (DEC-262).

`nora-computer` runs on the deploy host as the operator, outside the nora
container and outside the VM. The audit needs a row for each VM create,
recreate, computerd restart, token rotation and policy-log read; the tools'
own audit covers only their calls. This is the operator-side writer:

- **A host-side spool is the first record.** Each event is one line appended to
  `~/.local/state/nora-computer/lifecycle-events.jsonl` before anything else
  happens, so an event survives Postgres being down at boot (the unit starts
  before the db is healthy more often than not).
- **Postgres is the record of truth.** `sql` turns the spool into INSERTs into
  `computer_events` (actor `operator`, a value `ck_computer_events_actor` already
  allows, so no migration), `ON CONFLICT DO NOTHING` with no conflict target, so
  a row that is already there is skipped whichever unique key it collides on
  (the primary key too: two syncs racing to insert the same event must not make
  one of them fail). The table's append-only triggers are untouched: an INSERT
  that conflicts is skipped before any UPDATE could happen, and nothing here
  ever updates or deletes.
- **A sync sends only what is new.** A state file next to the spool holds the
  byte offset the last successful sync reached; `sql` reads from there, and `mark`
  moves it forward after psql succeeded. A line that is not an event (corrupt, or
  hand-edited) is logged to stderr and skipped, never raised: one bad line must
  not park every later event behind it. A final line with no newline is a write
  still in progress; it is left for the next sync, not read as corrupt.
- **Never the token.** A rotation is recorded as the first 12 hex characters of
  the token's SHA-256, enough to tell two tokens apart and to show a rotation
  happened, not enough to recover one.

stdlib only: it runs under the host's `python3` with no virtualenv.

    lifecycle_event.py record --spool FILE --event NAME [--status ok|failed]
                              [--name VM] [--detail key=value ...]
    lifecycle_event.py sql    --spool FILE [--state FILE] [--all]
    lifecycle_event.py mark   --state FILE --offset N
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
        "'{}'::jsonb, NULL) ON CONFLICT DO NOTHING;"
    )


REQUIRED = ("id", "operation_id", "at", "event", "status", "name", "host", "detail")


def _valid(event: Any) -> str | None:
    """Why `event` cannot be inserted, or None."""
    if not isinstance(event, dict):
        return "not a JSON object"
    missing = [k for k in REQUIRED if k not in event]
    if missing:
        return f"missing {', '.join(missing)}"
    if event["event"] not in EVENTS or event["status"] not in STATUSES:
        return "unknown event or status"
    if not isinstance(event["detail"], dict):
        return "detail is not an object"
    try:
        uuid.UUID(str(event["id"]))
        uuid.UUID(str(event["operation_id"]))
        datetime.fromisoformat(str(event["at"]))
        insert_sql(event)
    except ValueError as exc:
        return str(exc)
    return None


def read_spool(path: Path, start: int = 0) -> tuple[list[dict[str, Any]], int]:
    """The events in the spool from byte `start`, and the offset reached.

    Only COMPLETE lines (ending in a newline) are read: a last line with none is
    a write in progress, and the offset stops before it so the next sync reads it
    whole. A complete line that is not a valid event is logged to stderr and
    skipped, with the offset moved past it, so one bad line never parks every later
    event behind it (psql would stop at it, and a sync that raised on it would
    retry it forever)."""
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    if start > len(data):
        print(f"{path}: shorter than the synced offset {start}; reading it from the start",
              file=sys.stderr)
        start = 0
    events: list[dict[str, Any]] = []
    offset = start
    while True:
        end = data.find(b"\n", offset)
        if end == -1:
            break
        raw = data[offset:end]
        offset = end + 1
        if not raw.strip():
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            print(f"{path}: skipping a line that is not an event ({exc}): {raw[:60]!r}",
                  file=sys.stderr)
            continue
        why = _valid(event)
        if why is not None:
            print(f"{path}: skipping an invalid event ({why})", file=sys.stderr)
            continue
        events.append(event)
    return events, offset


def read_offset(state: Path | None) -> int:
    if state is None or not state.exists():
        return 0
    try:
        return max(0, int(state.read_text().strip()))
    except ValueError:
        return 0


def write_offset(state: Path, offset: int) -> None:
    """Move the synced offset forward, never back (two syncs may finish in either
    order), atomically."""
    if offset <= read_offset(state):
        return
    state.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.with_name(state.name + ".tmp")
    tmp.write_text(f"{offset}\n")
    tmp.replace(state)


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
    sql.add_argument("--state", type=Path, default=None)
    sql.add_argument("--all", action="store_true")
    mark = sub.add_parser("mark")
    mark.add_argument("--state", type=Path, required=True)
    mark.add_argument("--offset", type=int, required=True)
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
    if args.command == "mark":
        write_offset(args.state, args.offset)
        return 0
    events, offset = read_spool(args.spool, 0 if args.all else read_offset(args.state))
    for event in events:
        print(insert_sql(event))
    print(f"-- offset={offset}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
