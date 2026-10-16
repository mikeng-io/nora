"""`python -m evals` — corpus selection, no DB/network.

A one-line change must not cost a full run, so `--corpus`/`--class`/`--all` must actually narrow
what runs, and picking nothing must refuse rather than silently running everything.
"""

from __future__ import annotations

import argparse

import pytest

from evals.__main__ import CORPUS_CLASS, _corpora_for
from evals.scenarios import CORPUS_ENTRY, JUDGED_CORPORA


def _args(**kwargs: object) -> argparse.Namespace:
    defaults = {"all": False, "corpus": [], "cls": []}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_corpus_class_only_names_corpora_that_exist():
    assert set(CORPUS_CLASS) <= set(CORPUS_ENTRY)


def test_no_selection_is_empty_not_everything():
    """The reason this script exists: a one-line change must not have to
    cost a full run by default."""
    assert _corpora_for(_args()) == []


def test_explicit_corpus_names_are_selected():
    result = _corpora_for(_args(corpus=["no_trigger", "near_miss"]))
    assert result == ["near_miss", "no_trigger"]


def test_class_expands_to_every_corpus_in_it():
    result = _corpora_for(_args(cls=["S7"]))
    expected = {name for name, c in CORPUS_CLASS.items() if c == "S7"}
    assert set(result) == expected
    assert len(expected) > 1, "a meaningful smoke test needs a class with >1 corpus"


def test_all_ignores_narrower_selections_and_returns_everything():
    result = _corpora_for(_args(all=True, corpus=["no_trigger"]))
    assert set(result) == set(CORPUS_ENTRY)


def test_an_unknown_corpus_name_is_refused_not_silently_dropped():
    with pytest.raises(SystemExit) as exc_info:
        _corpora_for(_args(corpus=["not_a_real_corpus"]))
    assert exc_info.value.code == 2


def test_judged_corpora_are_reachable_by_class():
    """S3/S9 must be selectable by class, not only by exact corpus name —
    otherwise `--class S3` silently runs nothing."""
    s3 = set(_corpora_for(_args(cls=["S3"])))
    s9 = set(_corpora_for(_args(cls=["S9"])))
    # `standing` (the fold) is judged and sits in S6,
    # the class of what a WRITE keeps.
    s6 = set(_corpora_for(_args(cls=["S6"])))
    assert s3 | s9 | (s6 & JUDGED_CORPORA) == JUDGED_CORPORA
    assert "standing" in s6


def test_positions_prints_each_draws_written_position_and_its_length(capsys):
    """The judge's reason cannot say WHY a finding was left out, and the
    position's length against the cap is the first thing that does. The row
    is the `EvaluationData` dump, so the position rides on its
    `actual_environment_state` — read by NAME, so a sibling state cannot be
    printed in its place."""
    from types import SimpleNamespace

    from evals.__main__ import _print_positions
    from evals.backbone import NORA_POSITION, NORA_SHOWN

    body = "我應承咗幫佢睇場地。\n搵到三間，send 咗一間。"
    report = SimpleNamespace(
        cases=[
            {"name": "residue-x#1", "actual_environment_state": [
                {"name": NORA_SHOWN, "state": "the fold's own input"},
                {"name": NORA_POSITION, "state": body},
            ]},
            {"name": "residue-x#2", "actual_environment_state": []},
        ],
        detailed_results=[
            [SimpleNamespace(label="FAIL")],
            [SimpleNamespace(label="ERROR")],
        ],
    )
    _print_positions(report)
    out = capsys.readouterr().out
    assert f"[FAIL] residue-x#1  ({len(body)} chars)" in out
    assert "      | 搵到三間，send 咗一間。" in out
    assert "the fold's own input" not in out
    assert "[ERROR] residue-x#2  (none)" in out
