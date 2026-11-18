"""Executed SQL/Ory work for generated community registration and fixed-size arrivals."""

import asyncio
import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import aiohttp
import pytest
from sqlalchemy import event, text

from nora.config_identity import ManagedOryConfig
from nora.domain.identity import Identifier
from nora.domain.managed import ObservedMember, OutcomeStatus
from nora.domain.management import (
    AccountObservation,
    Actor,
    ActorKind,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.infra.sql import create_sql_engine
from nora.people import RosterProvisioning
from nora.people.wire import managed_services
from nora.policy import load_policy
from tests.integration.managed_ory import ory as ory
from tests.unit.test_managed_admission import POLICY

ACTOR = Actor(ActorKind.SERVICE, service_id="generated-cost-bridge", sources={"discord"})
UNIT = Scope(ScopeKind.UNIT, "family")


class WorkMeter:
    def __init__(self):
        self.sql = Counter()
        self.http = Counter()
        self.reads = {}
        self.enabled = False

    def sql_call(self, connection, cursor, statement, parameters, context, many):
        if not self.enabled:
            return
        verb = statement.lstrip().split(None, 1)[0].upper()
        self.sql[verb] += 1
        if verb == "SELECT" and "managed_" in statement:
            self.reads[statement] = parameters

    async def http_call(self, session, context, params):
        if not self.enabled:
            return
        path = params.url.path
        if path.startswith("/admin/identities/by/external/"):
            path = "/admin/identities/by/external/<key>"
        else:
            path = re.sub(r"(/admin/identities)/[^/]+", r"\1/<key>", path)
        self.http[f"{params.method} {path}"] += 1

    async def measure(self, action):
        self.sql.clear()
        self.http.clear()
        self.reads.clear()
        self.enabled = True
        started = time.monotonic()
        try:
            result = await action()
        finally:
            self.enabled = False
        return result, {"seconds": time.monotonic() - started,
                        "sql": dict(self.sql), "http": dict(self.http)}


async def executed_plans(engine, reads):
    plans = []
    async with engine.connect() as connection:
        for statement, parameters in reads.items():
            result = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters)
            value = result.scalar_one()
            value = json.loads(value) if isinstance(value, str) else value
            plans.append({"query_sha256": hashlib.sha256(statement.encode()).hexdigest(),
                          "statement": statement, "plan": value})
    return plans


def identifier(number):
    return Identifier("discord", "user_id", f"generated-cost-{number}")


def observation(number, suffix=""):
    account = identifier(number)
    return Operation(f"generated-arrival-{number}{suffix}", "observe_account", ACTOR,
        Target(TargetKind.ACCOUNT, account=account),
        AccountObservation(frozenset({account})), {"ownership": "0"})


async def sample_community(services, provisioning, meter, engine, population):
    members = tuple(ObservedMember(frozenset({identifier(n)}), "unknown")
                    for n in range(population))
    request = {"operation_id": "generated-community", "actor": ACTOR, "scope": UNIT,
               "collection": "generated-guild", "members": members,
               "complete_roster": True, "observed_ms": int(time.time() * 1000)}
    initial, creation = await meter.measure(lambda: provisioning.apply(**request))
    assert initial.status is OutcomeStatus.COMPLETE
    assert creation["http"].get("POST /admin/identities", 0) == population
    replay, retry = await meter.measure(lambda: provisioning.apply(**request))
    assert replay.status is OutcomeStatus.COMPLETE
    assert not retry["http"]
    request = {**request, "operation_id": "generated-rescan",
               "observed_ms": int(time.time() * 1000) + 1}
    rescanned, rescan = await meter.measure(lambda: provisioning.apply(**request))
    assert rescanned.status is OutcomeStatus.COMPLETE
    assert not rescan["http"].get("POST /admin/identities", 0)

    async def arrivals():
        return await asyncio.gather(*[
            services.registration.apply(observation(population + n, f"-{copy}"))
            for n in range(8) for copy in range(2)])

    arrivals_result, arrival_work = await meter.measure(arrivals)
    assert sum(o.status is OutcomeStatus.COMPLETE for o in arrivals_result) == 8
    assert all(o.status is OutcomeStatus.COMPLETE or (
        o.status is OutcomeStatus.DENIED and o.detail == "stale-ownership-revision")
        for o in arrivals_result)
    assert arrival_work["http"].get("POST /admin/identities", 0) == 8
    plans = await executed_plans(engine, dict(meter.reads))
    assert plans and all(plan["plan"][0]["Plan"]["Actual Loops"] >= 1 for plan in plans)
    async with engine.connect() as connection:
        counts = (await connection.execute(text(
            "SELECT (SELECT count(*) FROM managed_person), "
            "(SELECT count(*) FROM managed_account)"))).one()
    assert tuple(counts) == (population + 8, population + 8)
    memberships = await services.bindings.members_of(UNIT)
    assert len(memberships) == population
    assert not await services.bindings.members_of(Scope(ScopeKind.UNIT, "work"))
    bound = await services.bindings.record_for(account=identifier(population))
    assert bound is not None
    assert not await services.bindings.memberships_for(bound.person_id)
    assert not (await services.authority.check(
        scope=UNIT, person_id=bound.person_id, permit="manage_members")).allowed
    return {"population": population, "initial": creation, "exact_retry": retry,
            "rescan": rescan, "fixed_eight_arrivals": arrival_work,
            "arrival_outcomes": dict(Counter(o.status.value for o in arrivals_result)),
            "executed_arrival_select_plans": plans}


@pytest.mark.parametrize("population", [120, 240])
async def test_real_sql_and_ory_community_work(clean_pg_url, tmp_path, ory, population):
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(POLICY)
    engine = create_sql_engine(clean_pg_url)
    meter = WorkMeter()
    event.listen(engine.sync_engine, "before_cursor_execute", meter.sql_call)
    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(meter.http_call)
    try:
        async with aiohttp.ClientSession(trace_configs=[trace]) as session:
            services = managed_services(managed=ManagedOryConfig(
                kratos_admin_url=ory.kratos_admin, keto_read_url=ory.keto_read,
                keto_write_url=ory.keto_write), session=session,
                policy=load_policy(policy_path), engine=engine, credentials={})
            provisioning = RosterProvisioning(services.bindings, services.registry,
                services.topology, services.registration, services.authority)
            try:
                report = await sample_community(services, provisioning, meter, engine, population)
            finally:
                await services.aclose()
        folder = Path(os.environ.get("NORA_COMMUNITY_EVIDENCE_DIR", str(tmp_path)))
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        (folder / f"community-{population}.json").write_text(json.dumps(report, indent=2))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", meter.sql_call)
        await engine.dispose()
