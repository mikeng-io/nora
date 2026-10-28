"""Publish release images and render Komodo's Git-backed desired state.

Runs on trusted release/master jobs, never on the deployment host. No host credentials.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
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
VERSION = re.compile(TAG.pattern + r'(?:-([1-9][0-9]*)-g([0-9a-f]{7,40}))?')
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def run(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()  # noqa: S603


def validate_identity(version: str, commit: str, channel: str) -> tuple[int, ...]:
    match = VERSION.fullmatch(version)
    if not match or not SHA.fullmatch(commit) or channel not in ('release', 'master'):
        raise ValueError('invalid deployment version, commit or channel')
    if match[4] and (channel != 'master' or not commit.startswith(match[5])):
        raise ValueError('snapshot stamp must describe the master source commit')
    return tuple(map(int, match.groups()[:3]))


def validate_manifest(manifest: dict) -> None:
    validate_identity(manifest['version'], manifest['commit'], manifest.get('channel', 'release'))
    services = manifest['services']
    if set(services) != set(BUILDS) | {'presence', 'analytics'}:
        raise ValueError('deployment service set differs from owned cohort')
    for name, image in services.items():
        owner = 'nora' if name in ('presence', 'analytics') else name
        prefix = f'{REGISTRY}{owner}@'
        if not image.startswith(prefix) or not DIGEST.fullmatch(image[len(prefix):]):
            raise ValueError(f'invalid image digest for {name}')
    if len({services[s] for s in ('nora', 'presence', 'analytics')}) != 1:
        raise ValueError('shared Nora services must use one image')


def render(compose: dict, images: dict, tag: str, commit: str,
           channel: str = 'release') -> tuple[dict, dict]:
    validate_identity(tag, commit, channel)
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
    return result, {'version': tag, 'commit': commit, 'channel': channel, 'services': services}


def api(repo: str, path: str, body: dict | None = None):
    command = ['gh', 'api', f'repos/{repo}' + (f'/{path}' if path else '')]
    if body is not None:
        command += ['--method', 'POST', '--input', '-']
    output = subprocess.check_output(  # noqa: S603 -- fixed gh API argv
        command, input=json.dumps(body) if body is not None else None, text=True)
    return json.loads(output)


def validate_release(tag: str, repo: str, *, checkout: bool = True) -> str:
    if repo != 'mikeng-io/nora' or not TAG.fullmatch(tag):
        raise ValueError('only stable Nora releases may publish')
    commit = run('git', 'rev-parse', f'{tag}^{{commit}}')
    if checkout and run('git', 'rev-parse', 'HEAD') != commit:
        raise ValueError('checkout must be the exact release commit')
    run('git', 'merge-base', '--is-ancestor', commit, 'origin/master')
    release = api(repo, f'releases/tags/{tag}')
    if release['draft'] or release['prerelease'] or release['tag_name'] != tag:
        raise ValueError('release must be published and stable')
    return commit


def require_ci(repo: str, commit: str) -> None:
    """Only the latest exact-source master push run of the repository CI counts."""
    result = api(repo, 'actions/workflows/ci.yml/runs'
                 f'?branch=master&event=push&head_sha={commit}&per_page=1')
    runs = result['workflow_runs']
    if not result['total_count'] or len(runs) != 1:
        raise ValueError('successful trusted CI required for the exact source')
    ci = runs[0]
    if not (ci['head_sha'] == commit and ci['head_branch'] == 'master'
            and ci['event'] == 'push' and ci['path'] == '.github/workflows/ci.yml'
            and ci['repository']['full_name'] == repo
            and ci['head_repository']['full_name'] == repo
            and ci['status'] == 'completed' and ci['conclusion'] == 'success'):
        raise ValueError('successful trusted CI required for the exact source')


def select(repo: str, channel: str, tag: str, candidate: str) -> dict:
    """Resolve the source once, before granting any job registry write permission."""
    if channel == 'master':
        if not (repo == os.environ.get('GITHUB_REPOSITORY') == 'mikeng-io/nora'
                and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                and os.environ.get('GITHUB_REF') == 'refs/heads/master'
                and os.environ.get('GITHUB_ACTOR') == 'mikeng-io'
                and os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'mikeng-io'
                and os.environ.get('MASTER_DEPLOY_ENABLED') == 'true'):
            raise ValueError('master deployment requires an admitted owner dispatch on master')
        if tag:
            raise ValueError('master deployment does not accept a release tag')
        if api(repo, '')['default_branch'] != 'master':
            raise ValueError('master must be the trusted default branch')
        commit = api(repo, 'git/ref/heads/master')['object']['sha']
        if (not SHA.fullmatch(commit) or (candidate and candidate != commit)
                or os.environ.get('GITHUB_SHA') != commit
                or run('git', 'rev-parse', 'HEAD') != commit):
            raise ValueError('stale/mismatched master source; dispatch again from current master')
        require_ci(repo, commit)
        tag = run('git', 'describe', '--tags', '--match', 'v[0-9]*', commit)
    else:
        commit = validate_release(tag, repo, checkout=False)
    validate_identity(tag, commit, channel)
    compose = yaml.safe_load(run('git', 'show', f'{commit}:docker-compose.yml'))
    if {name for name, svc in compose['services'].items() if 'build' in svc} != set(BUILDS):
        raise ValueError('selected source differs from the owned image cohort')
    return {'commit': commit, 'version': tag, 'channel': channel, 'matrix': list(BUILDS)}


def validate_source(repo: str, channel: str, version: str, commit: str) -> str:
    if channel == 'release':
        actual = validate_release(version, repo)
        if commit and actual != commit:
            raise ValueError('release tag differs from the selected source')
        return actual
    validate_identity(version, commit, channel)
    if repo != 'mikeng-io/nora' or run('git', 'rev-parse', 'HEAD') != commit:
        raise ValueError('checkout must be the selected Nora source')
    run('git', 'merge-base', '--is-ancestor', commit, 'origin/master')
    if run('git', 'describe', '--tags', '--match', 'v[0-9]*', commit) != version:
        raise ValueError('version differs from the selected source stamp')
    require_ci(repo, commit)
    return commit


def bundle(directory: Path, version: str, commit: str, channel: str) -> tuple[dict, dict]:
    records = [json.loads((directory / f'{name}.json').read_text()) for name in BUILDS]
    if any(r['name'] != name or r['commit'] != commit or r['version'] != version
           or r['channel'] != channel for name, r in zip(BUILDS, records, strict=True)):
        raise ValueError('image receipts describe different sources, versions or channels')
    return render(yaml.safe_load(Path('docker-compose.yml').read_text()),
                  {r['name']: r['image'] for r in records}, version, commit, channel)


def promote(repo: str, directory: Path) -> None:
    """One fast-forward Git-data commit; neither channel may replace a newer source."""
    manifest = json.loads((directory / 'release.json').read_text())
    if repo != 'mikeng-io/nora':
        raise ValueError('only the Nora repository may publish')
    validate_manifest(manifest)
    # Query refs rather than treating an arbitrary API error as a missing branch.
    refs = api(repo, 'git/matching-refs/heads/deployments')
    heads = [r['object']['sha'] for r in refs if r['ref'] == 'refs/heads/deployments']
    if len(heads) > 1 or (heads and not SHA.fullmatch(heads[0])):
        raise ValueError('invalid deployments branch state')
    parent = heads[0] if heads else manifest['commit']
    if heads:
        previous = api(repo, f'contents/release.json?ref={parent}')
        old = json.loads(base64.b64decode(previous['content']))
        validate_manifest(old)
        old_version = validate_identity(old['version'], old['commit'],
                                        old.get('channel', 'release'))
        new_version = validate_identity(manifest['version'], manifest['commit'],
                                        manifest['channel'])
        if (old_version > new_version or (old_version == new_version
                and old.get('channel', 'release') == manifest['channel'] == 'release')):
            raise ValueError('desired state already contains this release or a newer one')
        comparison = api(repo, f'compare/{old["commit"]}...{manifest["commit"]}')
        if not (comparison['status'] == 'ahead'
                and comparison['base_commit']['sha'] == old['commit']
                and comparison['merge_base_commit']['sha'] == old['commit']):
            raise ValueError('desired state selects this source or a newer/divergent source')
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
    selected = api(repo, 'git/ref/heads/deployments')['object']['sha']
    published = api(repo, f'contents/release.json?ref={selected}')
    if selected != commit['sha'] or json.loads(base64.b64decode(published['content'])) != manifest:
        raise ValueError('desired-state publication readback differs from the selected bundle')
    print(f'Desired state selects {manifest["channel"]} {manifest["version"]} '
          f'source {manifest["commit"]} ({selected}); actual deployment is unverified')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['select', 'matrix', 'build', 'bundle', 'promote'])
    parser.add_argument('--tag', '--version', dest='version', default='')
    parser.add_argument('--channel', choices=['release', 'master'], default='release')
    parser.add_argument('--source', default='', help='full pinned source SHA')
    parser.add_argument('--candidate', default='', help='optional assessed current master SHA')
    parser.add_argument('--repo', default='mikeng-io/nora')
    parser.add_argument('--image', choices=BUILDS)
    parser.add_argument('--directory', type=Path, default=Path('release-output'))
    args = parser.parse_args()
    if args.operation == 'select':
        print(json.dumps(select(args.repo, args.channel, args.version, args.candidate)))
        return
    if args.operation == 'matrix':
        print(json.dumps(list(BUILDS)))
        return
    commit = validate_source(args.repo, args.channel, args.version, args.source)
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
            if (stamp['version'], stamp['commit']) != (args.version, commit):
                raise ValueError('build stamp does not describe the exact release')
            extra = ['--build-arg', f'NORA_VERSION={args.version}',
                     '--build-arg', f'NORA_GIT_SHA={commit}',
                     '--build-arg', f'NORA_BUILT_AT={stamp["built_at"]}']
        context, dockerfile = BUILDS[name]
        metadata = args.directory / f'{name}.metadata.json'
        identity = f'sha-{commit}' if args.channel == 'master' else args.version
        image = f'{REGISTRY}{name}:{identity}'
        subprocess.run(  # noqa: S603 -- Docker CLI, fixed inputs, validated tag
            ['docker', 'buildx', 'build', '--pull', '--platform', 'linux/amd64', '--push',  # noqa: S607 -- Docker CLI
             '--metadata-file', str(metadata),
             '--label', f'org.opencontainers.image.revision={commit}',
             '--label', f'org.opencontainers.image.version={args.version}',
             '--label', 'org.opencontainers.image.source=https://github.com/mikeng-io/nora',
             *extra, '-f', dockerfile, '-t', image, context], check=True)
        digest = json.loads(metadata.read_text())['containerimage.digest']
        if not DIGEST.fullmatch(digest):
            raise ValueError('build returned no immutable digest')
        (args.directory / f'{name}.json').write_text(json.dumps({
            'name': name, 'image': f'{REGISTRY}{name}@{digest}', 'commit': commit,
            'version': args.version, 'channel': args.channel,
        }))
    else:
        compose, manifest = bundle(args.directory, args.version, commit, args.channel)
        for name, value in [('compose.json', compose), ('release.json', manifest)]:
            if args.operation == 'bundle':
                (args.directory / name).write_text(json.dumps(value, indent=2) + '\n')
            elif json.loads((args.directory / name).read_text()) != value:
                raise ValueError('promotion bundle differs from the selected source receipts')
        if args.operation == 'promote':
            promote(args.repo, args.directory)


if __name__ == '__main__':
    main()
