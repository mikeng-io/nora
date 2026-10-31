"""The app-only release must leave the bridge cohort running — end to end.

The approved lifecycle proof: two release manifests that differ in the app
image alone reconcile through the real `komodo_deploy.apply_release` wrapper
against a faithful container fixture (ids, start times and restart counts
behaving like Docker's), and the bridges, Redis and the bridge-projection
publisher keep the same container identity — only `nora` is recreated.
Nothing here touches a real daemon; the fixture models compose's native
"unchanged service keeps its container" contract.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
INDEPENDENT = {
    'redis', 'signal-bridge', 'discord-bridge', 'whatsapp-bridge',
    'instagram-bridge', 'bridge-projection',
}


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def images(bundle):
    return {name: f'ghcr.io/mikeng-io/nora-{name}@sha256:' + 'a' * 64
            for name in bundle.BUILDS}


class Fixture:
    """Faithful containers: an `up` recreates only when the manifest image or
    the service definition changed — exactly compose's native hash rule —
    unless `--force-recreate` is passed for a scoped config reload."""

    def __init__(self, root: Path):
        self.root = root
        self.clock = 0
        self.table_file = root / 'containers.json'
        self.table: dict[str, dict] = {}

    def _save(self) -> None:
        self.table_file.write_text(json.dumps(self.table))

    def up(self, command: list[str], manifest_images: dict) -> None:
        force = '--force-recreate' in command
        services = [c for c in command[command.index('up') + 1:]
                    if c in manifest_images]
        for name in services:
            current = self.table.get(name)
            image = manifest_images[name]
            if current is None or force or current['image'] != image:
                self.clock += 1
                self.table[name] = {
                    'id': f'container-{name}-{self.clock:04d}',
                    'image': image,
                    'started_at': self.clock,
                    'restarts': 0,  # a recreated container's RestartCount resets
                }
        self._save()


def _verifier_module(control: Path, table_file: Path) -> None:
    (control / 'verify-deploy.py').write_text(
        'import json, os\n'
        f'TABLE = {str(table_file)!r}\n'
        'def _rows():\n'
        '    return json.loads(open(TABLE).read()) if os.path.exists(TABLE) else {}\n'
        'def containers_for_services(project, services):\n'
        '    return [dict(container_id=c["id"], restarts=c["restarts"], '
        'service=name)\n'
        '            for name, c in _rows().items() if name in services]\n'
        'def verify_services(project, images, *, baseline):\n'
        '    rows = _rows()\n'
        '    for name, image in images.items():\n'
        '        assert rows[name]["image"] == image, name\n'
        '        assert rows[name]["restarts"] <= baseline.get(\n'
        '            rows[name]["id"], 0), name\n')


def _apply(tmp_path: Path, monkeypatch, manifest: dict,
           compose: dict, fixture: Fixture, wrapper, control: Path,
           runtime: Path) -> None:
    def execute(command, *, stage, output=False):
        if command[:2] == ['docker', 'compose'] and 'config' in command:
            resolved = copy.deepcopy(compose)
            resolved['name'] = 'nora'
            for name, image in manifest['services'].items():
                resolved['services'][name]['image'] = image
            resolved['services']['nora']['volumes'] = [
                {'target': '/app/config.toml', 'source': str(runtime / 'config.toml')}]
            return json.dumps(resolved)
        if 'pull' in command or command[0:2] == ['docker', 'run']:
            return ''
        if 'up' in command:
            fixture.up(command, manifest['services'])
            return ''
        if 'exec' in command:
            return json.dumps({'version': manifest['version'],
                               'commit': manifest['commit']})
        raise AssertionError(f'unexpected operation: {command}')

    monkeypatch.setattr(wrapper, '__file__', str(control / 'komodo_deploy.py'))
    monkeypatch.setattr(wrapper, 'run', execute)
    wrapper.apply_release(['docker', 'compose'], manifest, runtime, 'nora',
                          site=False)


def test_app_only_release_preserves_bridge_and_publisher_containers(
        tmp_path, monkeypatch):
    """Two manifests differing in the app image alone leave the independent
    cohort — every bridge, Redis and the projection publisher — on the same
    container ids and start times. The publisher outliving the app is the
    whole reason this service exists; a recreation here would drop the
    authorization state the bridges read."""
    bundle = load('release_bundle')
    wrapper = load('komodo_deploy')
    compose, manifest_v1 = bundle.render(
        yaml.safe_load((ROOT / 'docker-compose.yml').read_text()),
        images(bundle), 'v9.9.9', 'a' * 40)
    assert set(manifest_v1['services']) | {'redis'} >= INDEPENDENT

    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'config.toml').write_text('[example]\nenabled=true\n')
    (runtime / 'secret.toml').write_text(
        '[credentials]\nNORA_REDIS_CONSUMER_PASSWORD="synthetic"\n')
    control = tmp_path / 'control'
    control.mkdir()
    fixture = Fixture(control)
    _verifier_module(control, fixture.table_file)

    _apply(tmp_path, monkeypatch, manifest_v1, compose, fixture, wrapper,
           control, runtime)
    first = copy.deepcopy(fixture.table)
    assert first, 'release did not create the cohort'
    missing = {s for s in manifest_v1['services']
               if s not in first and s != 'site'}
    assert not missing, f'unstarted services: {sorted(missing)}'

    # The next release changes the app image alone — an app-only source diff.
    # nora/presence/analytics share it, so all three move together.
    manifest_v2 = copy.deepcopy(manifest_v1)
    manifest_v2['version'] = 'v9.9.10'
    manifest_v2['commit'] = 'b' * 40
    app_image = 'ghcr.io/mikeng-io/nora-nora@sha256:' + 'b' * 64
    manifest_v2['services'] = {
        **manifest_v1['services'],
        **dict.fromkeys(('nora', 'presence', 'analytics'), app_image),
    }
    (control / 'release-attempt.json').unlink(missing_ok=True)
    _apply(tmp_path, monkeypatch, manifest_v2, compose, fixture, wrapper,
           control, runtime)
    second = fixture.table

    for name in ('nora', 'presence', 'analytics'):
        assert second[name]['id'] != first[name]['id'], name
        assert second[name]['image'] == manifest_v2['services'][name]
    for name in INDEPENDENT & first.keys():
        assert second[name]['id'] == first[name]['id'], name
        assert second[name]['started_at'] == first[name]['started_at'], name
        assert second[name]['restarts'] == first[name]['restarts'], name
