"""The session's own verbs, as the SPECS Strands would hand the model (ADR 0015).

A closure declared `**kwargs` reaches Strands as one untyped `kwargs`
parameter, and a `Sequence[str]` without an item type as an array of nothing:
the model is told nothing about what to pass. This builds the tool list a
session really hands its agent (`CognitionSessionRunner._build_tools`), turns
it into Strands tools the way the live runner does (`as_strands_tools`), and
holds every one to its named, typed parameters and a docstring.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nora.app.cognition_agent_runner import as_strands_tools
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000

#: verb -> ({param: (json type, item type or enum or None)}, required)
EXPECTED: dict[str, tuple[dict[str, tuple[str, Any]], set[str]]] = {
    "close": (
        {"item_index": ("integer", None),
         "reason": ("string", ["written", "no_change", "waiting", "covered"])},
        {"item_index", "reason"},
    ),
    "note_create": (
        {"title": ("string", None), "description": ("string", None),
         "uncertainty": ("string", None), "closing_condition": ("string", None),
         "body": ("string", None), "anchor_date": ("string", None),
         "anchor_entities": ("array", {"type": "string"}), "room": ("string", None),
         "item": ("integer", None)},
        {"title", "description", "uncertainty", "closing_condition", "body"},
    ),
    "note_revise": (
        {"note_id": ("string", None), "body": ("string", None),
         "parent_revision_id": ("string", None), "room": ("string", None),
         "item": ("integer", None)},
        {"note_id", "body", "parent_revision_id"},
    ),
    "note_set_status": (
        {"note_id": ("string", None),
         "reason": ("string", ["settled", "anchor_passed"]),
         "base_revision_id": ("string", None), "room": ("string", None),
         "item": ("integer", None)},
        {"note_id", "reason"},
    ),
    "note_merge": (
        {"survivor_id": ("string", None), "duplicate_id": ("string", None),
         "body": ("string", None), "room": ("string", None), "item": ("integer", None)},
        {"survivor_id", "duplicate_id", "body"},
    ),
    "memo_create": (
        {"title": ("string", None), "body": ("string", None),
         "mode": ("string", ["told", "deduced", "abduced"]), "room": ("string", None),
         "item": ("integer", None)},
        {"title", "body"},
    ),
    "memo_revise": (
        {"memo_id": ("string", None), "base_revision_id": ("string", None),
         "body": ("string", None), "title": ("string", None),
         "mode": ("string", ["told", "deduced", "abduced"]), "room": ("string", None),
         "item": ("integer", None)},
        {"memo_id", "base_revision_id", "body"},
    ),
    "memo_set_status": (
        {"memo_id": ("string", None), "base_revision_id": ("string", None),
         "cited_ids": ("array", {"type": "string"}), "room": ("string", None),
         "item": ("integer", None)},
        {"memo_id", "base_revision_id", "cited_ids"},
    ),
    "event_propose": (
        {"title": ("string", None),
         "reasoning": ("string", ["told", "deduced", "abduced"]),
         "confidence": ("string", ["low", "medium", "high"]),
         "cited_ids": ("array", {"type": "string"}),
         "when": ("string", None), "venue_text": ("string", None),
         "status": ("string", ["staged", "proposed"]), "room": ("string", None),
         "item": ("integer", None)},
        {"title", "reasoning", "confidence", "cited_ids"},
    ),
    "event_revise": (
        {"event_id": ("string", None),
         "reasoning": ("string", ["told", "deduced", "abduced"]),
         "confidence": ("string", ["low", "medium", "high"]),
         "cited_ids": ("array", {"type": "string"}),
         "when": ("string", None),
         "venue_text": ("string", None), "base_revision_id": ("string", None),
         "room": ("string", None),
         "item": ("integer", None)},
        {"event_id", "reasoning", "confidence", "cited_ids"},
    ),
    "event_set_status": (
        {"event_id": ("string", None),
         "status": ("string", ["confirmed", "cancelled", "happened", "lapsed"]),
         "reasoning": ("string", ["told", "deduced", "abduced"]),
         "confidence": ("string", ["low", "medium", "high"]),
         "cited_ids": ("array", {"type": "string"}),
         "base_revision_id": ("string", None), "room": ("string", None),
         "item": ("integer", None)},
        {"event_id", "status", "reasoning", "confidence", "cited_ids"},
    ),
    "standing_revise": (
        {"where_it_stands": ("string", None), "hold_open": ("string", None),
         "i_owe": ("string", None),
         "mode": ("string", ["deduced", "abduced", "said"]),
         "base_revision_id": ("string", None), "room": ("string", None),
         "item": ("integer", None)},
        {"where_it_stands"},
    ),
    "wake_at": (
        {"subject_rows": ("array", {"type": "string"}), "at": ("string", None),
         "why": ("string", None)},
        {"subject_rows", "at", "why"},
    ),
    "wake_move": (
        {"wake_id": ("string", None), "at": ("string", None), "why": ("string", None)},
        {"wake_id", "at", "why"},
    ),
    "wake_cancel": (
        {"wake_id": ("string", None), "why": ("string", None)}, {"wake_id", "why"},
    ),
    "remind": (
        {"intent": ("string", ["tell", "ask"]), "text": ("string", None),
         "subject": ("string", None), "action": ("string", None),
         "subject_rows": ("array", {"type": "string"}), "at": ("string", None),
         "room": ("string", None),
         "also_rooms": ("array", {"type": "string"}),
         "new_action": ("boolean", None)},
        {"intent", "text", "subject", "action", "subject_rows", "at"},
    ),
    "remind_move": (
        {"reminder_id": ("string", None), "at": ("string", None),
         "why": ("string", None)},
        {"reminder_id", "at", "why"},
    ),
    "remind_cancel": (
        {"reminder_id": ("string", None), "why": ("string", None)},
        {"reminder_id", "why"},
    ),
}


class _Spy:
    """Captures the tool list a session hands its agent."""

    def __init__(self) -> None:
        self.tools: list[Any] = []

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.tools = list(tools)
        return CognitionRunResult(stop_reason="end_turn")


def _const(value: Any) -> Any:
    async def f(*args: Any, **kwargs: Any) -> Any:
        return value
    return f


@pytest.fixture
async def specs() -> dict[str, dict[str, Any]]:
    spy = _Spy()
    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    stimuli.seed_wake("u", 1)
    runner = CognitionSessionRunner(
        store=ledger, stimulus_store=stimuli,
        agent_runner=spy, home_rooms={"u": Room("signal", "home")},
        read_tools_for=lambda unit, rooms: [], skill_text_for=lambda kinds: "",
        grounder=EmptyGrounder(),
        doctrine_text="D", doctrine_sha256=None, open_note_titles=_const(()),
        open_events=_const(()), prior_standing=_const(None),
        budget=AdaptorBudget(turns=1, total_tokens=1, output_tokens=1, timeout_seconds=5),
        cooldown_seconds=300, units=("u",), session_timeout_seconds=900.0,
        max_items=12, max_strikes=3,
    )
    pending = (Stimulus(
        id=1, unit="u", kind="message", item="message", ref="messages:1", at_ms=NOW,
        recorded_at_ms=NOW, platform="signal", conversation_id="home",
    ),)
    view = UnitView(unit="u", pending=pending, last_activity_ms=None, last_wake=None)
    await asyncio.wait_for(runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=1), 5)
    return {t.tool_name: t.tool_spec for t in as_strands_tools(spy.tools)}


def test_the_session_offers_exactly_the_verbs_this_table_names(specs):
    assert set(specs) == set(EXPECTED)


@pytest.mark.parametrize("verb", sorted(EXPECTED))
def test_every_verb_has_named_typed_parameters_and_no_kwargs(specs, verb):
    spec = specs[verb]
    schema = spec["inputSchema"]["json"]
    props = schema["properties"]
    want, required = EXPECTED[verb]

    assert "kwargs" not in props, "a **kwargs closure reaches the model as one untyped param"
    assert set(props) == set(want)
    assert set(schema.get("required", ())) == required
    for name, (json_type, extra) in want.items():
        got = props[name]
        assert got["type"] == json_type, (verb, name)
        assert got.get("description"), f"{verb}.{name} has no description"
        if json_type == "array":
            assert got["items"] == extra, f"{verb}.{name} is an array of nothing"
        elif extra is not None:
            assert got["enum"] == extra


@pytest.mark.parametrize("verb", sorted(EXPECTED))
def test_every_verb_carries_a_docstring_that_says_what_it_returns(specs, verb):
    description = specs[verb]["description"]
    assert len(description) > 40
    assert "status" in description
    assert not any(ch in description for ch in "—–"), "no dash (ADR 0015)"


@pytest.mark.parametrize("verb", sorted(EXPECTED))
def test_every_verb_says_it_can_come_back_unavailable(specs, verb):
    """Every verb can answer `unavailable` (a store or a read failed: the wrapper turns
    any failure into it), so its description must say `status` can be that, in the
    live verbs' words; a description that says only «ok or refused» is false."""
    description = specs[verb]["description"]
    assert "unavailable" in description
    assert "refused or unavailable" in " ".join(description.split())
