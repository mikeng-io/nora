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

from evals.rebuilt.runner import load, run, run_once

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPORA = ROOT / "evals" / "corpora"

_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}


@pytest.mark.asyncio
async def test_a_run_with_no_model_reports_incomplete_and_fails(
    pg_url, monkeypatch,
) -> None:
    """The old CLI's `render()` (`evals/rebuilt/report.py`) is gone
    (`design/evaluation/10-the-scenario-map.md` §15/16) — this asserted
    directly on `Verdict`/`Outcome` below even before the removal; only the
    printed-text checks against `render()`'s own format came out with it."""
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


@pytest.mark.asyncio
async def test_a_room_without_proactive_trust_is_an_error_not_silence(
    pg_url, monkeypatch,
) -> None:
    """INDUCED, not hoped for. `failed_policy` cannot occur once the corpora are
    configured correctly, so removing the guard that catches it leaves every
    test green — which is a pin that cannot fire.

    Here the case's own policy drops `proactive = true`, which is exactly the
    misconfiguration an operator makes. The screen then refuses the row and the
    turn never happens, producing an empty `said` byte-identical to a correct
    silence — and this case expects silence.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    case = next(
        c for c in load(CORPORA / "proactive_act.yaml") if c.expect == "silence"
    )
    untrusted = case.policy.replace("proactive = true\n", "")
    assert untrusted != case.policy, "the fixture no longer states the flag"

    outcome = await run_once(
        dataclasses.replace(case, sample=1, policy=untrusted),
        database_url=pg_url, overrides=_NO_PROVIDER,
    )
    assert "failed_policy" in (outcome.outcome_token or ""), outcome
    assert outcome.meets("silence") is None, (
        f"a room without proactive trust graded as silence: {outcome}. The "
        "screen refused the row, so nothing was measured."
    )


@pytest.mark.asyncio
async def test_a_workspace_with_no_home_room_is_an_error_not_none(
    pg_url, monkeypatch,
) -> None:
    """INDUCED, for the same reason. `no_home_room` makes the proactive adaptor
    return BEFORE it renders anything, answering `nothing_found` with an empty
    diff — which is what a workspace that had not moved answers too, and `none`
    is what several decide cases expect."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    case = next(
        c for c in load(CORPORA / "proactive_decide.yaml") if c.expect == "none"
    )
    # TWO workspaces, and the home room is in the OTHER one. Simply deleting
    # the flag does not induce this: with no home room anywhere the adaptor is
    # not BUILT, and a different guard catches that. `no_home_room` is the
    # adaptor existing and this workspace having none — which is what a policy
    # with more than one workspace produces, and it is the shape that ships.
    homeless = case.policy.replace("home_room = true\n", "") + (
        '[[conversations]]\nplatform = "signal"\nid = "elsewhere"\n'
        'persons = "*"\nunit = "somewhere-else"\n'
        "proactive = true\nhome_room = true\n"
    )
    assert "home_room = true" in homeless, "the adaptor must still be built"

    outcome = await run_once(
        dataclasses.replace(case, sample=1, policy=homeless),
        database_url=pg_url, overrides=_NO_PROVIDER,
    )
    assert outcome.meets("none") is None, (
        f"a workspace with no home room graded as none: {outcome}. The pass "
        "returned before rendering anything."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("corpus", ["proactive_decide.yaml", "proactive_act.yaml"])
async def test_a_proactive_case_never_grades_on_a_wiring_refusal(
    corpus, pg_url, monkeypatch,
) -> None:
    """The two proactive paths, driven, with no provider reachable.

    Every way these can end WITHOUT running produces an empty result that is
    byte-identical to a correct "nothing to do" — and `none`/`silence` is what
    most of these cases expect:

      `no_home_room`   the adaptor returned before rendering anything
      `failed_policy`  the room is not proactive-trusted
      `failed_error`   no note store, or a workspace the note cannot be reached
                       from
      `unavailable`    the model was asked for and could not be reached

    A unit test over `Outcome.meets` cannot catch a regression here, and did
    not: both guards were removed from `entry.py` and the unit suite stayed
    green. This drives the entry point, which is where the rule is applied.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    graded = []
    for case in load(CORPORA / corpus)[:3]:
        outcome = await run_once(
            dataclasses.replace(case, sample=1),
            database_url=pg_url, overrides=_NO_PROVIDER,
        )
        if outcome.meets(case.expect) is not None:
            graded.append(
                f"{case.name}: graded {outcome.meets(case.expect)} on "
                f"token={outcome.outcome_token!r} error={outcome.error!r}"
            )
    # `cancelled_*` IS a verdict and may legitimately grade — a note the tag
    # path overtook is the system working. Anything else must not.
    for line in graded:
        assert "cancelled_" in line, (
            f"a proactive case graded without running: {line}. Every path that "
            "ends without a turn produces an empty result, so an ungraded "
            "ERROR is the only reading that cannot lie."
        )
