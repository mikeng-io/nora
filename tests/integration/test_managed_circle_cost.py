"""Measured current-circle reads over generated SQL, directory HTTP and pinned Ory."""

import asyncio
import json
import time
from collections import Counter
from dataclasses import replace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import sqlalchemy as sa
from aiohttp import ClientSession, TraceConfig, web
from aiohttp.test_utils import TestServer

from nora.agent.room import Room
from nora.config_identity import ManagedOryConfig
from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.domain.presence_circles import CircleUnavailableError
from nora.infra.presence_circles import HttpCircleObservations
from nora.location.adapters.selected_presence import SelectedPresenceStore
from nora.location.adapters.tools import build
from nora.obs.metrics import Metrics
from nora.people import managed_services
from nora.policy import load_policy
from nora.presence.__main__ import _metrics_app
from nora.testing import FakeRosterSource
from tests.integration.managed_ory import ory as ory
from tests.integration.test_managed_presence import CIRCLE, MEMBER, PERSON, _row
from tests.integration.test_managed_presence import case as case
from tests.integration.test_managed_presence import location as location


async def _seed(case, location, services, population):
    ids, provider_rows = [], []
    now = int(time.time() * 1000)
    for index in range(population):
        person = PERSON if index == 0 else f"generated-cost-{population}-{index}"
        member = MEMBER if index == 0 else f"generated-cost-member-{population}-{index}"
        ids.append(person)
        if index:
            await case.bindings.register(person=ManagedPerson(person, PersonLifecycle.ACTIVE,
                display_name=f"Generated Cost Identity {index}"),
                account=ManagedAccount(str(uuid4()), person, "life360",
                    frozenset({Identifier("life360", "member_id", member)}),
                    AccountState.BOUND))
            await case.registry.ensure_identity(external_id=person, traits={})
        outcome, remote = await services.registry.ensure_identity(external_id=person,
            traits={"display_name": f"Generated Cost Identity {index}", "social": True})
        assert outcome.status.value == "complete" and remote
        await case.bindings.correlate_kratos(person_id=person, kratos_identity_id=remote)
        await case.bindings.set_membership(person_id=person, scope=location.room_scope,
            member=True, source="generated-cost")
        assert (await services.backend.put_relation(scope=location.room_scope,
            relation="member", person_id=person)).status.value == "complete"
        provider_rows.append(_row(member, ts=now))
    case.body = {"members": provider_rows}
    await case.collector.collect_once(case.circle, now_ms=now)
    return ids


@pytest.mark.parametrize("population", [16, 128])
async def test_current_reads_measure_real_requests_and_recheck_remote_lifecycle(
    case, location, ory, tmp_path, population,
):
    sql, requests, directory = Counter(), Counter(), Counter()
    unavailable = False
    ports = {urlsplit(ory.kratos_admin).port: "kratos", urlsplit(ory.keto_read).port: "keto",
             urlsplit(ory.keto_write).port: "keto"}

    async def on_request(_session, _context, params):
        requests[f"{ports[params.url.port]}:{params.method}"] += 1

    def on_sql(_conn, _cursor, statement, _params, _context, _many):
        sql[statement.split()[0]] += 1

    @web.middleware
    async def count_directory(request, handler):
        directory[request.path] += 1
        return web.Response(status=503) if unavailable else await handler(request)

    app = _metrics_app(Metrics(), case.collector, directory_key="generated-cost-directory-key")
    app.middlewares.append(count_directory)
    path = tmp_path / "generated-cost-policy.toml"
    path.write_text('[[conversations]]\nplatform="signal"\nid="generated-location-room"\n'
                    'label="Generated Cost Room"\nunit="generated-location-unit"\npersons="*"\n')
    trace = TraceConfig()
    trace.on_request_start.append(on_request)
    async with TestServer(app) as server, ClientSession(trace_configs=[trace]) as session:
        observations = HttpCircleObservations(base_url=str(server.make_url("/")),
            key="generated-cost-directory-key", circles=(CIRCLE,), ttl_ms=180_000)
        managed = ManagedOryConfig(kratos_admin_url=ory.kratos_admin,
            kratos_public_url=ory.kratos_public, keto_read_url=ory.keto_read,
            keto_write_url=ory.keto_write)
        services = managed_services(managed=managed, session=session, policy=load_policy(path),
            engine=case.engine, credentials={}, circle_observations=observations)
        ids = await _seed(case, location, services, population)
        measurements = {}
        sa.event.listen(case.engine.sync_engine, "before_cursor_execute", on_sql)
        try:
            async def measure(label, read):
                sql.clear()
                requests.clear()
                directory.clear()
                started = time.perf_counter()
                result = await read()
                measurements[label] = {"sql": dict(sql), "ory_http": dict(requests),
                    "directory_http": sum(directory.values()),
                    "elapsed_seconds": round(time.perf_counter() - started, 4)}
                assert measurements[label]["directory_http"] >= 1
                return result

            async def refs():
                return await services.access.life360_refs(scope=location.room_scope)

            first = await measure("first-scoped-read", refs)
            assert {ref.person_id for _, ref in first} == set(ids)
            second = await measure("overlapping-scoped-read", refs)
            assert second == first
            snapshot = await measure("lineage", lambda: services.access.lineage_snapshot(
                (location.room_scope,)))
            assert snapshot.members[location.room_scope.id] == frozenset(ids)
            assert snapshot.circles == (frozenset(ids),)
            assert measurements["lineage"]["ory_http"].get("keto:GET", 0) > 0
            assert measurements["first-scoped-read"]["ory_http"].get("kratos:GET", 0) > 0
            room = Room(platform="signal", conversation_id="generated-location-room",
                access=services.access, roster_source=FakeRosterSource(groups={
                    "generated-location-room": (frozenset({"+85290000140"}),)}))
            deps = replace(location.deps, room=room, persons=services.access.persons_view(),
                presence_source=SelectedPresenceStore(case.engine, access=services.access))
            tools = {tool.name: tool.call for tool in build(deps)}

            async def where_is():
                return await tools["where_is"]("Generated Global Presence")

            assert (await measure("where-is-tool", where_is))["status"] == "ok"
            entered, release = asyncio.Event(), asyncio.Event()

            class ParkedFix(SelectedPresenceStore):
                async def latest_fix(self, **kwargs):
                    row = await super().latest_fix(**kwargs)
                    entered.set()
                    await release.wait()
                    return row

            parked = {tool.name: tool.call for tool in build(replace(deps,
                presence_source=ParkedFix(case.engine, access=services.access)))}

            async def interrupted():
                read = asyncio.create_task(parked["where_is"]("Generated Global Presence"))
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    async with services.bindings.mutation():
                        await services.bindings.hold_identity(person_id=PERSON,
                            operation_id="generated-cost-concurrent-hold")
                finally:
                    release.set()
                    result = await asyncio.wait_for(read, 10)
                return result

            held = await measure("where-is-concurrent-hold", interrupted)
            assert held["status"] == "unavailable" and "lat" not in held
            await services.bindings.release_identity_hold(person_id=PERSON,
                operation_id="generated-cost-concurrent-hold")
            await services.registry.set_state(external_id=ids[-1], active=False)
            inactive = await measure("remote-inactive", refs)
            assert {ref.person_id for _, ref in inactive} == set(ids[:-1])
            unavailable = True
            with pytest.raises(CircleUnavailableError):
                await refs()
            assert (await measure("where-is-directory-outage", where_is))["status"] == "unavailable"
            unavailable = False
            await services.registry.set_state(external_id=ids[-1], active=True)
            assert {ref.person_id for _, ref in await measure("recovered", refs)} == set(ids)
        finally:
            sa.event.remove(case.engine.sync_engine, "before_cursor_execute", on_sql)
        print("CURRENT_CIRCLE_COST " + json.dumps({"population": population,
                                                  "measurements": measurements}, sort_keys=True))
