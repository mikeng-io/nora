"""The no-delivery inversion, tested at its four seams:

the reserved config keeps every delivery loop out of the composed
subject; the landed transports keep every adapter's sends and reacts
real-but-grounded; the egress guard is the backstop that enumerates
what composed; and everything after composition — the refusal itself
included — still closes the subject, inside one Honcho draw per case.

Offline: the only composition is `obtain`'s, monkeypatched to a fake
subject; the transports, composition steps and background shapes are
the production classes.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from nora.subject.eval_evidence import EvidenceError


def _subject(**deps):
    registry = SimpleNamespace(adapters=deps.pop("adapters", {}))
    base = {"bridge_runtime": None, "fire_path": None, "dispatcher": None,
            "social_poller": None, "computer_lifecycle": None,
            "background": SimpleNamespace(_tasks=set())}
    base.update(deps)
    return SimpleNamespace(deps=SimpleNamespace(registry=registry, **base))


# --------------------------------------------------------------------------
# the subject is composed so it cannot deliver
# --------------------------------------------------------------------------


def test_the_reserved_overrides_remove_every_delivery_path():
    from scripts.cognition_loop.run import reserved_overrides

    overrides = reserved_overrides("nora-eval-run-case-x")
    for key in ("computer.enabled", "computer.scoped.enabled",
                "heartbeat.enabled", "compute.enabled",
                "participation.enabled", "relevance_gate.enabled",
                "presence.enabled", "social.instagram.enabled",
                "social.instagram.summons.enabled", "social.poll.enabled"):
        assert overrides[key] is False, key
    for platform in ("signal", "discord", "whatsapp", "instagram"):
        assert overrides[f"platforms.{platform}.mode"] == "direct"
    assert overrides["honcho.workspace_id"] == "nora-eval-run-case-x"


async def test_an_arm_override_cannot_set_a_reserved_key(monkeypatch, tmp_path):
    from scripts.cognition_loop import run as run_mod

    async def obtain_never(spec):
        raise AssertionError("obtain ran on a refused arm")

    monkeypatch.setattr(run_mod, "obtain", obtain_never)
    room = SimpleNamespace(platform="signal", primary_id="room-1")
    for key in ("honcho.workspace_id", "social.poll.enabled",
                "heartbeat.enabled", "platforms.signal.mode"):
        args = SimpleNamespace(workdir=str(tmp_path), overrides={key: "x"})
        with pytest.raises(ValueError, match="reserved"):
            await run_mod._compose(
                args, "postgresql+asyncpg://x", 1_700_000_000_000, room,
                "policy", "identity", workspace="nora-eval-run-case-x")


async def test_an_arm_override_cannot_select_the_shared_eval_workspace(
    monkeypatch, tmp_path,
):
    """The shared `<deployment>-eval` workspace is a RESERVED key: naming
    it in an arm override refuses before `obtain` runs."""
    from scripts.cognition_loop import run as run_mod

    async def obtain_never(spec):
        raise AssertionError("obtain ran on a refused arm")

    monkeypatch.setattr(run_mod, "obtain", obtain_never)
    args = SimpleNamespace(
        workdir=str(tmp_path),
        overrides={"honcho.workspace_id": "nora-eval"},
    )
    with pytest.raises(ValueError, match="reserved"):
        await run_mod._compose(
            args, "postgresql+asyncpg://x", 1_700_000_000_000,
            SimpleNamespace(platform="signal", primary_id="room-1"),
            "policy", "identity", workspace="nora-eval-run-case-x")


# --------------------------------------------------------------------------
# every person-visible transport lands
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# the loops the reserved config keeps unborn
# --------------------------------------------------------------------------


def test_a_queued_summons_card_causes_no_network_call(monkeypatch):
    """A card awaiting flush does not resurrect its consumer: the
    composition step builds no consumer and no Instagram client when the
    reserved gates are off — even with a runner and subjects to use."""
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
    path — which the reserved config never composes — so the job stays
    unsaid and no task is spawned."""
    from nora.app.composition_fire import _path_lost_jobs

    spawned: list[Any] = []
    background = SimpleNamespace(spawn=spawned.append)
    _path_lost_jobs(
        metrics=None, background=background, lost_jobs=[object()],
        slots=None, allowlist=None, fire_path=None)
    assert spawned == []

    # Positive control: the same job DOES spawn recovery with a fire path.
    _path_lost_jobs(
        metrics=None, background=background, lost_jobs=[object()],
        slots=None, allowlist=None, fire_path=SimpleNamespace(speak=None))
    assert len(spawned) == 1
    spawned.pop().close()


# --------------------------------------------------------------------------
# the guard is a backstop, and it is fail-closed
# --------------------------------------------------------------------------


def _task(qualname_owner: type, method: str):
    task = asyncio.ensure_future(getattr(qualname_owner(), method)())
    return task


class SummonsConsumer:
    async def run(self):
        await asyncio.sleep(0)


class ReloadFiles:
    async def run(self):
        await asyncio.sleep(0)


async def test_the_guard_refuses_a_summons_consumer_task():
    from scripts.cognition_loop.egress import assert_no_egress

    task = _task(SummonsConsumer, "run")
    try:
        subject = _subject(background=SimpleNamespace(_tasks={task}))
        with pytest.raises(EvidenceError, match=r"SummonsConsumer\.run"):
            assert_no_egress(subject)
    finally:
        task.cancel()
        await asyncio.sleep(0)


async def test_the_guard_refuses_an_unknown_background_task():
    from scripts.cognition_loop.egress import assert_no_egress

    class Heartbeat:
        async def run(self):
            await asyncio.sleep(0)

    task = _task(Heartbeat, "run")
    try:
        subject = _subject(background=SimpleNamespace(_tasks={task}))
        with pytest.raises(EvidenceError, match=r"Heartbeat\.run"):
            assert_no_egress(subject)
    finally:
        task.cancel()
        await asyncio.sleep(0)


async def test_the_guard_accepts_the_config_watcher_task():
    from scripts.cognition_loop.egress import assert_no_egress

    task = _task(ReloadFiles, "run")
    try:
        subject = _subject(background=SimpleNamespace(_tasks={task}))
        report = assert_no_egress(subject)
        assert report["background"] == ["ReloadFiles.run"]
    finally:
        task.cancel()
        await asyncio.sleep(0)


def test_the_guard_refuses_when_background_cannot_be_enumerated():
    from scripts.cognition_loop.egress import assert_no_egress

    with pytest.raises(EvidenceError, match="cannot be enumerated"):
        assert_no_egress(_subject(background=None))
    with pytest.raises(EvidenceError, match="cannot be enumerated"):
        assert_no_egress(_subject(background=SimpleNamespace(_tasks=[])))


def test_a_live_reactor_is_a_refusal():
    from scripts.cognition_loop.egress import assert_no_egress

    class LiveSignalReactor:
        pass

    subject = _subject(adapters={
        "signal": SimpleNamespace(_reactor=LiveSignalReactor())})
    with pytest.raises(EvidenceError, match="real platform transports"):
        assert_no_egress(subject)


# --------------------------------------------------------------------------
# teardown on every exit
# --------------------------------------------------------------------------


async def test_a_refusal_still_closes_the_subject(monkeypatch, tmp_path):
    """The egress refusal happens INSIDE the wake's try/finally: the
    subject's teardown runs even when the guard stops the wake."""
    from scripts.cognition_loop import run as run_mod

    class RealSender:
        pass

    closed: list[str] = []

    async def cancel_all():
        closed.append("background cancelled")

    subject = _subject(
        adapters={"signal": SimpleNamespace(_sender=RealSender())})
    subject.deps.background = SimpleNamespace(
        _tasks=set(), cancel_all=cancel_all)
    subject.deps.audit = None
    subject.deps.client = None
    subject.deps.model_clients = []

    async def fake_obtain(spec):
        return subject

    monkeypatch.setattr(run_mod, "obtain", fake_obtain)
    args = SimpleNamespace(workdir=str(tmp_path), overrides={},
                           policy=tmp_path / "p", identity=tmp_path / "i")
    (tmp_path / "p").write_text("")
    (tmp_path / "i").write_text("")
    with pytest.raises(EvidenceError):
        await run_mod._run_wake(
            args, case=SimpleNamespace(unit="u"), index=0, wake_id=1,
            at_ms=1, url="postgresql+asyncpg://x", reason="user_message",
            judges=None, evidence=None, output=tmp_path,
            room=SimpleNamespace(platform="signal", primary_id="r"),
            workspace="nora-eval-run-case-x")
    assert closed == ["background cancelled"]


# --------------------------------------------------------------------------
# one Honcho draw per case
# --------------------------------------------------------------------------


def _run_stubs(monkeypatch, workspaces: list[str], wake_error=None):
    from scripts.cognition_loop import run as run_mod

    created, deleted = [], []

    @contextlib.asynccontextmanager
    async def fake_draw(case, *, run_id, admin=None):
        draw = SimpleNamespace(id=f"{run_id}-draw")
        created.append(draw.id)
        try:
            yield draw
        finally:
            deleted.append(draw.id)

    async def fake_recorded_wake(source, wake_id):
        return 1_700_000_000_000 + wake_id, SimpleNamespace(unit="u")

    async def fake_reason(source, wake_id):
        return "user_message"

    @contextlib.asynccontextmanager
    async def fake_sandbox(url, first):
        yield "postgresql+asyncpg://sandbox", None

    async def noop(*a, **k):
        return None

    async def fake_advance(*a, **k):
        return SimpleNamespace(wrote_only_record=lambda: True, written={})

    async def dispose():
        return None

    monkeypatch.setattr(run_mod, "draw_workspace", fake_draw)
    monkeypatch.setattr(run_mod, "recorded_wake", fake_recorded_wake)
    monkeypatch.setattr(run_mod, "_wake_reason", fake_reason)
    monkeypatch.setattr(run_mod, "sandbox_as_of", fake_sandbox)
    monkeypatch.setattr(run_mod, "bump_sequences", noop)
    monkeypatch.setattr(run_mod, "advance_record", fake_advance)
    monkeypatch.setattr(run_mod, "create_async_engine",
                        lambda url: SimpleNamespace(dispose=dispose))

    async def fake_run_wake(*a):
        workspaces.append(a[-1])
        if wake_error is not None:
            raise wake_error
        return {"status": "pass", "expectations": []}

    monkeypatch.setattr(run_mod, "_run_wake", fake_run_wake)
    return created, deleted


def _case_args(tmp_path):
    case = SimpleNamespace(name="c", unit="u", wakes=[1, 2], expectations=[])
    policy = SimpleNamespace(rooms=lambda: [
        SimpleNamespace(unit="u", platform="signal", primary_id="r")])
    evidence = SimpleNamespace(
        case=lambda *a, **k: contextlib.nullcontext())
    args = SimpleNamespace(
        database_url="postgresql+asyncpg://source", run_id="r1")
    return args, case, policy, evidence


async def test_the_case_lives_inside_one_honcho_draw(monkeypatch, tmp_path):
    """Both wakes see the SAME draw id, the draw is made before the first
    wake composes, and it is deleted when the case exits."""
    from scripts.cognition_loop import run as run_mod

    workspaces: list[str] = []
    created, deleted = _run_stubs(monkeypatch, workspaces)
    args, case, policy, evidence = _case_args(tmp_path)
    await run_mod.run_case(args, case, source=None, judges=None,
                           policy=policy, evidence=evidence, output=tmp_path)
    assert created == ["r1-draw"] and deleted == ["r1-draw"]
    assert workspaces == ["r1-draw", "r1-draw"]


async def test_the_draw_is_deleted_when_a_wake_fails(monkeypatch, tmp_path):
    from scripts.cognition_loop import run as run_mod

    workspaces: list[str] = []
    _created, deleted = _run_stubs(
        monkeypatch, workspaces, wake_error=RuntimeError("wake blew up"))
    args, case, policy, evidence = _case_args(tmp_path)
    report = await run_mod.run_case(
        args, case, source=None, judges=None,
        policy=policy, evidence=evidence, output=tmp_path)
    assert deleted == ["r1-draw"]
    assert report["status"] == "ERROR"
