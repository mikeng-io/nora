"""Startup smoke test for `nora.analytics.composition.build_rollup` — the
disabled-by-default path (DEC-115) must build nothing and touch neither
identity.toml nor the database."""

from __future__ import annotations

import pytest

from nora.analytics.composition import build_rollup
from nora.config import load


@pytest.fixture
def minimal_config(example_config_path):
    # analytics.enabled defaults False in config.example.toml (DEC-115's
    # inert posture), so this never touches identity.toml, policy.toml, or a
    # database — config.load() itself does not require those paths to exist,
    # only later consumers (load_persons, load_policy) do, and build_rollup's
    # disabled branch calls neither.
    return load(example_config_path, require_secrets=False)


def test_disabled_analytics_builds_nothing(minimal_config):
    deps = build_rollup(minimal_config)
    assert deps.runner is None
    assert deps.persons is None
    assert deps.subjects == ()
    assert deps.metrics_port == minimal_config.analytics.metrics_port
    assert deps.tick_seconds == minimal_config.analytics.tick_seconds


# ── _subjects_from_identity: the enabled/merge path ──────────────────────────
#
# `build_rollup` itself needs a live Postgres (`run_migrations`) to exercise
# end to end, which isn't practical here — these test `_subjects_from_identity`
# directly, the same level `nora.presence.composition`'s own sibling tests
# (`test_presence_collector.py::test_circles_are_derived_from_identity_grouped_by_circle_id`
# and friends) exercise `_circles_from_identity` at.

def test_a_renamed_persons_fixes_merge_under_one_subject_with_both_ids(tmp_path):
    # A former person_id can never appear as a `Life360Membership.person_id`
    # (load_persons refuses a former_person_ids entry that collides with a live
    # block), so resolving forward with subject_id_for over life360 memberships
    # alone is always a no-op. former_ids_for (the reverse lookup) is the only
    # way _subjects_from_identity can recover "clara_yates" here.
    from nora.analytics.composition import _subjects_from_identity
    from nora.persons import load_persons

    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.clara_lawson]\n'
        'name = "Clara"\n'
        'former_person_ids = ["clara_yates"]\n'
        '[[persons.clara_lawson.life360]]\n'
        'member_id = "m-clara"\n'
        'circle_id = "cir1"\n'
    )
    persons = load_persons(p)
    subjects = _subjects_from_identity(persons)

    assert len(subjects) == 1
    [subject] = subjects
    assert subject.subject_id == "clara_lawson"
    assert set(subject.person_ids) == {"clara_lawson", "clara_yates"}
    assert subject.circle_ids == ("cir1",)


def test_a_life360_membership_without_a_circle_id_refuses_to_start(tmp_path):
    # Matches nora.presence.composition._circles_from_identity's own
    # precedent over the identical source data: refuse to start rather than
    # silently write "" into a NOT NULL circle_id column.
    from nora.analytics.composition import AnalyticsStartupError, _subjects_from_identity
    from nora.persons import load_persons

    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles_novak]\nname = "Miles"\n'
        '[[persons.miles_novak.life360]]\nmember_id = "m-miles"\n'
    )
    persons = load_persons(p)
    with pytest.raises(AnalyticsStartupError, match="m-miles"):
        _subjects_from_identity(persons)
