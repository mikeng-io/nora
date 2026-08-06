"""R-24: no single call, and no single tool result, may spend the turn.

Every number asserted here was measured on the live deployment on 2026-08-06,
and every one of them was violated by the config that shipped before that day.
The rule exists because all three violations were invisible: a budget that is
too small does not fail loudly, it just stops the turn early — and until R-23
it stopped it in a way that read as the model choosing not to answer.

The measurements, once, so the constants below are not magic:

  per-call INPUT                    4,952 tokens (doctrine + transcript +
                                    tool schemas), paid again on EVERY call
  a 6-call turn, end to end         32,426 - 36,748 tokens total
  single-call completions           3,993 / 3,987 / 3,974 tokens against
                                    `max_tokens = 4000`
  largest legitimate completion     371 tokens
  read_buffer(200) round trip       6,592 tokens, in context for the rest
  read_buffer(40) round trip        1,368 tokens
  recall / lookup_person            151 / 112 tokens

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
from nora.agent.tools import NATIVE_TOOL_BUDGETS, READ_BUFFER_DEFAULT, READ_BUFFER_MAX

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ("config.toml", "config.example.toml")

# The worst measured per-call cost: 36,748 tokens over a 6-call turn. Used to
# ask whether `turns` is reachable at all, which is the question `total_tokens
# = 24000` silently answered "no" — it was crossed during call FIVE of eight.
MEASURED_TOKENS_PER_CALL = 36_748 // 6         # 6,124

# One tool result is charged again on every later call in the loop, so its
# cost is multiplied by the calls that follow it. A single tool result must
# stay well under a third of the turn.
MEASURED_TOKENS_PER_READ_BUFFER_ROW = 6_592 / 200      # ~33


def _llm(name: str) -> dict:
    return tomllib.loads((ROOT / name).read_text())["llm"]


@pytest.mark.parametrize("config_name", CONFIGS)
def test_no_single_call_can_spend_the_whole_turn_budget(config_name):
    """R-24. The named test for the rule.

    Three separate ways a single call used to be able to consume the turn,
    all asserted against the shipped config rather than against a builder —
    the numbers are the deliverable here, so a test that made up its own would
    assert nothing.
    """
    llm = _llm(config_name)
    limits = llm["limits"]

    # 1. The loop's output budget is not one call's ceiling. These were
    #    literally the same number (`build_limits(max_tokens=...)`), and a
    #    measured call emitted 99.8% of it.
    assert limits["output_tokens"] > llm["max_tokens"], config_name
    assert limits["output_tokens"] >= 2 * llm["max_tokens"], config_name

    # 2. `turns` is reachable, so it is the cap that binds. At the old 24,000
    #    a turn ran out of tokens during call five and `turns = 8` was dead
    #    config describing a limit that could never be reached.
    assert limits["total_tokens"] >= limits["turns"] * MEASURED_TOKENS_PER_CALL, config_name

    # 3. The per-call ceiling has headroom over what a real completion costs,
    #    without being so large that one call could swallow the loop budget.
    assert llm["max_tokens"] >= 4 * 371, config_name

    # 4. One `read_buffer` result — the only tool whose size the MODEL picks —
    #    cannot be a third of the turn. At the old ceiling of 200, two calls
    #    (its R-7 budget) were 13,184 tokens.
    worst_read_buffer = (
        READ_BUFFER_MAX * MEASURED_TOKENS_PER_READ_BUFFER_ROW
        * NATIVE_TOOL_BUDGETS["read_buffer"]
    )
    assert worst_read_buffer < limits["total_tokens"] / 3, config_name


@pytest.mark.parametrize("config_name", CONFIGS)
def test_both_config_files_declare_the_same_budget(config_name):
    """`config.example.toml` is what a fresh install runs and what
    `tests/test_config.py` loads; `config.toml` is what this deployment runs.
    A budget fix applied to one and not the other is the state master was in
    when this work started — green only because of an uncommitted tree."""
    assert _llm(config_name)["limits"] == _llm("config.toml")["limits"]
    assert _llm(config_name)["max_tokens"] == _llm("config.toml")["max_tokens"]


@pytest.mark.parametrize("config_name", CONFIGS)
def test_all_three_caps_are_declared(config_name):
    """A cap absent from `limits` is not a cap: Strands' `_check_limits` skips
    one it cannot find, so an omitted `output_tokens` silently means
    "unbounded". `nora.config.LimitsConfig` makes all three required; this
    checks the files actually carry them."""
    assert set(_llm(config_name)["limits"]) == {"turns", "total_tokens", "output_tokens"}


def test_the_read_buffer_clamp_is_the_only_bound_on_a_model_supplied_size():
    """`limit` is the one argument in `nora.agent.tools` a model invents on
    every call, and nothing downstream bounds it — `store.recent` will happily
    serve whatever it is given. 200 was 6,592 tokens per call, paid again on
    every subsequent call of the loop."""
    assert READ_BUFFER_MAX <= 60
    assert READ_BUFFER_DEFAULT <= READ_BUFFER_MAX
    # STRICTLY below the prefill's own context window (`store.context_window`
    # = 40, which the model already has in front of it): a call that does not
    # say how far back it wants is asking for a little more, not for the whole
    # room. `<=` would be satisfied by the old default of exactly 40.
    assert READ_BUFFER_DEFAULT < 40


def test_the_model_is_told_the_same_ceiling_the_clamp_enforces():
    """The docstring IS the model-facing schema (Strands derives `description`
    and `inputSchema` from the callable), and it cannot interpolate
    `READ_BUFFER_MAX` — an f-string would not be a docstring at all and
    `__doc__` would be None. So the literal is checked here instead. A clamp
    the model is not told about produces a silently truncated answer it thinks
    is complete."""
    source = (Path(tools.__file__)).read_text()
    assert f"limit: How many recent messages to fetch (max {READ_BUFFER_MAX})." in source
