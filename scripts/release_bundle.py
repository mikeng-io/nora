"""Publish release images and render Komodo's Git-backed desired state.

Runs on trusted release/master jobs, never on the deployment host. No host credentials.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
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
}
# Reviewed recipes: pinned external images, locked language dependencies, no
# inputs outside their context. A changed recipe builds until reviewed here.
REUSE_RECIPES = {
    'app': '3bf5dc79009ee6f79dacd671b1c6e91ab00022efeb07df1fe8874f83326b8019',
    'site': '3a8f159aaaf3b076a9e3c742480b39c236d81a7698886865081a1fb02bddd80d',
    'stt': '0f909a02fd4c8d484501ecf6f5ce85dd2b5ffed5481065fcec75dbb638c53ad0',
    'voice-sidecar': '69f0ecc9a3efc00732f6c4c364c6441014dba5355667f8b57fe60f39a71f8ba5',
    'voice-wake': 'fd5ee73065eab7b09c4bf08805c21573bce5296cc1bbdf7b7bf5b1218fe9449b',
    'signal-cli': 'cdaa57b8438964a2a72ea56187f3e72d81477655eec011ae959284de0226656c',
    'whatsapp-bridge': '71ae929c8dab80bf43ac27df9e390d1770933a1028d2c592e3ff041c93d7cdab',
    'instagram-bridge': '92a0e637d9c9a699f903bb745fdc753d366badb9186ed19d41e5a312dcf923c7',
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
    if 'artifacts' in manifest:
        if set(manifest['artifacts']) != set(BUILDS):
            raise ValueError('incomplete artifact provenance')
        for name, artifact in manifest['artifacts'].items():
            validate_artifact(name, artifact, services[name])
        if any(manifest['artifacts']['nora'][k] != manifest[k]
               for k in ('version', 'commit', 'channel')):
            raise ValueError('Nora artifact stamp must match selected release')


def validate_builds(compose: dict) -> None:
    built = {name: svc['build'] for name, svc in compose['services'].items() if 'build' in svc}
    if set(built) != set(BUILDS):
        raise ValueError('release images must cover every build service exactly')
    for name, definition in built.items():
        value = {'context': definition} if isinstance(definition, str) else definition
        context, dockerfile = BUILDS[name]
        actual_context = Path(value['context']).as_posix()
        actual_dockerfile = (
            Path(actual_context) / value.get('dockerfile', 'Dockerfile')).as_posix()
        args = ({'NORA_VERSION': '${NORA_VERSION:-}', 'NORA_GIT_SHA': '${NORA_GIT_SHA:-}',
                 'NORA_BUILT_AT': '${NORA_BUILT_AT:-}'} if name == 'nora' else {})
        if (actual_context != context or actual_dockerfile != dockerfile
                or value.get('args', {}) != args
                or value.get('network', 'host') != 'host'
                or set(value) - {'context', 'dockerfile', 'network', 'args'}):
            raise ValueError('review changed Compose build inputs before publishing')


def render(compose: dict, images: dict, tag: str, commit: str,
           channel: str = 'release') -> tuple[dict, dict]:
    validate_identity(tag, commit, channel)
    validate_builds(compose)
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
    validate_builds(compose)
    refs = api(repo, 'git/matching-refs/heads/deployments')
    heads = [r['object']['sha'] for r in refs if r['ref'] == 'refs/heads/deployments']
    if len(heads) > 1:
        raise ValueError('ambiguous previous deployment state')
    previous = heads[0] if heads else ''
    previous_manifest(repo, previous)  # unavailable/malformed state refuses selection
    return {'commit': commit, 'version': tag, 'channel': channel,
            'matrix': list(BUILDS), 'previous': previous}


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


def previous_manifest(repo: str, ref: str) -> dict:
    if not ref:
        return {}
    if not SHA.fullmatch(ref):
        raise ValueError('previous deployment must be an immutable Git commit')
    value = api(repo, f'contents/release.json?ref={ref}')
    manifest = json.loads(base64.b64decode(value['content']))
    validate_manifest(manifest)
    return manifest


def build_inputs(name: str, commit: str, external: dict | None = None) -> dict:
    """Conservative complete Git context, never a hand-maintained COPY path filter.

    Builds consume an archive of this tree, not the dependency-populated checkout.
    Ignored files count too: false rebuilds are preferable to false reuse. Remote
    apt indexes are not a freshness trigger; reuse selects the previously built
    output of the same reviewed recipe, not a claim of bit-reproducible rebuilding.
    """
    context, dockerfile = BUILDS[name]
    # Hash exact bytes (run strips trailing whitespace, unsuitable for this proof).
    recipe_bytes = subprocess.check_output(['git', 'show', f'{commit}:{dockerfile}'])  # noqa: S603,S607 -- fixed Git command
    workflow_ref = os.environ.get('GITHUB_WORKFLOW_SHA', commit)
    if not SHA.fullmatch(workflow_ref):
        raise ValueError('invalid publisher workflow revision')
    workflow = run('git', 'rev-parse', f'{workflow_ref}:.github/workflows/release-images.yml')
    tree = run('git', 'rev-parse', f'{commit}:' + ('' if context == '.' else context))
    return {'context': tree, 'dockerfile': hashlib.sha256(recipe_bytes).hexdigest(),
            'publisher': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'workflow': workflow, 'platform': 'linux/amd64',
            'external': external or {},
            'reusable': name != 'nora' and REUSE_RECIPES.get(name) == (
                hashlib.sha256(recipe_bytes).hexdigest())}


def validate_artifact(name: str, artifact: dict, image: str) -> None:
    validate_identity(artifact['version'], artifact['commit'], artifact['channel'])
    if artifact['name'] != name or artifact['image'] != image:
        raise ValueError('artifact provenance differs from selected image')
    inputs = artifact['inputs']
    if not isinstance(inputs, dict):
        raise ValueError('artifact has no build input evidence')
    external = inputs['external']
    if not isinstance(external, dict) or (external and name != 'signal-cli'):
        raise ValueError('unexpected external build inputs')
    archives = [f for f in external if f.endswith('.tar.gz')]
    if name == 'signal-cli' and (len(archives) != 1 or set(external) != {
            archives[0], archives[0] + '.asc'} or not all(
            re.fullmatch(r'signal-cli-[0-9.]+\.tar\.gz(?:\.asc)?', f)
            and re.fullmatch(r'[0-9a-f]{64}', digest) for f, digest in external.items())):
        raise ValueError('missing verified Signal input evidence')


def reusable_artifact(name: str, inputs: dict, previous: dict) -> dict | None:
    artifact = previous.get('artifacts', {}).get(name)
    if not artifact or not inputs['reusable'] or artifact.get('inputs') != inputs:
        return None
    validate_artifact(name, artifact, previous['services'][name])
    # Registry availability is checked before choosing reuse. Failure refuses the
    # job, never turns unknown evidence into a successful skip.
    run('docker', 'buildx', 'imagetools', 'inspect', artifact['image'])
    return artifact


def bundle(directory: Path, version: str, commit: str, channel: str,
           previous: dict | None = None) -> tuple[dict, dict]:
    selected = {'version': version, 'commit': commit, 'channel': channel}
    artifacts = {}
    for name in BUILDS:
        record = json.loads((directory / f'{name}.json').read_text())
        if record['selection'] != selected:
            raise ValueError('image receipts describe different selections')
        artifact = record['artifact']
        validate_artifact(name, artifact, artifact['image'])
        inputs = artifact['inputs']
        if inputs != build_inputs(name, commit, inputs.get('external')):
            raise ValueError('artifact build inputs differ from selected source/recipe')
        fresh = all(artifact[key] == value for key, value in selected.items())
        if not fresh and (not inputs['reusable'] or artifact != (
                (previous or {}).get('artifacts', {}).get(name))):
            raise ValueError('reused artifact lacks exact previous provenance/input proof')
        artifacts[name] = artifact
    compose, manifest = render(yaml.safe_load(Path('docker-compose.yml').read_text()),
                               {n: a['image'] for n, a in artifacts.items()},
                               version, commit, channel)
    manifest['artifacts'] = artifacts
    validate_manifest(manifest)
    return compose, manifest


def publish_image(args, commit: str, previous: dict) -> None:
    name = args.image
    validate_builds(yaml.safe_load(run('git', 'show', f'{commit}:docker-compose.yml')))
    external = {}
    if name == 'signal-cli':
        run('bash', 'deploy/signal-cli/fetch.sh')
        # The fetch script and Dockerfile both verify the signature. Include the
        # fetched bytes as well, so a replaced signed upstream asset invalidates reuse.
        files = sorted(Path('deploy/signal-cli').glob('signal-cli-*.tar.gz*'))
        if len(files) != 2 or not any(f.name.endswith('.asc') for f in files):
            raise ValueError('expected exactly the verified Signal archive/signature')
        external = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    inputs = build_inputs(name, commit, external)
    artifact = reusable_artifact(name, inputs, previous)
    if artifact is None:
        context, dockerfile = BUILDS[name]
        with tempfile.TemporaryDirectory(prefix='nora-release-build-') as temporary:
            root = Path(temporary)
            tree = run('git', 'rev-parse', f'{commit}:' + ('' if context == '.' else context))
            archive = subprocess.check_output(['git', 'archive', tree])  # noqa: S603,S607
            destination = root / context
            destination.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(archive)) as exported:
                exported.extractall(destination, filter='data')
            extra = []
            if name == 'nora':
                run('python3', 'scripts/gen_version_manifest.py')
                stamp = json.loads(Path('nora/version.json').read_text())
                if (stamp['version'], stamp['commit']) != (args.version, commit):
                    raise ValueError('build stamp does not describe the exact release')
                (root / 'nora/version.json').write_text(json.dumps(stamp))
                extra = ['--build-arg', f'NORA_VERSION={args.version}',
                         '--build-arg', f'NORA_GIT_SHA={commit}',
                         '--build-arg', f'NORA_BUILT_AT={stamp["built_at"]}']
            for filename in external:
                (root / context / filename).write_bytes((Path(context) / filename).read_bytes())
            metadata = args.directory.resolve() / f'{name}.metadata.json'
            identity = f'sha-{commit}' if args.channel == 'master' else args.version
            # Separate mutable cache manifests from immutable deployment artifacts.
            cache = f'{REGISTRY}{name}:buildcache-{inputs["platform"].replace("/", "-")}'
            subprocess.run(  # noqa: S603 -- clean Git export, validated identity, fixed CLI
                ['docker', 'buildx', 'build', '--network=host', '--pull',  # noqa: S607
                 '--allow', 'network.host', '--platform', inputs['platform'], '--push',
                 '--cache-from', f'type=registry,ref={cache}',
                 '--cache-to', f'type=registry,ref={cache},mode=max',
                 '--metadata-file', str(metadata),
                 '--label', f'org.opencontainers.image.revision={commit}',
                 '--label', f'org.opencontainers.image.version={args.version}',
                 '--label', 'org.opencontainers.image.source=https://github.com/mikeng-io/nora',
                 *extra, '-f', str(root / dockerfile), '-t', f'{REGISTRY}{name}:{identity}',
                 str(root / context)], check=True)
            digest = json.loads(metadata.read_text())['containerimage.digest']
            if not DIGEST.fullmatch(digest):
                raise ValueError('build returned no immutable digest')
            artifact = {'name': name, 'image': f'{REGISTRY}{name}@{digest}',
                        'commit': commit, 'version': args.version, 'channel': args.channel,
                        'inputs': inputs}
    (args.directory / f'{name}.json').write_text(json.dumps({
        'selection': {'commit': commit, 'version': args.version, 'channel': args.channel},
        'artifact': artifact}))


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
    parser.add_argument('--previous-ref', default='', help='pinned previous deployments commit')
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
        publish_image(args, commit, previous_manifest(args.repo, args.previous_ref))
    else:
        compose, manifest = bundle(args.directory, args.version, commit, args.channel,
                                   previous_manifest(args.repo, args.previous_ref))
        for name, value in [('compose.json', compose), ('release.json', manifest)]:
            if args.operation == 'bundle':
                (args.directory / name).write_text(json.dumps(value, indent=2) + '\n')
            elif json.loads((args.directory / name).read_text()) != value:
                raise ValueError('promotion bundle differs from the selected source receipts')
        if args.operation == 'promote':
            promote(args.repo, args.directory)


if __name__ == '__main__':
    main()
