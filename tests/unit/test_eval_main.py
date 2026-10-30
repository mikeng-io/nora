"""`python -m evals` — corpus selection and the default source URL, no DB/network.

A one-line change must not cost a full run, so `--corpus`/`--class`/`--all` must actually narrow
what runs, and picking nothing must refuse rather than silently running everything.
The source URL section pins `source_url_for` against generated credentials:
the default resolves the deployment's credential source the way the host
tooling does (process env > `.env` > `secret.toml`), an explicit URL reads
none of them, and the clone-name guard never relaxes.
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


def test_the_estimate_counts_draws_and_bounds_their_cost():
    """Per corpus, cases x samples x that corpus's measured per-draw cost."""
    from evals.__main__ import JUDGED_DRAW_SECONDS, UNJUDGED_DRAW_SECONDS, estimate

    est = estimate([(2, False), (1, True)], 3)
    assert (est.cases, est.sample, est.draws) == (3, 3, 9)
    assert est.seconds == 6 * UNJUDGED_DRAW_SECONDS + 3 * JUDGED_DRAW_SECONDS
    assert est.line() == (
        f"estimate: 3 cases x 3 samples x 1 arm = 9 draws, ~{est.seconds / 60:.0f} min"
    )
    # The judge's share scales with --judge-samples; at the default it is 211 s.
    one = estimate([(1, True)], 1, judge_samples=1).seconds
    assert estimate([(1, True)], 1, judge_samples=5).seconds > JUDGED_DRAW_SECONDS > one
    assert estimate([(1, False)], 1, judge_samples=5).seconds == UNJUDGED_DRAW_SECONDS


def test_the_run_that_held_the_lock_all_night_is_refused_unless_allowed():
    """tool_selection on the graph surface, every case x 3, holds the host's
    one eval lock for hours: refused, naming the way out. The same run starts
    with --allow-long, and two cases x 3 start without it."""
    from evals.__main__ import BUDGET_MINUTES, estimate, over_budget
    from evals.scenarios import selected_cases

    cases = len(selected_cases("tool_selection", memory_index=True))
    full = estimate([(cases, False)], 3)
    assert full.minutes > BUDGET_MINUTES
    why = over_budget(full, allow_long=False)
    assert why is not None and "--case" in why and "--allow-long" in why
    assert over_budget(full, allow_long=True) is None
    assert over_budget(estimate([(2, False)], 3), allow_long=False) is None


def test_the_budget_edge_is_inclusive():
    from evals.__main__ import BUDGET_MINUTES, UNJUDGED_DRAW_SECONDS, Estimate, over_budget

    at = Estimate(cases=1, sample=1, draws=1, seconds=BUDGET_MINUTES * 60)
    assert over_budget(at, allow_long=False) is None
    over = Estimate(cases=1, sample=1, draws=1, seconds=BUDGET_MINUTES * 60 + UNJUDGED_DRAW_SECONDS)
    assert over_budget(over, allow_long=False) is not None


def test_a_case_the_run_cannot_draw_is_refused_before_the_estimate():
    from evals.scenarios import selected_cases

    with pytest.raises(ValueError, match="no active case named"):
        selected_cases("tool_selection", only=frozenset({"no-such-case"}))


# --------------------------------------------------------------------------
# source_url_for: generated credentials only; every value is synthetic.
# --------------------------------------------------------------------------


def _deployment(tmp_path, *, secret=None, env=None):
    if secret is not None:
        (tmp_path / "secret.toml").write_text(
            f'[credentials]\nPOSTGRES_PASSWORD="{secret}"\n'
        )
    if env is not None:
        (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD={env}\n")
    return tmp_path


def test_source_url_default_accepts_secret_only(tmp_path, monkeypatch):
    """A deployment whose credential lives ONLY in secret.toml still gives
    the eval its clone URL — the .env-only default refused exactly this."""
    from sqlalchemy.engine import make_url

    from evals.__main__ import EVAL_CLONE, source_url_for

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    parsed = make_url(
        source_url_for("", _deployment(tmp_path, secret="synthetic-secret"))  # noqa: S106 -- synthetic fixture
    )
    assert (parsed.drivername, parsed.username, parsed.host, parsed.port,
            parsed.database, parsed.password) == (
        "postgresql+asyncpg", "nora", "127.0.0.1", 5435,
        EVAL_CLONE, "synthetic-secret")


def test_source_url_host_precedence_and_empty_override(tmp_path, monkeypatch):
    """Process env > .env > secret.toml; a PRESENT empty value wins the layers
    below it, so an operator override is a refusal, never the file's value."""
    from sqlalchemy.engine import make_url

    from evals.__main__ import source_url_for

    deployment = _deployment(
        tmp_path, secret="synthetic-secret", env="synthetic-legacy"  # noqa: S106 -- synthetic fixture
    )
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert make_url(source_url_for("", deployment)).password == "synthetic-legacy"  # noqa: S105 -- synthetic fixture
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-process")
    assert make_url(source_url_for("", deployment)).password == "synthetic-process"  # noqa: S105 -- synthetic fixture
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    with pytest.raises(ValueError, match="no POSTGRES_PASSWORD") as error:
        source_url_for("", deployment)
    assert "synthetic-legacy" not in str(error.value)


def test_source_url_explicit_skips_credentials_and_refuses_live_name(tmp_path):
    """A stated URL reads no credential file at all — this deployment dir is
    broken on purpose and would raise if read — and the clone-name guard is
    unchanged."""
    from evals.__main__ import source_url_for

    (tmp_path / "secret.toml").write_text("malformed = [")
    stated = "postgresql+asyncpg://u:p@h/generated_eval"
    assert source_url_for(stated, tmp_path) == stated
    with pytest.raises(ValueError, match="not a clone"):
        source_url_for("postgresql+asyncpg://u:p@h/nora", tmp_path)


def test_source_url_password_reserved_characters_roundtrip(tmp_path, monkeypatch):
    """The URL is built with `URL.create`, not string interpolation, so a
    password full of reserved characters parses back to itself."""
    from sqlalchemy.engine import make_url

    from evals.__main__ import source_url_for

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    password = "syn:thetic/p@ss?w#rd%&="  # noqa: S105 -- synthetic fixture
    url = source_url_for("", _deployment(tmp_path, env=password))
    parsed = make_url(url)
    assert parsed.password == password
    assert parsed.host == "127.0.0.1" and parsed.database == "nora_eval"


def test_source_url_default_refuses_missing_and_broken_credentials(
    tmp_path, monkeypatch,
):
    from evals.__main__ import source_url_for

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="no POSTGRES_PASSWORD"):
        source_url_for("", tmp_path)
    (tmp_path / "secret.toml").write_text("malformed = [")
    with pytest.raises(ValueError, match="unreadable"):
        source_url_for("", tmp_path)
