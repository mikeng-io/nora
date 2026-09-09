"""The operator control plane: the read-only commands (`nora groups`,
`nora persons pending`, `nora presence members`, `nora reminders list`) and
the config write surface (`nora config set/get/unset/validate` — see
`nora.app.config_cli`). This module's own commands only READ.

design/10 §6, plan Task 6. This answers the operator's original complaint —
*"I CANNOT identify that group ID belongs to which group ... there is NO
control plane for me"* — and it answers it without ever amending D6: both
commands only READ. Adoption stays a hand-edit of `identity.toml` followed by a
restart, at which point `nora.app.adoption` backfills the history (DEC-16).

Three properties are load-bearing, and each is enforced by something rather
than promised:

* **Nothing is written.** The record store is opened through
  `create_readonly_sql_engine` — every connection sets
  `default_transaction_read_only=on`, so any write raises
  `ReadOnlySqlTransaction` at the server (design/20 §3) — and the only daemon
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
  role (design/20 §3); the URL comes from `DATABASE_URL` there, same as Nora.
* **A broken daemon degrades to a sentence, never a traceback.** `nora groups`
  cannot do its job without signal-cli and says so with an exit code;
  `nora persons pending` can, losing only the display names, and says that too.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from nora.app import config_cli, report
from nora.app.pending import correlate
from nora.config import Config, ConfigError, load
from nora.domain.models import UnattributedRow
from nora.infra.life360 import Life360Client, Life360DiscoveryError
from nora.infra.sql import SqlRecordStore, create_readonly_sql_engine
from nora.infra.sql.reminders import SqlReminderStore
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
    # (`cancel_reminder`), in the conversation it was set in (design spec §7.4).
    reminders_list = reminders_sub.add_parser(
        "list", parents=[parent],
        help="every reminder still pending, soonest first, with what is overdue",
    )
    reminders_list.add_argument(
        "--limit", type=int, default=DEFAULT_REMINDER_LIMIT,
        help=f"pending rows to list (default {DEFAULT_REMINDER_LIMIT})",
    )
    reminders_list.set_defaults(func=cmd_reminders_list)

    presence = subparsers.add_parser(
        "presence", parents=[parent], help="Life360 presence helpers",
    )
    presence_sub = presence.add_subparsers(dest="presence_command", required=True)
    members = presence_sub.add_parser(
        "members", parents=[parent],
        help="list your Life360 circles + members (id + name) to paste into identity.toml",
    )
    members.set_defaults(func=cmd_presence_members)

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
    blocks the event loop). M8 (Kimi audit): the write is ATOMIC — tmp +
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
    entirely — that is the state file's job (design/proposed/29 §4.1).
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
    runs on (one Life360 library, design/19 §4) — nothing here is a second impl."""
    cfg = _config(args)
    presence = cfg.presence
    creds = {
        presence.client_credential_env: os.environ.get(presence.client_credential_env, ""),
        presence.username_env: os.environ.get(presence.username_env, ""),
        presence.password_env: os.environ.get(presence.password_env, ""),
    }
    missing = [name for name, value in creds.items() if not value]
    if missing:
        raise CliError(
            "these Life360 env vars are unset: " + ", ".join(missing) + "\n"
            f"Add them to .env. {presence.client_credential_env} is the Life360 "
            "app's Basic credential (a public app constant, the same for every "
            f"unofficial client); {presence.username_env}/{presence.password_env} "
            "are your Life360 login."
        )
    client = Life360Client(
        client_credential=creds[presence.client_credential_env],
        username=creds[presence.username_env],
        password=creds[presence.password_env],
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
