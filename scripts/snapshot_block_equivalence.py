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
3. Record the external services the subject and render touch into a
   recorded-services document (`nora.subject.recorded_services`), one JSON
   object with `bindings` and `services` — every `base_url` the recorded
   config resolves bound to a finite sequence of responses. Pass it as
   `--services`.
4. Run, with the flags the live `config.toml` had for the three tiers a tag
   reads from the stores (`notes`, `standing`, `culture` — the recorded
   `config.toml` under `--workdir` says what they were):

       uv run python scripts/snapshot_block_equivalence.py \
         --source "$EVAL_SOURCE_URL" --at YYYY-MM-DDTHH:MM:SS+08:00 \\
         --message-id 123456 --prefill /tmp/turn-prefill.txt \\
         --services /tmp/turn-services.json \\
         --workdir /tmp/snap-turn --set notes.enabled=true \\
         --set standing.enabled=true --set culture.enabled=true

   `--at` is the turn's SYSTEM time (when it ran), which is what the
   snapshot is taken at; the subject's clock is the tagging row's own `ts`.

The subject composes from the config, policy and identity recorded at `--at`
— a load that is absent is a named error, never a template fallback — inside
a case-owned environment (`nora.subject.environment`) that refuses any
endpoint the recording does not own. The render runs under the eval
evidence lock with canonical capture, so an unsettled or truncated path is
an evidence error, not a silent pass.

It prints the blocks compared, excepted (memory: `peer_card`,
`peer_history`; Live: `tool_catalogue`, `presence`) and any that differ, and
exits 0 only when every compared block is byte-identical and at least one
was compared. A doctrine, Dreamer or deploy load not recorded by `--at`
is named in the output; a missing `config.toml`, `policy.toml` or
`identity.toml` load exits 2 because the subject cannot be bound to what
was actually in force.
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

from evals.backbone import _close, _prefill_capture
from nora.domain.models import NormalizedMessage
from nora.infra.sql import create_readonly_sql_engine
from nora.infra.sql.snapshot_at import materialise
from nora.subject import SubjectSpec, obtain, sandbox_as_of
from nora.subject.as_of import blocks_of, compare_blocks, render_tag_blocks

REPO = Path(__file__).resolve().parents[1]

#: Config files the subject is composed FROM. An absent recorded load is an
#: error: composing `default_policy` or the example identity against a real
#: room silently measures a subject the deployment never was.
REQUIRED_LOADS: tuple[str, ...] = ("config.toml", "policy.toml", "identity.toml")


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
    missing = set(forced.missing())
    required_absent = sorted(set(REQUIRED_LOADS) & missing)
    if required_absent:
        print("required load(s) not recorded by --at: "
              + ", ".join(required_absent), file=sys.stderr)
        return 2
    optional_missing = sorted(missing - set(REQUIRED_LOADS))
    if optional_missing:
        print("no load recorded by --at for: " + ", ".join(optional_missing)
              + " (composed from the template instead)")
    in_force = workdir / "in_force"
    subject_workdir = workdir / "subject"
    overrides: dict[str, object] = {
        # identity/policy CONTENT come from the recorded files via
        # SubjectSpec; the config's path fields still point where the
        # deployment pointed, so they are bound to the subject's own copies.
        "identity_file": subject_workdir / "identity.toml",
        "policy_file": subject_workdir / "policy.toml",
    }
    if forced.doctrine is not None:
        overrides["doctrine.dir"] = in_force / "doctrine"
    if forced.dreamer is not None:
        overrides["dreamer.dir"] = in_force / "dreamer"
    for item in args.set:
        key, _, raw = item.partition("=")
        overrides[key] = _value(raw)

    from opentelemetry import trace

    from nora.subject.eval_evidence import RunEvidence, fingerprint, revision
    from nora.subject.recorded_services import RecordedServices
    from nora.subject.sandbox import eval_host_lock

    recording = RecordedServices.load(args.services)
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(
            run_id="snapshot-block-equivalence",
            metadata={"message_id": args.message_id, "at": at.isoformat(),
                      "config_sha256": fingerprint(in_force / "config.toml"),
                      **revision(REPO)},
            lock_fd=lock_fd)
        _prefill_capture()
        with evidence.capture(trace.get_tracer_provider()):
            async with sandbox_as_of(args.source, at) as (url, report):
                print(f"restored {sum(report.copied.values())} rows "
                      f"as of {at.isoformat()}")
                from nora.infra.sql import create_sql_engine
                eng = create_sql_engine(url)
                try:
                    async with eng.connect() as conn:
                        row = (await conn.execute(sa.text(
                            "SELECT platform, conversation_id, conversation_type, "
                            "sender_id, sender_name, ts, body FROM messages "
                            "WHERE id = :i"
                        ), {"i": args.message_id})).one_or_none()
                finally:
                    await eng.dispose()
                if row is None:
                    print(f"message {args.message_id} is not held at --at",
                          file=sys.stderr)
                    return 2
                async with recording.open(url, redis=True) as environment:
                    spec = SubjectSpec(
                        database_url=url, workdir=subject_workdir,
                        room=(row.platform, row.conversation_id),
                        now=datetime.fromtimestamp(row.ts / 1000, tz=UTC),
                        config=in_force / "config.toml",
                        overrides=overrides,
                        policy=(in_force / "policy.toml").read_text(
                            encoding="utf-8"),
                        identity=(in_force / "identity.toml").read_text(
                            encoding="utf-8"),
                        environment=environment,
                    )
                    subject = await obtain(spec)
                    try:
                        restored = await render_tag_blocks(
                            subject, message=NormalizedMessage(
                                platform=row.platform,
                                conversation_id=row.conversation_id,
                                conversation_type=row.conversation_type,
                                sender_id=row.sender_id,
                                sender_name=row.sender_name,
                                timestamp=row.ts, body=row.body or "", raw={},
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
    parser.add_argument("--services", required=True,
                        help="recorded-services JSON the subject and render use")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args()
    args.prefill_text = Path(args.prefill).read_text(encoding="utf-8")
    try:
        return asyncio.run(_main(args))
    except Exception:  # noqa: BLE001 -- execution/evidence errors are exit 2
        print("snapshot block equivalence unavailable; check inputs and "
              "evidence privately", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
