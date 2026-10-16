"""`evals.scenarios` — the corpus loader under ONE PATH.

Pure, no DB/network, and it never reads the real `evals/corpora/`: every
corpus here is written into `tmp_path`, because what is under test is the
LOADER's contract — which keys a case may state, how the three states
(active / unanchored / parked) are told apart and counted — not the content
of any real corpus. The real files' own invariant (every case anchored or
parked) is `tests/architecture/`'s job.
"""

from __future__ import annotations

import pytest
from strands_evals import Case, Experiment
from strands_evals.types.evaluation import EnvironmentState

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ADMITTED,
    NORA_ERROR,
    NORA_SAID,
    PASS,
    SNAPSHOT_KEYS,
    MeetsGrader,
)
from evals.scenarios import (
    CASE_KEYS,
    CORPUS_ENTRY,
    JUDGED_CORPORA,
    PARKED_KEYS,
    census,
    experiment_for,
    load_all,
    load_corpus,
)

_ANCHORED = """
- name: from_a_real_moment
  entry: tag
  expect: reply
  input: "{trigger} hi"
  mention: true
  person: tyler_cortez
  sample: 3
  safety: true
  category: world_fact
  expected_output: she answers
  metadata:
    prefill_sufficient: true
  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
"""

_PARKED = """
- name: no_such_moment
  entry: gate
  expect: no_go
  parked:
    reason: P-A
    why: no real room has produced this shape
    returns_when: a room produces it
"""

_UNANCHORED = """
- name: awaiting_the_seam
  entry: tag
  expect: reply
  input: what did the search say
  metadata:
    tool_result: the search said X
"""


def _corpus(tmp_path, monkeypatch, body: str, name: str = "probe") -> str:
    (tmp_path / f"{name}.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setattr("evals.scenarios._CORPORA", tmp_path)
    return name


# --- what a case carries -----------------------------------------------------


def test_an_anchored_case_carries_its_anchor_and_every_stated_field(tmp_path, monkeypatch):
    [case] = load_corpus(_corpus(tmp_path, monkeypatch, _ANCHORED))
    assert case.name == "from_a_real_moment"
    assert case.input == "{trigger} hi", "rendered at DRIVE time against the subject's trigger"
    assert case.expected_output == "she answers"
    md = case.metadata
    assert md["snapshot"] == {
        "platform": "signal", "conversation_id": "the-real-group", "before_ms": 1752000000000,
    }
    assert md["expect"] == "reply"
    assert md["person"] == "tyler_cortez"
    assert md["mention"] is True
    assert md["category"] == "world_fact"
    assert md["parked"] == {}
    # The case's own `metadata:` travels beside the reserved keys and never
    # shadows them.
    assert md["prefill_sufficient"] is True


def test_the_speaker_defaults_to_the_operator_and_no_mention(tmp_path, monkeypatch):
    [case] = load_corpus(_corpus(tmp_path, monkeypatch, """
- name: plain
  entry: participation
  expect: silence
  input: hi
  snapshot: {platform: signal, conversation_id: r, before_ms: 1}
"""))
    assert case.metadata["person"] == "miles_novak"
    assert case.metadata["mention"] is False


def test_a_bare_list_and_a_cases_mapping_are_the_same_corpus(tmp_path, monkeypatch):
    bare = _corpus(tmp_path, monkeypatch, _ANCHORED, "bare")
    wrapped = _corpus(tmp_path, monkeypatch, "cases:\n" + "\n".join(
        "  " + line if line else line for line in _ANCHORED.splitlines()
    ), "wrapped")
    assert [c.name for c in load_all(bare)] == [c.name for c in load_all(wrapped)]


# --- the three states --------------------------------------------------------


def test_a_parked_case_is_counted_and_never_active(tmp_path, monkeypatch):
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + _PARKED + _UNANCHORED)
    assert [c.name for c in load_all(name)] == [
        "from_a_real_moment", "no_such_moment", "awaiting_the_seam",
    ]
    assert [c.name for c in load_corpus(name)] == ["from_a_real_moment", "awaiting_the_seam"]
    parked = next(c for c in load_all(name) if c.name == "no_such_moment")
    assert parked.metadata["parked"]["reason"] == "P-A"


def test_an_unanchored_case_is_active_so_that_it_errors_rather_than_skips(tmp_path, monkeypatch):
    """§7.1: a case quietly skipped for lack of an anchor is the same false
    reading as one assembled without a tier. It stays in the active set —
    `_drive` refuses it by name — and is counted."""
    name = _corpus(tmp_path, monkeypatch, _UNANCHORED)
    [case] = load_corpus(name)
    assert case.metadata["snapshot"] == {}
    assert case.metadata["parked"] == {}
    assert case.metadata["tool_result"] == "the search said X"


def test_census_reports_every_state_beside_the_active_count(tmp_path, monkeypatch):
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + _PARKED + _UNANCHORED + _PARKED.replace(
        "no_such_moment", "another_parked",
    ))
    c = census(name)
    assert (c.total, c.active, c.parked, c.unanchored) == (4, 2, 2, 1)
    assert str(c) == "4 total, 2 active, 2 parked, 1 unanchored"


# --- refusals, each by name ---------------------------------------------------


@pytest.mark.parametrize("key", ["knows", "config", "policy", "room", "now", "transcript_limit"])
def test_a_case_stating_what_is_no_longer_a_cases_to_state_is_refused(tmp_path, monkeypatch, key):
    """Every one of these was a case compensating by hand for a subject that
    was not the deployment's. Under one path they have nothing to say, and a
    key the runner ignores is a corpus asserting more than is measured."""
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + f"  {key}: something\n")
    with pytest.raises(ValueError) as info:
        load_all(name)
    assert key in str(info.value)
    assert all(k in str(info.value) for k in CASE_KEYS)


@pytest.mark.parametrize("top", ["config", "policy"])
def test_a_corpus_level_setting_is_refused(tmp_path, monkeypatch, top):
    name = _corpus(tmp_path, monkeypatch, f"{top}:\n  x: y\ncases:\n" + "\n".join(
        "  " + line if line else line for line in _ANCHORED.splitlines()
    ))
    with pytest.raises(ValueError, match=top):
        load_all(name)


@pytest.mark.parametrize("missing", sorted(SNAPSHOT_KEYS))
def test_a_snapshot_missing_a_key_is_refused_by_name(tmp_path, monkeypatch, missing):
    lines = _ANCHORED.splitlines()
    body = "\n".join(line for line in lines if not line.strip().startswith(missing))
    name = _corpus(tmp_path, monkeypatch, body)
    with pytest.raises(ValueError) as info:
        load_all(name)
    assert missing in str(info.value)


@pytest.mark.parametrize("extra", ["culture_key", "unit", "transcript_limit"])
def test_a_snapshot_stating_a_derived_or_reduced_key_is_refused(tmp_path, monkeypatch, extra):
    """The culture key and unit are the deployment's policy entry's; a window
    is production's config's (R7). A case cannot state either."""
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + f"    {extra}: x\n")
    with pytest.raises(ValueError) as info:
        load_all(name)
    assert extra in str(info.value)


def test_a_parked_case_that_is_also_anchored_is_refused(tmp_path, monkeypatch):
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + """  parked:
    reason: P-A
    why: w
    returns_when: r
""")
    with pytest.raises(ValueError, match="parked AND anchored"):
        load_all(name)


@pytest.mark.parametrize("reason", ["P-B", "P-C", ""])
def test_only_p_a_is_a_parking_reason(tmp_path, monkeypatch, reason):
    """P-B — a seam this rebuild owes — was proposed as a parking reason and
    rejected: such a case is unfinished, not parked, and stays unanchored."""
    name = _corpus(tmp_path, monkeypatch, _PARKED.replace("P-A", reason))
    with pytest.raises(ValueError, match="P-B"):
        load_all(name)


def test_a_parked_block_must_state_exactly_the_three_fields(tmp_path, monkeypatch):
    body = _PARKED.replace("  returns_when: a room produces it\n", "")
    name = _corpus(tmp_path, monkeypatch, body)
    with pytest.raises(ValueError) as info:
        load_all(name)
    assert all(k in str(info.value) for k in PARKED_KEYS)


@pytest.mark.parametrize("key", ["name", "entry", "expect"])
def test_a_case_missing_its_identity_is_refused(tmp_path, monkeypatch, key):
    body = _ANCHORED.replace(
        f"- {key}:" if key == "name" else f"  {key}:",
        "- input_only:" if key == "name" else "  dropped:",
    )
    name = _corpus(tmp_path, monkeypatch, body)
    with pytest.raises(ValueError, match=key):
        load_all(name)


# --- experiment_for -----------------------------------------------------------


def test_every_corpus_has_a_declared_entry_point_and_judged_is_a_subset():
    assert set(CORPUS_ENTRY) >= JUDGED_CORPORA
    entries = {"tag", "participation", "gate", "dreamer", "proactive_act", "standing"}
    assert set(CORPUS_ENTRY.values()) <= entries


def test_experiment_for_returns_the_active_set(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, _ANCHORED + _PARKED + _UNANCHORED, "no_trigger")
    experiment, _task = experiment_for("no_trigger", source_url="x")
    assert [c.name for c in experiment.cases] == ["from_a_real_moment", "awaiting_the_seam"]


def test_experiment_for_threads_a_judge_only_for_judged_corpora(tmp_path, monkeypatch):
    """Passing a judge for a corpus that never sets `expect: reply` would
    judge cases the corpus never asked to be judged; omitting it for a
    JUDGED corpus silently drops S3/S9 to a structural-only reading."""
    _corpus(tmp_path, monkeypatch, _ANCHORED, "being_contradicted")
    _corpus(tmp_path, monkeypatch, _ANCHORED, "no_trigger")
    captured = []

    def fake_nora_task(spec):
        captured.append(spec)
        return lambda _case: {}

    monkeypatch.setattr("evals.scenarios.nora_task", fake_nora_task)
    fake_judge = object()

    experiment_for("being_contradicted", source_url="x", judge=fake_judge)
    assert captured[-1].judge is fake_judge
    assert captured[-1].source_url == "x"
    assert captured[-1].entry == CORPUS_ENTRY["being_contradicted"]

    experiment_for("no_trigger", source_url="x", judge=fake_judge)
    assert captured[-1].judge is None


def test_experiment_for_sample_draws_each_case_n_times(tmp_path, monkeypatch):
    """`--sample N`: a cheap smoke run at 1 must stay possible, and asking
    for more must multiply the case COUNT, not just re-read the same one
    (`09-the-backbone-measured.md` §3.1 — one draw is not a reading)."""
    _corpus(tmp_path, monkeypatch, _ANCHORED + _UNANCHORED, "near_miss")
    experiment, _task = experiment_for("near_miss", source_url="x", sample=3)
    assert len(experiment.cases) == 2 * 3
    names = {c.name for c in experiment.cases}
    assert {"from_a_real_moment#1", "from_a_real_moment#3"} <= names

    experiment, _task = experiment_for("near_miss", source_url="x", sample=1)
    assert [c.name for c in experiment.cases] == ["from_a_real_moment", "awaiting_the_seam"]


def test_a_corpus_is_gradable_end_to_end_through_a_real_experiment(tmp_path, monkeypatch):
    """Mechanical wiring, no DB/network: a fake task answering exactly what
    each case expects must score every one PASS through the SAME
    `Experiment`/`MeetsGrader` path a live run would use — and its opposite
    must score every one FAIL, never a silent PASS or an ERROR standing in."""
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + """
- name: quiet
  entry: tag
  expect: silence
  input: nothing here
  snapshot: {platform: signal, conversation_id: r, before_ms: 1}
""")
    cases = load_corpus(name)

    def task(right: bool):
        def _task(case: Case) -> dict:
            wants_silence = case.metadata["expect"] in ("no_reply", "silence")
            said = (0 if wants_silence else 1) if right else (1 if wants_silence else 0)
            return {
                "output": "",
                "environment_state": [
                    EnvironmentState(name=NORA_ERROR, state=""),
                    EnvironmentState(name=NORA_SAID, state=said),
                    EnvironmentState(name=NORA_ADMITTED, state=None),
                ],
            }
        return _task

    for right, want in ((True, PASS), (False, FAIL)):
        report = Experiment(cases=cases, evaluators=[MeetsGrader()]).run_evaluations(
            task=task(right),
        )
        labels = {out.label for outputs in report.detailed_results for out in outputs}
        assert labels == {want}
        assert ERROR not in labels


def _rows(*names: str) -> str:
    """A corpus of N minimal anchored cases, one per name."""
    return "".join(
        f"""
- name: {n}
  entry: tag
  expect: reply
  input: hi
  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
"""
        for n in names
    )


# --- `only` — re-measuring the cases that moved, not the corpus ------------


def test_only_selects_the_named_cases(tmp_path, monkeypatch):
    """A re-measurement asks about the cases that moved. Paying for a whole
    corpus to re-draw four of them is how a sample count gets chosen by
    budget instead of by the question."""
    _corpus(tmp_path, monkeypatch, _rows("a", "b", "c"), "no_trigger")
    experiment, _task = experiment_for(
        "no_trigger", source_url="x", only=frozenset({"a", "c"}),
    )
    assert sorted(c.name for c in experiment.cases) == ["a", "c"]


def test_only_multiplies_with_sample(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, _rows("a", "b"), "no_trigger")
    experiment, _task = experiment_for(
        "no_trigger", source_url="x", only=frozenset({"a"}), sample=3,
    )
    assert sorted(c.name for c in experiment.cases) == ["a#1", "a#2", "a#3"]


def test_only_refuses_a_name_that_matches_nothing(tmp_path, monkeypatch):
    """A typo that silently selected zero cases would report a clean corpus,
    which is the vacuous pass this whole layer exists to refuse."""
    _corpus(tmp_path, monkeypatch, _rows("a"), "no_trigger")
    with pytest.raises(ValueError, match="no active case named"):
        experiment_for("no_trigger", source_url="x", only=frozenset({"typo"}))


# --- the counterfactual state, and the guards that keep it narrow ----------

_COUNTERFACTUAL = """
- name: w1-chronicle
  entry: participation
  expect: speak
  input: hi
  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
  counterfactual:
    varies: culture
    why: |
      A chronicle and an account are the same facts in two representations,
      and no room ever held both at once.
"""


def test_a_counterfactual_case_is_active_and_counted_separately(tmp_path, monkeypatch):
    """The count is what makes the state safe to have. A counterfactual is a
    MANIPULATION, not a reconstruction, and an aggregate that folded the two
    together would let the rule erode by arithmetic rather than by anybody
    deciding to relax it."""
    name = _corpus(tmp_path, monkeypatch, _ANCHORED + _COUNTERFACTUAL, "participation")
    c = census(name)
    assert (c.total, c.active, c.parked, c.unanchored) == (2, 2, 0, 0)
    assert c.counterfactual == 1
    assert "1 COUNTERFACTUAL" in str(c)
    # It RUNS — it is a real moment with one tier varied, not a parked case.
    assert "w1-chronicle" in {case.name for case in load_corpus(name)}


def test_a_corpus_with_no_counterfactual_says_nothing_about_the_state(
    tmp_path, monkeypatch,
):
    """A column implying the state is ordinary is how it becomes ordinary."""
    assert "COUNTERFACTUAL" not in str(
        census(_corpus(tmp_path, monkeypatch, _ANCHORED, "participation"))
    )


def test_a_counterfactual_without_an_anchor_is_refused(tmp_path, monkeypatch):
    """Without the anchor it is authored content, which is the one thing this
    corpus has no path for."""
    body = _COUNTERFACTUAL.replace(
        """  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
""", "")
    with pytest.raises(ValueError, match="names no `snapshot:`"):
        load_all(_corpus(tmp_path, monkeypatch, body, "participation"))


def test_a_counterfactual_varying_an_unlisted_tier_is_refused(tmp_path, monkeypatch):
    """The set is CLOSED on purpose — a general override is what this state
    exists INSTEAD of, so widening it has to be a decision in this file."""
    body = _COUNTERFACTUAL.replace("varies: culture", "varies: transcript")
    # `prior` joined 2026-09-24 (issue #445): the standing fold's
    # position-before-the-turn, declared absent for a pre-chain turn, and
    # valid in the standing corpus only (`evals.scenarios._TIER_CORPORA`).
    with pytest.raises(
        ValueError, match="only \\['culture', 'lessons', 'prior', 'transport'\\] may be varied",
    ):
        load_all(_corpus(tmp_path, monkeypatch, body, "participation"))


def test_a_counterfactual_must_say_why_it_cannot_be_reconstructed(
    tmp_path, monkeypatch,
):
    body = _COUNTERFACTUAL.replace(
        """  counterfactual:
    varies: culture
    why: |
      A chronicle and an account are the same facts in two representations,
      and no room ever held both at once.
""", "  counterfactual:\n    varies: culture\n")
    with pytest.raises(ValueError, match="must state exactly"):
        load_all(_corpus(tmp_path, monkeypatch, body, "participation"))


def test_a_parked_case_cannot_also_be_counterfactual(tmp_path, monkeypatch):
    """A parked case is not run, so varying a tier in it asserts nothing."""
    body = _COUNTERFACTUAL.replace(
        """  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
""",
        """  parked:
    reason: P-A
    why: no real room produced it
    returns_when: a room produces it
""")
    with pytest.raises(ValueError, match=r"parked AND counterfactual|names no `snapshot:`"):
        load_all(_corpus(tmp_path, monkeypatch, body, "participation"))


# --- the transport tier: held for every case, varied only as a counterfactual


_TRANSPORT_VARIED = _COUNTERFACTUAL.replace(
    "varies: culture", "varies: transport",
) + """  metadata:
    transport:
      discord: needs_a_person
"""


def test_a_transport_counterfactual_states_its_varied_networks(tmp_path, monkeypatch):
    """design/self-awareness/05's degraded-Discord case: health at an instant
    is recorded nowhere, so «Discord is down» is a varied state — loaded,
    active, and counted apart."""
    name = _corpus(tmp_path, monkeypatch, _TRANSPORT_VARIED, "participation")
    [case] = load_corpus(name)
    assert case.metadata["transport"] == {"discord": "needs_a_person"}
    assert census(name).counterfactual == 1


def test_a_varied_transport_outside_a_counterfactual_is_refused(tmp_path, monkeypatch):
    """Otherwise `metadata.transport` is a general override on an ANCHORED
    case — a manipulation the census would count as a reconstruction."""
    body = _ANCHORED.replace(
        "    prefill_sufficient: true\n",
        "    prefill_sufficient: true\n    transport:\n      discord: needs_a_person\n",
    )
    with pytest.raises(ValueError, match="without being a counterfactual"):
        load_all(_corpus(tmp_path, monkeypatch, body, "participation"))


def test_a_varied_transport_names_a_real_state(tmp_path, monkeypatch):
    """A typo'd state would render nothing the case meant and read as a clean
    measurement of it."""
    body = _TRANSPORT_VARIED.replace("needs_a_person", "broken")
    with pytest.raises(ValueError, match="must map networks to one of"):
        load_all(_corpus(tmp_path, monkeypatch, body, "participation"))


# --- the lessons tier (design/self-growth/40 §8): attached at the moment that taught it


_LESSONS_VARIED = _COUNTERFACTUAL.replace(
    "varies: culture", "varies: lessons",
) + """  metadata:
    lessons:
      - name one place and why, not a list of five
"""


def test_a_lessons_counterfactual_states_its_lessons_in_its_own_corpus(tmp_path, monkeypatch):
    name = _corpus(tmp_path, monkeypatch, _LESSONS_VARIED, "lessons")
    [case] = load_corpus(name)
    assert case.metadata["lessons"] == ["name one place and why, not a list of five"]
    assert census(name).counterfactual == 1


def test_a_lessons_counterfactual_is_refused_outside_the_lessons_corpus(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="which only \\['lessons'\\] may vary"):
        load_all(_corpus(tmp_path, monkeypatch, _LESSONS_VARIED, "participation"))


def test_lessons_outside_a_counterfactual_or_empty_are_refused(tmp_path, monkeypatch):
    stated = _ANCHORED.replace(
        "    prefill_sufficient: true\n",
        "    prefill_sufficient: true\n    lessons:\n      - say less\n",
    )
    with pytest.raises(ValueError, match="without being a counterfactual that varies"):
        load_all(_corpus(tmp_path, monkeypatch, stated, "lessons"))
    empty = _LESSONS_VARIED.replace("      - name one place and why, not a list of five\n",
                                    "      - '  '\n")
    with pytest.raises(ValueError, match="non-empty list of lesson texts"):
        load_all(_corpus(tmp_path, monkeypatch, empty, "lessons"))


def test_the_judge_is_handed_the_lesson_and_the_cases_behaviour():
    """Both arms are graded on the same facts: from the case, never the subject."""
    from types import SimpleNamespace

    from evals.backbone import _lesson_facts, varied_lessons

    md = {"criteria": ["lesson_applied"], "lessons": ["say less"],
          "counterfactual": {"varies": "lessons", "why": "x"}}
    facts = _lesson_facts(md, SimpleNamespace(expected_output="a short reply"))
    assert facts is not None and "- say less" in facts and "a short reply" in facts
    assert _lesson_facts({"criteria": []}, SimpleNamespace(expected_output="x")) is None
    assert varied_lessons({"lessons": ["say less"]}) == ()


def test_memory_index_composes_the_graph_surface_and_only_when_asked(monkeypatch):
    """`python -m evals --memory-index` (DEC-316): the after arm of a memory
    routing run composes the subject with `[memory_index] enabled = true`
    through `Spec.overrides`, the subject-override path the full restore uses;
    without the flag nothing is overridden, so every other run is unchanged."""
    from evals import scenarios

    captured: list = []
    monkeypatch.setattr(scenarios, "nora_task", lambda spec: captured.append(spec))
    scenarios.experiment_for("tool_selection", source_url="x", memory_index=True)
    scenarios.experiment_for("tool_selection", source_url="x")
    assert captured[0].overrides == {"memory_index.enabled": True}
    assert captured[1].overrides is None


def test_each_arm_runs_only_the_cases_its_memory_surface_can_route():
    """A `memory_surface: graph` case runs only under `--memory-index`, a
    `honcho` one only without it, and a case stating neither on both. Naming
    a case of the other surface is refused, not skipped in silence."""
    from evals.scenarios import load_corpus

    cases = {c.name: c.metadata.get("memory_surface") for c in load_corpus("tool_selection")}
    graph = {n for n, s in cases.items() if s == "graph"}
    honcho = {n for n, s in cases.items() if s == "honcho"}
    assert graph and honcho, "positive control: the corpus marks both surfaces"
    on, _ = experiment_for("tool_selection", source_url="x", memory_index=True)
    off, _ = experiment_for("tool_selection", source_url="x")
    on_names = {c.name for c in on.cases}
    off_names = {c.name for c in off.cases}
    assert graph <= on_names and not honcho & on_names
    assert honcho <= off_names and not graph & off_names
    assert on_names - graph == off_names - honcho
    with pytest.raises(ValueError, match="pass --memory-index"):
        experiment_for("tool_selection", source_url="x", only=frozenset(graph))


def test_an_unknown_memory_surface_is_refused(tmp_path, monkeypatch):
    from evals import scenarios

    corpus = tmp_path / "tool_selection.yaml"
    corpus.write_text(
        "cases:\n"
        "  - name: c1\n    entry: tag\n    expect: reply\n    input: hi\n"
        "    metadata: {tools: [now], memory_surface: both}\n"
    )
    monkeypatch.setattr("evals.scenarios._CORPORA", tmp_path)
    with pytest.raises(ValueError, match="memory_surface"):
        scenarios.load_all("tool_selection")
