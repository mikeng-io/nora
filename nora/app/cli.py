"""The operator control plane: the read-only commands (`nora groups`,
`nora persons pending`, `nora presence members`, `nora reminders list`) and
the config write surface (`nora config set/get/unset/validate` — see
`nora.app.config_cli`). Most of this module's commands only READ the record
store; each one that writes says so where it is registered. Among them:
`xai-login` and `openai-codex-login` (the host `secret.toml`),
`lessons retire` (a graph lesson retirement, only with `--apply`),
`snapshot --into` (a separate empty sandbox database), and
`assess run-nightly` (an `assess_report` row).

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
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from nora.app import config_cli, graph_lessons_cli, lessons_cli, report
from nora.app.assess_run import AssessDisabledError
from nora.app.assess_run import render as assess_render
from nora.app.assess_run import run_nightly as assess_run_nightly
from nora.app.participation_measures import gather as gather_measures
from nora.app.participation_measures import measure as compute_measures
from nora.app.pending import correlate
from nora.app.place_backfill import backfill_places
from nora.app.place_backfill import render as render_place_backfill
from nora.config import Config, ConfigError, load
from nora.domain.models import UnattributedRow
from nora.domain.participation_measures import Refusal
from nora.domain.ports import ChannelDirectory
from nora.infra.life360 import Life360Client, Life360DiscoveryError
from nora.infra.sql import (
    SqlRecordStore,
    create_readonly_sql_engine,
    create_sql_engine,
    snapshot_at,
)
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.tension import SqlTensionStore
from nora.persons import Persons, PersonsError, load_persons
from nora.platform.discord import directory as discord_directory
from nora.platform.discord.channels import (
    _ROOM_CHANNEL_TYPES,
    StaticChannelDirectory,
)
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
    # firing rather than one row; cancelling ONE of her own rows is the Cognition
    # session's job (`remind_cancel`).
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
        "show", parents=[parent], help="one graph lesson by id, live or retired",
    )
    lessons_show.add_argument("lesson_id")
    lessons_show.set_defaults(func=cmd_lessons_show)
    lessons_retire = lessons_sub.add_parser(
        "retire", parents=[parent],
        help="retire a lesson as the operator; it is not learned again from "
             "outcomes no newer than now (a dry run unless --apply)",
    )
    lessons_retire.add_argument("lesson_id")
    lessons_retire.add_argument("--why", required=True, help="reason shown in the command result")
    lessons_retire.add_argument("--apply", action="store_true", help="write")
    lessons_retire.set_defaults(func=cmd_lessons_retire)
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

    memory = subparsers.add_parser(
        "memory", parents=[parent],
        help="memory maintenance (operator only)",
    )
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
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

    # `assess run-nightly` (DEC-240): the Assess adaptor's deterministic
    # data-quality checks. WRITABLE engine, because `--record` (the default)
    # writes one `assess_report` row; the
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

    # OAuth bootstrap writers; current chains remain in state/.
    xai_login = subparsers.add_parser(
        "xai-login", parents=[parent],
        help="xAI OAuth device flow — get XAI_OAUTH_REFRESH_TOKEN for image generation",
    )
    xai_login.add_argument(
        "--env-file", default=None,
        help="legacy .env bootstrap output (default: secret.toml beside config)",
    )
    xai_login.set_defaults(func=cmd_xai_login)

    codex_login = subparsers.add_parser(
        "openai-codex-login", parents=[parent],
        help="OpenAI device login — get OPENAI_CODEX_REFRESH_TOKEN for image generation",
    )
    codex_login.add_argument(
        "--env-file", default=None,
        help="legacy .env bootstrap output (default: secret.toml beside config)",
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


def _bootstrap_path(args: argparse.Namespace) -> Path:
    if args.env_file:
        return Path(args.env_file)
    return Path(args.config).resolve().parent / "secret.toml"


def _write_bootstrap(path: Path, marker: str, token: str) -> None:
    if path.suffix == ".toml":
        from nora.secret import write
        write(path, marker.removesuffix("="), token)
    else:
        _rewrite_env(path, marker, token)


def _rewrite_env(env_path: Path, marker: str, refresh_token: str) -> None:
    """Legacy explicit env output; atomic private replacement."""
    import tempfile
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    kept = [line for line in lines if not line.startswith(marker)]
    kept.append(f"{marker}{refresh_token}")
    fd, temporary = tempfile.mkstemp(prefix=".bootstrap-", dir=env_path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(kept) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, env_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def cmd_xai_login(args: argparse.Namespace) -> int:
    """Write a new bootstrap; rotating state stays in state/."""
    from nora.infra.image_gen.xai_oauth import (
        XaiOAuthFlowError,
        device_flow_login,
    )
    try:
        refresh_token = await device_flow_login()
    except XaiOAuthFlowError as exc:
        raise CliError(str(exc)) from exc

    env_path = _bootstrap_path(args)
    marker = "XAI_OAUTH_REFRESH_TOKEN="
    await asyncio.to_thread(_write_bootstrap, env_path, marker, refresh_token)

    print(f"ok — refresh token written to {env_path}")
    if not args.env_file:
        print("Remove legacy XAI_OAUTH_REFRESH_TOKEN overrides before recreation.")
    print("Recreate affected readers through the installed deployment path; "
          "see the configuration guide. Access tokens refresh automatically.")
    return 0


async def cmd_openai_codex_login(args: argparse.Namespace) -> int:
    """Write a fresh Codex bootstrap without changing current state."""
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

    env_path = _bootstrap_path(args)
    await asyncio.to_thread(
        _write_bootstrap, env_path, "OPENAI_CODEX_REFRESH_TOKEN=", refresh_token,
    )
    print(f"ok — bootstrap refresh token written to {env_path}")
    if not args.env_file:
        print("Remove legacy OPENAI_CODEX_REFRESH_TOKEN overrides before recreation.")
    if args.from_codex_auth:
        print(f"delete {args.from_codex_auth} and its CODEX_HOME now: a codex CLI "
              "that refreshes from it logs Nora out.")
    print("Recreate affected readers through the installed deployment path; "
          "see the configuration guide. Current tokens remain in "
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

    # Discord: the guilds the bot is in, and the channels each server entry
    # expands into. The policy is RELOADED against a `StaticChannelDirectory`
    # filled from these same REST reads, so `conversation()` — and therefore
    # the mode column — is the live verdict, not a copy of it.
    discord_cfg = cfg.platforms.discord
    if discord_cfg.enabled:
        token = cfg.credentials.get(discord_cfg.token_env, "")
        guild_entries = [c for c in policy.conversations if c.guild]
        error = ""
        guilds: list[discord_directory.DiscordGuild] = []
        by_guild: dict[str, list[discord_directory.DiscordChannel]] = {}
        if not token:
            error = f"{discord_cfg.token_env} is unset"
        else:
            try:
                guilds = await discord_directory.list_guilds(token=token)
                for entry in guild_entries:
                    raw = await discord_directory.list_channels(
                        token=token, guild_id=entry.guild,
                    )
                    by_guild[entry.guild] = raw
            except discord_directory.DirectoryError as exc:
                error = str(exc)
            channels_dir = StaticChannelDirectory(
                {
                    entry.guild: {
                        ch.id: ch.name for ch in by_guild.get(entry.guild, [])
                        if ch.type in _ROOM_CHANNEL_TYPES and not ch.parent_id
                    }
                    for entry in guild_entries
                },
                threads={
                    ch.id: ch.parent_id
                    for raw in by_guild.values() for ch in raw
                    if ch.parent_id
                },
            )
            policy = _policy_with(cfg, channels_dir)
        for line in report.render_discord_guilds(
            guilds=guilds, channels=by_guild, policy=policy, error=error,
        ):
            print(line)
    return 0


def _policy_with(cfg: Config, channels: ChannelDirectory) -> Policy:
    try:
        return load_policy(cfg.policy_file, channels=channels)
    except PolicyError as exc:
        raise CliError(f"policy error: {exc}") from exc


async def cmd_presence_members(args: argparse.Namespace) -> int:
    """`nora presence members` — one authenticated Life360 read that prints every
    circle and member (id + name), so the operator can paste the ids into
    identity.toml and config.toml. Reuses the SAME `Life360Client` the collector
    runs on (one Life360 library) — nothing here is a second impl."""
    cfg = _config(args)
    presence = cfg.presence
    creds = {
        presence.client_credential_env: cfg.credentials.get(presence.client_credential_env, ""),
        presence.username_env: cfg.credentials.get(presence.username_env, ""),
        presence.password_env: cfg.credentials.get(presence.password_env, ""),
    }
    access_token = cfg.credentials.get(presence.auth_token_env, "")
    missing = [name for name, value in creds.items() if not value and not access_token]
    if missing:
        raise CliError(
            "these Life360 env vars are unset: " + ", ".join(missing) + "\n"
            "Add them to secret.toml or the explicit environment. "
            f"{presence.client_credential_env} is the Life360 "
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


def _profile_tracing(cfg: Config) -> None:
    """Use the daemon's existing trace destination for explicitly requested model work."""
    if cfg.obs.tracing.enabled:
        from nora.agent.telemetry import setup_agent_tracing
        from nora.infra.version import VersionManifest
        from nora.obs.tracing import build_sampler
        entry = VersionManifest().current().entry
        setup_agent_tracing(sampler=build_sampler(), service_name="nora",
            exporter=cfg.obs.tracing.exporter, endpoint=cfg.obs.tracing.endpoint,
            headers_env=cfg.obs.tracing.headers_env, credentials=cfg.credentials,
            service_version=entry.version if entry is not None else "",
            environment=cfg.obs.errors.environment)








async def cmd_memory_backfill_places(args: argparse.Namespace) -> int:
    """`nora memory backfill-places [--apply] [--per-second N] [--limit N]`
    (`nora.app.place_backfill`). A WRITABLE engine, like `assess
    run-nightly`. Venue areas come from the Geocoding API with the route
    key, cached geocodes' from the configured reverse geocoder. Prints counts
    only. Exit 1 when a lookup or a write failed."""
    from nora.app.composition_readers import reverse_geocoder
    from nora.infra.geocode import CachingBilingualGeocoder, GoogleGeocoder

    cfg = _config(args)
    route_key = cfg.credentials.get(cfg.route.api_key_env, "") if cfg.route.enabled else ""
    places = GoogleGeocoder(api_key=route_key) if route_key else None
    found = reverse_geocoder(cfg, cfg.credentials, cache_store=None)
    geocoder = found if isinstance(found, CachingBilingualGeocoder) else None
    engine = create_sql_engine(_database_url(cfg))
    try:
        result = await backfill_places(
            events=SqlGatheringStore(engine), cache=SqlGeocodeCacheStore(engine),
            places=places, geocoder=geocoder, content_checks=cfg.content_checks.enabled,
            apply=bool(args.apply), per_second=float(args.per_second), limit=int(args.limit),
        )
    finally:
        await engine.dispose()
    for line in render_place_backfill(result):
        print(line)
    return 1 if result.venues.failed or result.geocodes.failed else 0






async def cmd_lessons_list(args: argparse.Namespace) -> int:
    return await graph_lessons_cli.list_lessons(
        _database_url(_config(args)), args.unit, everything=bool(args.all),
    )


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
    """`nora lessons show ID`, READ ONLY: one graph lesson."""
    try:
        return await graph_lessons_cli.show_lesson(_database_url(_config(args)), args.lesson_id)
    except graph_lessons_cli.GraphLessonError as exc:
        raise CliError(str(exc)) from exc


async def cmd_lessons_retire(args: argparse.Namespace) -> int:
    cfg = _config(args)
    try:
        return await graph_lessons_cli.retire_lesson(
            _database_url(cfg), _policy(cfg), args.lesson_id, apply=args.apply, why=args.why,
        )
    except graph_lessons_cli.GraphLessonError as exc:
        raise CliError(str(exc)) from exc


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
    url = cfg.credentials.get(cfg.store.database_url_env, "")
    if not url:
        raise CliError(
            f"environment variable {cfg.store.database_url_env} is not set — it "
            "must hold the PostgreSQL URL of the record store. After cutover this "
            "command runs via `docker exec` into a nora-network container, where "
            "the container already carries it (DEC-51)."
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
