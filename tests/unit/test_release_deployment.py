"""Release packaging must cover every built service without moving host state."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
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
    _, manifest = bundle.render(source(), images(bundle), 'v6.3.1', 'b' * 40)
    (tmp_path / 'release.json').write_text(json.dumps(manifest))
    _, previous = bundle.render(source(), images(bundle), 'v6.4.0', 'c' * 40)
    previous.pop('channel')  # manifests published by the installed stable publisher
    calls = []

    def api(repo, path, body=None):
        calls.append((path, body))
        if path == 'git/matching-refs/heads/deployments':
            return [{'ref': 'refs/heads/deployments', 'object': {'sha': 'c' * 40}}]
        if path.startswith('contents/release.json?ref='):
            return {'content': base64.b64encode(json.dumps(previous).encode()).decode()}
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
    for service in compose['services'].values():
        service['volumes'] = [{'target': v.split(':')[1]}
                              for v in service.get('volumes', [])]
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
            stage, stdout = 'stamp-readback', json.dumps(fault.get('stamp', manifest)).encode()
        else:
            stage, stdout = {'pull': 'pull', 'up': (
                'config-reload' if '--force-recreate' in command else 'recreate')}[command[2]], b''
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


@pytest.fixture
def publisher_source(tmp_path, monkeypatch):
    """Real source history/stamps; API responses distinguish trusted request shapes."""
    bundle = load('release_bundle')
    repo = tmp_path / 'source'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'nora').mkdir()
    (repo / 'nora/generated.txt').write_text('generated tracked image input')
    (repo / '.github/workflows').mkdir(parents=True)
    shutil.copy(ROOT / '.github/workflows/release-images.yml', repo / '.github/workflows')
    for _, dockerfile in bundle.BUILDS.values():
        target = repo / dockerfile
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / dockerfile, target)
    (repo / 'app/package-lock.json').write_text('{"generated": true}')
    shutil.copy(ROOT / 'docker-compose.yml', repo)
    shutil.copy(ROOT / 'scripts/gen_version_manifest.py', repo / 'scripts')

    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()  # noqa: S603

    git('init', '-q', '-b', 'master')
    for key, value in [('core.hooksPath', '/dev/null'), ('user.name', 'Test'),
                       ('user.email', 'test@example.invalid'), ('commit.gpgsign', 'false'),
                       ('tag.gpgsign', 'false')]:
        git('config', key, value)
    git('add', '.')
    git('commit', '-qm', 'chore: generated release source')
    git('tag', 'v7.11.0')
    released = git('rev-parse', 'HEAD')
    git('commit', '--allow-empty', '-qm', 'fix: generated master change')
    current = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/master', current)
    state = {'current': current, 'released': released, 'calls': [], 'ci': {
        'head_sha': current, 'head_branch': 'master', 'event': 'push',
        'path': '.github/workflows/ci.yml', 'repository': {'full_name': 'mikeng-io/nora'},
        'head_repository': {'full_name': 'mikeng-io/nora'},
        'status': 'completed', 'conclusion': 'success'}}

    def api(repository, path, body=None):
        assert repository == 'mikeng-io/nora' and body is None
        state['calls'].append(path)
        if path == 'git/matching-refs/heads/deployments':
            return []
        if path == '':
            return {'default_branch': state.get('default_branch', 'master')}
        if path == 'git/ref/heads/master':
            return {'object': {'sha': state['current']}}
        if path == 'releases/tags/v7.11.0':
            return {'draft': False, 'prerelease': False, 'tag_name': 'v7.11.0'}
        if path.startswith('actions/workflows/ci.yml/runs?'):
            assert path == ('actions/workflows/ci.yml/runs?branch=master&event=push'
                            f'&head_sha={state["current"]}&per_page=1')
            if state.get('ci_unavailable'):
                raise RuntimeError('CI API unavailable')
            runs = [] if state.get('ci_missing') else [state['ci']]
            return {'total_count': len(runs), 'workflow_runs': runs}
        raise AssertionError(f'unexpected API operation: {path}')

    monkeypatch.chdir(repo)
    monkeypatch.setattr(bundle, 'api', api)
    for key, value in {'GITHUB_REPOSITORY': 'mikeng-io/nora',
                       'GITHUB_EVENT_NAME': 'workflow_dispatch',
                       'GITHUB_REF': 'refs/heads/master', 'GITHUB_SHA': current,
                       'GITHUB_ACTOR': 'mikeng-io', 'GITHUB_TRIGGERING_ACTOR': 'mikeng-io',
                       'MASTER_DEPLOY_ENABLED': 'true'}.items():
        monkeypatch.setenv(key, value)
    return bundle, state, git


def test_master_selection_pins_once_and_survives_later_master_advance(publisher_source):
    bundle, state, git = publisher_source
    selected = bundle.select('mikeng-io/nora', 'master', '', state['current'])
    assert selected == {'commit': state['current'], 'version': git('describe', '--tags'),
                        'channel': 'master', 'matrix': list(bundle.BUILDS), 'previous': ''}
    git('commit', '--allow-empty', '-qm', 'fix: later generated change')
    git('update-ref', 'refs/remotes/origin/master', git('rev-parse', 'HEAD'))
    git('checkout', '--detach', selected['commit'])
    assert bundle.validate_source('mikeng-io/nora', 'master', selected['version'],
                                  selected['commit']) == selected['commit']
    assert state['calls'].count('git/ref/heads/master') == 1
    assert not any(path.startswith('releases/') for path in state['calls'])


@pytest.mark.parametrize(('key', 'value'), [
    ('MASTER_DEPLOY_ENABLED', ''), ('MASTER_DEPLOY_ENABLED', 'false'),
    ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_REF', 'refs/heads/other'),
    ('GITHUB_REPOSITORY', 'example/fork'), ('GITHUB_ACTOR', 'release-app[bot]'),
    ('GITHUB_TRIGGERING_ACTOR', 'daily-agent[bot]'), ('GITHUB_SHA', 'a' * 40),
])
def test_master_selection_requires_enabled_owner_default_branch_dispatch(
        publisher_source, monkeypatch, key, value):
    bundle, _, _ = publisher_source
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=r'dispatch|source'):
        bundle.select('mikeng-io/nora', 'master', '', '')


@pytest.mark.parametrize('fault', ['candidate', 'checkout', 'default-branch'])
def test_stale_or_non_master_selection_is_refused(publisher_source, fault):
    bundle, state, git = publisher_source
    candidate = state['released'] if fault == 'candidate' else ''
    if fault == 'checkout':
        git('checkout', '--detach', state['released'])
    if fault == 'default-branch':
        state['default_branch'] = 'other'
    with pytest.raises(ValueError, match=r'source|default branch'):
        bundle.select('mikeng-io/nora', 'master', '', candidate)


@pytest.mark.parametrize('fault', ['checkout', 'stamp', 'ancestry'])
def test_master_jobs_revalidate_the_pinned_checkout_stamp_and_ancestry(publisher_source, fault):
    bundle, state, git = publisher_source
    commit, version = state['current'], git('describe', '--tags')
    if fault == 'checkout':
        git('checkout', '--detach', state['released'])
    elif fault == 'stamp':
        version = 'v7.11.0'
    else:
        git('checkout', '-b', 'other', state['released'])
        git('commit', '--allow-empty', '-qm', 'fix: generated other branch')
        commit, version = git('rev-parse', 'HEAD'), git('describe', '--tags')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        bundle.validate_source('mikeng-io/nora', 'master', version, commit)


@pytest.mark.parametrize('fault', ['missing', 'pending', 'failed', 'unavailable',
                                  'foreign', 'wrong-source', 'wrong-workflow', 'pr-event'])
def test_master_build_requires_successful_trusted_exact_source_ci(
        publisher_source, monkeypatch, fault):
    bundle, state, git = publisher_source
    if fault in ('missing', 'unavailable'):
        state[f'ci_{fault}'] = True
    else:
        changes = {'pending': {'status': 'in_progress', 'conclusion': None},
                   'failed': {'conclusion': 'failure'},
                   'foreign': {'head_repository': {'full_name': 'example/fork'}},
                   'wrong-source': {'head_sha': state['released']},
                   'wrong-workflow': {'path': '.github/workflows/other.yml'},
                   'pr-event': {'event': 'pull_request'}}
        state['ci'].update(changes[fault])
    built = []
    real_execute = subprocess.run

    def execute(command, **kwargs):
        if command[:3] != ['docker', 'buildx', 'build']:
            return real_execute(command, **kwargs)
        # A falsified admission guard must never contact a registry during its proof.
        built.append(command)
        Path(command[command.index('--metadata-file') + 1]).write_text(
            json.dumps({'containerimage.digest': 'sha256:' + 'a' * 64}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', execute)
    monkeypatch.setattr(sys, 'argv', ['release_bundle.py', 'build', '--channel', 'master',
                                   '--version', git('describe', '--tags'),
                                   '--source', state['current'], '--image', 'app'])
    with pytest.raises((ValueError, RuntimeError), match='CI'):
        bundle.main()
    assert built == []
    assert not Path('release-output').exists()  # validation precedes any build/receipt


@pytest.mark.parametrize('channel', ['release', 'master', 'master-at-tag'])
def test_complete_image_build_uses_selected_source_version_and_channel(
        publisher_source, monkeypatch, channel):
    bundle, state, git = publisher_source
    if channel != 'master':
        git('checkout', '--detach', state['released'])
        state['current'] = state['released']
        state['ci']['head_sha'] = state['released']
        git('update-ref', 'refs/remotes/origin/master', state['released'])
        monkeypatch.setenv('GITHUB_SHA', state['released'])
    selected = bundle.select('mikeng-io/nora', channel.split('-')[0],
                             'v7.11.0' if channel == 'release' else '', '')
    real_execute, real_run = subprocess.run, bundle.run
    Path('app/untracked.generated').write_text('must never enter the image')
    built, prepared = [], []

    def run(*command):
        if command == ('bash', 'deploy/signal-cli/fetch.sh'):
            prepared.append('signal-cli')
            for suffix in ('', '.asc'):
                Path('deploy/signal-cli/signal-cli-0.0.0.tar.gz' + suffix).write_bytes(b'generated')
            return ''
        return real_run(*command)

    def execute(command, **kwargs):
        if command[:3] != ['docker', 'buildx', 'build']:
            return real_execute(command, **kwargs)
        built.append(command)
        if Path(command[-1]).name == 'app':
            assert not (Path(command[-1]) / 'untracked.generated').exists()
            assert (Path(command[-1]) / 'Dockerfile').read_bytes() == (
                ROOT / 'app/Dockerfile').read_bytes()
        metadata = Path(command[command.index('--metadata-file') + 1])
        metadata.write_text(json.dumps({'containerimage.digest': 'sha256:' + 'a' * 64}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bundle, 'run', run)
    monkeypatch.setattr(subprocess, 'run', execute)
    args = ['--channel', selected['channel'], '--version', selected['version'],
            '--source', selected['commit']]
    for name in bundle.BUILDS:
        monkeypatch.setattr(sys, 'argv', ['release_bundle.py', 'build', *args, '--image', name])
        bundle.main()
        receipt = json.loads(Path(f'release-output/{name}.json').read_text())['artifact']
        assert (receipt['name'], receipt['version'], receipt['commit'], receipt['channel']) == (
            name, selected['version'], selected['commit'], selected['channel'])
        identity = selected['version'] if channel == 'release' else f'sha-{selected["commit"]}'
        assert built[-1][built[-1].index('-t') + 1] == f'{bundle.REGISTRY}{name}:{identity}'
        assert f'org.opencontainers.image.revision={selected["commit"]}' in built[-1]
        assert f'org.opencontainers.image.version={selected["version"]}' in built[-1]
    assert prepared == ['signal-cli'] and len(built) == 10
    stamp = json.loads(Path('nora/version.json').read_text())
    assert (stamp['version'], stamp['commit']) == (selected['version'], selected['commit'])
    monkeypatch.setattr(sys, 'argv', ['release_bundle.py', 'bundle', *args])
    bundle.main()
    manifest = json.loads(Path('release-output/release.json').read_text())
    assert manifest['channel'] == selected['channel']
    assert load('komodo_deploy').expected_images(manifest, site=True) == manifest['services']


def write_image_receipts(bundle, directory, version, commit, channel):
    directory.mkdir(exist_ok=True)
    selected = {'commit': commit, 'version': version, 'channel': channel}
    for name, image in images(bundle).items():
        artifact = {**selected, 'name': name, 'image': image,
                    'inputs': bundle.build_inputs(name, commit,
                        {'signal-cli-0.0.0.tar.gz': 'a' * 64,
                         'signal-cli-0.0.0.tar.gz.asc': 'b' * 64}
                        if name == 'signal-cli' else None)}
        (directory / f'{name}.json').write_text(json.dumps({
            'selection': selected, 'artifact': artifact}))


@pytest.mark.parametrize('fault', ['missing', 'name', 'commit', 'version', 'channel', 'image'])
def test_bundle_refuses_receipts_outside_the_complete_selected_cohort(publisher_source, fault):
    bundle, state, git = publisher_source
    directory = Path('release-output')
    version = git('describe', '--tags')
    write_image_receipts(bundle, directory, version, state['current'], 'master')
    path = directory / 'app.json'
    record = json.loads(path.read_text())
    if fault == 'missing':
        path.unlink()
    else:
        record['artifact'][fault] = 'wrong'
        path.write_text(json.dumps(record))
    with pytest.raises((ValueError, FileNotFoundError)):
        bundle.bundle(directory, version, state['current'], 'master')


@pytest.mark.parametrize('filename', ['release.json', 'compose.json'])
def test_promotion_refuses_a_bundle_changed_after_receipt_validation(
        publisher_source, monkeypatch, filename):
    bundle, state, git = publisher_source
    directory = Path('release-output')
    directory.mkdir()
    version = git('describe', '--tags')
    write_image_receipts(bundle, directory, version, state['current'], 'master')
    args = ['--channel', 'master', '--source', state['current'], '--version', version]
    monkeypatch.setattr(sys, 'argv', ['release_bundle.py', 'bundle', *args])
    bundle.main()
    path = directory / filename
    changed = json.loads(path.read_text())
    changed['unexpected'] = 'altered bundle'
    path.write_text(json.dumps(changed))
    monkeypatch.setattr(sys, 'argv', ['release_bundle.py', 'promote', *args])
    with pytest.raises(ValueError, match='promotion bundle'):
        bundle.main()
    assert 'git/matching-refs/heads/deployments' not in state['calls']


@pytest.fixture
def publication(tmp_path, monkeypatch):
    """Git-data API fake with separate compare/create/CAS/readback operations."""
    import base64

    bundle = load('release_bundle')
    compose, candidate = bundle.render(source(), images(bundle), 'v7.11.0', 'a' * 40)
    _, previous = bundle.render(source(), images(bundle), 'v7.11.0-2-gbbbbbbb',
                                 'b' * 40, 'master')
    (tmp_path / 'compose.json').write_text(json.dumps(compose))
    (tmp_path / 'release.json').write_text(json.dumps(candidate))
    state = {'head': 'c' * 40, 'previous': previous, 'candidate': candidate,
             'comparison': 'behind', 'calls': [], 'writes': []}

    def api(repo, path, body=None):
        assert repo == 'mikeng-io/nora'
        state['calls'].append(path)
        if path == 'git/matching-refs/heads/deployments':
            return [{'ref': 'refs/heads/deployments', 'object': {'sha': state['head']}}]
        if path.startswith('contents/release.json?ref='):
            ref = path.split('ref=')[1]
            assert ref in ('c' * 40, 'e' * 40)
            manifest = state['previous'] if ref == 'c' * 40 else state['candidate']
            if ref == 'e' * 40 and state.get('wrong_readback'):
                manifest = dict(manifest, commit='f' * 40)
            return {'content': base64.b64encode(json.dumps(manifest).encode()).decode()}
        if path.startswith('compare/'):
            assert path == f'compare/{state["previous"]["commit"]}...{candidate["commit"]}'
            if state.get('unavailable'):
                raise RuntimeError('comparison API unavailable')
            status = ('identical' if state['previous']['commit'] == candidate['commit']
                      else state['comparison'])
            merge_base = (state['previous']['commit'] if status in ('ahead', 'identical')
                          else candidate['commit'] if status == 'behind' else 'f' * 40)
            return {'status': status,
                    'base_commit': {'sha': state['previous']['commit']},
                    'merge_base_commit': {'sha': merge_base}}
        if path == 'git/trees' and body:
            assert {item['path'] for item in body['tree']} == {'compose.json', 'release.json'}
            state['writes'].append('tree')
            return {'sha': 'd' * 40}
        if path == 'git/commits' and body:
            assert body['parents'] == ['c' * 40] and body['tree'] == 'd' * 40
            state['writes'].append('commit')
            return {'sha': 'e' * 40}
        if path == 'git/ref/heads/deployments':
            return {'object': {'sha': state['head']}}
        raise AssertionError(f'unexpected Git API operation: {path}')

    def execute(command, *, input, text, check, capture_output):
        assert command == ['gh', 'api', 'repos/mikeng-io/nora/git/refs/heads/deployments',
                           '--method', 'PATCH', '--input', '-']
        assert json.loads(input) == {'sha': 'e' * 40, 'force': False}
        if state.get('race'):
            raise subprocess.CalledProcessError(1, command, stderr='not a fast forward')
        state['head'] = 'e' * 40
        state['previous'] = copy.deepcopy(candidate)
        state['writes'].append('ref')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bundle, 'api', api)
    monkeypatch.setattr(subprocess, 'run', execute)
    return bundle, state, tmp_path


@pytest.mark.parametrize('comparison', ['behind', 'diverged', 'identical'])
def test_delayed_release_cannot_replace_selected_master_source(publication, comparison):
    bundle, state, directory = publication
    state['comparison'] = comparison
    if comparison == 'identical':
        state['candidate']['commit'] = state['previous']['commit']
        (directory / 'release.json').write_text(json.dumps(state['candidate']))
    with pytest.raises(ValueError, match='source'):
        bundle.promote('mikeng-io/nora', directory)
    assert state['writes'] == [] and state['head'] == 'c' * 40


def test_delayed_master_cannot_replace_newer_selected_release(publication):
    bundle, state, directory = publication
    state['previous'] = dict(state['previous'], version='v7.11.0', channel='release')
    state['candidate'].update(version='v7.11.0-1-gaaaaaaa', channel='master')
    (directory / 'release.json').write_text(json.dumps(state['candidate']))
    with pytest.raises(ValueError, match='source'):
        bundle.promote('mikeng-io/nora', directory)
    assert state['writes'] == []


@pytest.mark.parametrize('direction', ['release-to-master', 'master-to-release'])
def test_forward_publication_reads_back_complete_selection_without_claiming_deploy(
        publication, capsys, direction):
    bundle, state, directory = publication
    if direction == 'release-to-master':
        state['previous'] = dict(state['previous'], version='v7.11.0', channel='release')
        state['candidate'].update(version='v7.11.0-3-gaaaaaaa', channel='master')
    else:
        state['candidate'].update(version='v7.12.0')
    (directory / 'release.json').write_text(json.dumps(state['candidate']))
    state['comparison'] = 'ahead'
    bundle.promote('mikeng-io/nora', directory)
    assert state['writes'] == ['tree', 'commit', 'ref']
    assert state['head'] == 'e' * 40
    assert state['calls'][-1] == f'contents/release.json?ref={"e" * 40}'
    assert 'actual deployment is unverified' in capsys.readouterr().out


def test_identical_snapshot_rerun_refuses_without_a_deployment_claim(publication, capsys):
    bundle, state, directory = publication
    state['candidate'].update(version='v7.11.0-3-gaaaaaaa', channel='master')
    (directory / 'release.json').write_text(json.dumps(state['candidate']))
    state['comparison'] = 'ahead'
    bundle.promote('mikeng-io/nora', directory)
    capsys.readouterr()
    writes = list(state['writes'])
    with pytest.raises(ValueError, match='source'):
        bundle.promote('mikeng-io/nora', directory)
    assert state['writes'] == writes
    assert capsys.readouterr().out == ''


def test_publication_readback_mismatch_never_reports_success(publication, capsys):
    bundle, state, directory = publication
    state.update(comparison='ahead', wrong_readback=True)
    with pytest.raises(ValueError, match='readback'):
        bundle.promote('mikeng-io/nora', directory)
    assert state['writes'] == ['tree', 'commit', 'ref']  # selected, but not verified
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('fault', ['missing-source', 'invalid-version', 'partial-cohort',
                                  'unavailable', 'race'])
def test_invalid_or_unavailable_previous_state_cannot_be_overwritten(publication, fault):
    bundle, state, directory = publication
    if fault == 'missing-source':
        del state['previous']['commit']
    elif fault == 'invalid-version':
        state['previous']['version'] = 'untrusted'
    elif fault == 'partial-cohort':
        del state['previous']['services']['voice-wake']
    else:
        state[fault] = True
        state['comparison'] = 'ahead'
    with pytest.raises((ValueError, KeyError, RuntimeError, subprocess.CalledProcessError)):
        bundle.promote('mikeng-io/nora', directory)
    assert 'ref' not in state['writes'] and state['head'] == 'c' * 40


@pytest.mark.parametrize('failure', ['config', 'stamp'])
def test_snapshot_wrapper_retains_validation_failure_and_final_stamp_boundary(
        release_runtime, tmp_path, failure):
    wrapper, manifest, calls, fault = release_runtime
    manifest.update(version='v7.11.0-2-gbbbbbbb', channel='master')
    if failure == 'config':
        fault['stage'] = 'config-validation'
    else:
        fault['stamp'] = dict(manifest, commit='a' * 40)
    with pytest.raises(wrapper.CommandError if failure == 'config' else ValueError):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert not (tmp_path / 'release-deploy.json').exists()
    if failure == 'config':
        assert 'recreate' not in calls
    else:
        with pytest.raises(RuntimeError, match='blocked'):
            wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
        (tmp_path / 'release-attempt.json').unlink()  # explicit operator recovery in the fake
    fault.clear()
    wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls[-3:] == ['recreate', 'readback', 'stamp-readback']
    assert json.loads((tmp_path / 'release-deploy.json').read_text()) == manifest
    assert not (tmp_path / 'release-attempt.json').exists()


@pytest.mark.parametrize(('version', 'channel', 'commit'), [
    ('v7.11.0-2-gaaaaaaa', 'master', 'b' * 40),
    ('v7.11.0-2-gbbbbbbb', 'release', 'b' * 40),
    ('v7.11.0-2-gbbbbbbb', 'nightly', 'b' * 40),
    ('v7.11.0-0-gbbbbbbb', 'master', 'b' * 40),
])
def test_wrapper_refuses_forged_or_unsupported_snapshot_identity(version, channel, commit):
    bundle = load('release_bundle')
    _, manifest = bundle.render(source(), images(bundle), 'v7.11.0', 'b' * 40)
    manifest.update(version=version, channel=channel, commit=commit)
    with pytest.raises(ValueError):
        load('komodo_deploy').expected_images(manifest, site=False)


def test_reuse_preserves_original_provenance_and_checks_current_inputs(
        publisher_source, monkeypatch):
    from types import SimpleNamespace

    bundle, state, git = publisher_source
    old_commit = state['current']
    old_version = git('describe', '--tags')
    directory = Path('release-output')
    write_image_receipts(bundle, directory, old_version, old_commit, 'master')
    _, previous = bundle.bundle(directory, old_version, old_commit, 'master')
    old_app = copy.deepcopy(previous['artifacts']['app'])
    Path('unrelated.txt').write_text('generated unrelated change')
    git('add', '.')
    git('commit', '-qm', 'fix: generated unrelated source change')
    commit, version = git('rev-parse', 'HEAD'), git('describe', '--tags')
    write_image_receipts(bundle, directory, version, commit, 'master')
    commands = []
    real_run = bundle.run

    def run(*command):
        if command[:4] == ('docker', 'buildx', 'imagetools', 'inspect'):
            commands.append(command)
            return 'verified immutable image available'
        return real_run(*command)

    monkeypatch.setattr(bundle, 'run', run)
    bundle.publish_image(SimpleNamespace(image='app', directory=directory, version=version,
                                        channel='master'), commit, previous)
    generated, manifest = bundle.bundle(directory, version, commit, 'master', previous)
    assert manifest['commit'] == commit and manifest['version'] == version
    assert manifest['artifacts']['app'] == old_app  # no relabel/restamp of reused bytes
    assert manifest['artifacts']['app']['commit'] == old_commit != commit
    assert generated['services']['app']['image'] == old_app['image']
    assert len(manifest['services']) == 12 and len(manifest['artifacts']) == 10
    assert commands == [('docker', 'buildx', 'imagetools', 'inspect', old_app['image'])]
    # Same context is insufficient without the exact previous provenance receipt.
    forged = json.loads((directory / 'app.json').read_text())
    forged['artifact']['version'] = 'v1.0.0'
    (directory / 'app.json').write_text(json.dumps(forged))
    with pytest.raises(ValueError, match='provenance'):
        bundle.bundle(directory, version, commit, 'master', previous)


@pytest.mark.parametrize('change', ['lockfile', 'dockerfile', 'basepin', 'ignore',
                                   'specific-ignore', 'workflow', 'publisher', 'shared-copy',
                                   'signal-recipe', 'whatsapp-recipe'])
def test_reuse_invalidates_changed_effective_inputs(publisher_source, monkeypatch, change):
    bundle, state, git = publisher_source
    name = 'site' if change == 'shared-copy' else 'app'
    if change in ('signal-recipe', 'whatsapp-recipe'):
        name = 'signal-cli' if change == 'signal-recipe' else 'whatsapp-bridge'
    old = bundle.build_inputs(name, state['current'])
    assert old['reusable']  # positive control: actual reviewed recipe is admitted
    paths = {'lockfile': 'app/package-lock.json', 'dockerfile': 'app/Dockerfile',
             'basepin': 'app/Dockerfile', 'ignore': 'app/.dockerignore',
             'specific-ignore': 'app/Dockerfile.dockerignore',
             'workflow': '.github/workflows/release-images.yml',
             'shared-copy': 'assets/generated.txt',
             'signal-recipe': 'deploy/signal-cli/Dockerfile',
             'whatsapp-recipe': 'deploy/whatsapp-bridge/Dockerfile'}
    if change == 'publisher':
        altered = Path('publisher.py')
        altered.write_bytes(Path(bundle.__file__).read_bytes() + b'\n# changed flags\n')
        monkeypatch.setattr(bundle, '__file__', str(altered.resolve()))
    else:
        path = Path(paths[change])
        path.parent.mkdir(parents=True, exist_ok=True)
        old_bytes = path.read_text() if path.exists() else ''
        path.write_text(old_bytes.replace('node:24-', 'node:25-') if change == 'basepin'
                        else old_bytes + '\n# generated input change\n')
        git('add', '.')
        git('commit', '-qm', 'fix: generated effective input change')
    current = bundle.build_inputs(name, git('rev-parse', 'HEAD'))
    artifact = {'inputs': old, 'name': name, 'image': images(bundle)[name],
                'commit': state['current'], 'version': 'v7.11.0', 'channel': 'master'}
    previous = {'artifacts': {name: artifact}, 'services': {name: artifact['image']}}
    real_run = bundle.run
    monkeypatch.setattr(bundle, 'run', lambda *cmd: 'available' if cmd[0] == 'docker'
                        else real_run(*cmd))
    assert current != old
    assert bundle.reusable_artifact(name, current, previous) is None


def test_unknown_reuse_evidence_bootstraps_and_unavailable_registry_refuses(
        publisher_source, monkeypatch):
    bundle, state, _ = publisher_source
    inputs = bundle.build_inputs('app', state['current'])
    assert bundle.reusable_artifact('app', inputs, {}) is None
    assert bundle.reusable_artifact('app', inputs, {'artifacts': {'app': {'name': 'app'}}}) is None
    assert bundle.reusable_artifact('app', dict(inputs, reusable=False), {}) is None
    artifact = {'name': 'app', 'image': images(bundle)['app'], 'commit': state['current'],
                'version': 'v7.11.0', 'channel': 'master', 'inputs': inputs}
    previous = {'services': {'app': artifact['image']}, 'artifacts': {'app': artifact}}

    def unavailable(*command):
        assert command[:4] == ('docker', 'buildx', 'imagetools', 'inspect')
        raise RuntimeError('registry unavailable')

    monkeypatch.setattr(bundle, 'run', unavailable)
    with pytest.raises(RuntimeError, match='registry unavailable'):
        bundle.reusable_artifact('app', inputs, previous)


def test_nora_stamp_is_never_reused_and_signal_fetched_bytes_invalidate(publisher_source):
    bundle, state, _ = publisher_source
    assert not bundle.build_inputs('nora', state['current'])['reusable']
    one = bundle.build_inputs('signal-cli', state['current'], {'signed-archive': 'a' * 64})
    two = bundle.build_inputs('signal-cli', state['current'], {'signed-archive': 'b' * 64})
    assert one['reusable'] and one != two
    assert bundle.reusable_artifact('signal-cli', two, {
        'artifacts': {'signal-cli': {'inputs': one}}}) is None


def test_native_reconcile_forces_only_explicit_file_readers(tmp_path, monkeypatch):
    wrapper = load('komodo_deploy')
    request = tmp_path / 'config-reload.json'
    resolved = {'services': {
        'nora': {'volumes': [{'target': '/app/policy.toml'}, {'target': '/app/config.toml'}]},
        'presence': {'volumes': [{'target': '/app/config.toml'}]},
        'app': {'volumes': [{'target': '/app/identity.toml'}]},
        'signal-cli': {}}}
    pins = dict.fromkeys(resolved['services'], 'synthetic-image')
    commands = []
    monkeypatch.setattr(wrapper, 'run', lambda cmd, **kw: commands.append(cmd))
    wrapper.reconcile_services(['docker', 'compose'], pins,
                               wrapper.reload_services(resolved, pins, request))
    assert len(commands) == 1 and '--force-recreate' not in commands[0]
    assert commands[0][-4:] == ['nora', 'presence', 'app', 'signal-cli']
    request.write_text(json.dumps({'files': ['policy.toml']}))
    readers = wrapper.reload_services(resolved, pins, request)
    wrapper.reconcile_services(['docker', 'compose'], pins, readers)
    assert readers == ['nora']
    assert commands[-1][-2:] == ['--force-recreate', 'nora']
    assert '--force-recreate' not in commands[-2]
    for files in ([], ['.env'], ['config.toml', '.env']):
        request.write_text(json.dumps({'files': files}))
        with pytest.raises(ValueError, match='reload'):
            wrapper.reload_services(resolved, pins, request)


@pytest.mark.parametrize('failure_stage', ['config-reload', 'readback'])
def test_scoped_reload_consumed_only_after_verified_transaction(
        release_runtime, tmp_path, failure_stage):
    wrapper, manifest, calls, fault = release_runtime
    request = tmp_path / 'config-reload.json'
    request.write_text(json.dumps({'files': ['config.toml']}))
    fault['stage'] = failure_stage
    with pytest.raises(wrapper.CommandError):
        wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert request.exists() and (tmp_path / 'release-attempt.json').exists()
    fault.clear()
    (tmp_path / 'release-attempt.json').unlink()  # explicit recovery, same native transaction
    wrapper.apply_release(['docker', 'compose'], manifest, tmp_path, 'nora', site=False)
    assert calls.count('config-reload') == 2
    assert not request.exists() and not (tmp_path / 'release-attempt.json').exists()


@pytest.mark.parametrize('change', ['args', 'context', 'dockerfile', 'secret', 'platforms'])
def test_unknown_compose_build_contract_refuses_publication(change):
    bundle = load('release_bundle')
    compose = source()
    compose['services']['app']['build'][change] = (
        {'GENERATED_ARG': 'different'} if change == 'args' else 'unknown')
    with pytest.raises(ValueError, match='build inputs'):
        bundle.render(compose, images(bundle), 'v7.11.1', 'b' * 40)


def test_selection_pins_previous_manifest_by_immutable_commit(publisher_source, monkeypatch):
    import base64

    bundle, state, _ = publisher_source
    _, previous = bundle.render(source(), images(bundle), 'v7.11.0', state['released'])
    real_api = bundle.api
    reads = []
    deployment_commit = 'd' * 40

    def api(repo, path, body=None):
        if path == 'git/matching-refs/heads/deployments':
            return [{'ref': 'refs/heads/deployments', 'object': {'sha': deployment_commit}}]
        if path.startswith('contents/release.json?ref='):
            reads.append(path)
            assert path == 'contents/release.json?ref=' + deployment_commit
            return {'content': base64.b64encode(json.dumps(previous).encode()).decode()}
        return real_api(repo, path, body)

    monkeypatch.setattr(bundle, 'api', api)
    selected = bundle.select('mikeng-io/nora', 'master', '', '')
    assert selected['previous'] == deployment_commit
    assert reads == ['contents/release.json?ref=' + deployment_commit]
    assert bundle.previous_manifest('mikeng-io/nora', selected['previous']) == previous
