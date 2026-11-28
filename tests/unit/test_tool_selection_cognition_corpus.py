"""`evals/corpora/tool_selection_cognition.yaml` — the Cognition catalogue's
S2 corpus. COLLECTION only: this test loads
and censuses the corpus (`evals.scenarios.load_corpus`/`census`) the way
`python -m evals` does before any run — it makes no model call and drives no
case. Every case is parked: the legacy Cognition session it ran on was removed.
"""

from __future__ import annotations

from evals.scenarios import CORPUS_ENTRY, ROUTED_CORPORA, census, load_all
from nora.agent.catalog.entries import NATIVE_TOOLS

NAME = "tool_selection_cognition"

def test_the_corpus_is_registered_and_routed():
    assert CORPUS_ENTRY[NAME] == "dreamer"
    assert NAME in ROUTED_CORPORA


def test_every_case_is_parked_with_its_id_kept():
    cases = load_all(NAME)
    assert len(cases) == 23
    for case in cases:
        assert case.metadata["parked"]["reason"] == "P-A", case.name
    result = census(NAME)
    assert (result.total, result.active, result.parked) == (23, 0, 23)


def test_no_real_name_or_quoted_message_appears_in_any_case_input():
    """The safety rule ("never name room members or quote
    messages"), checked against this corpus's own content: `input` is
    always an agenda item's FACT sentence, never a transcript line. Of the
    names this repo's own fixtures use for a real member (Miles, Ada), only
    `Miles`, room A's pseudonym (`NORA_EVAL_PSEUDONYM_MAP` resolves it), and
    only in a `social_read` case: that tool takes a name and resolves it among
    the people the room lists, and the live agenda line it answers names the
    person («Miles posted 2 story frames»)."""
    cases = load_all(NAME)
    for case in cases:
        allowed = {"Miles"} if "social_read" in case.metadata.get("tools", ()) else set()
        for name in ("Miles", "Ada"):
            if name not in allowed:
                assert name not in case.input, (case.name, name)
    named = [c.name for c in cases if "Miles" in c.input]
    assert len(named) == 3, named


def test_parked_legacy_reads_keep_their_case_ids_without_expectations():
    cases = {case.name: case for case in load_all(NAME)}
    for name in (
        "cognition_standing_read_before_standing_revise",
        "cognition_standing_read_vs_note_read_where_the_room_stands",
        "cognition_memo_read_vs_note_read_before_revising",
    ):
        metadata = cases[name].metadata
        assert metadata["parked"]["why"]
        assert not {"tools", "not_tools", "before"} & metadata.keys()


def test_active_tool_selection_routing_uses_registered_tools():
    active = [case for case in load_all("tool_selection") if not case.metadata.get("parked")]
    assert active
    for case in active:
        metadata = case.metadata
        tools = set(metadata.get("tools", ())) | set(metadata.get("not_tools", ()))
        tools.update(metadata.get("required_tools", ()))
        for field in ("before", "args_include", "args_exclude", "args_date"):
            tools.update(metadata.get(field, {}))
        for successors in metadata.get("before", {}).values():
            tools.update(successors)
        assert tools <= NATIVE_TOOLS.keys(), (case.name, tools - NATIVE_TOOLS.keys())


def test_retained_read_cases_name_the_confusable_sibling():
    metadata = [case.metadata for case in load_all(NAME)]
    for tool, siblings in (
        ("rollup_read", {"record_query", "where_been"}),
        ("social_read", {"record_query", "rollup_read"}),
    ):
        negatives = {
            negative for row in metadata if tool in row.get("tools", ())
            for negative in row.get("not_tools", ())
        }
        assert siblings <= negatives
