"""The operator control plane: the read-only commands (`nora groups`,
`nora persons pending`, `nora presence members`, `nora reminders list`) and
the config write surface (`nora config set/get/unset/validate` — see
`nora.app.config_cli`). Most of this module's commands only READ the record
store; each one that writes says so where it is registered. Among them:
`xai-login` and `openai-codex-login` (the host `.env`), `memory backfill`,
`memory carry-conclusions` and `memory detach-unit`, each only with `--apply`
(Honcho, DEC-214), `memory rebuild` (the memory index, unless `--dry-run`),
`lessons retire` and `lessons rollback` (one lesson revision, only with
`--apply`), `snapshot --into` (a separate empty sandbox database),
`events backfill-units` (the `events` table) and `assess run-nightly` (an
`assess_report` row).

The read commands answer «which group does this ID belong to» without
amending D6: they only READ. Adoption stays a hand-edit of `identity.toml`
followed by a restart, at which point `nora.app.adoption` backfills the history
(DEC-16).

Three properties are load-bearing, and each is enforced by something rather
than promised:

* **Nothing is written.** The record store is opened through
  `create_readonly_sql_engine` — every connection sets
  `default_transaction_read_only=on`, so any write raises
  `ReadOnlySqlTransaction` at the server — and the only daemon
  calls go through `nora.platform.signal.directory`, whose method allowlist
  holds exactly `listGroups` and `listContacts`. In the deployment this engine
  additionally connects as a `GRANT SELECT`-only role whose write privileges do
  not exist at all; that role is defence in depth on top of the session setting.
* **It runs alongside `nora-db`, not the Nora agent.** Nothing here builds an
  adapter, binds the metrics port, opens the SSE stream, runs a migration or
  constructs an LLM client. `config.load(..., require_secrets=False)` keeps a
  missing `OMNIROUTE_API_KEY` — injected into the CONTAINER, not the operator's
  shell — from failing a command that never talks to the provider. It does need
  the `nora` network to reach `nora-db`, so after the migration the operator
  runs it via `docker exec` into a `nora`-network container as the read-only
  role; the URL comes from `DATABASE_URL` there, same as Nora.
* **A broken daemon degrades to a sentence, never a traceback.** `nora groups`
  cannot do its job without signal-cli and says so with an exit code;
  `nora persons pending` can, losing only the display names, and says that too.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from nora.agent.egress import scan_identifiers
from nora.app import config_cli, lessons_cli, report
from nora.app.assess_run import AssessDisabledError
from nora.app.assess_run import render as assess_render
from nora.app.assess_run import run_nightly as assess_run_nightly
from nora.app.events_backfill import backfill_unit_ids
from nora.app.events_backfill import render as render_events_backfill
from nora.app.memory_backfill import BackfillRefusedError, backfill_unit
from nora.app.memory_backfill import render as render_backfill
from nora.app.memory_carry import CarryRefusedError, carry_conclusions
from nora.app.memory_carry import render as render_carry
from nora.app.memory_detach import (
    DetachRefusedError,
    detach_unit,
    refresh_cards,
    render_refresh,
)
from nora.app.memory_detach import render as render_detach
from nora.app.memory_routing import MemoryRoutingError, build_workspace_router
from nora.app.participation_measures import gather as gather_measures
from nora.app.participation_measures import measure as compute_measures
from nora.app.pending import correlate
from nora.app.place_backfill import backfill_places
from nora.app.place_backfill import render as render_place_backfill
from nora.config import Config, ConfigError, load
from nora.domain.lessons import Lesson
from nora.domain.media_join import MediaJoin
from nora.domain.models import UnattributedRow
from nora.domain.participation_measures import Refusal
from nora.infra.embedder import fingerprint
from nora.infra.honcho import HonchoClient, WorkspaceRouter
from nora.infra.life360 import Life360Client, Life360DiscoveryError
from nora.infra.sql import (
    SqlRecordStore,
    create_readonly_sql_engine,
    create_sql_engine,
    memory_index,
    snapshot_at,
)
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.tension import SqlTensionStore
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError, load_persons
from nora.platform.signal import directory
from nora.policy import Policy, PolicyError, load_policy

# Deliberately larger than `nora.app.adoption.DEFAULT_SCAN_LIMIT`: adoption is a
# recurring chore competing for SQLite's single writer, while this is one
# operator-initiated read that should cover the whole retained backlog.
DEFAULT_SCAN_LIMIT = 5000

# Smaller than `DEFAULT_SCAN_LIMIT` by two orders of magnitude, and deliberately:
# that one scans an audit backlog for correlation, while this prints one block
# per row to a terminal. `max_pending_per_requester` is 20 (design spec §9), so
# 500 is a whole household's worth of backlog several times over — a listing
# longer than this is the runaway itself, and the truncation notice says so.
DEFAULT_REMINDER_LIMIT = 500

#: How far back `nora measure participation` reads by default. Seven days
#: because that is what Prometheus retains, and the refusal-side measures
#: cannot be joined past the shorter of the two horizons — a default that
#: outran the weaker instrument would silently report the note-side measures
#: over one window and the refusal-side ones over another.
DEFAULT_MEASURE_DAYS = 7


class CliError(Exception):
    """An operator-diagnosable failure. `main` prints it and exits 1 — this
    module never lets a stack trace reach the terminal, because a traceback at
    3am is a second problem, not a diagnosis."""


def add_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    parent: argparse.ArgumentParser,
) -> None:
    """Register `groups` and `persons` on an existing parser.

    `nora/__main__.py` owns the top-level parser (and its `--config`, whose
    behaviour is unchanged); this only adds to it, so the daemon invocation
    `python -m nora --config config.toml` keeps working with no subcommand at
    all.
    """
    groups = subparsers.add_parser(
        "groups", parents=[parent],
        help="every group signal-cli knows, with its id and policy verdict",
    )
    groups.set_defaults(func=cmd_groups)

    persons = subparsers.add_parser(
        "persons", parents=[parent], help="identity mapping helpers",
    )
    persons_sub = persons.add_subparsers(dest="persons_command", required=True)
    pending = persons_sub.add_parser(
        "pending", parents=[parent],
        help="senders in the audit DB that map to nobody, with a paste-ready stanza",
    )
    pending.add_argument(
        "--limit", type=int, default=DEFAULT_SCAN_LIMIT,
        help=f"unattributed audit rows to scan (default {DEFAULT_SCAN_LIMIT})",
    )
    pending.set_defaults(func=cmd_pending)

    reminders = subparsers.add_parser(
        "reminders", parents=[parent], help="reminders the heartbeat is still holding",
    )
    reminders_sub = reminders.add_subparsers(dest="reminders_command", required=True)
    # `list` and NOTHING ELSE. A `cancel` subcommand would break both of this
    # module's guarantees at once: the engine is opened through
    # `create_readonly_sql_engine` (`default_transaction_read_only=on`) and, in
    # the deployment, as a `GRANT SELECT`-only role — so the write would not
    # merely be discouraged, it would raise at the server. The operator's kill
    # switch is `[heartbeat] enabled = false` plus a restart, which stops all
    # firing rather than one row; cancelling ONE reminder is the model's job
    # (`cancel_reminder`), in the conversation it was set in.
    reminders_list = reminders_sub.add_parser(
        "list", parents=[parent],
        help="every reminder still pending, soonest first, with what is overdue",
    )
    reminders_list.add_argument(
        "--limit", type=int, default=DEFAULT_REMINDER_LIMIT,
        help=f"pending rows to list (default {DEFAULT_REMINDER_LIMIT})",
    )
    reminders_list.set_defaults(func=cmd_reminders_list)

    # `measure` — the four measures that grade the ambient path against
    # something other than its own opinion. READ ONLY, and
    # it changes no behaviour: it computes over rows that already exist.
    #
    # `--refusals` is a FILE and not a Prometheus URL on purpose. Gate
    # refusals come from a counter that resets with the process, and keeping
    # the fetch outside this command keeps the weak instrument visibly
    # separate from the durable rows — `scripts/gate_refusals.py` produces the
    # file, and the day a durable per-verdict row exists only that script is
    # replaced. Without the flag the two measures that need refusals report
    # `considered = 0`, which reads as «did not run» and not as «never
    # happens».
    measure = subparsers.add_parser(
        "measure", parents=[parent],
        help="measures the rooms produce for free — no model, no behaviour change",
    )
    measure_sub = measure.add_subparsers(dest="measure_command", required=True)
    measure_participation = measure_sub.add_parser(
        "participation", parents=[parent],
        help="expiry-without-action, HOLDING refusal, override, ignored",
    )
    measure_participation.add_argument(
        "--days", type=int, default=DEFAULT_MEASURE_DAYS,
        help=f"how far back to read rows (default {DEFAULT_MEASURE_DAYS})",
    )
    measure_participation.add_argument(
        "--refusals", type=Path, default=None,
        help="JSON from scripts/gate_refusals.py; without it the two "
             "refusal-side measures report nothing considered",
    )
    measure_participation.set_defaults(func=cmd_measure_participation)

    # `snapshot` — Nora as of T. The SOURCE is only ever read, through the read-only engine
    # every other command here uses; the one write is into `--into`, a
    # separate empty sandbox database, and without `--into` (or with
    # `--dry-run`) nothing is written anywhere.
    snap = subparsers.add_parser(
        "snapshot", parents=[parent],
        help="every store as of T, plus the doctrine/config/deploy in force, "
             "into an empty sandbox database (dry run without --into)",
    )
    snap.add_argument(
        "--at", required=True,
        help="the instant, ISO-8601 with an offset (2026-09-27T14:00:00+08:00) "
             "or epoch milliseconds",
    )
    snap.add_argument(
        "--source", default=None,
        help="database to read (default: the URL in the config's database env var); "
             "on the deploy host this is the eval clone, nora_eval",
    )
    snap.add_argument(
        "--into", default=None,
        help="an EMPTY migrated database to seed (e.g. `createdb -T nora_eval_blank X`); "
             "omitted, the command is a dry run",
    )
    snap.add_argument(
        "--files-out", type=Path, default=None,
        help="also write the doctrine, Dreamer corpus and config files in force at T here",
    )
    snap.add_argument(
        "--dry-run", action="store_true",
        help="report what would be copied and what was in force; write nothing",
    )
    snap.set_defaults(func=cmd_snapshot)

    # `lessons`: the operator's list, show,
    # retire and rollback of Nora's lessons. `list` and `show` read through the
    # read-only engine; `retire` and `rollback` WRITE one revision each, only with
    # `--apply` (`nora.app.lessons_cli`). No model can call these.
    lessons = subparsers.add_parser(
        "lessons", parents=[parent], help="Nora's lessons per unit (operator only)",
    )
    lessons_sub = lessons.add_subparsers(dest="lessons_command", required=True)
    lessons_list = lessons_sub.add_parser(
        "list", parents=[parent], help="a unit's lessons, active ones unless --all",
    )
    lessons_list.add_argument("--unit", required=True, help="the unit")
    lessons_list.add_argument(
        "--all", action="store_true", help="include retired lessons",
    )
    lessons_list.set_defaults(func=cmd_lessons_list)
    lessons_show = lessons_sub.add_parser(
        "show", parents=[parent], help="one lesson, every revision, newest first",
    )
    lessons_show.add_argument("lesson_id")
    lessons_show.set_defaults(func=cmd_lessons_show)
    lessons_retire = lessons_sub.add_parser(
        "retire", parents=[parent],
        help="retire a lesson as the operator; it is not learned again from "
             "outcomes no newer than now (a dry run unless --apply)",
    )
    lessons_retire.add_argument("lesson_id")
    lessons_retire.add_argument("--why", required=True, help="one sentence, kept with it")
    lessons_retire.add_argument("--apply", action="store_true", help="write")
    lessons_retire.set_defaults(func=cmd_lessons_retire)
    lessons_rollback = lessons_sub.add_parser(
        "rollback", parents=[parent],
        help="put an earlier revision back as the newest one (also undoes a "
             "retirement; a dry run unless --apply)",
    )
    lessons_rollback.add_argument("lesson_id")
    lessons_rollback.add_argument("--to", required=True, type=int, help="the revision")
    lessons_rollback.add_argument("--apply", action="store_true", help="write")
    lessons_rollback.set_defaults(func=cmd_lessons_rollback)
    # The lessons the doctrine check refused, with the law each pulled
    # against. Operator-only by design.
    lessons_tensions = lessons_sub.add_parser(
        "tensions", parents=[parent],
        help="lessons refused for loosening a law rule, newest first",
    )
    lessons_tensions.add_argument("--unit", help="one unit (default: all)")
    lessons_tensions.add_argument("--limit", type=int, default=20)
    lessons_tensions.set_defaults(func=cmd_lessons_tensions)

    presence = subparsers.add_parser(
        "presence", parents=[parent], help="Life360 presence helpers",
    )
    presence_sub = presence.add_subparsers(dest="presence_command", required=True)
    members = presence_sub.add_parser(
        "members", parents=[parent],
        help="list your Life360 circles + members (id + name) to paste into identity.toml",
    )
    members.set_defaults(func=cmd_presence_members)

    # `memory backfill` (DEC-214): a command here that WRITES, and
    # like `xai-login` it writes nowhere this module's read-only guarantee
    # covers — the record store is still opened read-only; what it writes
    # is Honcho, and only with `--apply`. See `nora.app.memory_backfill`.
    memory = subparsers.add_parser(
        "memory", parents=[parent],
        help="Honcho memory and memory index maintenance (operator only)",
    )
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    history = memory_sub.add_parser(
        "persona-history", parents=[parent],
        help="historical Persona coverage receipts; optionally restart one account's pass",
    )
    history.add_argument("--key", help="one scoped Persona account key")
    history.add_argument("--restart", action="store_true")
    history.add_argument("--apply", action="store_true")
    history.set_defaults(func=cmd_persona_history)
    backfill = memory_sub.add_parser(
        "backfill", parents=[parent],
        help="replay one unit's recorded human messages into its own Honcho "
             "workspace (a dry run unless --apply)",
    )
    backfill.add_argument("--unit", required=True, help="the unit to replay")
    backfill.add_argument(
        "--apply", action="store_true", help="write; without it nothing is fed",
    )
    backfill.add_argument(
        "--resume", action="store_true",
        help="allow sessions that already hold messages, skipping what they hold",
    )
    backfill.set_defaults(func=cmd_memory_backfill)
    carry = memory_sub.add_parser(
        "carry-conclusions", parents=[parent],
        help="copy the facts Nora was told before unit scopes into every unit "
             "scope of one workspace (a dry run unless --apply)",
    )
    carry.add_argument(
        "--workspace", required=True,
        help="the NORA workspace (policy.toml [units]); `default` without [units]",
    )
    carry.add_argument(
        "--before", required=True,
        help="the cutover instant, ISO 8601 with a timezone (e.g. "
             "2026-09-29T10:00:00+08:00): only conclusions created before it are copied",
    )
    carry.add_argument("--apply", action="store_true", help="write; without it nothing is copied")
    carry.set_defaults(func=cmd_memory_carry)
    detach = memory_sub.add_parser(
        "detach-unit", parents=[parent],
        help="delete a unit's rooms from the Honcho workspace it left, after its "
             "backfill landed (a dry run unless --apply; irreversible)",
    )
    detach.add_argument("--unit", help="the unit that moved (step 1)")
    detach.add_argument(
        "--from-honcho-workspace", help="the HONCHO workspace id it left, e.g. nora (step 1)",
    )
    detach.add_argument(
        "--peers-file", required=True, type=Path,
        help="where step 1 records the deleted sessions and their peers, and step 2 "
             "reads them — keep it under the ignored state/ directory",
    )
    detach.add_argument("--apply", action="store_true", help="step 1: delete (irreversible)")
    detach.add_argument(
        "--refresh-cards", action="store_true",
        help="step 2: once Honcho has finished deleting, rebuild the recorded peers' cards",
    )
    detach.set_defaults(func=cmd_memory_detach)
    # `memory rebuild`: WRITES
    # the memory index through a writable engine, like `events
    # backfill-units`; the stores it reads are never written.
    rebuild = memory_sub.add_parser(
        "rebuild", parents=[parent],
        help="re-project every store into the memory index (also its first "
             "backfill); idempotent, refused while another rebuild runs",
    )
    rebuild.add_argument(
        "--dry-run", action="store_true",
        help="count what would be projected and dropped; write nothing",
    )
    # Vectors only, the projection untouched;
    # the embedding worker backfills what this clears.
    rebuild.add_argument(
        "--embeddings-only", action="store_true",
        help="clear the stored vectors (not the projection) for the embedding "
             "worker to backfill; with --dry-run, count them",
    )
    rebuild.add_argument(
        "--other-models", action="store_true",
        help="with --embeddings-only: clear only vectors made by another model "
             "than [memory_index] embedder_model",
    )
    rebuild.set_defaults(func=cmd_memory_rebuild)
    # `memory backfill-places`: WRITES the events (through
    # `GatheringStore.update`) and the geocode cache, and asks the geocoder,
    # only with --apply. See `nora.app.place_backfill`.
    places = memory_sub.add_parser(
        "backfill-places", parents=[parent],
        help="give event venues and cached geocodes resolved before 0118 their "
             "district and city (a dry run unless --apply); idempotent",
    )
    places.add_argument(
        "--apply", action="store_true",
        help="ask the geocoder and write; without it nothing is asked or changed",
    )
    places.add_argument(
        "--per-second", type=float, default=2.0,
        help="at most this many lookups a second (each is one call per language)",
    )
    places.add_argument(
        "--limit", type=int, default=10_000, help="at most this many rows per list",
    )
    places.set_defaults(func=cmd_memory_backfill_places)

    # `events backfill-units` (DEC-237): the THIRD command here that WRITES — unlike `memory
    # backfill`'s Honcho-only write, this one writes the `events` table
    # itself (through `GatheringStore.update`, the same path `update_event`
    # writes through), so it needs a WRITABLE engine, not the read-only one
    # every other command in this module opens. A dry run by default; see
    # `nora.app.events_backfill`.
    events = subparsers.add_parser(
        "events", parents=[parent], help="Gathering/event maintenance (operator only)",
    )
    events_sub = events.add_subparsers(dest="events_command", required=True)
    backfill_units = events_sub.add_parser(
        "backfill-units", parents=[parent],
        help="set unit_id on every pre-migration event (unit_id='') from its "
             "conversation's policy.toml unit (a dry run unless --apply)",
    )
    backfill_units.add_argument(
        "--apply", action="store_true", help="write; without it nothing is changed",
    )
    backfill_units.set_defaults(func=cmd_events_backfill_units)

    # `assess run-nightly` (DEC-240): the Assess adaptor's deterministic
    # data-quality checks. WRITABLE engine like `events backfill-units`,
    # because `--record` (the default) writes one `assess_report` row; the
    # checks themselves never write a tier (`nora.app.assess_run`'s own
    # docstring). Refused when `[assess] mode` is `"off"`.
    assess = subparsers.add_parser(
        "assess", parents=[parent], help="the Assess adaptor's reports (operator only)",
    )
    assess_sub = assess.add_subparsers(dest="assess_command", required=True)
    run_nightly = assess_sub.add_parser(
        "run-nightly", parents=[parent],
        help="run every nightly data-quality check and print the report "
             "(written to assess_report unless --no-record)",
    )
    run_nightly.add_argument(
        "--no-record", action="store_true",
        help="print the report without writing it",
    )
    run_nightly.set_defaults(func=cmd_assess_run_nightly)

    # `config` — the operator's safe config mutation surface (deploy
    # convention §4.5). See config_cli.py for the atomic-write and
    # double-validation guarantees.
    config_cli.add_config_subcommands(subparsers, parent)

    # `xai-login` (DEC-137): the ONE command that WRITES — it walks Miles
    # through the xAI OAuth device flow and appends the refresh token to the
    # host `.env` (the same "edit .env, recreate" loop every other Nora
    # credential uses). Deliberately outside the read-only engine guarantees
    # this module otherwise holds: it never touches the DB, it talks to
    # `auth.x.ai`, and it fails cleanly (CliError) when the flow is refused
    # or the token was not issued.
    xai_login = subparsers.add_parser(
        "xai-login", parents=[parent],
        help="xAI OAuth device flow — get XAI_OAUTH_REFRESH_TOKEN for image generation",
    )
    xai_login.add_argument(
        "--env-file", default=".env",
        help="host .env file to append the refresh token to (default .env)",
    )
    xai_login.set_defaults(func=cmd_xai_login)

    # `openai-codex-login` (DEC-304): the same shape for the OpenAI Codex
    # lane — a device login against auth.openai.com (or, with
    # `--from-codex-auth`, a DEDICATED `codex login`'s auth.json), and the
    # refresh token appended to the host `.env` as the lane's BOOTSTRAP.
    codex_login = subparsers.add_parser(
        "openai-codex-login", parents=[parent],
        help="OpenAI device login — get OPENAI_CODEX_REFRESH_TOKEN for image generation",
    )
    codex_login.add_argument(
        "--env-file", default=".env",
        help="host .env file to append the refresh token to (default .env)",
    )
    codex_login.add_argument(
        "--from-codex-auth", default="",
        help="import from a dedicated `CODEX_HOME=<dir> codex login`'s auth.json "
             "instead of running the device flow (never ~/.codex/auth.json)",
    )
    codex_login.set_defaults(func=cmd_openai_codex_login)


def run(args: argparse.Namespace) -> int:
    """Execute the selected subcommand. Returns a process exit code."""
    return asyncio.run(execute(args))


async def execute(args: argparse.Namespace) -> int:
    """`run` without the loop — this is where a `CliError` becomes a message
    and an exit code, so the split is not cosmetic: `asyncio.run` refuses to
    nest, and a test that has to reimplement this `except` clause to reach the
    behaviour is a test of the copy, not of the code.

    `args.func` is set by `set_defaults` and so is untyped as far as mypy is
    concerned; `int(...)` keeps this function's own contract honest rather
    than a `# type: ignore`.
    """
    try:
        return int(await args.func(args))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _config(args: argparse.Namespace) -> Config:
    try:
        return load(Path(args.config), require_secrets=False)
    except ConfigError as exc:
        raise CliError(str(exc)) from exc


def _policy(cfg: Config) -> Policy:
    try:
        return load_policy(cfg.policy_file)
    except PolicyError as exc:
        raise CliError(f"policy error: {exc}") from exc


def _persons(cfg: Config) -> Persons:
    try:
        return load_persons(cfg.identity_file)
    except PersonsError as exc:
        raise CliError(f"person mapping error: {exc}") from exc


def _rewrite_env(env_path: Path, marker: str, refresh_token: str) -> None:
    """Idempotently replace-or-append one `KEY=value` line in `.env`.
    Sync by design — runs under `asyncio.to_thread` from the CLI (never
    blocks the event loop). The write is ATOMIC — tmp +
    os.replace — so a crash/interrupt mid-write can never corrupt the whole
    `.env` (which holds Postgres, Discord, xAI, Life360 secrets). The
    original file mode is preserved explicitly because os.replace installs
    the tmp file's inode."""
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    kept: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(marker):
            kept.append(f"{marker}{refresh_token}")
            replaced = True
        else:
            kept.append(line)
    if not replaced:
        kept.append(f"{marker}{refresh_token}")
    mode = 0o600
    with contextlib.suppress(OSError):
        mode = env_path.stat().st_mode & 0o777
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, env_path)


async def cmd_xai_login(args: argparse.Namespace) -> int:
    """Run the xAI OAuth device flow and append the refresh token to `.env`.

    The ONE command in this module that writes — but it never touches the
    DB: it talks to `auth.x.ai`, then edits the HOST `.env` the operator
    already owns (the same file every other Nora credential lives in,
    DEC-8). Idempotent: an existing `XAI_OAUTH_REFRESH_TOKEN` line is
    replaced, not duplicated. The access token stays out of `.env`
    entirely — that is the state file's job.
    """
    from nora.infra.image_gen.xai_oauth import (
        XaiOAuthFlowError,
        device_flow_login,
    )
    try:
        refresh_token = await device_flow_login()
    except XaiOAuthFlowError as exc:
        raise CliError(str(exc)) from exc

    env_path = Path(args.env_file)
    marker = "XAI_OAUTH_REFRESH_TOKEN="
    await asyncio.to_thread(_rewrite_env, env_path, marker, refresh_token)

    print(f"ok — refresh token written to {env_path}")
    print("recreate the container (`docker compose up -d --force-recreate nora`) "
          "for it to pick up the new env; access tokens refresh automatically.")
    return 0


async def cmd_openai_codex_login(args: argparse.Namespace) -> int:
    """Log Nora's OWN ChatGPT account in and write the bootstrap refresh
    token to `.env` (DEC-304). Like `xai-login` it touches no database.

    The token is a BOOTSTRAP: OpenAI rotates refresh tokens on use, so the
    first refresh spends it and the lane keeps the rotated one in
    `./state/openai-codex-oauth.json`. A new login here replaces that chain
    on the next start (the state file is fingerprinted to the `.env` token).
    """
    from nora.infra.image_gen.openai_codex_oauth import (
        CodexLoginError,
        device_flow_login,
        refresh_token_from_codex_auth,
    )
    try:
        if args.from_codex_auth:
            refresh_token = refresh_token_from_codex_auth(Path(args.from_codex_auth))
        else:
            refresh_token = (await device_flow_login()).refresh_token
    except CodexLoginError as exc:
        raise CliError(str(exc)) from exc

    env_path = Path(args.env_file)
    await asyncio.to_thread(
        _rewrite_env, env_path, "OPENAI_CODEX_REFRESH_TOKEN=", refresh_token,
    )
    print(f"ok — bootstrap refresh token written to {env_path}")
    if args.from_codex_auth:
        print(f"delete {args.from_codex_auth} and its CODEX_HOME now: a codex CLI "
              "that refreshes from it logs Nora out.")
    print("recreate the container (`docker compose up -d --force-recreate nora`); "
          "the first refresh spends this token and keeps the rotated one in "
          "./state/openai-codex-oauth.json.")
    return 0


async def cmd_groups(args: argparse.Namespace) -> int:
    cfg = _config(args)
    signal = cfg.platforms.signal
    policy = _policy(cfg)
    try:
        groups = await directory.list_groups(
            base_url=signal.base_url, account=signal.account,
        )
    except directory.DirectoryError as exc:
        raise CliError(
            f"{exc}\n"
            "`nora groups` has to ask the daemon which groups exist — there is "
            "nowhere else that knows. Check it with:\n"
            "    docker compose ps signal-cli\n"
            f"    curl -sI {signal.base_url.rstrip('/')}/api/v1/check"
        ) from exc

    for line in report.render_groups(
        groups=groups, policy=policy,
        policy_path=str(cfg.policy_file), base_url=signal.base_url,
    ):
        print(line)
    return 0


async def cmd_presence_members(args: argparse.Namespace) -> int:
    """`nora presence members` — one authenticated Life360 read that prints every
    circle and member (id + name), so the operator can paste the ids into
    identity.toml and config.toml. Reuses the SAME `Life360Client` the collector
    runs on (one Life360 library) — nothing here is a second impl."""
    cfg = _config(args)
    presence = cfg.presence
    creds = {
        presence.client_credential_env: os.environ.get(presence.client_credential_env, ""),
        presence.username_env: os.environ.get(presence.username_env, ""),
        presence.password_env: os.environ.get(presence.password_env, ""),
    }
    access_token = os.environ.get(presence.auth_token_env, "")
    missing = [name for name, value in creds.items() if not value and not access_token]
    if missing:
        raise CliError(
            "these Life360 env vars are unset: " + ", ".join(missing) + "\n"
            f"Add them to .env. {presence.client_credential_env} is the Life360 "
            "app's Basic credential (a public app constant, the same for every "
            f"unofficial client); {presence.username_env}/{presence.password_env} "
            f"are your Life360 login — or set {presence.auth_token_env} if "
            "Life360 refuses the password login."
        )
    client = Life360Client(
        client_credential=creds[presence.client_credential_env],
        username=creds[presence.username_env],
        password=creds[presence.password_env],
        access_token=access_token or None,
    )
    try:
        circles = await client.discover()
    except Life360DiscoveryError as exc:
        raise CliError(f"life360 discovery failed: {exc}") from exc
    for line in report.render_presence_members(circles):
        print(line)
    return 0


async def cmd_reminders_list(args: argparse.Namespace) -> int:
    """`nora reminders list` — what the heartbeat is still holding, READ ONLY.

    The one operator question the metrics cannot answer: which rows are waiting,
    for whom, in which conversation, and whether any of them are overdue. It
    opens the same read-only engine `nora persons pending` does, and there is no
    sibling subcommand that writes (see `add_subcommands`).
    """
    cfg = _config(args)
    limit = int(args.limit)
    if limit <= 0:
        # `check_limit` inside the store raises `ValueError` for this, which
        # would reach the operator as a traceback. Refused here, in the same
        # sentence-not-a-stack-trace shape `cmd_pending` uses.
        raise CliError(f"--limit must be positive; got {limit}")
    url = _database_url(cfg)
    store = readonly_reminder_store(url)
    try:
        # `limit + 1`, and the extra row is dropped below. The store answers at
        # most what it is asked for, so `len(rows) == limit` is the SAME shape
        # for a listing that happens to hold exactly `limit` rows and one that
        # was cut off — and `>= limit` called both of them truncated. That sent
        # an operator whose database holds precisely `--limit` reminders back to
        # re-run with a larger one, for nothing. One extra row is what makes the
        # two distinguishable at all.
        rows = list(await store.all_pending(limit=limit + 1))
    except Exception as exc:  # one readable line, never a traceback
        raise CliError(
            f"could not read the reminders in {_redacted(url)}: "
            f"{type(exc).__name__}: {exc}. Is nora-db up, and has Nora run "
            "against this config yet?"
        ) from exc
    finally:
        await store.aclose()
    truncated = len(rows) > limit
    rows = rows[:limit]

    for line in report.render_reminders(
        reminders=rows,
        now=datetime.now(UTC),
        source=_redacted(url),
        truncated=truncated,
    ):
        print(line)
    return 0


def _memory_client(cfg: Config) -> tuple[Policy, Persons, WorkspaceRouter, HonchoClient]:
    policy = _policy(cfg)
    persons = _persons(cfg)
    try:
        router = build_workspace_router(
            policy=policy, bindings=persons.honcho_workspaces,
            default_workspace=cfg.memory.workspace_id,
        )
    except MemoryRoutingError as exc:
        raise CliError(f"memory routing error: {exc}") from exc
    memory = HonchoClient(
        cfg.memory.base_url, cfg.memory.workspace_id, ai_peer=cfg.memory.ai_peer,
        workspace_router=router,
    )
    return policy, persons, router, memory


async def cmd_memory_detach(args: argparse.Namespace) -> int:
    """`nora memory detach-unit` (DEC-214); see `nora.app.memory_detach`."""
    cfg = _config(args)
    policy, persons, router, memory = _memory_client(cfg)
    if args.refresh_cards:
        if args.apply:
            raise CliError("--refresh-cards is its own step; do not combine it with --apply")
        try:
            refreshed = await refresh_cards(
                peers_file=args.peers_file, memory=memory, ai_peer=cfg.memory.ai_peer,
            )
        except DetachRefusedError as exc:
            raise CliError(str(exc)) from exc
        finally:
            await memory.aclose()
        for line in render_refresh(refreshed):
            print(line)
        return 1 if refreshed.failed else 0
    if not args.unit or not args.from_honcho_workspace:
        raise CliError("step 1 needs --unit and --from-honcho-workspace")
    store = readonly_store(_database_url(cfg))
    try:
        result = await detach_unit(
            unit=args.unit, from_honcho_workspace=args.from_honcho_workspace,
            policy=policy, persons=persons, router=router, records=store, memory=memory,
            peers_file=args.peers_file, apply=bool(args.apply),
        )
    except DetachRefusedError as exc:
        raise CliError(str(exc)) from exc
    finally:
        await store.aclose()
        await memory.aclose()
    for line in render_detach(result, args.peers_file):
        print(line)
    return 1 if result.failed else 0


def _cutover(text: str, flag: str = "--before") -> datetime:
    """A timezone-aware ISO 8601 instant. `flag` names the option in the error, so a
    bad `--since` says `--since` (it said `--before` for every caller)."""
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CliError(f"{flag} must be ISO 8601; got {text!r}") from exc
    if stamp.tzinfo is None:
        raise CliError(f"{flag} needs a timezone (e.g. +08:00); got {text!r}")
    return stamp


async def cmd_memory_carry(args: argparse.Namespace) -> int:
    """`nora memory carry-conclusions --workspace <w> [--apply]` (DEC-214).
    Touches Honcho only, and writes only under `--apply`; see
    `nora.app.memory_carry`."""
    cfg = _config(args)
    policy, _, router, memory = _memory_client(cfg)
    try:
        result = await carry_conclusions(
            workspace=args.workspace, policy=policy, router=router, memory=memory,
            ai_peer=cfg.memory.ai_peer, before=_cutover(args.before),
            apply=bool(args.apply),
        )
    except CarryRefusedError as exc:
        raise CliError(str(exc)) from exc
    finally:
        await memory.aclose()
    for line in render_carry(result):
        print(line)
    return 1 if result.failed else 0


async def cmd_memory_backfill(args: argparse.Namespace) -> int:
    """`nora memory backfill --unit <unit> [--apply] [--resume]` (DEC-214).

    The record store is opened READ-ONLY (`readonly_store`), exactly as the
    other commands here open it; the only writes are Honcho feeds, and only
    under `--apply`. The router is the one the daemon builds, from the same
    three files, so the backfill writes where live ingestion writes."""
    cfg = _config(args)
    policy, persons, router, memory = _memory_client(cfg)
    url = _database_url(cfg)
    store = readonly_store(url)
    media_engine = create_readonly_sql_engine(url)
    # The daemon's media join, over a read-only engine: what a photo showed
    # rides in the replayed row, as it does in lazy adoption's.
    media_join = MediaJoin(
        store=SqlMediaObjectStore(media_engine), screen=scan_identifiers,
        metrics=Metrics(), render_chars=cfg.media.comprehension_render_chars,
        timeout_seconds=cfg.media.join_timeout_seconds,
    )
    try:
        result = await backfill_unit(
            unit=args.unit, policy=policy, persons=persons, router=router,
            records=store, memory=memory, default_workspace=cfg.memory.workspace_id,
            media_join=media_join,
            apply=bool(args.apply), resume=bool(args.resume),
        )
    except BackfillRefusedError as exc:
        raise CliError(str(exc)) from exc
    finally:
        await store.aclose()
        await media_engine.dispose()
        await memory.aclose()
    for line in render_backfill(result):
        print(line)
    return 1 if result.failed else 0


async def cmd_persona_history(args: argparse.Namespace) -> int:
    """No model calls. Restart clears only history cursors, retaining all claims."""
    import sqlalchemy as sa

    from nora.app.composition import character_person_keys
    from nora.domain.persona import FACT_KINDS
    from nora.infra.sql.character import SqlCharacterEvidence
    from nora.infra.sql.persona import SqlPersonaStore
    from nora.infra.sql.persona_inputs import SqlPersonaInputs
    from nora.infra.sql.schema import CONSOLIDATION_WATERMARK as W

    if args.restart and not args.key:
        raise CliError("--restart requires --key")
    cfg = _config(args)
    engine = (create_sql_engine(_database_url(cfg)) if args.restart and args.apply
              else create_readonly_sql_engine(_database_url(cfg)))
    try:
        if args.restart and args.apply:
            async with engine.begin() as conn:
                await conn.execute(sa.delete(W).where(W.c.scope_id == args.key,
                    W.c.consolidator_name.in_([
                        "persona-person:history", "persona-person:history-boundary"])))
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.select(W).where(
                W.c.consolidator_name.in_([
                    "persona-person:history", "persona-person:history-boundary"])))).all()
        by_key: dict[str, dict[str, int]] = {}
        for row in rows:
            by_key.setdefault(row.scope_id, {})[row.consolidator_name] = row.last_message_id
        keys = character_person_keys(_policy(cfg), _persons(cfg))
        for key in keys:
            by_key.setdefault(key, {})
        if args.key:
            by_key = {args.key: by_key.get(args.key, {})}
        store = SqlPersonaStore(engine, screen=lambda _: None)
        receipts = SqlCharacterEvidence(engine)
        for key, state in sorted(by_key.items()):
            boundary = state.get("persona-person:history-boundary")
            cursor = state.get("persona-person:history", 0)
            heads = await store.account_heads(key=key)
            pk = keys.get(key)
            total, seen = (await receipts.historical_counts(
                rooms=[(r.platform, r.key) for r in pk.rooms if r.key in pk.listed],
                person_id=pk.person.person_id, cursor=cursor, through=boundary,
            ) if pk is not None and boundary is not None else (None, None))
            typed = [h for h in heads if h.part == "identity" and h.fact_key is not None
                     and h.state == "current"]
            present = {h.fact_key for h in typed}
            print(json.dumps({
                "key": key, "boundary": boundary, "cursor": cursor,
                "complete": boundary is not None and cursor >= boundary,
                "typed_current": len(typed), "eligible_sources": total, "sources_handed": seen,
                "unresolved_relationships": sum(h.fact_key == "relationship"
                    and isinstance(h.fact_value, dict) and "unresolved_name" in h.fact_value
                    for h in typed),
                "legacy_unresolved": sum(h.part == "identity" and h.fact_key is None
                                         and h.state in ("current", "disputed") for h in heads),
                "disputed": sum(h.state == "disputed" for h in heads),
                "missing_fields": sorted(set(FACT_KINDS) - {"purpose", "norm"} - present),
                "restart_applied": bool(args.restart and args.apply),
                "cached_media": (await SqlPersonaInputs(engine).media_recovery(
                    key=key, rooms=[(r.platform, r.key) for r in pk.rooms if r.key in pk.listed],
                    person=pk.person.person_id,
                )) if pk is not None else None,
                "cached_media_dry_run": True,
            }))
    finally:
        await engine.dispose()
    return 0


async def cmd_memory_rebuild(args: argparse.Namespace) -> int:
    """`nora memory rebuild [--dry-run]`. A WRITABLE engine, like `events backfill-units`:
    it writes the memory index (`nora.infra.sql.memory_index.rebuild`), never
    a store. Prints counts per kind only, never an id or a text. Exit 1 when
    a source stayed unprojected, so a script reads «incomplete» as a failure."""
    cfg = _config(args)
    if getattr(args, "embeddings_only", False):
        return await _memory_rebuild_embeddings(args, cfg)
    if getattr(args, "other_models", False):
        raise CliError("--other-models applies only with --embeddings-only")
    engine = create_sql_engine(_database_url(cfg))
    try:
        result = await memory_index.rebuild(engine, dry_run=bool(args.dry_run))
    except memory_index.RebuildRefusedError as exc:
        raise CliError(str(exc)) from exc
    finally:
        await engine.dispose()
    for line in render_rebuild(result):
        print(line)
    return 1 if result.unprojected and not result.dry_run else 0


async def cmd_memory_backfill_places(args: argparse.Namespace) -> int:
    """`nora memory backfill-places [--apply] [--per-second N] [--limit N]`
    (`nora.app.place_backfill`). A WRITABLE engine, like `events
    backfill-units`. Venue areas come from the Geocoding API with the route
    key, cached geocodes' from the configured reverse geocoder. Prints counts
    only. Exit 1 when a lookup or a write failed."""
    from nora.app.composition import reverse_geocoder
    from nora.infra.geocode import CachingBilingualGeocoder, GoogleGeocoder

    cfg = _config(args)
    route_key = os.environ.get(cfg.route.api_key_env, "") if cfg.route.enabled else ""
    places = GoogleGeocoder(api_key=route_key) if route_key else None
    found = reverse_geocoder(cfg, os.environ, cache_store=None)
    geocoder = found if isinstance(found, CachingBilingualGeocoder) else None
    engine = create_sql_engine(_database_url(cfg))
    try:
        result = await backfill_places(
            events=SqlGatheringStore(engine), cache=SqlGeocodeCacheStore(engine),
            places=places, geocoder=geocoder,
            apply=bool(args.apply), per_second=float(args.per_second), limit=int(args.limit),
        )
    finally:
        await engine.dispose()
    for line in render_place_backfill(result):
        print(line)
    return 1 if result.venues.failed or result.geocodes.failed else 0


async def _memory_rebuild_embeddings(args: argparse.Namespace, cfg: Config) -> int:
    """`nora memory rebuild --embeddings-only [--other-models] [--dry-run]`
    clears vectors, every one or only another model's, and reports counts: the
    embedding worker (`[memory_index] embed`) backfills them on its own ticks.
    The projection is untouched."""
    model = fingerprint(cfg.memory_index.embedder_model)
    keep = model if args.other_models else None
    engine = create_sql_engine(_database_url(cfg))
    try:
        async with engine.begin() as conn:
            cleared = await memory_index.clear_embeddings(
                conn, keep_model=keep, dry_run=bool(args.dry_run),
            )
            after = await memory_index.embed_counts(conn, model)
    finally:
        await engine.dispose()
    verb = "would clear" if args.dry_run else "cleared"
    which = "of other models" if keep is not None else "all"
    print(f"vectors: {verb} {cleared} ({which})")
    print(f"embedded with {model}: {after.embedded}; waiting for the worker: {after.pending}")
    print(
        f"failed at least once: {after.failing}; given up after "
        f"{memory_index.EMBED_MAX_ATTEMPTS} attempts: {after.given_up}"
    )
    if not cfg.memory_index.embed:
        print("[memory_index] embed is false: nothing backfills until it is on")
    return 0


def render_rebuild(result: memory_index.RebuildReport) -> list[str]:
    """One line per kind, then the queue: counts only."""
    verb = ("would project", "would drop") if result.dry_run else ("projected", "dropped")
    lines = [
        f"{kind}: {verb[0]} {n}, {verb[1]} {result.dropped.get(kind, 0)}"
        for kind, n in result.projected.items()
    ]
    lines += [
        f"{kind} hubs: {verb[1]} {n}"
        for kind, n in result.dropped.items() if kind not in result.projected
    ]
    if result.dry_run:
        lines.append(f"queued now: {result.unprojected} (dry run: nothing written)")
    else:
        lines.append(f"unprojected: {result.unprojected}; index status complete")
    return lines


async def cmd_events_backfill_units(args: argparse.Namespace) -> int:
    """`nora events backfill-units [--apply]` (DEC-237). Opens a WRITABLE engine (`create_sql_
    engine`, not `create_readonly_sql_engine`): unlike every other command
    in this module it writes the `events` table itself, through
    `GatheringStore.update` — the same path `update_event` writes through —
    so `row_history` records it like any other write. See
    `nora.app.events_backfill` for what is read, skipped and written."""
    cfg = _config(args)
    policy = _policy(cfg)
    engine = create_sql_engine(_database_url(cfg))
    store = SqlGatheringStore(engine)
    try:
        result = await backfill_unit_ids(policy=policy, store=store, apply=bool(args.apply))
    finally:
        await store.aclose()
    for line in render_events_backfill(result):
        print(line)
    return 1 if result.failed else 0


async def _lesson_of(store: SqlLessonStore, lesson_id: str) -> Lesson:
    lesson = await store.get(lesson_id=lesson_id)
    if lesson is None:
        raise CliError(f"no lesson {lesson_id}")
    return lesson


async def cmd_lessons_list(args: argparse.Namespace) -> int:
    """`nora lessons list --unit U [--all]`, READ ONLY."""
    engine = create_readonly_sql_engine(_database_url(_config(args)))
    try:
        found = await SqlLessonStore(engine).lessons(unit=args.unit)
    finally:
        await engine.dispose()
    for line in lessons_cli.render_list(args.unit, found, everything=bool(args.all)):
        print(line)
    return 0


async def cmd_lessons_tensions(args: argparse.Namespace) -> int:
    """`nora lessons tensions [--unit U] [--limit N]`, READ ONLY."""
    engine = create_readonly_sql_engine(_database_url(_config(args)))
    try:
        found = await SqlTensionStore(engine).recent(unit=args.unit, limit=args.limit)
    finally:
        await engine.dispose()
    for line in lessons_cli.render_tensions(found):
        print(line)
    return 0


async def cmd_lessons_show(args: argparse.Namespace) -> int:
    """`nora lessons show ID`, READ ONLY."""
    engine = create_readonly_sql_engine(_database_url(_config(args)))
    try:
        store = SqlLessonStore(engine)
        lesson = await _lesson_of(store, args.lesson_id)
        chain = await store.revisions(lesson_id=lesson.id)
    finally:
        await engine.dispose()
    for line in lessons_cli.render_chain(lesson, chain):
        print(line)
    return 0


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


async def cmd_lessons_retire(args: argparse.Namespace) -> int:
    """`nora lessons retire ID --why TEXT [--apply]`: a WRITABLE engine, one
    revision, a dry run without `--apply`."""
    engine = create_sql_engine(_database_url(_config(args)))
    try:
        lesson = await _lesson_of(SqlLessonStore(engine), args.lesson_id)
        if not args.apply:
            print(f"would retire {lesson.id} (unit {lesson.unit}, rev {lesson.head.seq}, "
                  f"{lesson.head.state}); nothing written")
            return 0
        result = await lessons_cli.book_for(engine, _now_ms).retire(
            unit=lesson.unit, lesson_id=lesson.id, by="operator", why=args.why,
            established_by=lessons_cli.OPERATOR,
            record_head=await lessons_cli.record_head(engine),
        )
    finally:
        await engine.dispose()
    print(json.dumps(result))
    return 0 if result["status"] == "ok" else 1


async def cmd_lessons_rollback(args: argparse.Namespace) -> int:
    """`nora lessons rollback ID --to N [--apply]`: a WRITABLE engine, one
    revision, a dry run without `--apply`."""
    engine = create_sql_engine(_database_url(_config(args)))
    try:
        lesson = await _lesson_of(SqlLessonStore(engine), args.lesson_id)
        if not args.apply:
            print(f"would put revision {args.to} of {lesson.id} back as the newest "
                  f"(now rev {lesson.head.seq}, {lesson.head.state}); nothing written")
            return 0
        result = await lessons_cli.book_for(engine, _now_ms).rollback(
            unit=lesson.unit, lesson_id=lesson.id, to_seq=int(args.to),
            established_by=lessons_cli.OPERATOR,
        )
    finally:
        await engine.dispose()
    print(json.dumps(result))
    return 0 if result["status"] == "ok" else 1


async def cmd_assess_run_nightly(args: argparse.Namespace) -> int:
    """`nora assess run-nightly [--no-record]` (DEC-240). Refused with a clear message, not a stack
    trace, when `[assess] mode` is `"off"` (`AssessDisabledError`). The
    Rollup rebuild diff is not run from this command (`nora.app.assess_run`'s
    own docstring: opt-in, a separate cost); it reports `not_applicable`.
    Exit code is nonzero when ANY check came back `fail` OR `error` — a check
    that raised is not a clean run either, and the operator paging on this
    exit code must not read an errored check as a quiet night."""
    cfg = _config(args)
    engine = create_sql_engine(_database_url(cfg))
    try:
        try:
            outcome = await assess_run_nightly(
                engine, mode=cfg.assess.mode, record=not args.no_record,
            )
        except AssessDisabledError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    finally:
        await engine.dispose()
    for line in assess_render(outcome.report):
        print(line)
    return 1 if outcome.report.failing_checks or outcome.report.errored_checks else 0




async def cmd_measure_participation(args: argparse.Namespace) -> int:
    """`nora measure participation` — the four, READ ONLY.

    Nothing here is a verdict and nothing calls a model. Every number is
    arithmetic over rows the rooms produced anyway, which is the point: a model
    agreeing with the instruction someone wrote it is not evidence about the
    room, and a `no_go` is unfalsifiable by construction.
    """
    cfg = _config(args)
    days = int(args.days)
    if days <= 0:
        raise CliError(f"--days must be positive; got {days}")
    now = datetime.now(UTC)
    # EPOCH MILLISECONDS — `decision_log.ts`, `messages.ts` and the measures
    # themselves are all ms. A seconds-valued bound is smaller than every row
    # in the table, so the window silently becomes ALL TIME while still
    # printing "last 7d".
    now_ms = int(now.timestamp() * 1000)
    since = now_ms - days * 86_400_000

    refusals: tuple[Refusal, ...] = ()
    refusal_source = "not supplied — the two refusal-side measures did not run"
    if args.refusals is not None:
        refusals, refusal_source = _load_refusals(args.refusals, since=since)

    url = _database_url(cfg)
    try:
        rows = await gather_measures(url, since=since)
    except Exception as exc:  # one readable line, never a traceback
        raise CliError(
            f"could not read the rows in {_redacted(url)}: "
            f"{type(exc).__name__}: {exc}. Is nora-db up, and has Nora run "
            "against this config yet?"
        ) from exc

    for line in report.render_measures(
        compute_measures(rows, refusals, now=now_ms),
        source=_redacted(url),
        window=f"last {days}d",
        refusals=len(refusals),
        refusal_source=refusal_source,
    ):
        print(line)
    return 0


def _load_refusals(path: Path, *, since: int) -> tuple[tuple[Refusal, ...], str]:
    """Read `scripts/gate_refusals.py`'s output.

    `at` and `covers_from` are EPOCH MILLISECONDS, matching every other instant
    these measures handle. `scripts/gate_refusals.py` converts from
    Prometheus's float seconds; nothing downstream does a second conversion.

    Refused rather than silently dropped when the file predates the window:
    a refusal list that stops short makes both measures that read it look
    clean, and «clean» is the answer they would give if the gate had refused
    nothing at all. The two must never be confusable — that confusion is the
    whole reason these measures exist.
    """
    try:
        payload = json.loads(path.read_text())
        rows = tuple(
            Refusal(
                conversation_id=str(r["conversation_id"]),
                at=int(r["at"]),
                provenance=r.get("provenance", "counter"),
            )
            for r in payload["refusals"]
        )
        covers_from = int(payload["covers_from"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CliError(
            f"could not read refusals from {path}: {type(exc).__name__}: {exc}. "
            "Produce it with scripts/gate_refusals.py."
        ) from exc
    if covers_from > since:
        raise CliError(
            f"{path} only covers from {datetime.fromtimestamp(covers_from / 1000, UTC)}, "
            f"but --days asks for {datetime.fromtimestamp(since / 1000, UTC)}. A short "
            "refusal list makes both refusal-side measures look clean, which is "
            "what they would also say if nothing had been refused. Re-run "
            "scripts/gate_refusals.py with a wider range, or lower --days."
        )
    provenance = {r.provenance for r in rows} or {"counter"}
    return rows, "/".join(sorted(provenance))


async def cmd_pending(args: argparse.Namespace) -> int:
    cfg = _config(args)
    signal = cfg.platforms.signal
    persons = _persons(cfg)
    limit = int(args.limit)
    if limit <= 0:
        # `RecordStore.unattributed` returns `[]` for a non-positive limit, and
        # the report would then say "no unattributed rows at all" — a false
        # statement about the database produced by a typo in a flag.
        raise CliError(f"--limit must be positive; got {limit}")
    url = _database_url(cfg)
    contacts, groups, note = await _directory(base_url=signal.base_url,
                                              account=signal.account)
    rows = await _unattributed(url, limit=limit)
    accounts = correlate(rows=rows, persons=persons, contacts=contacts, groups=groups)

    for line in report.render_pending(
        accounts=accounts,
        identity_path=str(cfg.identity_file),
        source=_redacted(url),
        scanned=len(rows),
        truncated=len(rows) >= limit,
        daemon_note=note,
        daemon_ok=not note,
    ):
        print(line)
    return 0


async def _directory(
    *, base_url: str, account: str
) -> tuple[list[directory.SignalContact], list[directory.SignalGroup], str]:
    """Contacts and group rosters, or empty lists and an honest note.

    `persons pending` must survive an unreachable daemon — the operator's 3am
    path is precisely the one where signal-cli may be the thing that is broken.
    What is lost is real and is stated rather than absorbed: without contacts
    there are no display names, and without either correlator a phone number
    and an ACI that belong to one account can only be merged when they arrived
    on the SAME envelope.
    """
    try:
        contacts = await directory.list_contacts(base_url=base_url, account=account)
        groups = await directory.list_groups(base_url=base_url, account=account)
    except directory.DirectoryError as exc:
        return [], [], (
            f"WARNING: signal-cli is unreachable ({exc}).\n"
            "Names below read 'unknown' and two ids of one account are merged "
            "only when they arrived on the same message — so one person may "
            "appear twice. Re-run once the daemon is back before pasting."
        )
    return contacts, groups, ""


#: The earliest epoch-ms `--at` accepts, 2020-01-01T00:00:00Z. Nora's record
#: starts years after it; a smaller number is a date typed without dashes
#: (`20260927` is 1970 in milliseconds) and is refused, never read as 1970.
EPOCH_MS_FLOOR = 1_577_836_800_000


def parse_instant(text: str) -> datetime:
    """`--at`: epoch milliseconds (from 2020 on), or ISO-8601 WITH an offset.
    A naive time is refused: system time is an instant, and guessing the zone
    would move the snapshot by hours without saying so."""
    raw = text.strip()
    if raw.isdigit():
        if int(raw) < EPOCH_MS_FLOOR:
            raise CliError(
                f"--at {text!r} is before 2020 as epoch milliseconds; give "
                "ISO-8601 with an offset (2026-09-27T14:00:00+08:00)"
            )
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
    try:
        at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CliError(f"--at {text!r} is neither epoch ms nor ISO-8601") from exc
    if at.tzinfo is None:
        raise CliError(f"--at {text!r} has no offset; give one (e.g. +08:00 or Z)")
    return at


async def cmd_snapshot(args: argparse.Namespace) -> int:
    """`nora snapshot --at T` — what every restorable store held at T, what
    was in force, and what cannot be restored; with `--into`, seeded into
    that (empty, migrated) database."""
    at = parse_instant(str(args.at))
    source_url = args.source or _database_url(_config(args))
    dry = bool(args.dry_run) or (args.into is None and args.files_out is None)
    source = create_readonly_sql_engine(source_url)
    try:
        try:
            shot = await snapshot_at.plan(source, at)
        except snapshot_at.SnapshotRefusedError as exc:
            raise CliError(str(exc)) from exc
        for line in snapshot_at.render_plan(shot, source=_redacted(source_url), dry_run=dry):
            print(line)
        forced = shot.in_force
        unavailable = forced.missing()
        if unavailable:
            print(
                "in force at T but unrecorded (a restore composes these from "
                "today's files or not at all): " + ", ".join(unavailable)
            )
        if dry:
            return 0
        if args.files_out is not None:
            _forced, written = await snapshot_at.materialise(source, at, Path(args.files_out))
            print(f"wrote {len(written)} files under {args.files_out}")
        if args.into is not None:
            target = create_sql_engine(str(args.into))
            try:
                seeded = await snapshot_at.seed(source, target, at)
            except snapshot_at.SnapshotRefusedError as exc:
                raise CliError(str(exc)) from exc
            finally:
                await target.dispose()
            print(
                f"seeded {_redacted(str(args.into))}: {sum(seeded.copied.values())} rows "
                f"in {len(seeded.copied)} tables, {seeded.history_rows} history rows"
            )
            for table, count in sorted(seeded.orphaned.items()):
                print(f"  not copied: {count} {table} rows whose parent was not held at T")
    finally:
        await source.dispose()
    return 0


def _database_url(cfg: Config) -> str:
    """The PostgreSQL URL, from the env var `store.database_url_env` names (DEC-8).

    A `CliError`, not a startup crash: the operator running this by hand needs a
    readable sentence about the one variable to export, not a traceback.
    """
    url = os.environ.get(cfg.store.database_url_env, "")
    if not url:
        raise CliError(
            f"environment variable {cfg.store.database_url_env} is not set — it "
            "must hold the PostgreSQL URL of the record store. After cutover this "
            "command runs via `docker exec` into a nora-network container, where "
            "the container already carries it (design/20 §3)."
        )
    return url


def _redacted(url: str) -> str:
    """`user@host:port/dbname`, never the password — this string is printed, and
    a credential must never reach the report (DEC-8)."""
    try:
        u = make_url(url)
    except ArgumentError:
        return "the PostgreSQL record store"
    where = "/".join(filter(None, [f"{u.host or ''}:{u.port}" if u.host else "", u.database]))
    return f"{u.username}@{where}" if u.username else where or "the PostgreSQL record store"


def readonly_store(url: str) -> SqlRecordStore:
    """The ONE place this package opens the record store.

    Public, and a named function rather than an inline constructor call, so a
    test can hold the actual object the commands use and prove a write through it
    is refused. Asserting only that `create_readonly_sql_engine` refuses writes
    proves nothing about whether the CLI calls it — swapping this line for
    `create_sql_engine` would leave such a test green, because a command that
    issues only SELECTs changes no bytes either way.
    """
    return SqlRecordStore(create_readonly_sql_engine(url))


def readonly_reminder_store(url: str) -> SqlReminderStore:
    """The ONE place this package opens the reminders table.

    Public and named for the reason `readonly_store` is: a test can hold the
    actual object `nora reminders list` uses and prove a write through it is
    refused. `cancel` is the write that matters here — it is the one this
    module could plausibly grow a subcommand for, and design spec §7.4 says it
    must not, so the driver refusing it is what keeps a future edit honest.

    Its own engine, unlike the daemon (which shares one pool between the record
    store, the presence reader and the reminders store): the CLI is a
    short-lived process running ONE command, `aclose()` disposes exactly what
    that command opened, and there is nothing else alive to share with.
    """
    return SqlReminderStore(create_readonly_sql_engine(url))


async def _unattributed(url: str, *, limit: int) -> list[UnattributedRow]:
    """Read the unattributed backlog through the port, read-only.

    No `run_migrations` call, deliberately: migrating is a write, and a
    control-plane command that silently upgraded the schema while claiming to be
    read-only would be doing the single most dangerous thing available to it.

    A store that cannot be reached at all — `nora-db` down, or a database that
    does not exist yet because Nora has never run against it — becomes one
    readable line, never a traceback.
    """
    store = readonly_store(url)
    try:
        return await store.unattributed(limit=limit)
    except Exception as exc:  # one readable line, never a traceback
        raise CliError(
            f"could not read the record store at {_redacted(url)}: "
            f"{type(exc).__name__}: {exc}. Is nora-db up, and has Nora run "
            "against this config yet?"
        ) from exc
    finally:
        await store.aclose()
