"""Komodo Compose command wrapper; install beside verify-deploy.py on the host.

Komodo owns Git, scheduling and change detection. This wraps only config/up.
No host secret is printed by config; up succeeds only after independent readback.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REGISTRY = 'ghcr.io/mikeng-io/nora-'
COHORT = {
    'nora': 'nora', 'presence': 'nora', 'analytics': 'nora',
    'app': 'app', 'site': 'site', 'stt': 'stt', 'voice-wake': 'voice-wake',
    'voice-sidecar': 'voice-sidecar', 'signal-cli': 'signal-cli',
    'whatsapp-bridge': 'whatsapp-bridge', 'instagram-bridge': 'instagram-bridge',
}


class CommandError(RuntimeError):
    """Only safe metadata may cross the Komodo output boundary."""

    def __init__(self, stage: str, returncode: int, diagnostic: Path | None):
        self.returncode = returncode
        detail = (f'private diagnostic: {diagnostic}' if diagnostic
                  else 'private diagnostic unavailable')
        super().__init__(f'{stage} failed (exit {returncode}); {detail}')


def run(command: list[str], *, stage: str, output: bool = False) -> str:
    # Capture subprocess output: Compose config and validation errors can quote secrets.
    result = subprocess.run(  # noqa: S603 -- validated Compose argv, no shell
        command, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    if result.returncode:
        diagnostic = None
        try:
            # mkstemp semantics: exclusive creation, mode 0600 even with a permissive umask.
            with tempfile.NamedTemporaryFile(prefix='failure-', suffix='.log',
                                             dir=Path(__file__).parent, delete=False) as private:
                private.write(b'stdout:\n' + result.stdout + b'\nstderr:\n' + result.stderr)
                diagnostic = Path(private.name)
        except OSError:
            # Recording failure must neither leak its exception nor hide the command's exit.
            diagnostic = None
        raise CommandError(stage, result.returncode, diagnostic) from None
    return result.stdout.decode(errors='replace') if output else ''


def compose_prefix(command: list[str]) -> tuple[list[str], str]:
    if command[:2] != ['docker', 'compose']:
        raise ValueError('expected native docker compose command')
    for index, token in enumerate(command[2:], 2):
        if token in ('config', 'up'):
            return command[:index], token
    raise ValueError('only config/up may use this wrapper')


def expected_images(manifest: dict, *, site: bool) -> dict[str, str]:
    number = r'(?:0|[1-9][0-9]*)'
    version = re.fullmatch(rf'v{number}\.{number}\.{number}(?:-[1-9][0-9]*-g([0-9a-f]{{7,40}}))?',
                           manifest['version'])
    channel = manifest.get('channel', 'release')  # existing stable manifests remain valid
    if version is None or channel not in ('release', 'master'):
        raise ValueError('invalid deployment version or channel')
    if not re.fullmatch(r'[0-9a-f]{40}', manifest['commit']):
        raise ValueError('invalid source commit')
    if version[1] and (channel != 'master' or not manifest['commit'].startswith(version[1])):
        raise ValueError('snapshot stamp differs from master source')
    images = manifest['services']
    if set(images) != set(COHORT):
        raise ValueError('release service set differs from installed ownership')
    for service, image in images.items():
        pattern = re.escape(REGISTRY + COHORT[service]) + r'@sha256:[0-9a-f]{64}'
        if not re.fullmatch(pattern, image):
            raise ValueError(f'invalid pinned image for {service}')
    if len({images[s] for s in ('nora', 'presence', 'analytics')}) != 1:
        raise ValueError('shared Nora services must use one image')
    return {s: image for s, image in images.items() if s != 'site' or site}


def reload_services(resolved: dict, images: dict, request: Path) -> list[str]:
    """Only an explicit host-local request forces loaded-once file readers."""
    if not request.exists():
        return []
    value = json.loads(request.read_text())
    if not isinstance(value, dict):
        raise ValueError('invalid scoped config reload request')
    files = value['files']
    if (set(value) != {'files'} or not isinstance(files, list) or not files
            or not all(isinstance(f, str) for f in files)
            or not set(files) <= {
                'config.toml', 'identity.toml', 'policy.toml', 'secret.toml', '.env'}):
        raise ValueError('invalid scoped config reload request')
    if set(files) & {'secret.toml', '.env'}:
        return [name for name in images if name not in {'site', 'stt', 'signal-cli'}]
    targets = {'/app/' + f for f in files}
    readers = [name for name in images if any(
        v['target'] in targets for v in resolved['services'][name].get('volumes', []))]
    if not readers:
        raise ValueError('reload request has no owned file readers')
    return readers


def reconcile_services(prefix: list[str], images: dict, reload: list[str]) -> None:
    options = ['up', '-d', '--no-deps', '--no-build', '--pull', 'never']
    # Native Compose hashes image + effective service config. Never force the
    # normal cohort: unchanged containers must retain their identity.
    run([*prefix, *options, *images], stage='recreate')
    if reload:
        run([*prefix, *options, '--force-recreate', *reload], stage='config-reload')


def apply_release(prefix: list[str], manifest: dict, runtime: Path, project: str,
                  *, site: bool) -> None:
    """Under the shared lock, allow preflight retries but never repeat a failed recreation."""
    images = expected_images(manifest, site=site)
    selected = {key: manifest[key] for key in ('version', 'commit', 'services')}
    fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    marker = Path(__file__).with_name('release-attempt.json')
    if marker.exists() and json.loads(marker.read_text())['manifest_sha256'] == fingerprint:
        raise RuntimeError('candidate blocked after failed/interrupted deployment; '
                           'inspect privately')
    try:
        from secret_delivery import prepare
    except ModuleNotFoundError:
        from scripts.secret_delivery import prepare
    prefix = [*prefix, *prepare(
        runtime, output_directory=Path(__file__).parent / '.secret-delivery')]
    if site:
        prefix = [*prefix, '--profile', 'site']
    # Resolve only in this captured host process; never return this config to Komodo.
    resolved = json.loads(run([*prefix, 'config', '--format', 'json'],
                              stage='compose-config', output=True))
    if resolved['name'] != project:
        raise ValueError('Compose project differs from installed project')
    for service, image in images.items():
        definition = resolved['services'][service]
        if definition['image'] != image or 'build' in definition:
            raise ValueError(f'{service}: Compose differs from pinned manifest')
    request = Path(__file__).with_name('config-reload.json')
    reload = reload_services(resolved, images, request)
    nora = resolved['services']['nora']
    if any(v['target'] in ('/app/nora/doctrine', '/app/nora/dreamer')
           for v in nora.get('volumes', [])):
        raise ValueError('release instructions must come from the image')
    config_mounts = [v for v in nora.get('volumes', [])
                     if v['target'] == '/app/config.toml']
    if len(config_mounts) != 1 or Path(config_mounts[0]['source']) != runtime / 'config.toml':
        raise ValueError('candidate config mount differs from validated host config')
    # Pull every candidate before stopping any running service. Infrastructure is excluded.
    print('PULL: fetch every owned image before recreation', flush=True)
    run([*prefix, 'pull', *images], stage='pull')
    print('VALIDATE: candidate image against installed host config', flush=True)
    # A copy avoids Docker creating a missing mount source in the runtime directory.
    # This directory must be bind-mounted at the SAME path in Periphery and the host.
    with tempfile.TemporaryDirectory(prefix='nora-config-check-',
                                     dir=Path(__file__).parent) as temporary:
        config = Path(temporary) / 'config.toml'
        config.write_bytes((runtime / 'config.toml').read_bytes())
        config.chmod(0o600)
        run(['docker', 'run', '--rm', '--network', 'none',
             '--user', f'{os.getuid()}:{os.getgid()}',
             '-v', f'{config}:/tmp/config.toml:ro', '--entrypoint', '/app/.venv/bin/nora',
             images['nora'], 'config', 'validate', '--config', '/tmp/config.toml'],  # noqa: S108 -- container path
            stage='config-validation')
        environment = nora.get('environment')
        if isinstance(environment, dict):
            if any(not isinstance(v, str) or '\n' in v or '\r' in v
                   for v in environment.values()):
                raise ValueError('invalid candidate credential environment')
            candidate_env = Path(temporary) / 'candidate.env'
            # Compose renders literal dollars escaped for a second parse.
            decoded = {k: v.replace('$$', '$') for k, v in environment.items()}
            candidate_env.write_text(''.join(f'{k}={v}\n' for k, v in decoded.items()))
            candidate_env.chmod(0o600)
            run(['docker', 'run', '--rm', '--network', 'none',
                 '--user', f'{os.getuid()}:{os.getgid()}',
                 '--env-file', str(candidate_env), '-v', f'{config}:/tmp/config.toml:ro',
                 '--entrypoint', '/app/.venv/bin/python', images['nora'], '-c',
                 'from pathlib import Path; from nora.config import load; '
                 'load(Path("/tmp/config.toml"))'], stage='credential-validation')
    spec = importlib.util.spec_from_file_location('verify_deploy',
                                                 Path(__file__).with_name('verify-deploy.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('installed verifier missing')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    # The standalone verifier includes raw Docker stderr in SystemExit. Keep its
    # subprocesses behind this wrapper's private diagnostic boundary as well.
    verifier._docker = lambda *args: run(['docker', *args], stage='readback', output=True)
    baseline = {row['container_id']: row['restarts']
                for row in verifier.containers_for_services(project, set(images))}
    # Only recreation can change the running cohort. Write BEFORE it so process
    # death, partial recreation and verifier SystemExit all leave the candidate blocked.
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({'manifest_sha256': fingerprint,
                                     'version': manifest['version'], 'commit': manifest['commit']}))
    temporary.chmod(0o600)
    temporary.replace(marker)
    print('RECONCILE: owned services; explicit config reload if requested', flush=True)
    reconcile_services(prefix, images, reload)
    print('READBACK: exact service membership, image IDs, health and version', flush=True)
    verifier.verify_services(project, images, baseline=baseline)
    stamp = json.loads(run([*prefix, 'exec', '-T', 'nora', '/app/.venv/bin/python', '-c',
                            'import json; print(json.dumps(json.load(open('
                            '"/app/nora/version.json"))))'], stage='stamp-readback', output=True))
    if (stamp['version'], stamp['commit']) != (manifest['version'], manifest['commit']):
        raise ValueError('running Nora stamp differs from release')
    receipt = Path(__file__).with_name('release-deploy.json')
    candidate = receipt.with_suffix('.tmp')
    candidate.write_text(json.dumps(manifest, indent=2) + '\n')
    candidate.replace(receipt)
    if reload:
        request.unlink()  # consumed only after the whole cohort and stamp are verified
    marker.unlink()  # only a fully verified transaction clears the outstanding attempt
    print(f'VERIFIED: deployed {manifest["version"]}; receipt recorded on host')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--site', action='store_true', help='include the optional public site')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    prefix, operation = compose_prefix(command)
    if operation == 'config':
        # Komodo stores this output. Resolve no host env values or paths here.
        print(run([*prefix, 'config', '--no-interpolate', '--no-env-resolution',
                   '--no-path-resolution'], stage='config-discovery', output=True), end='')
        return
    if not args.runtime_root.is_absolute() or not re.fullmatch(r'[a-z0-9_-]+', args.project):
        raise ValueError('install an absolute runtime path and fixed Compose project')
    # flock -o closes the descriptor for descendants; all manual deploys share this lock.
    if os.environ.get('NORA_DEPLOY_LOCKED') != '1':
        env = {**os.environ, 'NORA_DEPLOY_LOCKED': '1'}
        os.execvpe('flock',  # noqa: S606,S607 -- existing host lock protocol
                  ['flock', '-o', '-E', '75', '-w', '900',
                            '/run/nora-deploy.lock',  # same host inode; non-sticky parent
                            sys.executable, __file__, *sys.argv[1:]], env)
    manifest = json.loads(Path('release.json').read_text())
    apply_release(prefix, manifest, args.runtime_root, args.project, site=args.site)


if __name__ == '__main__':
    try:
        main()
    except CommandError as error:
        print(f'FAILED: {error}; no successful receipt was recorded', file=sys.stderr)
        exit_code = error.returncode if error.returncode > 0 else 128 - error.returncode
        raise SystemExit(exit_code) from None
    except (ValueError, RuntimeError, KeyError, OSError, json.JSONDecodeError) as error:
        # Do not echo data or command argv: they can contain private host configuration.
        print(f'FAILED: release deployment ({type(error).__name__}); '
              'no successful receipt was recorded', file=sys.stderr)
        raise SystemExit(1) from None
