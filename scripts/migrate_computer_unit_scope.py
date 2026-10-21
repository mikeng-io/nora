"""Host-only policy/session mapping for the offline computer-unit migration.

Run in Nora's environment with DATABASE_URL. The VM receives only mapping.json
(old -> unit hashes), never this host plan with room/session provenance.
Default is dry-run; --apply is required to bind session metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from nora.domain.computer import computer_scope, computer_unit_scope
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COMPUTER_SESSIONS as T
from nora.policy import Policy, load_policy


def mapping_for(policy: Policy, inventory: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for room in policy.conversations:
        if not room.unit:
            continue
        for room_id in room.ids:
            old = computer_scope(room.platform, room_id)
            new = computer_unit_scope(room.unit)
            if old in mapping and mapping[old] != new:
                raise ValueError("ambiguous legacy room mapping")
            mapping[old] = new
    if set(inventory) - mapping.keys() - set(mapping.values()):
        raise ValueError("unmapped workspace; hold untouched for operator mapping")
    return mapping


async def session_plan(conn: Any, mapping: dict[str, str]) -> list[dict[str, str]]:
    rows = (await conn.execute(sa.select(T))).mappings().all()
    result = []
    for row in rows:
        detail = row["stopped_at_detail"]
        old = row["scope"]
        # Already-unit-scoped sessions are never reassigned by current policy.
        if old not in mapping:
            if old in mapping.values() or detail.get("workspace_scope"):
                continue
            raise ValueError("unmapped historical session; operator mapping required")
        if old != computer_scope(row["platform"], row["conversation_id"]):
            raise ValueError("session scope does not match its creation room")
        target = detail.get("workspace_scope", mapping[old])
        if target != mapping[old]:
            raise ValueError("unit reassignment requires an explicit data move")
        result.append({"id": str(row["id"]), "old": old, "new": target, "prefix": f"rooms/{old}/"})
    return result


async def bind_sessions(conn: Any, document: dict, receipt: dict, *, apply: bool) -> int:
    mapping = document["mapping"]
    if receipt.get("verified") is not True or receipt.get("mapping") != document["moves"]:
        raise ValueError("all workspace moves must be verified before binding sessions")
    expected = document["sessions"]
    current = await session_plan(conn, mapping)
    if sorted(current, key=lambda s: s["id"]) != sorted(expected, key=lambda s: s["id"]):
        raise ValueError("session inventory changed; keep admission paused")
    count = 0
    for session in expected:
        from uuid import UUID

        row = (
            (
                await conn.execute(
                    sa.select(T).where(T.c.id == UUID(session["id"])).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        detail = dict(row["stopped_at_detail"])
        detail.update(workspace_scope=session["new"], resume_prefix=session["prefix"])
        if detail == row["stopped_at_detail"]:
            continue
        if apply:
            await conn.execute(
                sa.update(T).where(T.c.id == row["id"]).values(stopped_at_detail=detail)
            )
        count += 1
    return count


async def run(args: argparse.Namespace) -> None:
    engine = create_sql_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            if args.action == "plan":
                mapping = mapping_for(
                    load_policy(args.policy), json.loads(args.inventory.read_text())
                )
                document = {
                    "mapping": mapping,
                    "moves": {
                        s: mapping[s]
                        for s in json.loads(args.inventory.read_text())
                        if s in mapping
                    },
                    "sessions": await session_plan(conn, mapping),
                }
                args.output.write_text(json.dumps(document, sort_keys=True))
                args.output.chmod(0o600)
                args.vm_mapping.write_text(json.dumps(document["moves"], sort_keys=True))
                args.vm_mapping.chmod(0o600)
                print(
                    f"planned {len(document['moves'])} workspaces, "
                    f"{len(document['sessions'])} sessions"
                )
            else:
                document = json.loads(args.input.read_text())
                count = await bind_sessions(
                    conn, document, json.loads(args.receipt.read_text()), apply=args.apply
                )
                print(f"{'bound' if args.apply else 'would bind'} {count} sessions")
    finally:
        await engine.dispose()


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "bind"])
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--vm-mapping", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
