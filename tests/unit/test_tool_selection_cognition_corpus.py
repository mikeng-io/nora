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
from nora.app.cognition_session import SESSION_VERB_NAMES
from nora.app.cognition_wiring import COGNITION_READ_CATALOGUE
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NAME = "tool_selection_cognition"
NOW = 1_800_000_000_000

#: The vocabulary a `metadata.tools`/`not_tools` entry may name — THE SAME
#: catalogue the occasion itself is built from: the session's own verbs
#: (`SESSION_VERB_NAMES`, which `CognitionSessionRunner` constructs from its
#: real verb tools) and the read surface the catalogue declares
#: (`COGNITION_READ_CATALOGUE`). Not a third authored list: a corpus case can
#: name only what the probed session could actually be handed, so a read
#: added to the catalogue is measurable without this file ever being edited,
#: and one that leaves it stops being nameable here.
_COGNITION_CATALOGUE = SESSION_VERB_NAMES | COGNITION_READ_CATALOGUE


def test_the_corpus_is_registered_and_routed():
    assert CORPUS_ENTRY[NAME] == "dreamer"
    assert NAME in ROUTED_CORPORA


def test_the_runner_collects_every_case_with_no_error():
    cases = load_corpus(NAME)
    assert len(cases) == 26
    for case in cases:
        assert case.metadata["snapshot"], case.name
        assert not case.metadata["parked"], case.name


def test_census_reports_every_case_active_and_none_parked():
    result = census(NAME)
    assert result.total == 26
    assert result.active == 26
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


async def test_every_verb_the_corpus_names_is_a_tool_the_probe_offers():
    """The corpus measures choices among tools — so every VERB it names must be
    one the probed session's real assembly hands the model. `probe` runs
    `_session_input` (the wake's own assembly: production objective, skills and
    verb tools), and this binds the corpus's verb vocabulary to the tools that
    assembly actually offers, by name. A verb the session stopped offering
    would make its cases measure a call the model could never make. The read
    half binds differently — the eval subject's composition builds it, not
    this runner — so reads are bound through `COGNITION_READ_CATALOGUE`
    itself, which the wiring's own tests bind to the registry surface."""
    from tests.unit.test_cognition_agenda_skills import _Spy, build_runner

    spy = _Spy()
    runner = build_runner(InMemoryCognitionStore(), spy, InMemoryStimulusStore())
    await runner.probe(unit="u", summary="room A has 2 new messages", now_ms=NOW)
    offered = {
        getattr(t, "tool_name", None) or getattr(t, "__name__", None)
        for t in spy.tools
    }
    named_verbs = {
        t for case in load_corpus(NAME)
        for t in (set(case.metadata["tools"]) | set(case.metadata.get("not_tools", [])))
        if t in SESSION_VERB_NAMES
    }
    assert named_verbs <= offered, (named_verbs - offered)


async def test_probe_and_wake_reuse_the_same_descriptions_and_schemas():
    from nora.agent.ports import adapt
    from nora.agent.registry import ToolRegistry
    from nora.app.cognition_agent_runner import as_strands_tools
    from tests.unit.test_cognition_agenda_skills import _Spy, build_runner, one_message_view

    native = {
        spec.name: adapt(spec)
        for spec in ToolRegistry()._introspection_specs()
        if spec.name in COGNITION_READ_CATALOGUE
    }
    assert {"load_transcript", "web_search", "web_fetch", "media_fetch"} <= native.keys()

    def reads(unit, rooms, **kwargs):
        return list(native.values())

    spy = _Spy()
    runner = build_runner(
        InMemoryCognitionStore(), spy, InMemoryStimulusStore(),
    ).with_collaborators(read_tools_for=reads, read_only_tools=frozenset(native))

    def offered():
        return {tool.tool_name: tool.tool_spec for tool in as_strands_tools(spy.tools)}

    await runner.probe(unit="u", summary="1 new message in this room", now_ms=NOW)
    probe_specs = offered()
    for name, tool in native.items():
        assert probe_specs[name] == tool.tool_spec, name
    await runner.replay(view=one_message_view(), now_ms=NOW)
    assert offered() == probe_specs


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
