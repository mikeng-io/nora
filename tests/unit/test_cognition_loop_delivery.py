"""Transport helper protocols and case-owned replay scope failure paths."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest


async def test_the_direct_signal_adapter_lands_every_send_and_react(
    monkeypatch,
):
    """A REAL SignalAdapter built inside `landed_platform_transports`:
    its send and react answer as the platform would, and any attempt to
    open an HTTP session fails the test."""
    import aiohttp

    from nora.domain.models import ConversationTarget
    from nora.platform.signal.adapter import SignalAdapter
    from nora.subject.sandbox_platforms import landed_platform_transports

    monkeypatch.setattr(
        aiohttp, "ClientSession",
        lambda *a, **k: pytest.fail("a transport opened an HTTP session"))
    group = ConversationTarget(
        platform="signal", conversation_id="g", conversation_type="group")
    with landed_platform_transports():
        adapter = SignalAdapter("http://127.0.0.1:1", "+15550000000",
                                metrics=None)
    sent = await adapter.send(target=group, text="hi")
    reacted = await adapter.react(
        target=group, emoji="👀", to_author="a", to_timestamp=1)
    assert sent.ok and reacted.ok
    # The transport's own refusals stay real — a dm react is still refused.
    dm = ConversationTarget(
        platform="signal", conversation_id="d", conversation_type="dm")
    refused = await adapter.react(
        target=dm, emoji="👀", to_author="a", to_timestamp=1)
    assert not refused.ok
    assert type(adapter._sender).__name__.startswith("Landed")
    assert type(adapter._reactor).__name__.startswith("Landed")


async def test_every_platform_adapter_is_patched():
    """All four adapters' sender AND reactor resolve to a `Landed*` class
    inside the block, and the production classes are restored after it."""
    import importlib

    from nora.subject import sandbox_platforms as sp

    originals = {
        (m, a): getattr(importlib.import_module(m), a)
        for m, a, _ in sp._PATCHES
    }
    with sp.landed_platform_transports():
        for module_name, attr, _ in sp._PATCHES:
            landed = getattr(importlib.import_module(module_name), attr)
            assert landed.__name__.startswith("Landed"), (module_name, attr)
    for (module_name, attr), original in originals.items():
        assert getattr(importlib.import_module(module_name), attr) is original


def test_a_queued_summons_card_causes_no_network_call(monkeypatch):
    """A card awaiting flush does not resurrect its consumer: the
    composition step builds no consumer and no Instagram client when the
    feature is explicitly off — even with a runner and subjects to use."""
    from nora.app import composition_social

    cfg = SimpleNamespace(social=SimpleNamespace(instagram=SimpleNamespace(
        enabled=False, summons=SimpleNamespace(enabled=False))))
    wiring = composition_social.inbox(cfg, metrics=None)
    assert wiring.summons_wired is False
    # The intake is closed too: a card landing now is counted `disabled`.
    assert wiring.summons_inbox.enabled is False

    monkeypatch.setattr(
        "nora.infra.social.summons_client.InstagramSummonsClient",
        lambda *a, **k: pytest.fail("a summons client was built"))
    spawned: list[Any] = []
    background = SimpleNamespace(spawn=spawned.append)
    composition_social.summons(
        background=background, cfg=cfg, clock=None, content_checks=None,
        doctrine_store=None, engine=None, limits=None, metrics=None,
        persons=None, registry=None,
        social_subjects=object(), summons_inbox=wiring.summons_inbox,
        summons_runner=object(), summons_wired=wiring.summons_wired,
        tier=None, tool_registry=None)
    assert spawned == []


def test_a_lost_compute_job_cannot_reach_speak():
    """Recovery of a lost compute job reaches `speak` only through a fire
    path; a missing fire path leaves the job unsaid and spawns no task."""
    from nora.app.composition_fire import _path_lost_jobs

    spawned: list[Any] = []
    background = SimpleNamespace(spawn=spawned.append)
    _path_lost_jobs(
        metrics=None, background=background, lost_jobs=[object()],
        slots=None, allowlist=None, fire_path=None, access=None)
    assert spawned == []

    # Positive control: the same job DOES spawn recovery with a fire path.
    _path_lost_jobs(
        metrics=None, background=background, lost_jobs=[object()],
        slots=None, allowlist=None, fire_path=SimpleNamespace(speak=None), access=None)
    assert len(spawned) == 1
    spawned.pop().close()



def _run_stubs(monkeypatch, environments: list, wake_error=None,
               advance_error=None):
    from evals.cognition_loop import run as run_mod

    created, deleted = [], []

    @contextlib.asynccontextmanager
    async def services(url, *, redis):
        environment = SimpleNamespace(active=True)
        created.append(environment)
        try:
            yield environment
        finally:
            environment.active = False
            deleted.append(environment)

    recording = SimpleNamespace(open=services)

    async def fake_recorded_wake(source, wake_id):
        return (1_700_000_000_000 + wake_id,
                SimpleNamespace(unit="u", pending=()))

    async def fake_reason(source, wake_id):
        return "user_message"

    async def fake_session(source, wake_id):
        return False

    @contextlib.asynccontextmanager
    async def fake_sandbox(url, first):
        yield "postgresql+asyncpg://sandbox", None

    async def noop(*a, **k):
        return None

    async def fake_advance(*a, **k):
        if advance_error is not None:
            raise advance_error
        return SimpleNamespace(wrote_only_record=lambda: True, written={})

    async def dispose():
        return None

    monkeypatch.setattr(run_mod, "recorded_wake", fake_recorded_wake)
    monkeypatch.setattr(run_mod, "_wake_reason", fake_reason)
    monkeypatch.setattr(run_mod, "_recorded_session", fake_session)
    monkeypatch.setattr(run_mod, "sandbox_as_of", fake_sandbox)
    monkeypatch.setattr(run_mod, "bump_sequences", noop)
    monkeypatch.setattr(run_mod, "advance_record", fake_advance)
    monkeypatch.setattr(run_mod, "create_async_engine",
                        lambda url: SimpleNamespace(dispose=dispose))

    async def fake_run_wake(*a, **k):
        environments.append(a[0].environment)
        if wake_error is not None:
            raise wake_error
        return {"status": "PASS", "expectations": []}

    monkeypatch.setattr(run_mod, "_run_wake", fake_run_wake)
    return created, deleted, recording


def _case_args(tmp_path, recording):
    case = SimpleNamespace(name="c", unit="u", wakes=[1, 2], expectations=[])
    policy = SimpleNamespace(rooms=lambda: [
        SimpleNamespace(unit="u", platform="signal", primary_id="r")])
    evidence = SimpleNamespace(
        case=lambda *a, **k: contextlib.nullcontext())
    args = SimpleNamespace(
        database_url="postgresql+asyncpg://source", run_id="r1",
        service_inputs=SimpleNamespace(corpora={"cognition-loop": {"c": recording}}))
    return args, case, policy, evidence


async def test_the_case_lives_inside_one_service_scope(monkeypatch, tmp_path):
    """Both wakes use one scope, closed after the case on every exit."""
    from evals.cognition_loop import run as run_mod

    environments: list = []
    created, deleted, recording = _run_stubs(monkeypatch, environments)
    args, case, policy, evidence = _case_args(tmp_path, recording)
    await run_mod.run_case(args, case, source=None, judges=None,
                           policy=policy, evidence=evidence, output=tmp_path)
    assert len(created) == 1 and deleted == created
    assert environments == [created[0], created[0]]
    assert not created[0].active and args.environment is None


async def test_the_service_scope_closes_when_a_wake_fails(monkeypatch, tmp_path):
    from evals.cognition_loop import run as run_mod

    environments: list = []
    created, deleted, recording = _run_stubs(
        monkeypatch, environments, wake_error=RuntimeError("wake blew up"))
    args, case, policy, evidence = _case_args(tmp_path, recording)
    report = await run_mod.run_case(
        args, case, source=None, judges=None,
        policy=policy, evidence=evidence, output=tmp_path)
    assert deleted == created and not created[0].active
    assert args.environment is None
    assert report["status"] == "ERROR"


async def test_an_advance_failure_freezes_the_wakes_error(
    monkeypatch, tmp_path,
):
    """A failed advance is that wake's ERROR entry — named step, frozen —
    so the report and its HTML have a wake section to show."""
    import json

    from evals.cognition_loop import run as run_mod

    _, _, recording = _run_stubs(monkeypatch, [], advance_error=RuntimeError("pg gone"))
    args, case, policy, evidence = _case_args(tmp_path, recording)
    report = await run_mod.run_case(
        args, case, source=None, judges=None,
        policy=policy, evidence=evidence, output=tmp_path)
    assert report["status"] == "ERROR"
    assert len(report["wakes"]) == 2
    wake = report["wakes"][1]
    assert wake["status"] == "ERROR" and wake["step"] == "advance"
    frozen = json.loads((tmp_path / "wake-1-2.json").read_text())
    assert frozen["error"]["step"] == "advance"
    assert "pg gone" in frozen["error"]["message"]


async def test_replay_rejects_a_room_resolved_into_another_unit(monkeypatch, tmp_path):
    from tests.unit.test_cognition_loop_run_wake import _runner_subject, _wake_args, _wake_seam

    subject = _runner_subject()
    subject.unit = "another-unit"
    module = _wake_seam(monkeypatch, tmp_path, subject)
    args, evidence = _wake_args(tmp_path)
    result = await module._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, 1_800_000_000_000,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path, ("signal", "r"))
    assert result["status"] == "ERROR" and result["step"] == "compose"
    assert "another unit" in result["error"]
