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

    def execute(command, *, stage, output=False):
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
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
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

    def execute(command, *, stage, output=False):
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
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
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
    assert '/tmp/nora-deploy.lock:/run/nora-deploy.lock' in mounts  # noqa: S108 -- shared inode


def test_wrapper_uses_the_shared_lock_outside_sticky_tmp():
    import os
    import sys

    wrapper = load('komodo_deploy')
    from unittest.mock import patch

    with patch.object(sys, 'argv', ['komodo_deploy.py', '--runtime-root', '/runtime',
                                   '--project', 'nora', '--', 'docker', 'compose', 'up']), \
         patch.dict(os.environ, {}, clear=True), \
         patch.object(os, 'execvpe', side_effect=RuntimeError('reexec')) as execute, \
         pytest.raises(RuntimeError, match='reexec'):
        wrapper.main()
    assert execute.call_args.args[1][6] == '/run/nora-deploy.lock'


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

    def execute(command, *, stage, output=False):
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
        wrapper.apply_release(['docker', 'compose', '-p', 'nora', '-f', 'compose.json'],
                              manifest, tmp_path, 'nora', site=True)


@pytest.fixture
def release_runtime(tmp_path, monkeypatch):
    """Exercise the transaction with command-specific subprocess responses."""
    import json
    import subprocess

    wrapper = load('komodo_deploy')
    bundle = load('release_bundle')
    compose, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    compose['name'] = 'nora'
    compose['services']['nora']['volumes'] = [
        {'target': '/app/config.toml', 'source': str(tmp_path / 'config.toml')}]
    (tmp_path / 'config.toml').write_text('[example]\nenabled=true\n')
    (tmp_path / 'verify-deploy.py').write_text(
        'def _docker(*args):\n'
        '    raise SystemExit("PRIVATE_STDERR")\n'
        'def verify_services(project, images):\n'
        '    assert project == "nora" and "presence" in images\n'
        '    _docker("ps")\n')
    monkeypatch.setattr(wrapper, '__file__', str(tmp_path / 'komodo_deploy.py'))
    calls = []
    fault = {}

    def execute(command, **kwargs):
        if command[:2] == ['docker', 'run']:
            stage, stdout = 'config-validation', b''
        elif command[1] == 'ps':
            stage, stdout = 'readback', b''
        elif command[2] == 'config':
            stage, stdout = 'compose-config', json.dumps(compose).encode()
        elif command[2] == 'exec':
            stage, stdout = 'stamp-readback', json.dumps(manifest).encode()
        else:
            stage, stdout = {'pull': 'pull', 'up': 'recreate'}[command[2]], b''
        calls.append(stage)
        if stage in ('recreate', 'readback', 'stamp-readback'):
            assert (tmp_path / 'release-attempt.json').exists()
        if stage == fault.get('stage'):
            failure = fault.get('exception')
            if failure:
                raise failure('interrupted command')
            return subprocess.CompletedProcess(command, 23, b'PRIVATE_STDOUT', b'PRIVATE_STDERR')
        return subprocess.CompletedProcess(command, 0, stdout, b'')

    monkeypatch.setattr(wrapper.subprocess, 'run', execute)
    return wrapper, manifest, calls, fault


@pytest.mark.parametrize('stage', ['compose-config', 'pull', 'config-validation'])
def test_preflight_failure_can_retry_the_identical_release(
        release_runtime, tmp_path, capsys, stage):
    import json

    wrapper, manifest, calls, fault = release_runtime
    receipt = tmp_path / 'release-deploy.json'
    receipt.write_text('previous successful deployment')
    fault['stage'] = stage
    with pytest.raises(wrapper.CommandError) as error:
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    captured = capsys.readouterr()
    public = str(error.value) + captured.out + captured.err
    assert 'PRIVATE_STDOUT' not in public and 'PRIVATE_STDERR' not in public
    assert f'{stage} failed (exit 23)' in public
    assert 'recreate' not in calls
    assert not (tmp_path / 'release-attempt.json').exists()
    assert receipt.read_text() == 'previous successful deployment'
    fault.clear()
    wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls.count(stage) == 2
    assert calls.count('recreate') == 1
    assert json.loads(receipt.read_text()) == manifest
    assert not (tmp_path / 'release-attempt.json').exists()


@pytest.mark.parametrize('stage', ['recreate', 'readback', 'stamp-readback'])
@pytest.mark.parametrize('failure', [None, SystemExit, KeyboardInterrupt])
def test_failed_or_interrupted_candidate_cannot_be_applied_again(
        release_runtime, tmp_path, stage, failure):
    import json

    wrapper, manifest, calls, fault = release_runtime
    receipt = tmp_path / 'release-deploy.json'
    receipt.write_text('previous successful deployment')
    fault.update(stage=stage, exception=failure)
    with pytest.raises(failure or wrapper.CommandError):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    attempted = list(calls)
    assert (tmp_path / 'release-attempt.json').stat().st_mode & 0o777 == 0o600
    assert receipt.read_text() == 'previous successful deployment'
    for candidate in (manifest, dict(manifest, annotation='changed')):
        with pytest.raises(RuntimeError, match='blocked'):
            wrapper.apply_release(['docker', 'compose'], candidate, tmp_path, 'nora', site=False)
    assert calls == attempted  # not even preflight is repeated after a possible mutation
    fault.clear()
    # Simulate the explicit operator recovery after inspecting the mutation.
    (tmp_path / 'release-attempt.json').unlink()
    wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert json.loads(receipt.read_text()) == manifest
    assert not (tmp_path / 'release-attempt.json').exists()


def test_different_release_can_roll_forward_after_failed_recreation(release_runtime, tmp_path):
    import json

    wrapper, manifest, calls, fault = release_runtime
    fault['stage'] = 'recreate'
    with pytest.raises(wrapper.CommandError):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    fault.clear()
    manifest.update(version='v6.3.2', commit='c' * 40)
    wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls.count('recreate') == 2
    assert json.loads((tmp_path / 'release-deploy.json').read_text()) == manifest
    assert not (tmp_path / 'release-attempt.json').exists()


def test_existing_marker_still_requires_operator_recovery(release_runtime, tmp_path):
    import hashlib
    import json

    wrapper, manifest, calls, _ = release_runtime
    selected = {key: manifest[key] for key in ('version', 'commit', 'services')}
    marker = tmp_path / 'release-attempt.json'
    fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    marker.write_text(json.dumps({
        'manifest_sha256': fingerprint,
        'version': manifest['version'], 'commit': manifest['commit']}))
    with pytest.raises(RuntimeError, match='blocked'):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls == []
    assert marker.exists()


def test_verified_release_can_be_reapplied_after_operator_config_change(release_runtime, tmp_path):
    wrapper, manifest, calls, _ = release_runtime
    for _ in range(2):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls.count('recreate') == 2


@pytest.mark.parametrize('recording_fails', [False, True])
def test_failed_command_keeps_raw_output_private_and_preserves_exit(
        tmp_path, recording_fails):
    import os
    import shutil
    import subprocess
    import sys

    control = tmp_path / 'control'
    control.mkdir(mode=0o700)
    shutil.copyfile(ROOT / 'scripts/komodo_deploy.py', control / 'komodo_deploy.py')
    docker = tmp_path / 'docker'
    docker.write_text(f'#!{sys.executable}\n'
                      'import sys\n'
                      'print("PRIVATE_STDOUT")\n'
                      'print("PRIVATE_STDERR", file=sys.stderr)\n'
                      'sys.exit(23)\n')
    docker.chmod(0o700)
    if recording_fails:
        # The installed control directory disappears before the diagnostic can be created.
        docker.write_text(docker.read_text().replace('import sys',
            f'import sys, shutil; shutil.rmtree({str(control)!r})'))
    previous_umask = os.umask(0)
    try:
        result = subprocess.run(  # noqa: S603 -- synthetic CLI, no Docker daemon
            [sys.executable, str(control / 'komodo_deploy.py'),
             '--runtime-root', str(tmp_path), '--project', 'nora', '--',
             'docker', 'compose', '-f', 'PRIVATE_ARGV', 'config'],
            env={**os.environ, 'PATH': f'{tmp_path}{os.pathsep}' + os.environ['PATH']},
            capture_output=True, text=True, check=False)
    finally:
        os.umask(previous_umask)
    assert result.returncode == 23
    public = result.stdout + result.stderr
    assert 'config-discovery failed (exit 23)' in public
    assert all(sentinel not in public for sentinel in
               ('PRIVATE_STDOUT', 'PRIVATE_STDERR', 'PRIVATE_ARGV'))
    if recording_fails:
        assert 'private diagnostic unavailable' in public
    else:
        diagnostics = list(control.glob('failure-*.log'))
        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert str(diagnostic) in public
        assert diagnostic.stat().st_mode & 0o777 == 0o600
        assert diagnostic.read_bytes() == b'stdout:\nPRIVATE_STDOUT\n\nstderr:\nPRIVATE_STDERR\n'


def test_native_procedure_is_disabled_until_explicit_cutover():
    import tomllib

    resources = tomllib.loads((ROOT / 'deploy/komodo/resources.example.toml').read_text())
    assert 'action' not in resources  # No unsupported self-modifying Action.
    procedure = resources['procedure'][0]['config']
    assert procedure['schedule_enabled'] is False
    assert procedure['stage'][0]['executions'] == [
        {'execution': {'type': 'DeployStackIfChanged', 'params': {'stack': 'nora-releases'}}}]
