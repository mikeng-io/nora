"""Fact judges see cited rows; context-only facts never enter the misses."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nora.agent.replay_inspection import compare_inspection
from scripts.memory_graph_spike import smoke_fact_support


@pytest.mark.parametrize("malformed", [False, True])
async def test_scoring_citation_scope_new_rows_and_reasons(malformed):
    calls = []

    async def call(model, instruction, payload):
        calls.append(payload)
        if "statement" in payload:
            assert payload["cited_rows"] == {"1": {"text": "Generated train arrives"}}
            assert "uncited secret detail" not in json.dumps(payload["cited_rows"])
            return json.dumps({"verdict": "unsupported", **(
                {} if malformed else {"reason": "detail absent from cited row"})})
        assert "passing moments" in instruction
        assert "no durability" in instruction.lower()
        return json.dumps({"missed": [{"fact": "moment", "quote": "train arrives",
                                       "location": "1", "reason": "new source"}],
                           "context_only": [{"fact": "older", "quote": "old context",
                                             "location": "9", "reason": "older row"}]})

    judges = SimpleNamespace(models=["generated-judge"], call=call)
    inspection = {"new_sources": {"1": {"text": "Generated train arrives"}}}
    exchanges = [{"name": "memory_propose", "input": {"statements": [
        {"what": "uncited secret detail", "cite": [{"source": 1}]}]},
        "source_rows": {"1": {"text": "Generated train arrives"},
                        "2": {"text": "uncited secret detail"}},
        "result": {"statements": [{"index": 0, "status": "saved"}]}}]
    result = await smoke_fact_support(judges, inspection, exchanges)
    assert result["facts_judge_errors"] == int(malformed)
    assert result["facts_unsupported"] == int(not malformed)
    assert result["facts_missed"] == 1
    assert result["facts_context_only"] == 1
    assert len(calls) == 2


def test_inspection_compares_only_normalized_system_and_agenda():
    inspection = {"system": "Generated — system", "agenda": "Generated — agenda"}
    request = {"messages": [{"role": "system", "content": "Generated, system"},
                            {"role": "user", "content": [{"type": "text",
                                                          "text": "Generated, agenda"}]}],
               "tools": [{"unrelated": "tool schema"}], "temperature": 0.5}
    assert compare_inspection(inspection, request)
    request["messages"][0]["content"] = "Wrong system"
    assert not compare_inspection(inspection, request)


def test_nested_entity_attributes_keep_their_own_cited_rows():
    from scripts.memory_graph_spike import cited_source_numbers
    assert cited_source_numbers({"cite": [{"source": 1}], "subject": {"new": {
        "attributes": {"name": {"value": "generated", "cite": [{"source": 2}]}}}}}) == {"1", "2"}
