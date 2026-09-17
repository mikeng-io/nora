"""End to end: corpora in, measurements out — with no model reachable.

The reading that matters here is the NEGATIVE one. A run that reached no model
must say so FIRST and must not print a clean bar: an unreachable provider
producing `PASS` is the exact substitution this layer was rebuilt to stop, and
it has happened before in this repo's own instruments.

One case in three DOES grade without a model, and that is a property rather
than a leak: a `no_trigger` near-miss is refused by `nora/tag.py` inside ingest
before any model call, so "she did not answer" is a complete and correct
reading. The gate and the participation turn cannot say that, and both come
back as ERROR. Two different answers from one runner, each right for its own
reason, is what the fail-closed rule buys.
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from evals.rebuilt.report import render
from evals.rebuilt.runner import load, run

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPORA = ROOT / "evals" / "corpora"

_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}


def test_every_corpus_loads() -> None:
    """Ten files, one loader, no per-suite code."""
    files = sorted(CORPORA.glob("*.yaml"))
    assert len(files) == 11, [f.name for f in files]
    total = sum(len(load(f)) for f in files)
    assert total == 128, total


@pytest.mark.asyncio
async def test_a_run_with_no_model_reports_incomplete_and_fails(
    pg_url, monkeypatch,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    # One case per entry point — the cheapest run that still exercises all
    # three. `sample=1` is forced: the corpora ask for five draws because one
    # draw of a stochastic process is not a reading of it, and this test is not
    # reading anything, it is checking that nothing gets read.
    picks = [
        dataclasses.replace(load(CORPORA / "relevance_gate.yaml")[0], sample=1),
        dataclasses.replace(load(CORPORA / "no_trigger.yaml")[0], sample=1),
        dataclasses.replace(load(CORPORA / "participation.yaml")[0], sample=1),
    ]
    verdicts = [
        await run(c, database_url=pg_url, overrides=_NO_PROVIDER) for c in picks
    ]
    passed, text = render(verdicts, subject="nora.subject.obtain")

    assert passed is False, f"a run that reached no model reported PASS:\n{text}"
    assert text.splitlines()[0] == "FAIL"
    assert "0. THE RUN ITSELF — INCOMPLETE" in text, text
    assert "1/3 draws returned a verdict" in text, text

    by_entry = {v.case.entry: v for v in verdicts}
    # The two that need a model: ERROR, and therefore ungradable. Not FAIL —
    # `meets` returns the third answer rather than scoring an un-run case.
    for name in ("gate", "participation"):
        v = by_entry[name]
        assert not v.gradable, f"{name} produced a verdict with no model"
        assert v.errors, name
        assert all(o.meets(v.case.expect) is None for o in v.outcomes)

    # The one that does not: graded, and graded RIGHT. `nora/tag.py` refused
    # admission, so no turn ran, so nothing was silently counted as silence —
    # which is the reading the tag path's fail-closed rule exists to separate
    # from this one.
    v = by_entry["tag"]
    assert v.gradable and not v.errors, v.errors
    assert v.hits == 1, f"a genuine near-miss engaged her: {v.outcomes}"
