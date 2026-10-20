"""Release packaging must cover every built service without moving host state."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source():
    return yaml.safe_load((ROOT / 'docker-compose.yml').read_text())


def images(bundle):
    return {name: f'ghcr.io/mikeng-io/nora-{name}@sha256:' + 'a' * 64
            for name in bundle.BUILDS}


def test_release_bundle_covers_all_repository_built_services():
    bundle = load('release_bundle')
    compose = source()
    assert {name for name, svc in compose['services'].items() if 'build' in svc} == set(
        bundle.BUILDS)
    generated, manifest = bundle.render(compose, images(bundle), 'v6.3.1', 'b' * 40)
    assert not any('build' in svc for svc in generated['services'].values())
    for name, image in manifest['services'].items():
        assert generated['services'][name]['image'] == image
    assert manifest['services']['presence'] == manifest['services']['analytics'] == (
        manifest['services']['nora'])
    assert generated['services']['db'] == compose['services']['db']
    for svc in generated['services'].values():
        for mount in svc.get('volumes', []):
            assert not mount.startswith('./')
    volumes = generated['services']['nora']['volumes']
    assert not any(':/app/nora/doctrine' in v or ':/app/nora/dreamer' in v for v in volumes)
    assert any('NORA_RUNTIME_ROOT' in v and '/state:/app/state' in v for v in volumes)


@pytest.mark.parametrize('fault', ['missing', 'foreign', 'mutable', 'extra'])
def test_incomplete_or_untrusted_bundle_is_refused(fault):
    bundle = load('release_bundle')
    pins = images(bundle)
    if fault == 'missing':
        del pins['voice-wake']
    elif fault == 'extra':
        pins['db'] = pins['nora']
    elif fault == 'foreign':
        pins['nora'] = 'example.invalid/other@sha256:' + 'a' * 64
    else:
        pins['nora'] = 'ghcr.io/mikeng-io/nora-nora:latest'
    with pytest.raises(ValueError):
        bundle.render(source(), pins, 'v6.3.1', 'b' * 40)


def test_new_build_service_cannot_silently_stay_outside_release():
    bundle = load('release_bundle')
    compose = copy.deepcopy(source())
    compose['services']['new-service'] = {'build': './new-service', 'image': 'new:latest'}
    with pytest.raises(ValueError, match='build'):
        bundle.render(compose, images(bundle), 'v6.3.1', 'b' * 40)


def test_host_validation_failure_never_recreates_services_or_records_success(tmp_path, monkeypatch):
    wrapper = load('komodo_deploy')
    bundle = load('release_bundle')
    compose, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    (tmp_path / 'config.toml').write_text('[example]\nenabled=true\n')
    commands = []

    def execute(command, *, output=False):
        import json
        commands.append(command)
        if command[0:2] == ['docker', 'compose'] and 'config' in command:
            resolved = copy.deepcopy(compose)
            resolved['name'] = 'nora'
            resolved['services']['nora']['volumes'] = [
                {'target': '/app/config.toml', 'source': str(tmp_path / 'config.toml')}]
            return json.dumps(resolved)
        if 'pull' in command:
            return ''
        if command[0:2] == ['docker', 'run']:
            raise RuntimeError('invalid candidate config')
        raise AssertionError(f'unexpected operation: {command}')

    monkeypatch.setattr(wrapper, '__file__', str(tmp_path / 'komodo_deploy.py'))
    monkeypatch.setattr(wrapper, 'run', execute)
    with pytest.raises(RuntimeError, match='invalid candidate config'):
        wrapper.deploy(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert not any('up' in command for command in commands)
    assert not (tmp_path / 'release-deploy.json').exists()


def test_failed_health_readback_preserves_the_previous_success_receipt(tmp_path, monkeypatch):
    import json

    wrapper = load('komodo_deploy')
    bundle = load('release_bundle')
    compose, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    (tmp_path / 'config.toml').write_text('[example]\nenabled=true\n')
    receipt = tmp_path / 'release-deploy.json'
    receipt.write_text('previous successful deployment')
    verifier = tmp_path / 'verify-deploy.py'
    verifier.write_text('def verify_services(project, images):\n'
                        '    assert project == "nora"\n'
                        '    assert "presence" in images and "analytics" in images\n'
                        '    raise RuntimeError("stale presence")\n')
    monkeypatch.setattr(wrapper, '__file__', str(tmp_path / 'komodo_deploy.py'))
    commands = []

    def execute(command, *, output=False):
        commands.append(command)
        if 'config' in command and command[0:2] == ['docker', 'compose']:
            resolved = copy.deepcopy(compose)
            resolved['name'] = 'nora'
            resolved['services']['nora']['volumes'] = [
                {'target': '/app/config.toml', 'source': str(tmp_path / 'config.toml')}]
            return json.dumps(resolved)
        if 'pull' in command or 'up' in command or command[0:2] == ['docker', 'run']:
            return ''
        raise AssertionError(f'unexpected operation: {command}')

    monkeypatch.setattr(wrapper, 'run', execute)
    with pytest.raises(RuntimeError, match='stale presence'):
        wrapper.deploy(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    up = next(command for command in commands if 'up' in command)
    assert '--no-deps' in up and '--no-build' in up
    assert not {'db', 'rustfs', 'prometheus', 'site'} & set(up)
    assert receipt.read_text() == 'previous successful deployment'


def test_older_release_cannot_replace_newer_desired_state(tmp_path, monkeypatch):
    import base64
    import json

    bundle = load('release_bundle')
    (tmp_path / 'release.json').write_text(json.dumps({'version': 'v6.3.1', 'commit': 'b' * 40}))
    calls = []

    def api(repo, path, body=None):
        calls.append((path, body))
        if path == 'git/matching-refs/heads/deployments':
            return [{'ref': 'refs/heads/deployments', 'object': {'sha': 'c' * 40}}]
        if path.startswith('contents/release.json?ref='):
            return {'content': base64.b64encode(b'{"version":"v6.4.0"}').decode()}
        raise AssertionError('must not create or promote older Git objects')

    monkeypatch.setattr(bundle, 'api', api)
    with pytest.raises(ValueError, match='newer'):
        bundle.promote('mikeng-io/nora', tmp_path)
    assert len(calls) == 2


def test_real_compose_config_capture_does_not_expand_host_secret(tmp_path, monkeypatch):
    import json
    import os
    import shutil
    import subprocess
    import sys

    if shutil.which('docker') is None:
        pytest.skip('Docker Compose CLI unavailable')
    available = subprocess.run(['docker', 'compose', 'version'], capture_output=True)
    if available.returncode:
        pytest.skip('Docker Compose CLI unavailable')
    bundle = load('release_bundle')
    compose, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    (tmp_path / 'compose.json').write_text(json.dumps(compose))
    (tmp_path / 'release.json').write_text(json.dumps(manifest))
    monkeypatch.setenv('POSTGRES_PASSWORD', 'PRIVATE_SENTINEL_MUST_NOT_APPEAR')
    result = subprocess.run(  # noqa: S603 -- fixed wrapper and Compose CLI
        [sys.executable, str(ROOT / 'scripts/komodo_deploy.py'),
         '--runtime-root', str(tmp_path), '--project', 'nora', '--',
         'docker', 'compose', '-p', 'nora', '-f', 'compose.json', 'config'],
        cwd=tmp_path, env=os.environ, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'PRIVATE_SENTINEL_MUST_NOT_APPEAR' not in result.stdout + result.stderr
    parsed = yaml.safe_load(result.stdout)
    assert parsed['services']['nora']['image'] == manifest['services']['nora']


def test_periphery_shares_the_existing_host_lock_inode():
    override = yaml.safe_load((ROOT / 'deploy/komodo/periphery.override.example.yml').read_text())
    mounts = override['services']['periphery']['volumes']
    assert '/tmp/nora-deploy.lock:/tmp/nora-deploy.lock' in mounts  # noqa: S108 -- shared inode


def test_optional_profiles_are_excluded_from_native_default_monitoring():
    import tomllib

    resources = tomllib.loads((ROOT / 'deploy/komodo/resources.example.toml').read_text())
    assert {'site', 'embedder'} <= set(resources['stack'][0]['config']['ignore_services'])


def test_site_profile_is_enabled_before_real_candidate_resolution(tmp_path, monkeypatch):
    import json
    import os
    import re
    import shutil
    import subprocess

    if shutil.which('docker') is None:
        pytest.skip('Docker Compose CLI unavailable')
    bundle = load('release_bundle')
    wrapper = load('komodo_deploy')
    compose, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    text = json.dumps(compose)
    (tmp_path / 'compose.json').write_text(text)
    for key in re.findall(r'\$\{([A-Z0-9_]+):\?', text):
        monkeypatch.setenv(key, 'synthetic-placeholder')
    monkeypatch.setenv('NORA_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('AUTH_URL', 'https://example.invalid')
    monkeypatch.chdir(tmp_path)

    class PullReached(Exception):
        pass

    def execute(command, *, output=False):
        if command[-3:] == ['config', '--format', 'json']:
            result = subprocess.run(  # noqa: S603 -- real, network-free Compose resolution
                command, env=os.environ, text=True, capture_output=True, check=True)
            return result.stdout
        if 'pull' in command:
            assert '--profile' in command and 'site' in command
            raise PullReached
        raise AssertionError('must stop before any image pull or recreation')

    monkeypatch.setattr(wrapper, 'run', execute)
    with pytest.raises(PullReached):
        wrapper.deploy(['docker', 'compose', '-p', 'nora', '-f', 'compose.json'],
                       manifest, tmp_path, 'nora', site=True)


@pytest.mark.parametrize('failure', [RuntimeError, SystemExit, KeyboardInterrupt])
def test_failed_or_interrupted_candidate_cannot_be_applied_again(tmp_path, monkeypatch, failure):
    wrapper = load('komodo_deploy')
    bundle = load('release_bundle')
    _, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    monkeypatch.setattr(wrapper, '__file__', str(tmp_path / 'komodo_deploy.py'))
    calls = []

    def attempt(*args, **kwargs):
        assert (tmp_path / 'release-attempt.json').exists()
        calls.append(args[1]['version'])
        if len(calls) == 1:
            raise failure('failed attempt')

    monkeypatch.setattr(wrapper, 'deploy', attempt)
    with pytest.raises(failure):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    with pytest.raises(RuntimeError, match='blocked'):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls == ['v6.3.1']
    # An unrelated metadata field cannot bypass the guard.
    with pytest.raises(RuntimeError, match='blocked'):
        wrapper.apply_release(['docker', 'compose'], dict(manifest, annotation='changed'),
                              tmp_path, 'nora', site=False)
    # A newer release may roll forward; success clears the outstanding marker.
    next_release = dict(manifest, version='v6.3.2', commit='c' * 40)
    wrapper.apply_release(['docker', 'compose'], next_release, tmp_path, 'nora', site=False)
    assert calls == ['v6.3.1', 'v6.3.2']
    assert not (tmp_path / 'release-attempt.json').exists()


def test_verified_release_can_be_reapplied_after_operator_config_change(tmp_path, monkeypatch):
    wrapper = load('komodo_deploy')
    bundle = load('release_bundle')
    _, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    monkeypatch.setattr(wrapper, '__file__', str(tmp_path / 'komodo_deploy.py'))
    calls = []
    monkeypatch.setattr(wrapper, 'deploy', lambda *a, **kw: calls.append(a[1]['version']))
    for _ in range(2):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls == ['v6.3.1', 'v6.3.1']


def test_native_procedure_is_disabled_until_explicit_cutover():
    import tomllib

    resources = tomllib.loads((ROOT / 'deploy/komodo/resources.example.toml').read_text())
    assert 'action' not in resources  # No unsupported self-modifying Action.
    procedure = resources['procedure'][0]['config']
    assert procedure['schedule_enabled'] is False
    assert procedure['stage'][0]['executions'] == [
        {'execution': {'type': 'DeployStackIfChanged', 'params': {'stack': 'nora-releases'}}}]
