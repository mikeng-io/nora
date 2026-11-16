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

Exit codes: 0 complete/ok · 2 refused (source invalid, unsupported input,
foreign or occupied target) · 3 incomplete/unavailable (retryable).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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


async def run(
    argv: list[str] | None = None,
    *,
    services: RuntimeFactory | None = None,
) -> int:
    args = _parser().parse_args(argv)
    prepared = prepare(Path(args.identity), Path(args.policy))
    if isinstance(prepared, Outcome):
        print(f"refused: {prepared.detail}")
        return REFUSED
    counts = prepared.snapshot["counts"]
    print(
        f"plan {prepared.import_id} "
        + " ".join(f"{key}={counts[key]}" for key in sorted(counts))
    )
    need_target = args.command == "apply" or bool(args.database_url)
    if not need_target:
        return COMPLETE
    missing = [
        name for name in
        ("database_url", "kratos_admin_url", "keto_read_url", "keto_write_url")
        if not getattr(args, name)
    ]
    if missing:
        print(f"refused: the managed target needs {', '.join(missing)}")
        return REFUSED
    factory = services or (lambda: _target_services(args, prepared))
    async with factory() as admission:
        if args.command == "plan":
            outcome = await admission.assess_prepared(prepared)
            prefix = "target"
        else:
            outcome = await admission.apply_prepared(prepared)
            prefix = "import"
    if outcome.status is OutcomeStatus.COMPLETE:
        print(f"{prefix}: {outcome.detail or 'complete'}")
        return COMPLETE
    if outcome.status is OutcomeStatus.DENIED:
        print(f"refused: {outcome.detail}")
        return REFUSED
    print(f"{outcome.status.value}: {outcome.detail}")
    return RETRYABLE


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
