"""`evals/corpora/tool_selection_cognition.yaml` — the Cognition catalogue's
S2 corpus. COLLECTION only: this test loads
and censuses the corpus (`evals.scenarios.load_corpus`/`census`) the way
`python -m evals` does before any run — it makes no model call and drives no
case. Running it for real goes through `evals.rebuilt.entry._cognition_probe`
on the session runner the subject builds under `[cognition] enabled =
true` (`tests/unit/test_cognition_wiring.py` pins that seam).
"""

from __future__ import annotations

from evals.scenarios import CORPUS_ENTRY, ROUTED_CORPORA, census, load_corpus

NAME = "tool_selection_cognition"

#: Every Cognition write/pace/act verb, plus the
#: two confusable reads this corpus's last case checks — the vocabulary a
#: `metadata.tools`/`not_tools` entry may name.
_COGNITION_CATALOGUE = frozenset({
    "note_create", "note_update", "event_propose", "event_revise",
    "event_set_status", "standing_revise", "wake_at", "wake_move", "wake_cancel",
    "remind", "remind_move", "remind_cancel", "close", "record_query", "rollup_read",
    # The rollup and standing reads and the siblings they are measured against.
    "standing_read", "where_been", "note_read", "note_search",
    # DEC-306: the memo verbs, the memo read, and the retire a settled note's
    # memo is followed by.
    "memo_write", "memo_revise", "memo_withdraw", "memo_read", "note_retire",
    # `social_read`, measured against `record_query` and `rollup_read` above.
    "social_read",
})


def test_the_corpus_is_registered_and_routed():
    assert CORPUS_ENTRY[NAME] == "dreamer"
    assert NAME in ROUTED_CORPORA


def test_the_runner_collects_every_case_with_no_error():
    cases = load_corpus(NAME)
    assert len(cases) == 27
    for case in cases:
        assert case.metadata["snapshot"], case.name
        assert not case.metadata["parked"], case.name


def test_census_reports_every_case_active_and_none_parked():
    result = census(NAME)
    assert result.total == 27
    assert result.active == 27
    assert result.parked == 0
    assert result.unanchored == 0


def test_no_real_name_or_quoted_message_appears_in_any_case_input():
    """The safety rule ("never name room members or quote
    messages"), checked against this corpus's own content: `input` is
    always an agenda item's FACT sentence, never a transcript line. Of the
    names this repo's own fixtures use for a real member (Miles, Ada), only
    `Miles`, room A's pseudonym (`NORA_EVAL_PSEUDONYM_MAP` resolves it), and
    only in a `social_read` case: that tool takes a name and resolves it among
    the people the room lists, and the live agenda line it answers names the
    person («Miles posted 2 story frames»)."""
    cases = load_corpus(NAME)
    for case in cases:
        allowed = {"Miles"} if "social_read" in case.metadata["tools"] else set()
        for name in ("Miles", "Ada"):
            if name not in allowed:
                assert name not in case.input, (case.name, name)
    named = [c.name for c in cases if "Miles" in c.input]
    assert len(named) == 3, named


def test_every_case_names_only_verbs_from_the_cognition_catalogue():
    cases = load_corpus(NAME)
    for case in cases:
        named = set(case.metadata["tools"]) | set(case.metadata.get("not_tools", []))
        assert named <= _COGNITION_CATALOGUE, (case.name, named - _COGNITION_CATALOGUE)


def test_each_new_read_is_measured_against_the_sibling_it_is_confusable_with():
    """`rollup_read`, `standing_read` and `social_read` each have routing
    cases against their confusable siblings: `rollup_read` against
    `record_query` and `where_been`, `standing_read` against
    `note_read`/`note_search`, `social_read` against `record_query` and
    `rollup_read`, and the order a revision needs (read before
    `standing_revise`). `character_read` and `event_search` are not
    `social_read`'s siblings: a Cognition session reads them to orient
    itself, so a case forbidding them fails on a right session."""
    by_name = {c.name: c.metadata for c in load_corpus(NAME)}
    against = {
        tool: {n for m in by_name.values() if tool in m["tools"] for n in m.get("not_tools", [])}
        for tool in ("rollup_read", "standing_read", "social_read")
    }
    assert {"record_query", "where_been"} <= against["rollup_read"]
    assert {"note_read", "note_search"} <= against["standing_read"]
    assert {"record_query", "rollup_read"} <= against["social_read"]
    ordered = by_name["cognition_standing_read_before_standing_revise"]
    assert ordered["before"] == {"standing_read": ["standing_revise"]}
