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
           and export-manifest.json into a FRESH DIR (an existing path —
           file, link or directory, empty or not — is refused).

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
import hashlib
import json
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
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
                help="fresh output directory — refused if the path exists")
        target.add_argument(
            "--database-url", default=os.environ.get("DATABASE_URL", ""),
            help="postgresql+asyncpg URL of the managed target (DATABASE_URL)")
        target.add_argument("--kratos-admin-url", default="")
        target.add_argument("--keto-read-url", default="")
        target.add_argument("--kratos-admin-key-env", default="")
        target.add_argument("--keto-key-env", default="")
        target.add_argument("--timeout-seconds", type=float, default=5.0)
    return parser


class _TargetRefusedError(Exception):
    """The target could not be wired at all — a bounded refusal, not a
    retryable failure. Carries only a refusal code, never a path or id."""


@asynccontextmanager
async def _target_services(args: argparse.Namespace) -> AsyncIterator[ManagedExport]:
    """Live managed services over one immutable topology snapshot."""
    import aiohttp

    from nora.config_identity import ManagedOryConfig
    from nora.domain.managed import Outcome
    from nora.infra.ory import KratosClient
    from nora.infra.sql.engine import create_sql_engine
    from nora.people.adapters.managed_export import SqlExportProbe
    from nora.people.app.managed_export_prepare import _read_policy
    from nora.people.wire import managed_services

    loaded = _read_policy(Path(args.policy))
    if isinstance(loaded, Outcome):
        raise _TargetRefusedError("invalid-policy-file")
    policy, source = loaded
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
            # The probe's read-only correlated-identity proof — the same
            # admin endpoint the registry reads, never an ensure/create.
            key = (
                os.environ.get(args.kratos_admin_key_env)
                if args.kratos_admin_key_env else None
            )
            kratos = KratosClient(
                session=session, admin_url=managed.kratos_admin_url,
                timeout_seconds=managed.timeout_seconds, api_key=key,
            )
            try:
                yield ManagedExport(
                    bindings=services.bindings,
                    backend=services.backend,
                    topology=services.topology,
                    access=services.access,
                    probe=SqlExportProbe(engine, kratos),
                    source_snapshot=(policy, source),
                )
            finally:
                await services.aclose()
    finally:
        await engine.dispose()


RuntimeFactory = Callable[[], AbstractAsyncContextManager[ManagedExport]]


def _report(report) -> int:
    """Bounded refusal codes, counts and digests."""
    for code in dict.fromkeys(r.code for r in report.refusals):
        print(f"refused: {code}")
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


def _cleanup(output: Path, written: list[Path]) -> bool:
    failed = False
    for path in [*reversed(written), output]:
        try:
            path.rmdir() if path == output else path.unlink()
        except OSError as error:
            print(f"unavailable: destination-cleanup:{type(error).__name__}")
            failed = True
    return not failed


def _publish(output: Path, bundle, policy_path: Path) -> int:
    """Stage privately, then link the closed manifest as the atomic completeness mark."""
    try:
        output.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        print("refused: output not fresh")
        return REFUSED
    except OSError as error:
        print(f"unavailable: destination-create:{type(error).__name__}")
        return RETRYABLE
    written: list[Path] = []
    try:
        for name, text in (
            ("identity.toml", bundle.identity_toml),
            ("policy.toml", bundle.policy_toml),
            (".export-manifest.pending", bundle.manifest_json),
        ):
            path = output / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written.append(path)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        source_digest = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        if source_digest != json.loads(bundle.manifest_json)["topology_sha256"]:
            print("refused: untrustworthy:source-changed")
            return REFUSED if _cleanup(output, written) else RETRYABLE
        os.link(output / ".export-manifest.pending", output / "export-manifest.json")
    except OSError as error:
        _cleanup(output, written)
        print(f"unavailable: destination-write:{type(error).__name__}")
        return RETRYABLE
    try:
        (output / ".export-manifest.pending").unlink()
    except OSError as error:
        print(f"warning: staging-cleanup:{type(error).__name__}")
    print("export: complete")
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
    try:
        async with factory() as export:
            if args.command == "plan":
                return _report(await export.assess(Path(args.policy)))
            async with export.prepared(Path(args.policy)) as report:
                rc = _report(report)
                if rc != COMPLETE:
                    return rc
                if report.bundle is None:
                    print("unavailable: empty-bundle")
                    return RETRYABLE
                return _publish(Path(args.output), report.bundle, Path(args.policy))
    except _TargetRefusedError as error:
        print(f"refused: {error}")
        return REFUSED
    except Exception as error:  # noqa: BLE001 — fail closed, bounded:
        # the exception's TYPE name only, never its message (identifiers).
        print(f"unavailable: target-init:{type(error).__name__}")
        return RETRYABLE


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
