import shutil
import tomllib
from pathlib import Path

import pytest
import tomli_w
from aiohttp import web

from nora.__main__ import StartupError, build_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def env(tmp_path, monkeypatch, aiohttp_server):
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

    with (ROOT / "config.example.toml").open("rb") as f:
        cfg = tomllib.load(f)
    base = str(daemon.make_url("")).rstrip("/")
    cfg["platforms"]["signal"]["base_url"] = base
    cfg["memory"]["base_url"] = base
    cfg["store"]["db_path"] = str(tmp_path / "messages.db")
    cfg["obs"]["logging"]["path"] = str(tmp_path / "nora.log")
    cfg["doctrine"]["dir"] = str(ROOT / "nora" / "doctrine")
    cfg["identity_file"] = str(tmp_path / "identity.toml")
    cfg["policy_file"] = str(tmp_path / "policy.toml")
    cfg["obs"]["metrics"]["port"] = 0
    (tmp_path / "identity.toml").write_text(
        "[persons.miles]\nname = \"Miles\"\n[[persons.miles.signal]]\nphone = \"+852111\"\n"
    )
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "Z3JvdXA="\npersons = "*"\n'
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
    shutil.copy(ROOT / "nora" / "doctrine" / "identity.md", doctrine_dir / "identity.md")
    (doctrine_dir / "broken.md").write_text("no frontmatter")
    cfg = tomllib.loads(env.read_text())
    cfg["doctrine"]["dir"] = str(doctrine_dir)
    env.write_text(tomli_w.dumps(cfg))
    with pytest.raises(StartupError, match="doctrine"):
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
    cfg["memory"]["base_url"] = "http://127.0.0.1:1"
    env.write_text(tomli_w.dumps(cfg))
    app = await build_app(env)
    try:
        assert app.deps.dispatcher is not None
    finally:
        await app.shutdown()


async def test_a_broken_migration_refuses_to_start(env, tmp_path):
    """`run_migrations` used to be the only fatal startup step not wrapped in
    `StartupError` handling: an Alembic failure exited with a raw traceback
    instead of `startup failed: ...`. A directory where SQLite expects a
    file is a reliable way to make `command.upgrade` fail."""
    broken = tmp_path / "messages.db"
    broken.mkdir()
    cfg = tomllib.loads(env.read_text())
    cfg["store"]["db_path"] = str(broken)
    env.write_text(tomli_w.dumps(cfg))
    with pytest.raises(StartupError, match="migration"):
        await build_app(env)


async def test_the_database_is_migrated_at_startup(env):
    from nora.infra.sql.migrations_runner import current_revision, head_revision

    app = await build_app(env)
    try:
        assert current_revision(app.cfg.store.db_path) == head_revision()
        assert (await app.store.stats())["schema_revision"] == head_revision()
    finally:
        await app.shutdown()


async def test_shutdown_closes_the_omniroute_client(env):
    app = await build_app(env)
    client = app.deps.client
    await app.shutdown()
    assert client.is_closed()


async def test_the_prune_sweep_actually_prunes(tmp_path, monkeypatch):
    """Proves `runtime.prune_loop` — the sweep `App.run` wires in via
    `nora.app.runtime` (moved there from `__main__._prune_loop` in Task 7,
    wired into `App.run` in Task 8) — runs and calls `store.prune_all`, not
    merely that the coroutine exists. `append` stopped pruning inline in
    Task 4, so this sweep is the only thing left that enforces the retention
    window in production."""
    import asyncio
    import contextlib

    import nora.__main__ as main_module
    from nora.app.runtime import prune_loop
    from nora.domain.models import NormalizedMessage
    from nora.infra.sql import SqlAuditStore, create_sql_engine, run_migrations
    from nora.obs.metrics import Metrics

    monkeypatch.setattr(main_module, "PRUNE_INTERVAL_SECONDS", 0.01)
    path = tmp_path / "messages.db"
    run_migrations(path)
    store = SqlAuditStore(create_sql_engine(path), max_per_conversation=3)
    metrics = Metrics()
    try:
        for i in range(10):
            await store.append(message=NormalizedMessage(
                "signal", "g1", "group", "+1", "A", i, f"m{i}", {}))
        assert len(await store.recent(conversation_id="g1", limit=1000)) == 10

        task = asyncio.create_task(
            prune_loop(store, metrics, interval_seconds=main_module.PRUNE_INTERVAL_SECONDS)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(await store.recent(conversation_id="g1", limit=1000)) == 3
    finally:
        await store.aclose()


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
