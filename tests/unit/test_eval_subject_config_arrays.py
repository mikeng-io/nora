"""The eval subject's config keeps an array an array (a generator that quoted `sources =
["google_drive"]` into the string "['google_drive']" would make every eval run refuse at the drift
check)."""

from __future__ import annotations

import tomllib

from evals.subject_config import _fmt, build, divergences


def test_an_array_and_a_backslash_survive_the_round_trip():
    for value in (["google_drive"], ["a", "b"], [1, 2], [], "C:\\path", 'say "hi"'):
        assert tomllib.loads(f"k = {_fmt(value)}")["k"] == value


def test_a_deployed_array_setting_is_not_drift(example_config_path):
    example = example_config_path.read_text(encoding="utf-8")
    deployment = example.replace(
        "[external_files]\nenabled = false",
        "[external_files]\nenabled = true",
    )
    assert deployment != example
    drift = divergences(build(example, deployment), deployment)
    assert not any(key.startswith("external_files.") for key in drift), drift


def test_an_omitted_signal_mode_preserves_the_deployments_code_default():
    example = '[platforms.signal]\nmode = "bridge"\n'
    deployment = '[platforms.signal]\n'
    generated = build(example, deployment)
    assert tomllib.loads(generated)["platforms"]["signal"]["mode"] == "direct"
    assert not divergences(generated, deployment)


def test_an_explicit_signal_mode_is_preserved():
    for mode in ("direct", "bridge"):
        deployment = f'[platforms.signal]\nmode = "{mode}"\n'
        generated = build('[platforms.signal]\nmode = "bridge"\n', deployment)
        assert tomllib.loads(generated)["platforms"]["signal"]["mode"] == mode
        assert not divergences(generated, deployment)
