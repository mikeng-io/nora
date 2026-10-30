"""The `--all` fleet shape: aggregate health, per-platform legs, entry wiring.

The deployment's one-service shape hosts every platform's publisher in a
single process with a shared health surface — a platform's own failure must
deny only itself, and the aggregate `/health` must stay 503 while ANY running
publisher is stale, while `/live` keeps reporting the process is alive.
Entry-module tests load deploy/bridge-projection/entry.py directly (it is the
composition root, not an importable package member).
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import os
from pathlib import Path

import pytest
from aiohttp import ClientSession

from nora import config
from nora.platform.bridge.projection import (
    ProjectionPublisher,
    run_fleet,
)

ROOT = Path(__file__).resolve().parents[2]

_MILES_PHONE = "+85212345678"
_MILES_UUID = "a1000001-0000-4000-8000-000000000001"
_ROOM = "Q2hhdEdyb3VwSWRFeGFtcGxlQmFzZTY0PT0="

_IDENTITY = f"""\
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "{_MILES_PHONE}"
uuid = "{_MILES_UUID}"
"""

_POLICY = f"""\
default = "deny"

[[conversations]]
platform = "signal"
id = "{_ROOM}"
label = "Family"
persons = ["miles"]
"""

_ACCOUNT_KEY_LINE = '# Opaque stream leg — must equal SIGNAL_BRIDGE_ACCOUNT_KEY'


def _load_entry(monkeypatch: pytest.MonkeyPatch):
    """Freshly import entry.py: module-level state + env reads stay isolated."""
    for key in list(os.environ):
        if key.endswith('_BRIDGE_ACCOUNT_KEY') or key.startswith('NORA_BRIDGE'):
            monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location(
        'bridge_projection_entry', ROOT / 'deploy/bridge-projection/entry.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sources(
        tmp_path: Path, config_template: Path, *, platform_key: str = 'leg-7',
        whatsapp_key: str | None = None) -> Path:
    (tmp_path / 'policy.toml').write_text(_POLICY)
    (tmp_path / 'identity.toml').write_text(_IDENTITY)
    template = config_template.read_text()
    template = template.replace(
        'account_key = "signal-acct-1"', f'account_key = "{platform_key}"')
    # The configured-account floor establishes identity without a live
    # /v1/self — matches the deploy shape where signal keeps its number here.
    template = template.replace(
        'account = ""                     # empty = derive from the signal-cli daemon',
        'account = "+85287654321"')
    if whatsapp_key is not None:
        template = template.replace(
            '[platforms.whatsapp]\nenabled = false',
            '[platforms.whatsapp]\nenabled = true\nmode = "bridge"\n'
            f'account_key = "{whatsapp_key}"')
    (tmp_path / 'config.toml').write_text(template)
    return tmp_path / 'config.toml'


def _publisher(
        tmp_path: Path, platform: str, *, key: str,
        config_template: Path,
        whatsapp_key: str | None = None) -> ProjectionPublisher:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return ProjectionPublisher(
        platform=platform,
        config_path=_sources(
            tmp_path, config_template, whatsapp_key=whatsapp_key),
        out_dir=tmp_path,
        account_key=key,
        config_loader=functools.partial(config.load, require_secrets=False),
        ack_emoji='👀',
    )


def _projection_at(path: Path) -> dict | None:
    """Sync file read — Path I/O stays out of async defs (ASYNC240)."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


async def _ready(path: Path, limit: float = 10.0) -> dict:
    deadline = asyncio.get_event_loop().time() + limit
    while asyncio.get_event_loop().time() < deadline:
        if (found := _projection_at(path)) is not None:
            return found
        await asyncio.sleep(0.05)
    raise AssertionError(f'publisher never wrote {path}')


@pytest.mark.asyncio
async def test_fleet_health_aggregates_per_platform_and_live_never_stales(
        tmp_path, unused_tcp_port, example_config_path):
    """One stale platform keeps /health 503 while the rest keep publishing."""
    good = _publisher(tmp_path / 'sig', 'signal', key='leg-7',
                      config_template=example_config_path)
    bad = _publisher(
        tmp_path / 'wa', 'whatsapp', key='not-the-configured-leg',
        config_template=example_config_path, whatsapp_key='wa-leg-1')
    port = unused_tcp_port
    task = asyncio.create_task(run_fleet([good, bad], health_port=port))
    try:
        sig = await _ready(tmp_path / 'sig' / 'signal.json')
        wa = await _ready(tmp_path / 'wa' / 'whatsapp.json')
        assert sig['enabled'] and sig['rooms'] == [_ROOM]
        assert not wa['enabled']  # leg mismatch denies only itself
        async with ClientSession() as session:
            deadline = asyncio.get_event_loop().time() + 10
            while True:
                async with session.get(
                        f'http://127.0.0.1:{port}/health') as resp:
                    body = await resp.json()
                    if resp.status == 503 and 'signal' in body['platforms']:
                        break
                assert asyncio.get_event_loop().time() < deadline
                await asyncio.sleep(0.1)
            assert not body['ok']
            assert body['platforms']['signal']['ok']
            assert not body['platforms']['whatsapp']['ok']
            async with session.get(f'http://127.0.0.1:{port}/live') as resp:
                assert resp.status == 200 and (await resp.json())['live']
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_fleet_health_200_only_when_every_publisher_is_fresh(
        tmp_path, unused_tcp_port, example_config_path):
    good = _publisher(tmp_path / 'sig', 'signal', key='leg-7',
                      config_template=example_config_path)
    also = _publisher(tmp_path / 'ig', 'instagram', key='leg-7',
                      config_template=example_config_path)
    port = unused_tcp_port
    task = asyncio.create_task(run_fleet([good, also], health_port=port))
    try:
        await _ready(tmp_path / 'sig' / 'signal.json')
        await _ready(tmp_path / 'ig' / 'instagram.json')
        async with ClientSession() as session:
            deadline = asyncio.get_event_loop().time() + 10
            while True:
                async with session.get(
                        f'http://127.0.0.1:{port}/health') as resp:
                    body = await resp.json()
                    if resp.status == 200:
                        break
                assert asyncio.get_event_loop().time() < deadline
                await asyncio.sleep(0.1)
            assert body['ok'] and set(body['platforms']) == {'signal', 'instagram'}
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── entry.py construction ──────────────────────────────────────────────────


def test_all_mode_uses_per_platform_legs_and_tolerates_disabled(
        tmp_path, monkeypatch):
    entry = _load_entry(monkeypatch)
    monkeypatch.setenv('SIGNAL_BRIDGE_ACCOUNT_KEY', 'leg-7')
    monkeypatch.setenv('WHATSAPP_BRIDGE_ACCOUNT_KEY', 'wa-leg')
    publishers = entry._fleet_publishers(
        None, config_path=tmp_path / 'config.toml',
        out_dir=tmp_path, metrics=None)
    by_platform = {p.platform: p for p in publishers}
    assert set(by_platform) == {'signal', 'discord', 'whatsapp', 'instagram'}
    assert by_platform['signal'].account_key == 'leg-7'
    assert by_platform['whatsapp'].account_key == 'wa-leg'
    # Missing legs become deny-sentinels, never a guessed grant.
    assert by_platform['discord'].account_key == 'unset'
    assert by_platform['instagram'].account_key == 'unset'


def test_all_mode_disabled_platform_needs_no_credential(
        tmp_path, monkeypatch, example_config_path):
    entry = _load_entry(monkeypatch)
    cfg_path = _sources(tmp_path, example_config_path)
    cfg = entry._boot_config(cfg_path)
    assert cfg is not None
    assert not cfg.platforms.discord.enabled  # example ships disabled
    publishers = entry._fleet_publishers(
        cfg, config_path=cfg_path, out_dir=tmp_path, metrics=None)
    by_platform = {p.platform: p for p in publishers}
    # Disabled platforms carry their configured leg (or a placeholder), not
    # the deny-sentinel — their deny artifact comes from enabled:false.
    assert by_platform['discord'].account_key != 'unset'
    assert by_platform['instagram'].account_key != 'unset'


def test_all_mode_malformed_env_leg_degrades_not_crashes(tmp_path, monkeypatch):
    entry = _load_entry(monkeypatch)
    monkeypatch.setenv('SIGNAL_BRIDGE_ACCOUNT_KEY', 'has space:bad')
    publishers = entry._fleet_publishers(
        None, config_path=tmp_path / 'config.toml',
        out_dir=tmp_path, metrics=None)
    by_platform = {p.platform: p for p in publishers}
    assert by_platform['signal'].account_key == 'unset'


def test_single_mode_refuses_without_platform_or_key(tmp_path, monkeypatch):
    entry = _load_entry(monkeypatch)
    parser = entry.build_parser()
    args = parser.parse_args(['--config', 'x.toml'])
    assert entry._single(args) == 2
    args = parser.parse_args(['--config', 'x.toml', '--platform', 'signal'])
    assert entry._single(args) == 2


def test_url_env_chain_per_platform_then_shared_then_config(
        tmp_path, monkeypatch):
    entry = _load_entry(monkeypatch)
    monkeypatch.setenv('SIGNAL_BRIDGE_BASE_URL', 'http://sig-bridge:18090')
    monkeypatch.setenv('DISCORD_BRIDGE_SELF_URL', 'http://dc:18093/v1/self')
    assert entry._self_url('signal') == 'http://sig-bridge:18090/v1/self'
    assert entry._reads_url('signal') == 'http://sig-bridge:18090/v1/reads'
    assert entry._self_url('discord') == 'http://dc:18093/v1/self'
    monkeypatch.setenv('NORA_BRIDGE_BASE_URL', 'http://shared:8080')
    assert entry._reads_url('whatsapp') == 'http://shared:8080/v1/reads'
    assert entry._self_url('instagram') == 'http://shared:8080/v1/self'
