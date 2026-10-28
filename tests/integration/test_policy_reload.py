"""Edits reach the real composition's next admission without recreating it."""

import asyncio
import os

import pytest

from nora.analytics.composition import build_rollup
from nora.app.composition import build_dependencies
from nora.domain.models import NormalizedMessage
from nora.ingest.stages import scope
from nora.presence.composition import build_collector
from tests.builders import override_config
from tests.test_composition import cfg as cfg


def room(room_id, label):
    return (
        '[[conversations]]\nplatform = "signal"\n'
        f'id = "{room_id}"\nlabel = "{label}"\npersons = "*"\n'
    )


@pytest.fixture
async def running(cfg, monkeypatch):
    monkeypatch.setattr("nora.config_reload.POLL_SECONDS", 0.01)
    deps = await build_dependencies(cfg)
    try:
        yield deps
    finally:
        await deps.background.cancel_all()
        await deps.audit.aclose()


async def reloaded(deps, file, outcome, count=1):
    async with asyncio.timeout(2):
        while deps.metrics.get(  # noqa: ASYNC110 — observe the real asynchronous poller
            "nora_config_reloads_total", file=file, outcome=outcome,
        ) < count:
            await asyncio.sleep(0.005)


async def admitted(deps, room_id):
    return await scope.run(
        message=NormalizedMessage(
            "signal", room_id, "group", "+85212345678", "Miles", 1, "hi", {},
        ),
        policy=deps.ingest._allowlist, persons=deps.persons, metrics=deps.metrics,
    )


async def test_policy_reload_admits_the_next_message_from_a_new_room(running, cfg):
    assert not await admitted(running, "g2")
    old_stat = cfg.policy_file.stat()
    cfg.policy_file.write_text('default = "deny"\n' + room("g1", "First") + room("g2", "Second"))
    os.utime(cfg.policy_file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    await reloaded(running, "policy.toml", "applied")  # positive control: poller ran
    assert await admitted(running, "g2")
    # The Honcho router must know the added room too; admission alone is insufficient.
    assert running.memory._router.unit_of("g2") == "nora"
    assert {r.label for r in running.assembler._lineage.rooms} == {"First", "Second"}
    assert not running.config_reload.status()["policy"]["stale"]


@pytest.mark.parametrize("edit", ["malformed", "missing", "empty"])
async def test_invalid_policy_keeps_the_last_applied_policy_and_counts_refused(running, cfg, edit):
    cfg.policy_file.write_text('default = "deny"\n' + room("g2", "Second"))
    await reloaded(running, "policy.toml", "applied")
    if edit == "missing":
        cfg.policy_file.unlink()
    else:
        cfg.policy_file.write_text('default = "deny"\n' if edit == "empty" else "[[broken")
    await reloaded(running, "policy.toml", "refused")
    assert await admitted(running, "g2")
    assert not await admitted(running, "g1")
    await asyncio.sleep(0.04)
    assert running.metrics.get("nora_config_reloads_total", outcome="refused") == 1
    assert running.config_reload.status()["policy"]["stale"]


async def test_restart_only_policy_change_refuses_the_entire_candidate(running, cfg):
    cfg.policy_file.write_text(
        'default = "deny"\n[units.nora]\nworkspace = "home"\n'
        + room("g2", "Second")
    )
    await reloaded(running, "policy.toml", "restart_required")
    assert await admitted(running, "g1")
    assert not await admitted(running, "g2")
    assert not running.ingest._allowlist.units_declared
    assert running.metrics.get("nora_config_reloads_total", outcome="applied") == 0


async def test_identity_reload_resolves_a_new_person_and_keeps_last_good(running, cfg):
    assert running.persons.resolve("signal", "+85287654321") is None
    cfg.identity_file.write_text(
        '[persons.miles]\nname = "Miles"\n'
        '[persons.miles_old_id]\nname = "Miles"\n'
        '[[persons.miles_old_id.signal]]\nphone = "+85287654321"\n'
    )
    await reloaded(running, "identity.toml", "applied")
    assert running.persons.resolve("signal", "+85287654321").person_id == "miles_old_id"
    cfg.identity_file.write_text('[persons.nora]\nname = "Miles"\n')
    await reloaded(running, "identity.toml", "refused")
    assert running.persons.resolve("signal", "+85287654321").person_id == "miles_old_id"
    assert "nora" not in running.persons.by_person


async def test_identity_binding_change_requires_restart_without_changing_people(running, cfg):
    # A declared unit permits a valid Honcho binding. Establish it before composing.
    await running.background.cancel_all()
    cfg.policy_file.write_text(
        'default = "deny"\n[units.nora]\nworkspace = "home"\n' + room("g1", "First")
    )
    deps = await build_dependencies(cfg)
    try:
        cfg.identity_file.write_text(
            '[workspaces.home]\nhoncho_workspace = "nora-home"\n'
            '[persons.miles_old_id]\nname = "Miles"\n'
        )
        await reloaded(deps, "identity.toml", "restart_required")
        assert "miles" in deps.persons.by_person
        assert "miles_old_id" not in deps.persons.by_person
    finally:
        await deps.background.cancel_all()
        await deps.audit.aclose()


async def test_presence_and_analytics_recompute_their_identity_and_room_scopes(cfg, monkeypatch):
    monkeypatch.setattr("nora.config_reload.POLL_SECONDS", 0.01)
    monkeypatch.setenv("LIFE360_AUTH_TOKEN", "synthetic-token")
    identity = (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.life360]]\nmember_id = "m_alpha"\ncircle_id = "c_one"\n'
    )
    cfg.identity_file.write_text(identity)
    cfg = override_config(cfg, {"presence.enabled": True, "analytics.enabled": True})
    presence, analytics = build_collector(cfg), build_rollup(cfg)
    assert len(presence.collector.circle_summary()) == 1
    assert len(analytics.subjects) == 1
    tasks = [asyncio.create_task(presence.identity_reload.run()),
             asyncio.create_task(analytics.reload_files.run())]
    try:
        cfg.identity_file.write_text(identity + (
            '[persons.miles_old_id]\nname = "Miles"\n'
            '[[persons.miles_old_id.life360]]\nmember_id = "m_beta"\ncircle_id = "c_two"\n'
        ))
        cfg.policy_file.write_text('default = "deny"\n' + room("g2", "Second"))
        await reloaded(presence, "identity.toml", "applied")
        await reloaded(analytics, "identity.toml", "applied")
        await reloaded(analytics, "policy.toml", "applied")
        assert {s.subject_id for s in analytics.runner._subjects} == {"miles", "miles_old_id"}
        assert {s.subject_id for s in analytics.subjects} == {"miles", "miles_old_id"}
        assert presence.collector.circle_summary() == [
            {"circle_id": "c_one", "members": 1}, {"circle_id": "c_two", "members": 1},
        ]
        assert analytics.runner._units._units[0].rooms == (("signal", "g2"),)
        # These are the same startup checks, not a permissive reload-only parser.
        cfg.identity_file.write_text('[persons.miles]\n[[persons.miles.life360]]\nmember_id="m_alpha"')
        await reloaded(presence, "identity.toml", "refused")
        await reloaded(analytics, "identity.toml", "refused")
        assert len(presence.collector.circle_summary()) == 2
        assert len(analytics.subjects) == 2
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await presence.store.aclose()
