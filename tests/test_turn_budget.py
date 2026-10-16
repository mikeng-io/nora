"""R-24: no single call, and no single tool result, may spend the turn.

The numbers asserted here were measured on the live deployment. The rule
exists because a violation is invisible: a budget that is too small does not
fail loudly, it just stops the turn early; R-23 keeps that from reading as the
model choosing not to answer.

The measurements, once, so the constants below are not magic:

  per-call INPUT                    4,952 tokens (doctrine + transcript +
                                    tool schemas), paid again on EVERY call
  a 6-call turn, end to end         32,426 - 36,748 tokens total
  single-call completions           3,993 / 3,987 / 3,974 tokens against
                                    `max_tokens = 4000`
  largest legitimate completion     371 tokens
  load_transcript(200) round trip       6,592 tokens, in context for the rest
  load_transcript(40) round trip        1,368 tokens
  about / lookup_person    151 / 112 tokens (the first measured
                                    before DEC-38 widened it; see tools.py)

Asserted against BOTH config files. `config.toml` is what the deployment runs;
`config.example.toml` is what a fresh install gets and what
`tests/test_config.py::test_config_uses_the_target_shape` copies — they drift,
and a fix applied to one of them is half a fix.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from nora.agent import tools
from nora.agent.registry import ToolRegistry
from nora.agent.tools import LOAD_TRANSCRIPT_DEFAULT, LOAD_TRANSCRIPT_MAX

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ("config.toml", "config.example.toml")

# The worst measured per-call cost: 36,748 tokens over a 6-call turn. It
# decides whether `turns` is reachable at all: a `total_tokens` below `turns`
# times this means the turn runs out of tokens first.
MEASURED_TOKENS_PER_CALL = 36_748 // 6         # 6,124

# One tool result is charged again on every later call in the loop, so its
# cost is multiplied by the calls that follow it. A single tool result must
# stay well under a third of the turn.
MEASURED_TOKENS_PER_LOAD_TRANSCRIPT_ROW = 6_592 / 200      # ~33


def _llm(name: str) -> dict:
    """The `[llm]` table of one config file, or a SKIP when it is absent.

    `config.example.toml` is tracked and always present. `config.toml` is the
    deployment's own file and untracked, so a fresh clone has none — and this
    file asserts against BOTH on purpose (the template is what a new deployment
    copies; the live file is what this one actually runs).

    A skip rather than a silent pass: pytest prints it, so a run that compared
    the two and a run that had only one to look at stay distinguishable. It
    does not run somewhere else instead: `config.toml` is gitignored in every
    dev worktree and in CI, and the deploy checkout is off-limits for
    verification, so this check against the live budget is not proven to run
    anywhere under the current workflow.
    """
    path = ROOT / name
    if not path.is_file():
        pytest.skip(
            f"no {name} in this checkout — untracked since 2026-09-07, so the "
            "budget cross-check has nothing to read here. It does not run "
            "anywhere in the current workflow either: config.toml is "
            "gitignored in every worktree and CI, and the deploy checkout is "
            "off-limits for verification."
        )
    return tomllib.loads(path.read_text())["llm"]


@pytest.mark.parametrize("config_name", CONFIGS)
def test_no_single_call_can_spend_the_whole_turn_budget(config_name):
    """R-24. The named test for the rule.

    Three separate ways a single call could consume the turn,
    all asserted against the shipped config rather than against a builder —
    the numbers are the deliverable here, so a test that made up its own would
    assert nothing.
    """
    llm = _llm(config_name)
    limits = llm["limits"]

    # 1. The loop's output budget is not one call's ceiling. They must not be
    #    the same number (`build_limits(max_tokens=...)`): a measured call emitted
    #    99.8% of its ceiling.
    assert limits["output_tokens"] > llm["max_tokens"], config_name
    assert limits["output_tokens"] >= 2 * llm["max_tokens"], config_name

    # 2. `turns` is reachable, so it is the cap that binds. A `total_tokens` of
    #    24,000 would run a turn out of tokens during call five and leave
    #    `turns = 8` dead config describing a limit that could never be reached.
    assert limits["total_tokens"] >= limits["turns"] * MEASURED_TOKENS_PER_CALL, config_name

    # 3. The per-call ceiling has headroom over what a real completion costs,
    #    without being so large that one call could swallow the loop budget.
    assert llm["max_tokens"] >= 4 * 371, config_name

    # 4. One `load_transcript` result — the only tool whose size the MODEL picks —
    #    cannot be a third of the turn. At a ceiling of 200, two calls
    #    (its R-7 budget) were 13,184 tokens.
    #    The budget is RESOLVED through the registry, not read off the table:
    #    `[llm.budgets]` can move it per deployment, and reading the table would
    #    leave this proof green while the property it proves stopped holding in
    #    production. `budget_overrides` comes from the
    #    config file under test, so each config is checked against ITS OWN caps.
    effective = ToolRegistry(
        budget_overrides=llm.get("budgets", {})).budgets()["load_transcript"]
    worst_load_transcript = (
        LOAD_TRANSCRIPT_MAX * MEASURED_TOKENS_PER_LOAD_TRANSCRIPT_ROW * effective
    )
    assert worst_load_transcript < limits["total_tokens"] / 3, (
        f"{config_name}: load_transcript budget {effective} x "
        f"{LOAD_TRANSCRIPT_MAX} rows exceeds a third of the turn"
    )


@pytest.mark.parametrize("config_name", CONFIGS)
def test_both_config_files_declare_the_same_budget(config_name):
    """`config.example.toml` is what a fresh install runs and what
    `tests/test_config.py` loads; `config.toml` is what this deployment runs.
    A budget fix applied to one and not the other is half a fix."""
    assert _llm(config_name)["limits"] == _llm("config.toml")["limits"]
    assert _llm(config_name)["max_tokens"] == _llm("config.toml")["max_tokens"]


@pytest.mark.parametrize("config_name", CONFIGS)
def test_all_three_caps_are_declared(config_name):
    """A cap absent from `limits` is not a cap: Strands' `_check_limits` skips
    one it cannot find, so an omitted `output_tokens` silently means
    "unbounded". `nora.config.LimitsConfig` makes all three required; this
    checks the files actually carry them."""
    assert set(_llm(config_name)["limits"]) == {"turns", "total_tokens", "output_tokens"}


def test_the_load_transcript_clamp_is_the_only_bound_on_a_model_supplied_size():
    """`limit` is the one argument in `nora.agent.tools` a model invents on
    every call, and nothing downstream bounds it — `store.recent` will happily
    serve whatever it is given. 200 costs 6,592 tokens per call, paid again on
    every subsequent call of the loop."""
    assert LOAD_TRANSCRIPT_MAX <= 60
    assert LOAD_TRANSCRIPT_DEFAULT <= LOAD_TRANSCRIPT_MAX
    # STRICTLY below the prefill's own context window (`store.context_window`
    # = 40, which the model already has in front of it): a call that does not
    # say how far back it wants is asking for a little more, not for the whole
    # room. `<=` would be satisfied by a default of exactly 40.
    assert LOAD_TRANSCRIPT_DEFAULT < 40


def test_the_model_is_told_the_same_ceiling_the_clamp_enforces():
    """The docstring IS the model-facing schema (Strands derives `description`
    and `inputSchema` from the callable), and it cannot interpolate
    `LOAD_TRANSCRIPT_MAX` — an f-string would not be a docstring at all and
    `__doc__` would be None. So the literal is checked here instead. A clamp
    the model is not told about produces a silently truncated answer it thinks
    is complete."""
    source = (Path(tools.__file__)).read_text()
    # Twice: `load_transcript` and `room_read` share the clamp. «recent» is not in
    # the wording because `before` makes the window movable.
    assert source.count(
        f"limit: How many messages to fetch (max {LOAD_TRANSCRIPT_MAX})."
    ) == 2
