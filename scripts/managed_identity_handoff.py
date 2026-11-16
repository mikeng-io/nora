"""Admit one established `identity.toml`/`policy.toml` mapping into an empty
managed target — the installation authority's initial file-to-managed
handoff (`design/managed-identity-access/identity.md` «Backend selection
and lifecycle», `docs/maintainers/managed-identity-handoff.md`).

INSTALLATION-ONLY: a local command an operator runs against a prepared
managed target. It is not reachable from HTTP, a model or a room; it never
edits backend selection, production configuration or credentials, and a
successful import does not activate managed mode — that is a separate
configuration change.

    plan  --identity PATH --policy PATH
          validates the mapping and prints the durable import id and counts.
          With the managed target flags it also assesses the target:
          fresh / resume / already-imported / refused.
    apply --identity PATH --policy PATH
          --database-url URL --kratos-admin-url URL
          --keto-read-url URL --keto-write-url URL
          claims the target and runs the journaled import. An interrupted
          run is finished by re-running the SAME command — the import id is
          derived from the file contents, so changed files are a different
          import and are refused.

One invocation reads the files ONCE into a validated immutable effect:
the printed plan, the target composition and the applied import all bind
that single capture, so a file changed during setup cannot swap the
effect behind the plan it reported — the next run simply names a
different import. An exact completed retry returns the recorded
historical receipt; it asserts prior completion, never that current
state still equals the original file.

A target-connected plan runs the SAME non-mutating preflight apply runs:
the durable attribution, the empty-or-owned reads and the Kratos
absence/collision check. Fresh is reported only on an explicit absence
answer — a collision refuses and a target that cannot answer is
unavailable, never fresh.

Every printed line is bounded: fixed statuses and the reason codes in
`_REASONS`, the plan's own counts and the digest-derived import id. An
outcome detail outside that vocabulary prints as `detail-withheld` —
source paths, file text, room labels and ids, person/account names,
endpoints and provider answers never reach stdout, stderr or the log.

Exit codes: 0 complete/ok · 2 refused (invalid arguments, invalid or
unsupported source, foreign or occupied target) · 3 unavailable —
unreadable source, a target/credential that cannot be wired, answered or
closed, an incomplete journal (retryable).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import NoReturn

from nora.config_identity import ManagedOryConfig
from nora.domain.managed import Outcome, OutcomeStatus
from nora.people.app.managed_admission import (
    FileAdmission,
    PreparedImport,
    prepare,
)

COMPLETE = 0
REFUSED = 2
RETRYABLE = 3

#: Every reason the command may name — an unlisted reason is never trusted
#: to be safe, however plausible its prefix looks.
_REASONS = frozenset({
    # the claim, its receipt and the journaled effect
    "fresh", "resume", "complete", "read-back",
    "effect-conflict", "receipt-unconfirmed", "journal-missing",
    "absent-operation", "different-import", "foreign-journal",
    "unknown-person", "absent-person", "absent-binding", "already-revoked",
    "alias-conflict", "former-id-is-live", "conflicting-correlation",
    "kratos-id-claimed", "no-block",
    # the target's managed and remote state
    "foreign-authority-state", "kratos-correlation-present",
    "kratos-foreign-identity", "external-id-conflict",
    "conflict-unresolved", "owner-evidence-mismatch",
    "identity-unconfirmed", "identity-absent",
    "identity-publication-pending", "read-back-mismatch", "store-failure",
    "malformed-body", "absent", "subject-less tuple",
    # the source files and what they cannot express
    "unreadable-source", "invalid-identity-file", "invalid-policy-file",
    "unsupported-input:dynamic-membership", "unsupported-input:guild",
    "foreign-target-state", "foreign-target-state:person",
    "foreign-target-state:account", "foreign-target-state:identifier",
    "foreign-target-state:alias", "foreign-target-state:membership",
    "foreign-target-state:membership-support", "foreign-target-state:hold",
})

_CLASS = re.compile(r"[A-Z][A-Za-z0-9_]*")       # an exception class name
_STATUS = re.compile(r"[0-9]{3}")                # an HTTP status
_PERSONS = re.compile(r"[0-9]+-persons")         # a bounded input count
_COUNTS = re.compile(r"[a-z_]+=[0-9]+( [a-z_]+=[0-9]+)*")  # the plan's counts

#: Parametric codes whose tail the producer itself generates — an
#: exception class, an HTTP status or a count; never a free value.
_GENERATED = (
    ("unreadable-source:", _CLASS),
    ("invalid-identity-file:", _CLASS),
    ("invalid-policy-file:", _CLASS),
    ("transport:", _CLASS),
    ("status:", _STATUS),
    ("redirect:", _STATUS),
    ("unsupported-input:dm-admission:", _PERSONS),
)


def _bounded_segment(segment: str) -> bool:
    if segment in _REASONS:
        return True
    return any(
        segment.startswith(prefix)
        and bool(pattern.fullmatch(segment[len(prefix):]))
        for prefix, pattern in _GENERATED
    )


def _bounded_detail(detail: str) -> str | None:
    """The detail when every `;` segment is bounded, else None — withheld."""
    if _COUNTS.fullmatch(detail):
        return detail
    parts = detail.split(";")
    if parts and all(_bounded_segment(part) for part in parts):
        return detail
    return None


def _report(outcome: Outcome, *, prefix: str) -> int:
    detail = _bounded_detail(outcome.detail)
    if outcome.status is OutcomeStatus.COMPLETE:
        print(f"{prefix}: {detail or 'complete'}")
        return COMPLETE
    if outcome.status is OutcomeStatus.DENIED:
        print(f"refused: {detail or 'detail-withheld'}")
        return REFUSED
    print(f"{outcome.status.value}: {detail or 'detail-withheld'}")
    return RETRYABLE


class _Parser(argparse.ArgumentParser):
    """argparse whose usage errors stay bounded — the default `error`
    echoes the offending argv token, which can be a private path or id."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(REFUSED, "refused: invalid-arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply"):
        sub.add_parser(command)
    for command in ("plan", "apply"):
        target = sub.choices[command]
        target.add_argument("--identity", required=True, help="identity.toml path")
        target.add_argument("--policy", required=True, help="policy.toml path")
        target.add_argument(
            "--database-url", default=os.environ.get("DATABASE_URL", ""),
            help="postgresql+asyncpg URL of the managed target (DATABASE_URL)",
        )
        target.add_argument("--kratos-admin-url", default="")
        target.add_argument("--keto-read-url", default="")
        target.add_argument("--keto-write-url", default="")
        target.add_argument("--kratos-admin-key-env", default="")
        target.add_argument("--keto-key-env", default="")
        target.add_argument("--timeout-seconds", type=float, default=5.0)
    return parser


@asynccontextmanager
async def _target_services(
    args: argparse.Namespace, prepared: PreparedImport,
) -> AsyncIterator[FileAdmission]:
    """The real managed wiring plus the SQL-aware probe, fed by `prepared.policy` alone."""
    import aiohttp

    from nora.infra.ory import KratosClient
    from nora.infra.sql.engine import create_sql_engine
    from nora.people.adapters.managed_handoff import KratosAdmission, SqlAdmissionProbe
    from nora.people.wire import managed_services

    def api_key(name: str) -> str | None:
        if not name:
            return None
        value = os.environ.get(name)
        if not value:
            raise ValueError("configured Ory credential is unavailable")
        return value

    managed = ManagedOryConfig(
        kratos_admin_url=args.kratos_admin_url,
        keto_read_url=args.keto_read_url,
        keto_write_url=args.keto_write_url,
        kratos_admin_key_env=args.kratos_admin_key_env,
        keto_key_env=args.keto_key_env,
        timeout_seconds=args.timeout_seconds,
    )
    engine = create_sql_engine(args.database_url)
    try:
        async with aiohttp.ClientSession() as session:
            services = managed_services(
                managed=managed, session=session, policy=prepared.policy,
                engine=engine, credentials=os.environ,
            )
            try:
                yield FileAdmission(
                    bindings=services.bindings,
                    backend=services.backend,
                    kratos=KratosAdmission(KratosClient(
                        session=session,
                        admin_url=args.kratos_admin_url,
                        timeout_seconds=args.timeout_seconds,
                        api_key=api_key(args.kratos_admin_key_env),
                    )),
                    probe=SqlAdmissionProbe(engine),
                )
            finally:
                await services.aclose()
    finally:
        await engine.dispose()


RuntimeFactory = Callable[[], AbstractAsyncContextManager[FileAdmission]]


_TARGET_FLAGS = (
    "database_url", "kratos_admin_url", "keto_read_url", "keto_write_url",
    "kratos_admin_key_env", "keto_key_env",
)


async def run(
    argv: list[str] | None = None,
    *,
    services: RuntimeFactory | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exited:  # argparse exits on --help and on refusal
        return exited.code if isinstance(exited.code, int) else REFUSED
    try:
        prepared = prepare(Path(args.identity), Path(args.policy))
    except Exception as error:  # noqa: BLE001 — a source fault is unavailable
        print(f"unavailable: source-read:{type(error).__name__}")
        return RETRYABLE
    if isinstance(prepared, Outcome):
        return _report(prepared, prefix="source")
    counts = prepared.snapshot["counts"]
    print(
        f"plan {prepared.import_id} "
        + " ".join(f"{key}={counts[key]}" for key in sorted(counts))
    )
    if args.command == "plan" and not any(
        getattr(args, name) for name in _TARGET_FLAGS
    ):
        return COMPLETE
    missing = [
        name for name in _TARGET_FLAGS[:4] if not getattr(args, name)
    ]
    if missing:
        print(f"refused: the managed target needs {', '.join(missing)}")
        return REFUSED
    factory = services or (lambda: _target_services(args, prepared))
    prefix = "target" if args.command == "plan" else "import"
    stage = "target-setup"
    outcome: Outcome | None = None
    try:
        async with factory() as admission:
            stage = "target-check"
            if args.command == "plan":
                outcome = await admission.assess_prepared(prepared)
            else:
                outcome = await admission.apply_prepared(prepared)
            stage = "target-close"
    except Exception as error:  # noqa: BLE001 — every stage stays unavailable;
        # a cleanup fault after a committed import is still honestly
        # unavailable until an exact retry confirms the durable receipt.
        print(f"unavailable: {stage}:{type(error).__name__}")
        return RETRYABLE
    if outcome is None:  # unreachable: the except returns on every fault
        return RETRYABLE
    return _report(outcome, prefix=prefix)


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
