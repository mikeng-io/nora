"""Managed journal recovery through the actual process entry and owned SQL/Ory."""

import asyncio
import contextlib
import tomllib

import asyncpg
import pytest
import tomli_w

from nora.__main__ import build_app
from nora.domain.identity import Identifier
from nora.domain.managed import OperationJournalState, OutcomeStatus
from nora.domain.management import (
    AccountObservation,
    Actor,
    ActorKind,
    Operation,
    Target,
    TargetKind,
)
from tests.integration.managed_ory import ory as ory
from tests.test_main import env as env
from tests.unit.test_managed_profile import _authentication


@pytest.mark.fresh_database
async def test_a_restarted_process_recovers_a_profile_without_roster_collections(
    env, clean_pg_url, ory,
):
    values = tomllib.loads(env.read_text())
    values['identity'] = {
        'backend': 'managed',
        'managed': {'kratos_admin_url': ory.kratos_admin,
                    'keto_read_url': ory.keto_read, 'keto_write_url': ory.keto_write},
    }
    values['dreamer']['enabled'] = False
    values.setdefault('cognition', {})['enabled'] = False
    values.setdefault('participation', {})['enabled'] = False
    env.write_text(tomli_w.dumps(values))
    connection = await asyncpg.connect(clean_pg_url.replace('postgresql+asyncpg:', 'postgresql:'))
    app = await build_app(env)
    try:
        services = app.deps.managed_services
        account = Identifier('discord', 'user_id', 'generated-recovery-account')
        registered = await services.registration.apply(Operation(
            'generated-recovery-register', 'observe_account',
            Actor(ActorKind.SERVICE, service_id='generated-discovery', sources={'discord'}),
            Target(TargetKind.ACCOUNT, account=account),
            AccountObservation(frozenset({account})), {'ownership': '0'}))
        assert registered.status is OutcomeStatus.COMPLETE
        binding = await services.bindings.record_for(account=account)
        assert binding is not None
        await connection.execute('''
            CREATE FUNCTION generated_reject_projection() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'generated projection outage'; END $$;
            CREATE TRIGGER generated_reject_projection BEFORE UPDATE OF display_name
            ON managed_person FOR EACH ROW EXECUTE FUNCTION generated_reject_projection();
        ''')
        outcome = await services.profile.update_display_name(
            await _authentication(services.bindings, account=account),
            operation_id='generated-recover-name', display_name='Generated Recovery Name')
        assert outcome.status is OutcomeStatus.INCOMPLETE
        record = await services.bindings.operation('generated-recover-name')
        assert record.state is OperationJournalState.INCOMPLETE
        person = await services.bindings.person(binding.person_id)
        assert person.display_name != 'Generated Recovery Name'
        await connection.execute('DROP TRIGGER generated_reject_projection ON managed_person')
    finally:
        await app.shutdown()

    # PostgreSQL delivers NOTIFY after the completion transaction commits.
    recovered = asyncio.Event()
    await connection.add_listener('generated_recovery_complete', lambda *args: recovered.set())
    await connection.execute('''
        CREATE FUNCTION generated_notify_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.operation_id = 'generated-recover-name' AND NEW.state = 'complete' THEN
                PERFORM pg_notify('generated_recovery_complete', 'complete');
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER generated_notify_recovery AFTER UPDATE ON managed_operation
        FOR EACH ROW EXECUTE FUNCTION generated_notify_recovery();
    ''')
    app = await build_app(env)
    run = asyncio.create_task(app.run())
    try:
        assert app.deps.onboarding is None
        try:
            await asyncio.wait_for(recovered.wait(), timeout=10)
        except TimeoutError:
            assert recovered.is_set(), "startup did not commit the pending profile recovery"
        services = app.deps.managed_services
        record = await services.bindings.operation('generated-recover-name')
        assert record.state is OperationJournalState.COMPLETE
        person = await services.bindings.person(binding.person_id)
        assert person.display_name == 'Generated Recovery Name'
        outcome, traits = await services.registry.identity_traits(external_id=binding.person_id)
        assert outcome.status is OutcomeStatus.COMPLETE
        assert traits['display_name'] == 'Generated Recovery Name'
    finally:
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        await app.shutdown()
        await connection.close()
