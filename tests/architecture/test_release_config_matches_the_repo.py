"""release-please's config agrees with the repository it releases (DEC-314).

release-please runs on GitHub, never in the gate, so a mismatch here surfaces
only as a release PR that is wrong or never opens. Each check below is one way
that has to stay true for the release PR to mean what it says:

- every commit type the hook accepts has a CHANGELOG section, visible or
  hidden. An unlisted type is neither rendered nor dropped on purpose;
- the four files release-please bumps agree on one version today, so the
  release PR moves them together from one starting point;
- a `release-as` pin is ahead of the released version. Once the pinned
  version ships, the pin would ask for the same version again, so it must be
  deleted in the PR after the release.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _commit_types() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "check_commit_subject", ROOT / "scripts" / "check_commit_subject.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.TYPES)


def test_every_commit_type_has_a_changelog_section():
    """`revert` is the one extra: git writes `Revert "<subject>"`, which the
    hook allows as-is, and release-please reads as a revert."""
    sections = {s["type"] for s in CONFIG["changelog-sections"]}
    assert sections == _commit_types() | {"revert"}


def test_only_user_facing_types_are_visible():
    """A visible type is what opens a release PR; the rest only ride along."""
    visible = {s["type"] for s in CONFIG["changelog-sections"] if not s.get("hidden")}
    assert visible == {"feat", "fix", "security", "perf", "change", "doctrine", "revert"}


def test_the_versioned_files_agree_with_the_manifest():
    released = MANIFEST["."]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    init = (ROOT / "nora" / "__init__.py").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == released
    assert [p["version"] for p in lock["package"] if p["name"] == "nora"] == [released]
    assert re.search(rf'^__version__ = "{re.escape(released)}"$', init, re.M)


def test_a_release_as_pin_is_ahead_of_the_released_version():
    pin = CONFIG["packages"]["."].get("release-as")
    if pin is None:
        return
    assert _version(pin) > _version(MANIFEST["."]), (
        f"release-as {pin} has shipped; delete it from release-please-config.json")


def test_the_uv_lock_updater_names_the_nora_package():
    """release-please parses TOML into tagged values, so the filter reads
    `@.name.value`; a path that matches nothing logs a warning and leaves
    uv.lock behind pyproject.toml."""
    extra = CONFIG["packages"]["."]["extra-files"]
    assert {"type": "toml", "path": "uv.lock",
            "jsonpath": "$.package[?(@.name.value=='nora')].version"} in extra


@pytest.mark.parametrize(
    ('event', 'target', 'release_enabled', 'master_enabled', 'ref', 'allowed'), [
    ('release', '', 'true', '', 'refs/tags/v7.11.0', True),
    ('release', '', '', 'true', 'refs/tags/v7.11.0', False),
    ('workflow_dispatch', 'release', 'true', '', 'refs/heads/master', True),
    ('workflow_dispatch', 'master', 'true', '', 'refs/heads/master', False),
    ('workflow_dispatch', 'master', '', 'true', 'refs/heads/master', True),
    ('workflow_dispatch', 'master', 'true', 'true', 'refs/heads/other', False),
    ('push', '', 'true', 'true', 'refs/heads/master', False),
    ('push', 'master', 'true', 'true', 'refs/heads/master', False),
])
def test_image_publication_has_explicit_separate_master_admission(
        event, target, release_enabled, master_enabled, ref, allowed):
    workflow = yaml.load((ROOT / '.github/workflows/release-images.yml').read_text(),
                         Loader=yaml.BaseLoader)  # noqa: S506 -- scalars only; preserve the YAML `on` key
    assert set(workflow['on']) == {'release', 'workflow_dispatch'}
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert inputs['target']['default'] == 'release'
    assert inputs['target']['options'] == ['release', 'master']
    assert inputs['tag']['required'] == 'false'
    expression = workflow['jobs']['prepare']['if']
    context = {'github.repository': 'mikeng-io/nora', 'github.event_name': event,
               'github.ref': ref, 'inputs.target': target,
               'vars.RELEASE_IMAGES_ENABLED': release_enabled,
               'vars.MASTER_DEPLOY_ENABLED': master_enabled}
    for key, value in context.items():
        expression = expression.replace(key, repr(value))
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    assert eval(expression, {'__builtins__': {}}) is allowed  # noqa: S307 -- repo-owned expression


def test_both_image_channels_share_pinned_tooling_source_and_publication_lane():
    workflow = yaml.load((ROOT / '.github/workflows/release-images.yml').read_text(),
                         Loader=yaml.BaseLoader)  # noqa: S506 -- scalars only; preserve the YAML `on` key
    assert workflow['concurrency'] == {'group': 'release-images', 'cancel-in-progress': 'false'}
    jobs = workflow['jobs']
    assert jobs['prepare']['outputs'] == {'selection': '${{ steps.select.outputs.selection }}'}
    assert jobs['build']['strategy']['matrix']['image'] == (
        '${{ fromJSON(needs.prepare.outputs.selection).matrix }}')
    assert jobs['promote']['needs'] == ['prepare', 'build']
    assert jobs['prepare']['permissions'].get('packages') is None
    builder = next(step for step in jobs['build']['steps'] if step.get('id') == 'buildx')
    assert builder['uses'] == (
        'docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f')
    assert builder['with'] == {'driver': 'docker-container',
                               'driver-opts': 'network=host',
                               'buildkitd-flags': '--allow-insecure-entitlement network.host'}
    publisher = next(step for step in jobs['build']['steps']
                     if step.get('name') == 'Publish exact selected image')
    assert publisher['env']['BUILDX_BUILDER'] == '${{ steps.buildx.outputs.name }}'
    for name in ('build', 'promote'):
        steps = jobs[name]['steps']
        checkouts = [step for step in steps if step.get('uses', '').startswith('actions/checkout@')]
        assert checkouts[0]['with']['ref'] == '${{ github.workflow_sha }}'
        assert checkouts[1]['with']['ref'] == (
            '${{ fromJSON(needs.prepare.outputs.selection).commit }}')
        assert all(step['with']['persist-credentials'] == 'false' for step in checkouts)
        runs = '\n'.join(step.get('run', '') for step in steps)
        assert 'cp scripts/release_bundle.py "$RUNNER_TEMP/release_bundle.py"' in runs
        assert '--channel "$CHANNEL" --version "$VERSION" --source "$SOURCE"' in runs
        assert 'python scripts/release_bundle.py' not in runs  # never revive source's old guards
        assert jobs[name]['permissions']['actions'] == 'read'
        assert '--previous-ref "$PREVIOUS"' in runs
        publication = next(step for step in steps if 'PREVIOUS' in step.get('env', {}))
        assert publication['env']['PREVIOUS'] == (
            '${{ fromJSON(needs.prepare.outputs.selection).previous }}')
