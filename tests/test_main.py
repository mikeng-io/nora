import shutil
import tomllib
from pathlib import Path

import pytest
import tomli_w
from aiohttp import web

from nora.__main__ import StartupError, build_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def env(tmp_path, monkeypatch, aiohttp_server, pg_url, example_config_path):
    # `pg_url` creates a fresh Postgres database and exports DATABASE_URL, which
    # the composition root reads (store.database_url_env).
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")

    async def accounts(request):
        return web.json_response(["+85290000100"])

    app = web.Application()
    app.router.add_get("/api/v1/accounts", accounts)
    # `id` is what makes this catch-all a *working* Honcho as well as a working
    # signal-cli: `HonchoClient.ensure_session` reads `id` off the session POST
    # and returns None (so `feed` returns False) without it. Nothing asserts on
    # the extra key; it is what lets a test drive a successful feed end to end.
    app.router.add_route("*", "/{tail:.*}",
                         lambda r: web.json_response({"ok": True, "id": "session-1"}))
    daemon = await aiohttp_server(app)

    with example_config_path.open("rb") as f:
        cfg = tomllib.load(f)
    base = str(daemon.make_url("")).rstrip("/")
    cfg["platforms"]["signal"]["base_url"] = base
    cfg["honcho"]["base_url"] = base
    cfg["store"]["data_dir"] = str(tmp_path)
    cfg["doctrine"]["dir"] = str(ROOT / "nora" / "doctrine")
    # Same reason as the doctrine line above: the config file lives in tmp_path,
    # which has no `nora/dreamer`, and the corpus is validated at startup.
    cfg["dreamer"]["dir"] = str(ROOT / "nora" / "dreamer")
    cfg["identity_file"] = str(tmp_path / "identity.toml")
    cfg["policy_file"] = str(tmp_path / "policy.toml")
    cfg["obs"]["metrics"]["port"] = 0
    (tmp_path / "identity.toml").write_text(
        "[persons.miles]\nname = \"Miles\"\n[[persons.miles.signal]]\nphone = \"+852111\"\n"
    )
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "Z3JvdXA="\nlabel = "group"\npersons = "*"\n'
    )
    path = tmp_path / "config.toml"
    with path.open("wb") as f:
        tomli_w.dump(cfg, f)
    return path


async def test_build_app_wires_everything(env):
    app = await build_app(env)
    try:
        assert app.deps.dispatcher is not None
        assert app.adapter.platform == "signal"
        assert app.account == "+85290000100"
        assert app.doctrine.sha256
    finally:
        await app.shutdown()


async def test_missing_secret_refuses_to_start(env, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(StartupError, match="OMNIROUTE_API_KEY"):
        await build_app(env)


async def test_a_broken_doctrine_refuses_to_start(env, tmp_path):
    doctrine_dir = tmp_path / "doctrine"
    doctrine_dir.mkdir()
    shutil.copy(ROOT / "nora" / "doctrine" / "core" / "identity.md", doctrine_dir / "identity.md")
    (doctrine_dir / "broken.md").write_text("no frontmatter")
    cfg = tomllib.loads(env.read_text())
    cfg["doctrine"]["dir"] = str(doctrine_dir)
    env.write_text(tomli_w.dumps(cfg))
    with pytest.raises(StartupError, match="doctrine"):
        await build_app(env)


async def test_a_broken_dreamer_objective_refuses_to_start(env, tmp_path):
    """The Dreamer's corpus is validated at STARTUP, not lazily on the first
    tick, and the difference is the whole point: `dream_loop` runs every 300s in
    a background task whose failures are per-scope and logged, so a malformed
    objective discovered there is a Dreamer that quietly stops writing rows
    hours after the deploy that broke it. Refused here instead, with the
    filename in the message."""
    dreamer_dir = tmp_path / "dreamer"
    shutil.copytree(ROOT / "nora" / "dreamer", dreamer_dir)
    (dreamer_dir / "objectives" / "broken.md").write_text("no frontmatter")
    cfg = tomllib.loads(env.read_text())
    cfg["dreamer"]["dir"] = str(dreamer_dir)
    env.write_text(tomli_w.dumps(cfg))
    with pytest.raises(StartupError, match=r"broken\.md"):
        await build_app(env)


async def test_a_missing_dreamer_corpus_refuses_to_start(env, tmp_path):
    """Deny-by-default for instructions: an absent tree is not an empty one.
    A Dreamer that came up with no objectives would look identical to a healthy
    one until somebody went looking for rows that were never written."""
    cfg = tomllib.loads(env.read_text())
    cfg["dreamer"]["dir"] = str(tmp_path / "not-there")
    env.write_text(tomli_w.dumps(cfg))
    with pytest.raises(StartupError, match="Dreamer"):
        await build_app(env)


async def test_a_duplicate_person_mapping_refuses_to_start(env, tmp_path):
    (tmp_path / "identity.toml").write_text(
        "[persons.a]\nname = \"A\"\n[[persons.a.signal]]\nphone = \"+1\"\n"
        "[persons.b]\nname = \"B\"\n[[persons.b.signal]]\nphone = \"+1\"\n"
    )
    with pytest.raises(StartupError, match="duplicate"):
        await build_app(env)


async def test_an_undeterminable_account_refuses_to_start(env):
    cfg = tomllib.loads(env.read_text())
    cfg["platforms"]["signal"]["base_url"] = "http://127.0.0.1:1"
    env.write_text(tomli_w.dumps(cfg))
    with pytest.raises(StartupError, match="account"):
        await build_app(env)


async def test_honcho_being_down_does_not_prevent_startup(env):
    cfg = tomllib.loads(env.read_text())
    cfg["honcho"]["base_url"] = "http://127.0.0.1:1"
    env.write_text(tomli_w.dumps(cfg))
    app = await build_app(env)
    try:
        assert app.deps.dispatcher is not None
    finally:
        await app.shutdown()


async def test_a_broken_migration_refuses_to_start(env, monkeypatch):
    """`run_migrations` is wrapped in `StartupError` handling: an Alembic failure
    exits with `startup failed: ...`, not a raw traceback. A DATABASE_URL
    pointing nowhere reachable is a reliable way to make `command.upgrade`
    fail."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://bad:bad@127.0.0.1:1/nope"
    )
    with pytest.raises(StartupError, match="migration"):
        await build_app(env)


async def test_the_database_is_migrated_at_startup(env, pg_url):
    from nora.infra.sql.migrations_runner import current_revision, head_revision

    app = await build_app(env)
    try:
        assert current_revision(pg_url) == head_revision()
        assert (await app.store.stats())["schema_revision"] == head_revision()
    finally:
        await app.shutdown()


async def test_shutdown_closes_the_omniroute_client(env):
    app = await build_app(env)
    client = app.deps.client
    await app.shutdown()
    assert client.is_closed()


async def test_the_prune_sweep_actually_prunes(pg_url, monkeypatch):
    """Proves `runtime.prune_loop` — the sweep `App.run` wires in via
    `nora.app.runtime` — runs and calls `store.prune_all`, not merely that the
    coroutine exists. `append` does not prune inline, so this sweep is the only
    thing that enforces the retention window in production."""
    import asyncio
    import contextlib

    import nora.__main__ as main_module
    from nora.app.runtime import prune_loop
    from nora.domain.models import NormalizedMessage
    from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations
    from nora.obs.metrics import Metrics

    monkeypatch.setattr(main_module, "PRUNE_INTERVAL_SECONDS", 0.01)
    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url), max_per_conversation=3)
    metrics = Metrics()
    try:
        for i in range(10):
            await store.append(message=NormalizedMessage(
                "signal", "g1", "group", "+1", "A", i, f"m{i}", {}))
        assert len(await store.recent(conversation_id="g1", limit=1000)) == 10

        task = asyncio.create_task(
            prune_loop(store, metrics, interval_seconds=main_module.PRUNE_INTERVAL_SECONDS)
        )
        # Poll the CONDITION (the row count actually dropping), not the clock:
        # a fixed sleep is true on an idle loop and false on a loaded one.
        # The timeout is a DEADLOCK GUARD, not a measurement.
        try:
            deadline = asyncio.get_running_loop().time() + 5.0
            rows = await store.recent(conversation_id="g1", limit=1000)
            while len(rows) != 3:
                if asyncio.get_running_loop().time() > deadline:
                    pytest.fail(
                        "the prune sweep did not prune within 5s; "
                        f"rows={len(rows)}, expected 3"
                    )
                await asyncio.sleep(0.01)
                rows = await store.recent(conversation_id="g1", limit=1000)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert len(await store.recent(conversation_id="g1", limit=1000)) == 3
    finally:
        await store.aclose()


async def _run_briefly_and_record_prune_wiring(env, monkeypatch) -> list[dict]:
    """Drive `App.run()` far enough to reach its background spawns, with
    `runtime.prune_loop` replaced by a recorder.

    A spy rather than a row count on purpose: the question here is the WIRING
    (does `App.run` spawn the sweep at all), which is a different question from
    `test_the_prune_sweep_actually_prunes` above — that one drives `prune_loop`
    directly and stays green whether or not anything wires it in. Between the
    two, both halves are covered: the sweep works, and the flag decides whether
    it runs.
    """
    import asyncio
    import contextlib

    import nora.__main__ as main_module

    calls: list[dict] = []

    async def _never() -> None:
        await asyncio.sleep(3600)

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        return _never()

    monkeypatch.setattr(main_module.runtime, "prune_loop", _spy)
    app = await build_app(env)
    try:
        task = asyncio.create_task(app.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if calls:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await app.shutdown()
    return calls


async def test_the_retention_sweep_is_not_spawned_when_the_flag_is_off(env, monkeypatch):
    """`store.prune_enabled = false` must stop the sweep being wired in at all.

    Not merely widened: `prune_all` runs a `row_number()` window over the WHOLE
    `messages` table on every tick, so a huge `max_per_conversation` would keep
    paying for that scan forever in order to delete nothing. The flag has to
    reach the spawn, which is what this asserts.
    """
    import tomllib

    import tomli_w

    cfg = tomllib.loads(env.read_text())
    cfg["store"]["prune_enabled"] = False
    env.write_text(tomli_w.dumps(cfg))

    assert await _run_briefly_and_record_prune_wiring(env, monkeypatch) == [], (
        "prune_enabled = false must not spawn the retention sweep"
    )


async def test_the_retention_sweep_is_spawned_when_the_flag_is_on(env, monkeypatch):
    """The control for the test above — without it, deleting the spawn entirely
    would leave that one green and this file would assert nothing about pruning
    ever happening. `config.example.toml` carries no `prune_enabled`, so this
    also pins the DEFAULT: absent means enabled, i.e. today's behaviour."""
    import tomllib

    cfg = tomllib.loads(env.read_text())
    assert "prune_enabled" not in cfg["store"], (
        "this test's point is the DEFAULT; the fixture must not set the flag"
    )

    assert await _run_briefly_and_record_prune_wiring(env, monkeypatch), (
        "an unset prune_enabled must keep spawning the retention sweep"
    )


async def test_the_memory_index_drain_is_spawned_at_the_configured_interval(env, monkeypatch):
    """`App.run` spawns `memory_drain_loop` with `[memory.index]
    drain_seconds`: the queue a failed projection fills
    is drained by the nora process, not only by analytics."""
    import asyncio
    import contextlib
    import tomllib

    import tomli_w

    import nora.__main__ as main_module

    cfg = tomllib.loads(env.read_text())
    cfg["memory"]["index"] = {"refresh_seconds": 7.5}
    env.write_text(tomli_w.dumps(cfg))
    calls: list[dict] = []

    async def _never() -> None:
        await asyncio.sleep(3600)

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        return _never()

    monkeypatch.setattr(main_module.runtime, "memory_drain_loop", _spy)
    app = await build_app(env)
    try:
        task = asyncio.create_task(app.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if calls:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await app.shutdown()
    # `[memory.index] embed` is off by default: no embedding worker is handed in.
    assert calls == [{"interval_seconds": 7.5, "embed": None}]


async def _run_briefly_and_record_dream_wiring(env, monkeypatch) -> list[dict]:
    """The `dream_loop` sibling of `_run_briefly_and_record_prune_wiring`
    above — same shape, same reasoning: a spy proves the WIRING (does
    `App.run` spawn the loop at all), independent of whether `dream_loop`
    itself works (that is `tests/unit/test_runtime.py`'s job)."""
    import asyncio
    import contextlib

    import nora.__main__ as main_module

    calls: list[dict] = []

    async def _never() -> None:
        await asyncio.sleep(3600)

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        return _never()

    monkeypatch.setattr(main_module.runtime, "dream_loop", _spy)
    app = await build_app(env)
    try:
        task = asyncio.create_task(app.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if calls:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await app.shutdown()
    return calls


async def test_the_dream_loop_is_not_spawned_when_the_dreamer_is_off(env, monkeypatch):
    """A new subsystem does not switch itself on.

    The clock belongs to the runtime; a tier flag never selects whether it
    runs. The condition is one flag and the assertion names one: asserting a
    tier flag too would make the coupling look intended, and let `[dreamer]
    enabled = true` ship inert with a green suite behind it."""
    import tomllib

    cfg = tomllib.loads(env.read_text())
    assert cfg.get("dreamer", {}).get("enabled", False) is False, (
        "this test's point is the DEFAULT; the fixture must not enable it"
    )

    assert await _run_briefly_and_record_dream_wiring(env, monkeypatch) == [], (
        "with [dreamer] off, nothing may spawn the clock"
    )


async def test_the_dream_loop_is_spawned_by_the_dreamer_flag_alone(
    env, monkeypatch,
):
    """**The dreamer flag alone spawns the clock.**

    `[dreamer] enabled = true` is the live production configuration and must
    spawn the clock: the runtime, its adaptors, budgets and instructions are
    built, and the clock is not gated on a TIER's flag, which would leave one
    ERROR line in `nora.log` as the only evidence.

    The clock's own numbers live in `[dreamer]`, so this also asserts they reach
    the spawn — a flag that starts a loop on numbers from nowhere is the same
    defect one level down."""
    import tomllib

    import tomli_w

    cfg = tomllib.loads(env.read_text())
    cfg.setdefault("dreamer", {})["enabled"] = True
    env.write_text(tomli_w.dumps(cfg))

    calls = await _run_briefly_and_record_dream_wiring(env, monkeypatch)
    assert calls, (
        "[dreamer] enabled = true must spawn the clock on its own — this "
        "asserting nothing is what let an inert runtime ship as 'on'"
    )
    # The config values actually reached the spawn, not just the flag.
    assert calls[0]["tick_seconds"] == 300.0
    assert calls[0]["safety_lag"] == 300
    assert calls[0]["batch_limit"] == 400


# ---- which search backend gets built (DEC-157) ------------------------------
#
# `[search] provider` is the whole user-facing feature, and "the config field
# exists and is readable on `cfg`" is not evidence that it selects anything.
# These assert the CONSTRUCTED object and the key it resolved, because picking
# the right class while reading the wrong env var is a silent way to answer
# `unavailable` forever.
#
# They observe the client where it lands: the `ToolRegistry` the reply path
# builds `web_search` from.


@pytest.mark.parametrize(
    ("provider", "expected_class", "expected_key"),
    [
        ("tavily", "TavilySearchProvider", "tavily-key"),
        ("exa", "ExaSearchProvider", "exa-key"),
    ],
)
async def test_build_app_builds_the_search_backend_the_config_names(
    env, monkeypatch, provider, expected_class, expected_key,
):
    """Both keys are exported, so the branch cannot pass by accident: only the
    SELECTED backend's `api_key_env` may be the one that was read."""
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    cfg = tomllib.loads(env.read_text())
    cfg["search"] = {"enabled": True, "provider": provider}
    env.write_text(tomli_w.dumps(cfg))

    app = await build_app(env)
    try:
        client = app.deps.engagement._tool_registry._search
        assert type(client).__name__ == expected_class
        assert client._api_key == expected_key
    finally:
        await app.shutdown()


async def test_build_app_leaves_search_unwired_when_the_selected_backend_is_keyless(
    env, monkeypatch,
):
    """The control: Tavily's key is
    present and Exa's is not, so a branch that resolved the key before reading
    `provider` would build a working-looking Exa client on Tavily's credential
    and 401 on every call. Off means `None`, and both tools answer
    `unavailable` (R-5), never a silent empty."""
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    cfg = tomllib.loads(env.read_text())
    cfg["search"] = {"enabled": True, "provider": "exa"}
    env.write_text(tomli_w.dumps(cfg))

    app = await build_app(env)
    try:
        assert app.deps.engagement._tool_registry._search is None
    finally:
        await app.shutdown()


async def test_app_run_spawns_the_lazy_adoption_backfill(env, monkeypatch):
    """The wiring, not the coroutine: build the real app, audit a message from a
    sender identity.toml *does* know, then drive `App.run()` long enough for the
    background sweep to fire, and assert the row came back marked.

    `App.run()` blocks forever by design (it gathers the adapter tasks), so this
    runs it as a task and cancels it — the same shape production shutdown uses.
    """
    import asyncio
    import contextlib

    import nora.__main__ as main_module
    from nora.domain.models import NormalizedMessage

    monkeypatch.setattr(main_module, "ADOPTION_START_DELAY_SECONDS", 0.0)
    app = await build_app(env)
    try:
        # Ingested while unmapped: person_id NULL, nothing ever fed.
        row_id = await app.deps.audit.append(message=NormalizedMessage(
            "signal", "Z3JvdXA=", "group", "+852111", "Miles", 1, "congee", {}))
        assert (await app.deps.audit.unattributed(limit=10))[0].id == row_id

        task = asyncio.create_task(app.run())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not await app.deps.audit.unattributed(limit=10):
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert await app.deps.audit.unattributed(limit=10) == [], (
            "App.run() must spawn the lazy-adoption backfill"
        )
    finally:
        await app.shutdown()


async def test_app_run_starts_a_listener_for_every_adapter_not_just_signal(env, monkeypatch):
    """`App.run` supervises EVERY adapter in the registry, not a single-element
    `SupervisedAdapter` list built from `deps.adapter`: a second enabled
    platform is built and identity-resolved at startup, and must also have
    `listen()` called or it is present in `/health` but deaf in practice.
    `runtime.start_adapters`/`supervise_adapter` are generic over any number of
    adapters; this pins the call site to the whole registry.

    A second adapter is inserted into the live registry rather than enabled
    through config, because building a real `DiscordAdapter` would need a bot
    token and a Gateway to connect to. `AdapterRegistry.adapters` is an ordinary
    dict on a frozen dataclass, so this is the registry the app actually reads.
    """
    import asyncio
    import contextlib

    import nora.app.runtime as runtime_module

    class _SecondAdapter:
        platform = "discord"

        def __init__(self) -> None:
            self.listened = False

        async def listen(self, *, on_message) -> None:
            self.listened = True
            await asyncio.sleep(3600)

    app = await build_app(env)
    second = _SecondAdapter()
    app.deps.registry.adapters["discord"] = second

    started: list[str] = []
    real_start = runtime_module.start_adapters

    def _capture(background, adapters, metrics):
        started.extend(a.platform for a in adapters)
        return real_start(background, adapters, metrics)

    monkeypatch.setattr(runtime_module, "start_adapters", _capture)

    try:
        task = asyncio.create_task(app.run())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if second.listened:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert sorted(started) == ["discord", "signal"], (
            "App.run must supervise every adapter in the registry"
        )
        assert second.listened, "the second platform's listen() must actually be called"
    finally:
        await app.shutdown()


# ---- the restart floor -------------------------------------------------------

async def _run_briefly(app, *, until, polls: int = 100) -> None:
    import asyncio
    import contextlib

    task = asyncio.create_task(app.run())
    try:
        for _ in range(polls):
            await asyncio.sleep(0.02)
            if until():
                break
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_app_run_releases_what_a_previous_process_left_before_the_wake_loop_starts(
    env, monkeypatch,
):
    """The ORDER is the claim: a tick that read the unit before the release would see
    it held. The reclaim is awaited, then the loop is spawned, and it is handed THIS
    process's start (so a wake this process records is never released)."""
    import time

    import nora.__main__ as main_module

    cfg = tomllib.loads(env.read_text())
    cfg["cognition"] = {"enabled": True}
    cfg["dreamer"]["enabled"] = True
    cfg["notes"] = {"enabled": True}
    cfg["dreamer"].setdefault("budgets", {})["cognition"] = {
        "turns": 200, "total_tokens": 10240000, "output_tokens": 120000,
        "timeout_seconds": 3600.0,
    }
    env.write_text(tomli_w.dumps(cfg))
    policy = env.parent / "policy.toml"
    policy.write_text(policy.read_text() + 'unit = "friends"\nhome_room = true\n')
    order: list[str] = []
    started: list[int] = []

    async def _never(*args, **kwargs) -> None:
        import asyncio
        await asyncio.sleep(3600)

    def _loop_spy(*args, **kwargs):
        order.append("wake_loop")
        return _never()

    monkeypatch.setattr(main_module.wake, "wake_loop", _loop_spy)
    app = await build_app(env)
    assert app.deps.wake_evaluator is not None

    async def _reclaim(*, process_started_ms: int) -> int:
        order.append("reclaim")
        started.append(process_started_ms)
        return 0

    monkeypatch.setattr(app.deps.wake_evaluator, "reclaim_orphans", _reclaim)
    try:
        before = int(time.time() * 1000)
        await _run_briefly(app, until=lambda: "wake_loop" in order)
    finally:
        await app.shutdown()
    assert order == ["reclaim", "wake_loop"]
    assert before <= started[0] <= int(time.time() * 1000)


async def test_no_reclaim_and_no_wake_loop_when_cognition_is_off(env, monkeypatch):
    """The control: with `[cognition] enabled = false` there is no evaluator to ask."""
    app = await build_app(env)
    try:
        assert app.deps.wake_evaluator is None
    finally:
        await app.shutdown()
