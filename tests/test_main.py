import shutil
from pathlib import Path

import pytest
import yaml
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
    app.router.add_route("*", "/{tail:.*}", lambda r: web.json_response({"ok": True}))
    daemon = await aiohttp_server(app)

    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    base = str(daemon.make_url("")).rstrip("/")
    cfg["signal_cli"]["base_url"] = base
    cfg["honcho"]["base_url"] = base
    cfg["buffer"]["db_path"] = str(tmp_path / "messages.db")
    cfg["logging"]["path"] = str(tmp_path / "nora.log")
    cfg["persona"]["dir"] = str(ROOT / "nora" / "persona")
    cfg["persons_file"] = str(tmp_path / "persons.yaml")
    cfg["metrics"]["port"] = 0
    (tmp_path / "persons.yaml").write_text(
        "persons:\n  miles:\n    name: Miles\n    platforms:\n      signal: \"+852111\"\n"
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


async def test_build_app_wires_everything(env):
    app = await build_app(env)
    try:
        assert app.handler is not None
        assert app.adapter.platform == "signal"
        assert app.account == "+85290000100"
        assert app.persona.sha256
    finally:
        await app.shutdown()


async def test_missing_secret_refuses_to_start(env, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(StartupError, match="OMNIROUTE_API_KEY"):
        await build_app(env)


async def test_a_broken_persona_refuses_to_start(env, tmp_path):
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    shutil.copy(ROOT / "nora" / "persona" / "soul.md", persona_dir / "soul.md")
    (persona_dir / "broken.md").write_text("no frontmatter")
    cfg = yaml.safe_load(env.read_text())
    cfg["persona"]["dir"] = str(persona_dir)
    env.write_text(yaml.safe_dump(cfg))
    with pytest.raises(StartupError, match="persona"):
        await build_app(env)


async def test_a_duplicate_person_mapping_refuses_to_start(env, tmp_path):
    (tmp_path / "persons.yaml").write_text(
        "persons:\n  a:\n    name: A\n    platforms:\n      signal: \"+1\"\n"
        "  b:\n    name: B\n    platforms:\n      signal: \"+1\"\n"
    )
    with pytest.raises(StartupError, match="duplicate"):
        await build_app(env)


async def test_an_undeterminable_account_refuses_to_start(env):
    cfg = yaml.safe_load(env.read_text())
    cfg["signal_cli"]["base_url"] = "http://127.0.0.1:1"
    env.write_text(yaml.safe_dump(cfg))
    with pytest.raises(StartupError, match="account"):
        await build_app(env)


async def test_honcho_being_down_does_not_prevent_startup(env):
    cfg = yaml.safe_load(env.read_text())
    cfg["honcho"]["base_url"] = "http://127.0.0.1:1"
    env.write_text(yaml.safe_dump(cfg))
    app = await build_app(env)
    try:
        assert app.handler is not None
    finally:
        await app.shutdown()


async def test_the_database_is_migrated_at_startup(env):
    from nora.store import db

    app = await build_app(env)
    try:
        assert (await db.stats(app.cfg.buffer.db_path))["schema_version"] == db.SCHEMA_VERSION
    finally:
        await app.shutdown()


async def test_shutdown_closes_the_omniroute_client(env):
    from nora.agent import factory

    app = await build_app(env)
    await app.shutdown()
    assert factory._CLIENT is None
