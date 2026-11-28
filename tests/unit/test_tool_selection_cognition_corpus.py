"""`evals/corpora/tool_selection_cognition.yaml` — the Cognition catalogue's
S2 corpus. COLLECTION only: this test loads
and censuses the corpus (`evals.scenarios.load_corpus`/`census`) the way
`python -m evals` does before any run — it makes no model call and drives no
case. Every case is parked: the legacy Cognition session it ran on was removed.
"""

from __future__ import annotations

from evals.scenarios import CORPUS_ENTRY, ROUTED_CORPORA, census, load_all

NAME = "tool_selection_cognition"
NOW = 1_800_000_000_000

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
        allowed = {"Miles"} if "social_read" in case.metadata["tools"] else set()
        for name in ("Miles", "Ada"):
            if name not in allowed:
                assert name not in case.input, (case.name, name)
    named = [c.name for c in cases if "Miles" in c.input]
    assert len(named) == 3, named








def test_each_new_read_is_measured_against_the_sibling_it_is_confusable_with():
    """`rollup_read`, `standing_read` and `social_read` each have routing
    cases against their confusable siblings: `rollup_read` against
    `record_query` and `where_been`, `standing_read` against
    `note_read`/`note_search`, `social_read` against `record_query` and
    `rollup_read`, and the order a revision needs (read before
    `standing_revise`). `character_read` and `event_search` are not
    `social_read`'s siblings: a Cognition session reads them to orient
    itself, so a case forbidding them fails on a right session."""
    by_name = {c.name: c.metadata for c in load_all(NAME)}
    against = {
        tool: {n for m in by_name.values() if tool in m["tools"] for n in m.get("not_tools", [])}
        for tool in ("rollup_read", "standing_read", "social_read")
    }
    assert {"record_query", "where_been"} <= against["rollup_read"]
    assert {"note_read", "note_search"} <= against["standing_read"]
    assert {"record_query", "rollup_read"} <= against["social_read"]
    ordered = by_name["cognition_standing_read_before_standing_revise"]
    assert ordered["before"] == {"standing_read": ["standing_revise"]}
