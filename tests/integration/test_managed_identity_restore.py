"""Coordinated generated SQL/Ory backup restored onto independent storage.

The original writers stop before capture; the original Ory services remain
stopped while the same composition reads a new database and new Ory volumes.
Gateway assertions remain generated protocol evidence, not provider callbacks.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import aiohttp
import sqlalchemy as sa
from aiohttp import web
from sqlalchemy.engine import make_url

from nora.config_identity import ManagedOryConfig
from nora.domain.managed import CheckResult, OutcomeStatus, Verdict
from nora.infra.sql.engine import create_sql_engine
from nora.people import AccountSessions, PortalScopes, wire
from nora.policy import load_policy
from nora.web.management_auth import AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from tests.conftest import _pg_database
from tests.integration.managed_ory import (
    KETO_IMAGE,
    KRATOS_IMAGE,
    ory_stack,
    snapshot_ory,
)
from tests.integration.managed_ory import (
    ory as ory,
)
from tests.integration.test_managed_identity_journey import (
    CHECK,
    DISC,
    GATEWAY,
    OP,
    POLICY_TOML,
    ROOM_A,
    ROOM_B,
    ROOM_C,
    SIG_A1,
    SIG_A2,
    TG,
    _four_account_readback,
    _link_additional_account,
    _link_first_pair,
    _manage_portal,
    _register_accounts,
    _unlink_one_account,
)
from tests.integration.test_managed_identity_journey import (
    world as world,
)


async def _seed_completed_journey(world):
    ids = await _register_accounts(world)
    await _link_first_pair(world, *ids)
    await _manage_portal(world, *ids)
    survivor, second, discord, telegram, _ = ids
    await _link_additional_account(world, TG, telegram, survivor)
    await _link_additional_account(world, SIG_A2, second, survivor)
    await _four_account_readback(world, survivor, (second, discord, telegram))
    await _unlink_one_account(world, survivor)
    return ids


async def _durable_rows(engine):
    async with engine.connect() as conn:
        tables = (await conn.execute(sa.text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"))).scalars()
        result = {}
        for table in tables:
            quoted = conn.dialect.identifier_preparer.quote(table)
            rows = (await conn.execute(
                sa.text(f"SELECT * FROM {quoted}"))).mappings()  # noqa: S608 -- quoted catalog name
            result[table] = sorted(
                json.dumps(dict(row), default=str, sort_keys=True) for row in rows)
        return result


def _pg_tool(command, url, archive, postgres):
    parsed = make_url(str(url))
    container = postgres.get_wrapped_container().id
    args = ["docker", "exec", "--interactive", "--env", f"PGPASSWORD={parsed.password}",
            container, "pg_dump" if command == "dump" else "pg_restore",
            "--username", parsed.username, "--dbname", parsed.database, "--no-owner"]
    args += (["--format=custom"] if command == "dump" else ["--no-acl", "--exit-on-error"])
    with archive.open("wb" if command == "dump" else "rb") as stream:
        return subprocess.run(  # noqa: S603 -- exact owned container and generated database
            args, stdin=subprocess.DEVNULL if command == "dump" else stream,
            stdout=stream if command == "dump" else subprocess.PIPE,
            stderr=subprocess.PIPE, check=True, timeout=60)


def _manifest(directory):
    root = Path(__file__).resolve().parents[2]
    files = [root / "deploy/ory/kratos/kratos.dev.yaml",
             root / "deploy/ory/kratos/person.schema.json",
             root / "deploy/ory/keto/keto.dev.yaml", root / "deploy/ory/keto/namespaces.ts"]
    value = {"images": [KRATOS_IMAGE, KETO_IMAGE],
             "configuration": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in files},
             "files": {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in directory.rglob("*") if p.is_file()}}
    target = directory / "manifest.json"
    target.touch(mode=0o600)
    target.write_text(json.dumps(value, indent=2))
    return value


async def _restored_readback(world, engine, services, ids, client):
    survivor, second, discord, telegram, operator = ids
    for account in (SIG_A1, DISC, TG):
        assert (await services.bindings.record_for(account=account)).person_id == survivor
    assert await services.bindings.record_for(account=SIG_A2) is None
    assert (await services.bindings.record_for(account=OP)).person_id == operator
    for former in (second, discord, telegram):
        assert await services.bindings.canonical_person(former) == survivor
        state = await services.registry.identity_state(external_id=former)
        assert state.verdict is Verdict.DENIED
    assert (await services.registry.identity_state(external_id=survivor)).verdict is Verdict.ALLOWED
    for scope, allowed in ((ROOM_A, True), (ROOM_B, True), (ROOM_C, False)):
        assert (await services.authority.check(
            scope=scope, person_id=survivor, permit="view")).allowed is allowed
    assert not (await services.authority.check(
        scope=ROOM_A, person_id=survivor, permit="manage_members")).allowed
    assert await services.bindings.identity_held(operator) is True
    held = await client.post(CHECK, data=b"", headers=world.session_headers(OP, path=CHECK))
    assert held.status == 503
    blocked = await PortalScopes(services.bindings, services.topology, services.authority).snapshot(
        survivor)
    assert isinstance(blocked, CheckResult) and blocked.verdict is Verdict.UNAVAILABLE
    await services.reconcile()
    assert await services.bindings.identity_held(operator) is True
    released = await services.bindings.release_identity_hold(
        person_id=operator, operation_id="generated-restore-foreign-hold")
    assert released.status is OutcomeStatus.COMPLETE
    assert await services.bindings.identity_held(operator) is False
    portal = await PortalScopes(services.bindings, services.topology, services.authority).snapshot(
        survivor)
    assert portal.rooms == frozenset({("signal", "generated-room-a"),
                                      ("signal", "generated-room-b")})
    for account, expected in ((SIG_A1, 200), (SIG_A2, 403)):
        response = await client.post(CHECK, data=b"", headers=world.session_headers(
            account, path=CHECK))
        assert response.status == expected, await response.text()


async def test_coordinated_backup_restores_linked_identity_and_revocation(
        world, ory, pg_admin, _pg_container, aiohttp_client, tmp_path):
    ids = await _seed_completed_journey(world)
    held = await world.services.bindings.hold_identity(
        person_id=ids[-1], operation_id="generated-restore-foreign-hold")
    assert held.status is OutcomeStatus.COMPLETE
    expected = await _durable_rows(world.engine)
    assert len(expected["managed_link_request"]) == 3
    assert len(expected["managed_link_decision"]) == 6
    source_url = world.engine.url.render_as_string(hide_password=False)
    await world.client.close()
    await world.engine.dispose()
    archive = tmp_path / "coordinated"
    archive.mkdir(mode=0o700)
    snapshot_ory(ory, archive)
    dump = archive / "nora.dump"
    dump.touch(mode=0o600)
    _pg_tool("dump", source_url, dump, _pg_container)
    manifest = _manifest(archive)
    assert manifest["files"]["nora.dump"]
    policy_path = tmp_path / "restored-policy.toml"
    policy_path.write_text(POLICY_TOML)
    with _pg_database(pg_admin, None) as target_url, ory_stack(archive) as restored:
        assert target_url != source_url
        assert restored.containers != ory.containers
        assert restored.kratos_admin != ory.kratos_admin
        _pg_tool("restore", target_url, dump, _pg_container)
        engine = create_sql_engine(target_url)
        try:
            assert await _durable_rows(engine) == expected
            async with aiohttp.ClientSession() as session:
                services = wire.managed_services(
                    managed=ManagedOryConfig(kratos_admin_url=restored.kratos_admin,
                        kratos_public_url=restored.kratos_public,
                        keto_read_url=restored.keto_read, keto_write_url=restored.keto_write,
                        timeout_seconds=15.0),
                    session=session, policy=load_policy(policy_path), engine=engine,
                    credentials={})
                app = web.Application()
                register_session_routes(app, sessions=AccountSessions(
                    services.bindings, services.registry),
                    verifier=AuthenticationVerifier([GATEWAY]))
                client = await aiohttp_client(app)
                await _restored_readback(world, engine, services, ids, client)
                await client.close()
                _write_readback(expected, manifest)
        finally:
            await engine.dispose()


def _write_readback(rows, manifest):
    folder = Path("/tmp/nora-managed-intake/restore-proof")  # noqa: S108 -- generated evidence
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = {"independent_database_and_volumes": True,
             "original_ory_remained_stopped": True,
             "public_tables": {name: {"rows": len(values),
                 "sha256": hashlib.sha256(json.dumps(values).encode()).hexdigest()}
                 for name, values in rows.items()},
             "archive_manifest": manifest,
             "restored_behavior": ["canonical accounts and retired redirects",
                 "restriction overrides grant", "unlinked session denied",
                 "foreign hold retained across reconciliation and refuses session/portal",
                 "exact owner release restores portal access"]}
    target = folder / "readback.json"
    target.touch(mode=0o600)
    target.write_text(json.dumps(value, indent=2))
