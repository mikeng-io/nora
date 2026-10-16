"""R-99 — a call carrying an argument the tool does not declare must not run
silently.

THE DEFECT. `strands.tools.decorator.FunctionToolMetadata._create_input_model`
builds a pydantic model from exactly a tool's own signature, with no
`model_config` set anywhere in that class — so pydantic v2's own default
(`extra="ignore"`) makes `validate_input` SILENTLY DROP any key a call carries
that the tool never declared. E.g. `media_fetch` called with `file=`, which it
does not accept, arrives empty. `instagram_read` carries a deliberate `url`
alias for one such case; this rule is the GENERIC guard, so the next tool does
not need one.

THE GUARD. `ReplyGuard.evaluate` (`nora/agent/guards.py`) vetoes such a call
BEFORE Strands' own validation drops anything, from `ToolRegistry.
tool_param_names()` — each tool's accepted argument names, read off its own
signature, never hand-copied. `conversation_id` is exempted: R-3, right below
R-99 in `evaluate`, is the deliberate DEC-24 decision that an invented one is
counted and never vetoed, and this file proves the two rules do not collide.
"""

from __future__ import annotations

import json

import pytest

from nora.agent.guards import ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics
from nora.testing import FakeAgentRunner, ScriptedTurn

REGISTRY = ToolRegistry()
BUDGETS = REGISTRY.budgets()
TERMINAL = REGISTRY.terminal_tools()
PARAM_NAMES = REGISTRY.tool_param_names()

#: Tools this registry never BUILDS at all — a `plugin=True` entry (`skills`,
#: DEC-50) is registered by `strands.vended_plugins.skills.AgentSkills`, not
#: by `build_tool_specs`, so there is no callable here to read a signature
#: off. Named explicitly so the parametrized coverage test below states what
#: it does NOT reach rather than silently skipping an unnamed subset.
UNCOVERED = frozenset(name for name in BUDGETS if name not in PARAM_NAMES)


def _guard(metrics: Metrics | None = None) -> ReplyGuard:
    return ReplyGuard(
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics or Metrics(), param_names=PARAM_NAMES,
    )


def test_at_least_one_plugin_tool_is_uncovered_and_named():
    """`skills` is the known, documented gap — if this ever goes empty, the
    reasoning in `UNCOVERED`'s docstring above has gone stale and needs a
    fresh read of `NATIVE_TOOLS`."""
    assert "skills" in UNCOVERED


def test_media_fetch_called_with_file_is_named_by_the_veto():
    """The exact live defect. `file` must be refused, not silently dropped,
    and the refusal must name it AND `media_fetch`'s real parameters, so the
    model can retry correctly."""
    msg = _guard().evaluate("media_fetch", {"file": "https://example.com/x.jpg"})
    assert msg is not None
    payload = json.loads(msg)
    assert payload["status"] == "bad_arguments"
    assert "file" in payload["note"]
    for name in PARAM_NAMES["media_fetch"]:
        assert name in payload["note"]


def test_a_well_formed_media_fetch_call_is_not_vetoed():
    """The control: `url` IS one of `media_fetch`'s real parameters (a link,
    not `file`), so a call naming it must pass through untouched."""
    assert _guard().evaluate(
        "media_fetch", {"url": "https://example.com/x.jpg"},
    ) is None


def test_instagram_reads_deliberate_url_alias_still_works():
    """The `url` alias this rule must not break: `instagram_read` declares
    `url` as a REAL second parameter (`nora.agent.tools`), so it is in
    `PARAM_NAMES` like any other declared name and a call naming it is not
    an R-99 violation."""
    assert "url" in PARAM_NAMES["instagram_read"]
    assert _guard().evaluate(
        "instagram_read", {"url": "https://instagram.com/p/abc/"},
    ) is None


def test_conversation_id_alone_is_still_tolerated_by_r99():
    """R-3's tolerance (DEC-24) must survive R-99 unchanged: a model that
    cannot help inventing a conversation id must still be able to reply."""
    assert _guard().evaluate(
        "send_reply", {"text": "hi", "conversation_id": "g2"},
    ) is None


def test_conversation_id_does_not_shield_a_second_unknown_key():
    """The exemption is for `conversation_id` alone, not a free pass for the
    rest of the call — a genuinely unknown key beside it is still refused."""
    msg = _guard().evaluate(
        "media_fetch", {"file": "x", "conversation_id": "g2"},
    )
    assert msg is not None
    assert "file" in msg


def test_a_tool_this_registry_cannot_introspect_is_left_alone():
    """A plugin tool has no entry in `PARAM_NAMES` (see `UNCOVERED`), so R-99
    has nothing to check its input against. `evaluate` must read that as
    "cannot verify", never as "reject everything" — the same fail-closed
    direction applied to the CHECK itself rather than to the tool's answer."""
    assert _guard().evaluate("skills", {"anything": "goes"}) is None


def test_a_guard_built_with_no_param_names_never_fires_r99():
    """The default (`param_names=None`) is the rule OFF, the same shape
    `lookups=None` takes — every test that builds a `ReplyGuard` without this
    axis keeps a veto-free behaviour."""
    guard = ReplyGuard(
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL, metrics=Metrics(),
    )
    assert guard.evaluate("media_fetch", {"file": "x"}) is None


@pytest.mark.parametrize("name", sorted(PARAM_NAMES))
def test_every_introspectable_tool_rejects_an_argument_it_does_not_declare(name):
    """THE PARAMETRIZED COVERAGE. Every tool this registry can introspect
    refuses a call carrying a key its own signature does not declare — proven
    against the REAL signature (`ToolRegistry.tool_param_names()`), not a
    hand-picked handful, so a new tool is covered the moment it is added. See
    `UNCOVERED` for the named tools this cannot reach."""
    probe = "__r99_unknown_argument_probe__"
    msg = _guard().evaluate(name, {probe: "x"})
    assert msg is not None, f"{name} silently accepted an undeclared argument"
    payload = json.loads(msg)
    assert payload["status"] == "bad_arguments"
    assert probe in payload["note"]


async def test_the_veto_fires_before_the_tool_body_ever_runs():
    """THE END-TO-END PROOF, through the REAL guard hooks
    (`nora.testing.FakeAgentRunner`, the same fidelity
    `tests/test_scripted_agent_fidelity.py` exists for) rather than a bare
    call to `evaluate`: an unknown argument must not reach the tool's body,
    the returned text must be Nora's own status shape, and it must name both
    the unknown argument and what the tool actually accepts.
    """
    calls: list[dict] = []

    async def _echo(text: str = "") -> dict:
        """Echo `text` back.

        Returns: a status dict.
        """
        calls.append({"text": text})
        return {"status": "ok"}

    tool = adapt(ToolSpec(name="echo", description="", schema={}, call=_echo, budget=1))

    class _Recording(ReplyGuard):
        """Records every after-tool `cancel_message`, the same pattern
        `test_scripted_agent_fidelity.py` uses — subclassed so the REAL rule
        still applies and this cannot pass by doing nothing."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cancel_messages: list[str | None] = []

        def _after_tool(self, event):
            self.cancel_messages.append(getattr(event, "cancel_message", None))
            super()._after_tool(event)

    guard = _Recording(
        conversation_id="g1", budgets={"echo": 1}, terminal=frozenset(),
        metrics=Metrics(), param_names={"echo": frozenset({"text"})},
    )
    await FakeAgentRunner([ScriptedTurn(tool_calls=[("echo", {"bogus": "x"})])]).invoke(
        tools=[tool], guard=guard, limits=None,
    )

    assert calls == [], "the tool body ran despite the unknown argument"
    assert len(guard.cancel_messages) == 1
    cancel_message = guard.cancel_messages[0]
    assert cancel_message is not None
    payload = json.loads(cancel_message)
    assert payload["status"] == "bad_arguments"
    assert "bogus" in payload["note"]
    assert "text" in payload["note"]
    # The budget interaction: a call this guard vetoed must not spend the
    # tool's budget (DEC-33's mechanism, reused here rather than re-invented —
    # `_after_tool` never `record()`s a cancelled call), so the model has its
    # whole budget left to retry with the correct argument name.
    assert guard.counts.get("echo", 0) == 0


async def test_a_well_formed_retry_runs_normally_after_a_veto():
    """The other half of the budget claim: a vetoed call costs nothing, so the
    SAME budget-of-1 tool can still run once the model retries correctly."""
    calls: list[dict] = []

    async def _echo(text: str = "") -> dict:
        """Echo `text` back.

        Returns: a status dict.
        """
        calls.append({"text": text})
        return {"status": "ok"}

    tool = adapt(ToolSpec(name="echo", description="", schema={}, call=_echo, budget=1))
    guard = ReplyGuard(
        conversation_id="g1", budgets={"echo": 1}, terminal=frozenset(),
        metrics=Metrics(), param_names={"echo": frozenset({"text"})},
    )
    await FakeAgentRunner([ScriptedTurn(tool_calls=[
        ("echo", {"bogus": "x"}), ("echo", {"text": "hi"}),
    ])]).invoke(tools=[tool], guard=guard, limits=None)

    assert calls == [{"text": "hi"}]
    assert guard.counts.get("echo", 0) == 1
