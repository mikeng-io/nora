"""Publish release images and render Komodo's Git-backed desired state.

Runs on trusted release jobs, never on the deployment host. No host credentials.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import subprocess
from pathlib import Path

import yaml

BUILDS = {
    'nora': ('.', 'Dockerfile'),
    'app': ('app', 'app/Dockerfile'),
    'site': ('.', 'site/Dockerfile'),
    'stt': ('deploy/stt', 'deploy/stt/Dockerfile'),
    'voice-sidecar': ('deploy/voice-sidecar', 'deploy/voice-sidecar/Dockerfile'),
    'voice-wake': ('deploy/voice-wake', 'deploy/voice-wake/Dockerfile'),
    'signal-cli': ('deploy/signal-cli', 'deploy/signal-cli/Dockerfile'),
    'whatsapp-bridge': ('deploy/whatsapp-bridge', 'deploy/whatsapp-bridge/Dockerfile'),
    'instagram-bridge': ('deploy/instagram-bridge', 'deploy/instagram-bridge/Dockerfile'),
    'alert-bridge': ('deploy/alert-bridge', 'deploy/alert-bridge/Dockerfile'),
}
REGISTRY = 'ghcr.io/mikeng-io/nora-'
TAG = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def run(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()  # noqa: S603


def render(compose: dict, images: dict, tag: str, commit: str) -> tuple[dict, dict]:
    if not TAG.fullmatch(tag) or not SHA.fullmatch(commit):
        raise ValueError('invalid release tag or commit')
    built = {name for name, svc in compose['services'].items() if 'build' in svc}
    if built != set(BUILDS) or set(images) != built:
        raise ValueError('release images must cover every build service exactly')
    for name, image in images.items():
        prefix = f'{REGISTRY}{name}@'
        if not image.startswith(prefix) or not DIGEST.fullmatch(image[len(prefix):]):
            raise ValueError(f'invalid image digest for {name}')
    result = copy.deepcopy(compose)
    services = {**images, 'presence': images['nora'], 'analytics': images['nora']}
    for name, svc in result['services'].items():
        svc.pop('build', None)
        if name in services:
            svc['image'] = services[name]
        mounts = []
        for mount in svc.get('volumes', []):
            if not isinstance(mount, str):
                raise ValueError('review new long-form volume before releasing')
            if mount in ('./nora/doctrine:/app/nora/doctrine:ro',
                         './nora/dreamer:/app/nora/dreamer:ro'):
                continue
            mount = mount.replace(':-./', ':-${NORA_RUNTIME_ROOT:?}/')
            if mount.startswith('./'):
                mount = '${NORA_RUNTIME_ROOT:?}/' + mount[2:]
            mounts.append(mount)
        if 'volumes' in svc:
            svc['volumes'] = mounts
    return result, {'version': tag, 'commit': commit, 'services': services}


def api(repo: str, path: str, body: dict | None = None):
    command = ['gh', 'api', f'repos/{repo}/{path}']
    if body is not None:
        command += ['--method', 'POST', '--input', '-']
    output = subprocess.check_output(  # noqa: S603 -- fixed gh API argv
        command, input=json.dumps(body) if body is not None else None, text=True)
    return json.loads(output)


def validate_release(tag: str, repo: str) -> str:
    if repo != 'mikeng-io/nora' or not TAG.fullmatch(tag):
        raise ValueError('only stable Nora releases may publish')
    commit = run('git', 'rev-parse', f'{tag}^{{commit}}')
    if run('git', 'rev-parse', 'HEAD') != commit:
        raise ValueError('checkout must be the exact release commit')
    run('git', 'merge-base', '--is-ancestor', commit, 'origin/master')
    release = api(repo, f'releases/tags/{tag}')
    if release['draft'] or release['prerelease'] or release['tag_name'] != tag:
        raise ValueError('release must be published and stable')
    return commit


def promote(repo: str, directory: Path) -> None:
    """One fast-forward Git-data commit; a delayed old release cannot win."""
    manifest = json.loads((directory / 'release.json').read_text())
    # Query refs rather than treating an arbitrary API error as a missing branch.
    refs = api(repo, 'git/matching-refs/heads/deployments')
    heads = [r['object']['sha'] for r in refs if r['ref'] == 'refs/heads/deployments']
    parent = heads[0] if heads else manifest['commit']
    if heads:
        previous = api(repo, f'contents/release.json?ref={parent}')
        old = json.loads(base64.b64decode(previous['content']))
        def version(v):
            match = TAG.fullmatch(v)
            if match is None:
                raise ValueError('invalid previous release')
            return tuple(map(int, match.groups()))
        if version(old['version']) >= version(manifest['version']):
            raise ValueError('desired state already contains this release or a newer one')
    tree = api(repo, 'git/trees', {'tree': [
        {'path': name, 'mode': '100644', 'type': 'blob',
         'content': (directory / name).read_text()}
        for name in ('compose.json', 'release.json')
    ]})
    commit = api(repo, 'git/commits', {
        'message': f'chore(deploy): select {manifest["version"]}',
        'tree': tree['sha'], 'parents': [parent],
    })
    if heads:
        command = ['gh', 'api', f'repos/{repo}/git/refs/heads/deployments',
                   '--method', 'PATCH', '--input', '-']
        subprocess.run(  # noqa: S603 -- fixed gh API command
            command, input=json.dumps({'sha': commit['sha'], 'force': False}),
            text=True, check=True, capture_output=True)
    else:
        api(repo, 'git/refs', {'ref': 'refs/heads/deployments', 'sha': commit['sha']})
    print(f'Desired state selects {manifest["version"]} ({commit["sha"]})')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['matrix', 'build', 'bundle', 'promote'])
    parser.add_argument('--tag', default='')
    parser.add_argument('--repo', default='mikeng-io/nora')
    parser.add_argument('--image', choices=BUILDS)
    parser.add_argument('--directory', type=Path, default=Path('release-output'))
    args = parser.parse_args()
    if args.operation == 'matrix':
        print(json.dumps(list(BUILDS)))
        return
    commit = validate_release(args.tag, args.repo)
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.operation == 'build':
        if args.image is None:
            parser.error('--image is required for build')
        name = args.image
        if name == 'signal-cli':
            run('bash', 'deploy/signal-cli/fetch.sh')
        extra = []
        if name == 'nora':
            run('python3', 'scripts/gen_version_manifest.py')
            stamp = json.loads(Path('nora/version.json').read_text())
            if (stamp['version'], stamp['commit']) != (args.tag, commit):
                raise ValueError('build stamp does not describe the exact release')
            extra = ['--build-arg', f'NORA_VERSION={args.tag}',
                     '--build-arg', f'NORA_GIT_SHA={commit}',
                     '--build-arg', f'NORA_BUILT_AT={stamp["built_at"]}']
        context, dockerfile = BUILDS[name]
        metadata = args.directory / f'{name}.metadata.json'
        image = f'{REGISTRY}{name}:{args.tag}'
        subprocess.run(  # noqa: S603 -- Docker CLI, fixed inputs, validated tag
            ['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--push',  # noqa: S607 -- Docker CLI
             '--metadata-file', str(metadata),
             '--label', f'org.opencontainers.image.revision={commit}',
             '--label', f'org.opencontainers.image.version={args.tag}',
             '--label', 'org.opencontainers.image.source=https://github.com/mikeng-io/nora',
             *extra, '-f', dockerfile, '-t', image, context], check=True)
        digest = json.loads(metadata.read_text())['containerimage.digest']
        if not DIGEST.fullmatch(digest):
            raise ValueError('build returned no immutable digest')
        (args.directory / f'{name}.json').write_text(json.dumps({
            'name': name, 'image': f'{REGISTRY}{name}@{digest}', 'commit': commit,
            'version': args.tag,
        }))
    elif args.operation == 'bundle':
        records = [json.loads((args.directory / f'{name}.json').read_text()) for name in BUILDS]
        if any(r['commit'] != commit or r['version'] != args.tag for r in records):
            raise ValueError('image receipts describe different releases')
        compose, manifest = render(yaml.safe_load(Path('docker-compose.yml').read_text()),
                                   {r['name']: r['image'] for r in records}, args.tag, commit)
        for name, value in [('compose.json', compose), ('release.json', manifest)]:
            (args.directory / name).write_text(json.dumps(value, indent=2) + '\n')
    else:
        promote(args.repo, args.directory)


if __name__ == '__main__':
    main()
