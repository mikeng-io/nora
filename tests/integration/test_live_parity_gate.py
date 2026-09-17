"""The live parity arm — `design/evaluation/05-live-parity.md`, made repeatable.

SKIPPED unless a provider is reachable, because it costs real model calls. Run
it with an `ssh -L 20128:localhost:20128 norty` tunnel up and
`OMNIROUTE_API_KEY` set:

    NORA_LIVE_EVAL=1 uv run pytest tests/integration/test_live_parity_gate.py

Both arms are handed the SAME `Config` object, so the only variable is the
assembly. That is the whole point: a parity run whose two arms differ in
configuration measures the configuration.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib

import pytest

from evals.rebuilt.runner import load, run_once
from nora.app.eval_support import build_gate_probe
from nora.subject import SubjectSpec
from nora.subject.obtain import build_config

ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not os.environ.get("NORA_LIVE_EVAL"),
    reason="live model run; set NORA_LIVE_EVAL=1 with a provider reachable",
)

#: The DEPLOYED aliases, read off norty's `config.toml`. A parity run against
#: an alias the deployment does not use measures a model nobody ships.
LIVE = {
    "llm.base_url": "http://127.0.0.1:20128/v1",
    "relevance_gate.model": "nora-gate",
    "llm.model": "nora",
    "relevance_gate.enabled": True,
    "participation.enabled": True,
}


def _bodies(case) -> tuple[str, ...]:
    """The old driver took bare strings; the corpus now states its speakers."""
    return tuple(
        r["body"] if isinstance(r, dict) else r
        for r in (case.knows or {}).get("transcript") or ()
    )


@pytest.mark.asyncio
async def test_the_two_layers_agree_on_the_gate(pg_url, tmp_path, capsys) -> None:
    """One corpus, two assemblies, one config.

    The bar is AGREEMENT, not accuracy: whether the new layer is right is what
    the corpus's labels are for, and whether it is the same Nora is what this
    is for. They are different questions and a single number would answer
    neither.
    """
    cases = [
        c for c in load(ROOT / "evals" / "corpora" / "relevance_gate.yaml")
        if c.name.startswith(("real/", "closed/"))
    ]
    cfg = build_config(
        SubjectSpec(database_url=pg_url, workdir=tmp_path, overrides=LIVE)
    )
    probe = build_gate_probe(cfg)

    disagreed = []
    for case in cases:
        old = await probe.run(
            transcript=_bodies(case),
            standing=(case.knows or {}).get("standing"),
            label=case.name,
        )
        got = await run_once(
            dataclasses.replace(case, sample=1),
            database_url=pg_url, overrides=LIVE,
        )
        new = "ERROR" if got.error else ("go" if got.admitted else "no_go")
        if old.verdict != new:
            disagreed.append(f"{case.name}: old={old.verdict} new={new}")

    with capsys.disabled():
        print(f"\nagreement {len(cases) - len(disagreed)}/{len(cases)}")
        for line in disagreed:
            print(f"  {line}")

    # AT MOST ONE, and the report names which — but NOT which one it will be.
    #
    # Measured twice on 2026-09-17: 15/16 both times, on a DIFFERENT case each
    # time (`closed/where-is-named`, then `closed/settled-place-scrolled-out`).
    # A disagreement that moves is variance on cases sitting near the boundary,
    # not two different Noras; the first run's case answered 4/5 against 5/5 at
    # n=5, which is the same reading.
    #
    # So this pins the RATE and deliberately not the identity. Pinning a name
    # would have been wrong on the second run, and a test that has to be edited
    # after every run is a test nobody trusts. TWO disagreements is a different
    # claim and this is where it surfaces.
    assert len(disagreed) <= 1, (
        f"{len(disagreed)} cases disagree between the two assemblies: "
        f"{disagreed}. One borderline case is expected and it moves between "
        "runs (05-live-parity §3); two means the layers are not driving the "
        "same Nora."
    )
