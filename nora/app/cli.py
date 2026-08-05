"""The read-only operator control plane: `nora groups`, `nora persons pending`.

design/10 §6, plan Task 6. This answers the operator's original complaint —
*"I CANNOT identify that group ID belongs to which group ... there is NO
control plane for me"* — and it answers it without ever amending D6: both
commands only READ. Adoption stays a hand-edit of `identity.toml` followed by a
restart, at which point `nora.app.adoption` backfills the history (DEC-16).

Three properties are load-bearing, and each is enforced by something rather
than promised:

* **Nothing is written.** The audit database is opened through
  `create_readonly_sql_engine` — `mode=ro` plus `PRAGMA query_only=ON`, so a
  write fails at the driver — and the only daemon calls go through
  `nora.platform.signal.directory`, whose method allowlist holds exactly
  `listGroups` and `listContacts`. One honest caveat: reading a wal-mode
  SQLite database materialises its `-shm`/`-wal` sidecars, and a read-only
  connection cannot checkpoint, so it cannot remove them on close either.
  They contain no message data, no row is touched, and the only way to avoid
  them (`immutable=1`) asserts the file cannot change — false whenever Nora is
  running, and it would buy a clean directory listing with torn reads.
  `tests/test_cli_control_plane.py` exempts those two names and nothing else.
* **It works with the Nora container stopped.** Nothing here builds an adapter,
  binds the metrics port, opens the SSE stream, runs a migration or constructs
  an LLM client. `config.load(..., require_secrets=False)` is what keeps a
  missing `OMNIROUTE_API_KEY` — which lives in `.env` and is injected into the
  CONTAINER, not into the operator's shell — from failing a command that never
  talks to the provider.
* **A broken daemon degrades to a sentence, never a traceback.** `nora groups`
  cannot do its job without signal-cli and says so with an exit code;
  `nora persons pending` can, losing only the display names, and says that too.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from nora.app import report
from nora.app.pending import correlate
from nora.config import Config, ConfigError, load
from nora.domain.models import UnattributedRow
from nora.infra.sql import SqlAuditStore, create_readonly_sql_engine
from nora.persons import Persons, PersonsError, load_persons
from nora.platform.signal import directory
from nora.policy import Policy, PolicyError, load_policy

# Deliberately larger than `nora.app.adoption.DEFAULT_SCAN_LIMIT`: adoption is a
# recurring chore competing for SQLite's single writer, while this is one
# operator-initiated read that should cover the whole retained backlog.
DEFAULT_SCAN_LIMIT = 5000


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


async def cmd_pending(args: argparse.Namespace) -> int:
    cfg = _config(args)
    signal = cfg.platforms.signal
    persons = _persons(cfg)
    limit = int(args.limit)
    if limit <= 0:
        # `AuditStore.unattributed` returns `[]` for a non-positive limit, and
        # the report would then say "no unattributed rows at all" — a false
        # statement about the database produced by a typo in a flag.
        raise CliError(f"--limit must be positive; got {limit}")
    contacts, groups, note = await _directory(base_url=signal.base_url,
                                              account=signal.account)
    rows = await _unattributed(cfg.store.db_path, limit=limit)
    accounts = correlate(rows=rows, persons=persons, contacts=contacts, groups=groups)

    for line in report.render_pending(
        accounts=accounts,
        identity_path=str(cfg.identity_file),
        db_path=str(cfg.store.db_path),
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


def readonly_store(db_path: Path) -> SqlAuditStore:
    """The ONE place this package opens the audit database.

    Public, and a named function rather than an inline constructor call, so a
    test can hold the actual object the commands use and prove a write through
    it is refused. Asserting only that `create_readonly_sql_engine` refuses
    writes proves nothing about whether the CLI calls it — swapping this line
    for `create_sql_engine` would leave such a test green, because a command
    that issues only SELECTs changes no bytes either way.
    """
    return SqlAuditStore(create_readonly_sql_engine(db_path))


async def _unattributed(db_path: Path, *, limit: int) -> list[UnattributedRow]:
    """Read the unattributed backlog through the port, read-only.

    No `run_migrations` call, deliberately: migrating is a write, and a
    control-plane command that silently upgraded the schema of a database whose
    daemon is stopped would be doing the single most dangerous thing available
    to it while claiming to be read-only.
    """
    if not await asyncio.to_thread(db_path.exists):
        raise CliError(
            f"no audit database at {db_path} — Nora has not run against this "
            "config yet, so there are no senders to adopt."
        )
    store = readonly_store(db_path)
    try:
        return await store.unattributed(limit=limit)
    except Exception as exc:  # one readable line, never a traceback
        raise CliError(f"could not read {db_path}: {type(exc).__name__}: {exc}") from exc
    finally:
        await store.aclose()
