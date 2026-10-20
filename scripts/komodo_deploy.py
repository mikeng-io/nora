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
    'alert-bridge': 'alert-bridge',
}


def run(command: list[str], *, output: bool = False) -> str:
    # Capture subprocess output: Compose config and validation errors can quote secrets.
    result = subprocess.run(  # noqa: S603 -- validated Compose argv, no shell
        command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, check=False)
    if result.returncode:
        raise RuntimeError(f'{command[0]} operation failed (exit {result.returncode}); '
                           'inspect privately on the host')
    return result.stdout if output else ''


def compose_prefix(command: list[str]) -> tuple[list[str], str]:
    if command[:2] != ['docker', 'compose']:
        raise ValueError('expected native docker compose command')
    for index, token in enumerate(command[2:], 2):
        if token in ('config', 'up'):
            return command[:index], token
    raise ValueError('only config/up may use this wrapper')


def expected_images(manifest: dict, *, site: bool) -> dict[str, str]:
    if not re.fullmatch(r'v\d+\.\d+\.\d+', manifest['version']):
        raise ValueError('invalid stable release')
    if not re.fullmatch(r'[0-9a-f]{40}', manifest['commit']):
        raise ValueError('invalid source commit')
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


def deploy(prefix: list[str], manifest: dict, runtime: Path, project: str, *, site: bool) -> None:
    images = expected_images(manifest, site=site)
    if site:
        prefix = [*prefix, '--profile', 'site']
    # Resolve only in this captured host process; never return this config to Komodo.
    resolved = json.loads(run([*prefix, 'config', '--format', 'json'], output=True))
    if resolved['name'] != project:
        raise ValueError('Compose project differs from installed project')
    for service, image in images.items():
        definition = resolved['services'][service]
        if definition['image'] != image or 'build' in definition:
            raise ValueError(f'{service}: Compose differs from pinned manifest')
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
    run([*prefix, 'pull', *images])
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
             images['nora'], 'config', 'validate', '--config', '/tmp/config.toml'])  # noqa: S108 -- container path
    print('RECREATE: owned service cohort; infrastructure excluded', flush=True)
    run([*prefix, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never',
         '--force-recreate', *images])
    spec = importlib.util.spec_from_file_location('verify_deploy',
                                                 Path(__file__).with_name('verify-deploy.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('installed verifier missing')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    print('READBACK: exact service membership, image IDs, health and version', flush=True)
    verifier.verify_services(project, images)
    stamp = json.loads(run([*prefix, 'exec', '-T', 'nora', '/app/.venv/bin/python', '-c',
                            'import json; print(json.dumps(json.load(open('
                            '"/app/nora/version.json"))))'], output=True))
    if (stamp['version'], stamp['commit']) != (manifest['version'], manifest['commit']):
        raise ValueError('running Nora stamp differs from release')
    receipt = Path(__file__).with_name('release-deploy.json')
    candidate = receipt.with_suffix('.tmp')
    candidate.write_text(json.dumps(manifest, indent=2) + '\n')
    candidate.replace(receipt)
    print(f'VERIFIED: deployed {manifest["version"]}; receipt recorded on host')


def apply_release(prefix: list[str], manifest: dict, runtime: Path, project: str,
                  *, site: bool) -> None:
    """Under the shared lock, refuse a failed/interrupted candidate until cleared.

    Komodo cannot disable a running resource's schedule. This one marker prevents
    its next change check from recreating a partially deployed cohort again.
    Write BEFORE the attempt so process death and verifier SystemExit are covered.
    """
    expected_images(manifest, site=site)
    selected = {key: manifest[key] for key in ('version', 'commit', 'services')}
    fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    marker = Path(__file__).with_name('release-attempt.json')
    if marker.exists() and json.loads(marker.read_text())['manifest_sha256'] == fingerprint:
        raise RuntimeError('candidate blocked after failed/interrupted deployment; '
                           'inspect privately')
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({'manifest_sha256': fingerprint,
                                     'version': manifest['version'], 'commit': manifest['commit']}))
    temporary.chmod(0o600)
    temporary.replace(marker)
    deploy(prefix, manifest, runtime, project, site=site)
    marker.unlink()  # only a fully verified transaction clears the outstanding attempt


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
                   '--no-path-resolution'], output=True), end='')
        return
    if not args.runtime_root.is_absolute() or not re.fullmatch(r'[a-z0-9_-]+', args.project):
        raise ValueError('install an absolute runtime path and fixed Compose project')
    # flock -o closes the descriptor for descendants; all manual deploys share this lock.
    if os.environ.get('NORA_DEPLOY_LOCKED') != '1':
        env = {**os.environ, 'NORA_DEPLOY_LOCKED': '1'}
        os.execvpe('flock',  # noqa: S606,S607 -- existing host lock protocol
                  ['flock', '-o', '-E', '75', '-w', '900',
                            '/tmp/nora-deploy.lock',  # noqa: S108 -- existing shared lock
                            sys.executable, __file__, *sys.argv[1:]], env)
    manifest = json.loads(Path('release.json').read_text())
    apply_release(prefix, manifest, args.runtime_root, args.project, site=args.site)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError, json.JSONDecodeError) as error:
        # Do not echo data or command argv: they can contain private host configuration.
        print(f'FAILED: release deployment ({type(error).__name__}); '
              'no successful receipt was recorded', file=sys.stderr)
        raise SystemExit(1) from None
