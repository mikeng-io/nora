"""Export the current managed identity/access state into a fresh private
`identity.toml`/`policy.toml` bundle — the preparation half of the
managed-to-file handoff (`docs/maintainers/managed-identity-export.md`,
`design/managed-identity-access/identity.md` «Backend selection and
lifecycle»).

INSTALLATION-ONLY and READ-ONLY: a local operator command, never reachable
from HTTP, a model or a room. It writes only into a FRESH `--output`
directory after every check passes, and it never edits backend selection,
configuration, source files, secrets or sessions — adopting the bundle is
the separate operator-approved authority switch in the runbook.

    plan   --policy PATH
           --database-url URL --kratos-admin-url URL --keto-read-url URL
           validates current managed state and prints the would-be bundle's
           digest and bounded counts. Writes nothing.
    export --policy PATH --output DIR
           --database-url URL --kratos-admin-url URL --keto-read-url URL
           the same validation, then publishes identity.toml, policy.toml
           and export-manifest.json into a fresh DIR (existing non-empty
           directories are refused).

`--policy` is the deployment's CURRENT topology file (room declarations,
units, flags — not grant authority). The emitted pair is validated through
the real file loaders and compared verdict-for-verdict against live
managed checks; any shape file mode cannot represent exactly — management
authority, speak/view divergence, denied persons in open rooms, ambiguous
ownership, unfinished operations — refuses the whole export.

Exit codes: 0 complete/ok · 2 refused (unsupported, ambiguous or
unfaithful state; occupied destination) · 3 unavailable/incomplete
(retryable — a store could not answer).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from nora.domain.managed import OutcomeStatus
from nora.people.app.managed_export import ManagedExport
from nora.policy import load_policy

COMPLETE = 0
REFUSED = 2
RETRYABLE = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "export"):
        target = sub.add_parser(command)
        target.add_argument(
            "--policy", required=True,
            help="the deployment's current policy.toml (topology source)")
        if command == "export":
            target.add_argument(
                "--output", required=True,
                help="fresh output directory — refused if it exists non-empty")
        target.add_argument(
            "--database-url", default=os.environ.get("DATABASE_URL", ""),
            help="postgresql+asyncpg URL of the managed target (DATABASE_URL)")
        target.add_argument("--kratos-admin-url", default="")
        target.add_argument("--keto-read-url", default="")
        target.add_argument("--kratos-admin-key-env", default="")
        target.add_argument("--keto-key-env", default="")
        target.add_argument("--timeout-seconds", type=float, default=5.0)
    return parser


@asynccontextmanager
async def _target_services(args: argparse.Namespace) -> AsyncIterator[ManagedExport]:
    """The real managed wiring plus the SQL-aware probe, lazily imported."""
    import aiohttp

    from nora.config_identity import ManagedOryConfig
    from nora.infra.sql.engine import create_sql_engine
    from nora.people.adapters.managed_export import SqlExportProbe
    from nora.people.wire import managed_services

    policy = load_policy(Path(args.policy))
    managed = ManagedOryConfig(
        kratos_admin_url=args.kratos_admin_url,
        keto_read_url=args.keto_read_url,
        # The export never writes Keto — the read listener alone suffices.
        keto_write_url=args.keto_read_url,
        kratos_admin_key_env=args.kratos_admin_key_env,
        keto_key_env=args.keto_key_env,
        timeout_seconds=args.timeout_seconds,
    )
    engine = create_sql_engine(args.database_url)
    try:
        async with aiohttp.ClientSession() as session:
            services = managed_services(
                managed=managed, session=session, policy=policy,
                engine=engine, credentials=os.environ,
            )
            try:
                yield ManagedExport(
                    bindings=services.bindings,
                    backend=services.backend,
                    topology=services.topology,
                    registry=services.registry,
                    access=services.access,
                    probe=SqlExportProbe(engine),
                )
            finally:
                await services.aclose()
    finally:
        await engine.dispose()


RuntimeFactory = Callable[[], AbstractAsyncContextManager[ManagedExport]]


def _report(report) -> int:
    for refusal in report.refusals:
        print(f"refused: {refusal}")
    status = report.outcome.status
    if status is OutcomeStatus.COMPLETE:
        counts = " ".join(f"{k}={report.counts[k]}" for k in sorted(report.counts))
        print(f"{report.digest} {counts}" if report.digest else "plan: complete")
        return COMPLETE
    if status is OutcomeStatus.DENIED:
        if not report.refusals:
            print(f"refused: {report.outcome.detail}")
        return REFUSED
    print(f"{status.value}: {report.outcome.detail}")
    return RETRYABLE


def _publish(output: Path, bundle) -> int:
    """Stage the bundle into a FRESH directory, manifest last, then rename."""
    if output.exists() and any(output.iterdir()):
        print(f"refused: output not fresh: {output}")
        return REFUSED
    staging = output.parent / f".{output.name}.staging-{os.getpid()}"
    if staging.exists():
        print(f"refused: stale staging directory: {staging}")
        return REFUSED
    staging.mkdir(parents=True)
    try:
        (staging / "identity.toml").write_text(bundle.identity_toml)
        (staging / "policy.toml").write_text(bundle.policy_toml)
        # The manifest is written LAST — it is the bundle's completeness mark.
        (staging / "export-manifest.json").write_text(bundle.manifest_json)
        if output.exists():
            # The freshness check above guarantees it is an empty directory —
            # the only shape `os.replace` cannot rename onto.
            for entry in staging.iterdir():
                os.replace(entry, output / entry.name)
            staging.rmdir()
        else:
            os.replace(staging, output)
    except OSError as error:
        print(f"unavailable: destination-write:{type(error).__name__}")
        return RETRYABLE
    print(f"export: complete {output}")
    return COMPLETE


async def run(
    argv: list[str] | None = None,
    *,
    services: RuntimeFactory | None = None,
) -> int:
    args = _parser().parse_args(argv)
    from nora.policy import PolicyError

    try:
        load_policy(Path(args.policy))
    except (PolicyError, OSError, UnicodeError) as error:
        print(f"refused: invalid-policy-file:{type(error).__name__}")
        return REFUSED
    missing = [
        name for name in
        ("database_url", "kratos_admin_url", "keto_read_url")
        if not getattr(args, name)
    ]
    if missing:
        print(f"refused: the managed target needs {', '.join(missing)}")
        return REFUSED
    factory = services or (lambda: _target_services(args))
    async with factory() as export:
        if args.command == "plan":
            report = await export.assess(Path(args.policy))
        else:
            report = await export.export(Path(args.policy))
    rc = _report(report)
    if rc != COMPLETE or args.command == "plan":
        return rc
    if report.bundle is None:
        # A complete export always carries the bundle — its absence is a
        # broken pipeline, reported as retryable rather than asserted away.
        print("unavailable: empty-bundle")
        return RETRYABLE
    return _publish(Path(args.output), report.bundle)


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
