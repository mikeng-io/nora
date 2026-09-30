"""`evals/corpora/tool_selection_cognition.yaml` — the Cognition catalogue's
S2 corpus (migration steps 3a-ii and 3a-iii). COLLECTION only: this test loads
and censuses the corpus (`evals.scenarios.load_corpus`/`census`) the way
`python -m evals` does before any run — it makes no model call and drives no
case. Running it for real goes through `evals.rebuilt.entry._cognition_probe`
on the session runner the subject builds under `[cognition] session_mode =
"shadow"` (`tests/unit/test_cognition_wiring.py` pins that seam).
"""

from __future__ import annotations

from evals.scenarios import CORPUS_ENTRY, ROUTED_CORPORA, census, load_corpus

NAME = "tool_selection_cognition"

#: Every verb `30-cognition.md` §2's write/pace/act table names, plus the
#: two confusable reads this corpus's last case checks — the vocabulary a
#: `metadata.tools`/`not_tools` entry may name.
_COGNITION_CATALOGUE = frozenset({
    "note_create", "note_update", "event_propose", "event_revise",
    "event_set_status", "standing_revise", "wake_at", "wake_move", "wake_cancel",
    "remind", "remind_move", "remind_cancel", "close", "record_query", "rollup_read",
})


def test_the_corpus_is_registered_and_routed():
    assert CORPUS_ENTRY[NAME] == "dreamer"
    assert NAME in ROUTED_CORPORA


def test_the_runner_collects_every_case_with_no_error():
    cases = load_corpus(NAME)
    assert len(cases) == 13
    for case in cases:
        assert case.metadata["snapshot"], case.name
        assert not case.metadata["parked"], case.name


def test_census_reports_every_case_active_and_none_parked():
    result = census(NAME)
    assert result.total == 13
    assert result.active == 13
    assert result.parked == 0
    assert result.unanchored == 0


def test_no_real_name_or_quoted_message_appears_in_any_case_input():
    """The migration's own safety rule ("never name room members or quote
    messages"), checked against this corpus's own content: `input` is
    always an agenda item's FACT sentence, never a transcript line — none
    of the names this repo's own fixtures use for a real member (Miles,
    Ada) appear anywhere in a case's `input`."""
    cases = load_corpus(NAME)
    forbidden = ("Miles", "Ada")
    for case in cases:
        for name in forbidden:
            assert name not in case.input, (case.name, name)


def test_every_case_names_only_verbs_from_the_cognition_catalogue():
    cases = load_corpus(NAME)
    for case in cases:
        named = set(case.metadata["tools"]) | set(case.metadata.get("not_tools", []))
        assert named <= _COGNITION_CATALOGUE, (case.name, named - _COGNITION_CATALOGUE)
