"""The block gate, live: a real tag turn's recorded blocks against the
same turn rendered from `nora snapshot` at its moment.

The offline form is
`tests/integration/test_snapshot_renders_the_recorded_blocks.py`; this runs
the same comparison (`nora.subject.as_of`) against a trace from production.
Run it ON the deploy host, from a worktree at the source's migration head,
against the eval clone (never production itself), after the clone was taken
AFTER the turn:

1. Pick a tag turn in Langfuse (nora-readback has the authenticated read)
   and note its trace's start time and its tag message. From its agent
   GENERATION's input, save the SECOND user message's text (the clock line,
   then the blocks) to a file, e.g. `/tmp/turn-prefill.txt`. The file holds
   room speech: keep it in /tmp, never in the repo or an issue.
2. Find the tagging row's `messages.id` in the clone (the row whose `ts` and
   body match the tag message).
3. Run, with the flags the live `config.toml` had for the three tiers a tag
   reads from the stores (`notes`, `standing`, `culture` — the materialised
   `config.toml` under `--workdir` says what they were if a load was
   recorded by then):

       uv run python scripts/snapshot_block_equivalence.py \\
         --source "$EVAL_SOURCE_URL" --at YYYY-MM-DDTHH:MM:SS+08:00 \\
         --message-id 123456 --prefill /tmp/turn-prefill.txt \\
         --workdir /tmp/snap-turn --set notes.enabled=true \\
         --set standing.enabled=true --set culture.enabled=true

   `--at` is the turn's SYSTEM time (when it ran), which is what the
   snapshot is taken at; the subject's clock is the tagging row's own `ts`.

It prints the blocks compared, excepted (memory: `peer_card`,
`peer_history`; Live: `tool_catalogue`, `presence`) and any that differ, and
exits 0 only when every compared block is byte-identical and at least one
was compared. A doctrine, Dreamer or config load not recorded by `--at`
is composed from the template, and the script says
so; that affects no block this gate compares.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.backbone import _close
from nora.domain.models import NormalizedMessage
from nora.infra.sql import create_readonly_sql_engine
from nora.infra.sql.snapshot_at import materialise
from nora.subject import SubjectSpec, obtain, sandbox_as_of
from nora.subject.as_of import blocks_of, compare_blocks, render_tag_blocks


def _value(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return text


async def _main(args: argparse.Namespace) -> int:
    at = datetime.fromisoformat(args.at)
    if at.tzinfo is None:
        print("--at needs an offset", file=sys.stderr)
        return 2
    recorded = blocks_of(args.prefill_text)
    workdir = Path(args.workdir)
    source = create_readonly_sql_engine(args.source)
    try:
        forced, _ = await materialise(source, at, workdir / "in_force")
    finally:
        await source.dispose()
    if forced.missing():
        print("no load recorded by --at for: " + ", ".join(forced.missing())
              + " (composed from the template instead)")
    overrides: dict[str, object] = {}
    if forced.doctrine is not None:
        overrides["doctrine.dir"] = str(workdir / "in_force" / "doctrine")
    if forced.dreamer is not None:
        overrides["dreamer.dir"] = str(workdir / "in_force" / "dreamer")
    for item in args.set:
        key, _, raw = item.partition("=")
        overrides[key] = _value(raw)

    async with sandbox_as_of(args.source, at) as (url, report):
        print(f"restored {sum(report.copied.values())} rows as of {at.isoformat()}")
        from nora.infra.sql import create_sql_engine
        eng = create_sql_engine(url)
        try:
            async with eng.connect() as conn:
                row = (await conn.execute(sa.text(
                    "SELECT platform, conversation_id, conversation_type, sender_id, "
                    "sender_name, ts, body FROM messages WHERE id = :i"
                ), {"i": args.message_id})).one_or_none()
        finally:
            await eng.dispose()
        if row is None:
            print(f"message {args.message_id} is not held at --at", file=sys.stderr)
            return 2
        policy = workdir / "in_force" / "policy.toml"
        identity = workdir / "in_force" / "identity.toml"
        spec = SubjectSpec(
            database_url=url, workdir=workdir / "subject",
            room=(row.platform, row.conversation_id),
            now=datetime.fromtimestamp(row.ts / 1000, tz=UTC), overrides=overrides,
            **({"policy": policy.read_text(encoding="utf-8")} if policy.exists() else {}),
            **({"identity": identity.read_text(encoding="utf-8")} if identity.exists() else {}),
        )
        subject = await obtain(spec)
        try:
            restored = await render_tag_blocks(subject, message=NormalizedMessage(
                platform=row.platform, conversation_id=row.conversation_id,
                conversation_type=row.conversation_type, sender_id=row.sender_id,
                sender_name=row.sender_name, timestamp=row.ts, body=row.body or "", raw={},
            ), now=spec.now or at)
        finally:
            await _close(subject)
    diff = compare_blocks(recorded, restored)
    print(f"compared: {', '.join(diff.compared) or '(none)'}")
    print(f"excepted: {', '.join(diff.excepted) or '(none)'}")
    for label, names in (("DIFFER", diff.differs), ("MISSING from the restore", diff.missing),
                         ("EXTRA in the restore", diff.extra)):
        if names:
            print(f"{label}: {', '.join(names)}")
    print("EQUAL" if diff.equal else "NOT EQUAL")
    return 0 if diff.equal else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--at", required=True)
    parser.add_argument("--message-id", type=int, required=True)
    parser.add_argument("--prefill", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args()
    args.prefill_text = Path(args.prefill).read_text(encoding="utf-8")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
