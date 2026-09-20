"""`evals.scenarios.load_corpus` — the corpus port
(`design/evaluation/10-the-scenario-map.md` §7, operator override
2026-09-20). Pure, no DB/network: reads the real YAML files under
`evals/corpora/` and checks the conversion, not the seeded content itself
(the seeded content is the OLD harness's own, already covered by
`tests/architecture/test_the_near_miss_corpora_exercise_their_rules.py` and
friends).
"""

from __future__ import annotations

import pytest
from strands_evals import Case, Experiment
from strands_evals.types.evaluation import EnvironmentState

from evals.backbone import ERROR, FAIL, NORA_ADMITTED, NORA_ERROR, NORA_SAID, PASS, MeetsGrader
from evals.scenarios import CORPUS_ENTRY, JUDGED_CORPORA, experiment_for, load_corpus

#: Every corpus file this port covers must load to exactly this many cases —
#: the known count (`10-the-scenario-map.md` §2's own table), so a corpus
#: silently losing or gaining a row is caught here rather than downstream.
_EXPECTED_COUNTS = {
    "no_trigger": 12,
    "near_miss": 4,
    "trigger": 12,
    "injections": 5,
    "transcript_injection": 1,
    "being_contradicted": 6,
    "identity_grounding": 8,
    "source_scope": 4,
    "participation": 15,
    "relevance_gate": 40,
    "note_candidates": 21,
    "proactive_decide": 11,
    "proactive_act": 11,
}


def test_every_corpus_this_port_covers_has_a_declared_entry_point():
    assert set(CORPUS_ENTRY) == set(_EXPECTED_COUNTS)


def test_loading_every_corpus_reproduces_the_known_150_case_count():
    total = 0
    for name, expected in _EXPECTED_COUNTS.items():
        cases = load_corpus(name)
        assert len(cases) == expected, f"{name}: expected {expected}, got {len(cases)}"
        total += len(cases)
    assert total == 150


def test_a_case_carries_expect_and_knows_in_metadata():
    cases = load_corpus("relevance_gate")
    first = cases[0]
    assert first.metadata["expect"] in ("go", "no_go")
    assert "transcript" in first.metadata["knows"]


def test_trigger_substitution_reaches_the_ported_input():
    """`no_trigger.yaml` states `engagement.trigger.text: nora.000` and its
    cases are literal near-misses OF that string — the same substitution
    `evals.rebuilt.runner.load()` applies must reach the ported input too."""
    cases = load_corpus("no_trigger")
    by_name = {c.name: c for c in cases}
    case = by_name["leading_word_char_glued"]
    assert "nora.000" in case.input
    assert "{trigger}" not in case.input


def test_a_proactive_decide_case_carries_its_own_room_and_now():
    """The reason `Spec`/`_drive()` gained per-case metadata reads
    (2026-09-20): `proactive_decide.yaml` names a real room and a real
    `now` PER CASE, which the port must not lose."""
    cases = load_corpus("proactive_decide")
    by_name = {c.name: c for c in cases}
    case = by_name["d01_dinner_note_the_day_before"]
    assert case.metadata["room"] == ["signal", "ridgeline-sig"]
    assert case.metadata["now"] == "2026-09-11T07:00:00+08:00"
    assert case.metadata["config"]["memory.workspace_id"] == "ridgeline"
    assert "home_room = true" in case.metadata["policy"]


def test_a_cases_own_metadata_never_shadows_a_reserved_key():
    """`trigger.yaml` cases carry their own `metadata: {prefill_sufficient:
    true}` — real content the port must keep — beside `expect`, which
    `MeetsGrader`/`AbsenceGrader` read. The two must not collide."""
    cases = load_corpus("trigger")
    case = next(c for c in cases if "prefill_sufficient" in c.metadata)
    assert case.metadata["prefill_sufficient"] is True
    assert case.metadata["expect"] == "reply"


def test_a_ported_corpus_is_gradable_end_to_end_through_a_real_experiment():
    """Mechanical wiring, no DB/network: a fake task answering exactly what
    each `no_trigger`/`near_miss` case expects must score every one PASS
    through the SAME `Experiment`/`MeetsGrader` path a live run would use."""
    cases = load_corpus("no_trigger") + load_corpus("near_miss")

    def fake_task(case: Case) -> dict:
        expect = case.metadata["expect"]
        said = 0 if expect in ("no_reply", "silence") else 1
        return {
            "output": "",
            "environment_state": [
                EnvironmentState(name=NORA_ERROR, state=""),
                EnvironmentState(name=NORA_SAID, state=said),
                EnvironmentState(name=NORA_ADMITTED, state=None),
            ],
        }

    report = Experiment(cases=cases, evaluators=[MeetsGrader()]).run_evaluations(
        task=fake_task,
    )
    labels = {out.label for outputs in report.detailed_results for out in outputs}
    assert labels == {PASS}


def test_judged_corpora_are_a_subset_of_the_covered_corpora():
    assert set(CORPUS_ENTRY) >= JUDGED_CORPORA


def test_experiment_for_returns_the_full_case_set():
    experiment, _task = experiment_for("no_trigger", database_url="x")
    assert len(experiment.cases) == 12


def test_experiment_for_threads_a_judge_only_for_judged_corpora(monkeypatch):
    """Passing a judge for a corpus that never sets `expect: reply` would
    judge cases the corpus never asked to be judged; omitting it for a
    JUDGED corpus silently drops S3/S9 to a structural-only reading."""
    captured = []

    def fake_nora_task(spec):
        captured.append(spec)
        return lambda _case: {}

    monkeypatch.setattr("evals.scenarios.nora_task", fake_nora_task)
    fake_judge = object()

    experiment_for("being_contradicted", database_url="x", judge=fake_judge)
    assert captured[-1].judge is fake_judge

    experiment_for("no_trigger", database_url="x", judge=fake_judge)
    assert captured[-1].judge is None


def test_experiment_for_sample_draws_each_case_n_times():
    """`--sample N`: a cheap smoke run at 1 must stay possible, and asking
    for more must multiply the case COUNT, not just re-read the same one
    (`09-the-backbone-measured.md` §3.1 — one draw is not a reading)."""
    experiment, _task = experiment_for("near_miss", database_url="x", sample=3)
    assert len(experiment.cases) == 4 * 3
    names = {c.name for c in experiment.cases}
    assert "near_miss_trailing_word_char#1" in names
    assert "near_miss_trailing_word_char#3" in names


def test_experiment_for_sample_one_is_the_untouched_corpus():
    experiment, _task = experiment_for("near_miss", database_url="x", sample=1)
    assert len(experiment.cases) == 4
    assert "near_miss_trailing_word_char" in {c.name for c in experiment.cases}


def test_a_ported_corpus_correctly_fails_a_wrong_answer():
    """Falsification of the wiring test above: a task that answers the
    OPPOSITE of what every case expects must score every one FAIL, never a
    silent PASS or an ERROR standing in for it."""
    cases = load_corpus("no_trigger")

    def wrong_task(_case: Case) -> dict:
        return {
            "output": "",
            "environment_state": [
                EnvironmentState(name=NORA_ERROR, state=""),
                EnvironmentState(name=NORA_SAID, state=1),
                EnvironmentState(name=NORA_ADMITTED, state=None),
            ],
        }

    report = Experiment(cases=cases, evaluators=[MeetsGrader()]).run_evaluations(
        task=wrong_task,
    )
    labels = {out.label for outputs in report.detailed_results for out in outputs}
    assert labels == {FAIL}
    assert ERROR not in labels


# --- `snapshot:` — a case seeded from a REAL room at a REAL instant ----------
#
# The shape that replaces `knows:`'s hand-authored content. Both end in the
# same `know()` (see `evals.backbone._drive`), which is what makes them
# interchangeable: whatever the content came from, it is written through the
# stores production writes and rendered by the prefill production renders.


def _corpus(tmp_path, body: str, monkeypatch) -> None:
    """A corpus file of this test's own, so validation can be exercised
    without a fixture case living in the real `evals/corpora/`."""
    (tmp_path / "probe.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setattr("evals.scenarios._CORPORA", tmp_path)


def test_a_snapshot_case_carries_its_real_room_and_instant(tmp_path, monkeypatch):
    _corpus(tmp_path, """
- name: from_a_real_moment
  entry: tag
  expect: reply
  input: hi
  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
    culture_key: the-real-group
    unit: friends
""", monkeypatch)

    shot = load_corpus("probe")[0].metadata["snapshot"]

    assert shot["conversation_id"] == "the-real-group"
    assert shot["before_ms"] == 1752000000000
    assert shot["culture_key"] == "the-real-group"
    assert shot["unit"] == "friends"


def test_a_hand_authored_case_carries_an_empty_snapshot(tmp_path, monkeypatch):
    """The two shapes are told apart by `snapshot:` being EMPTY, not by a
    flag, so an existing case needs no edit to keep working."""
    _corpus(tmp_path, """
- name: still_hand_authored
  entry: tag
  expect: reply
  input: hi
  knows:
    transcript:
      - hello
""", monkeypatch)

    case = load_corpus("probe")[0]

    assert case.metadata["snapshot"] == {}
    assert case.metadata["knows"]["transcript"] == ["hello"]


def test_a_case_stating_both_snapshot_and_knows_is_refused(tmp_path, monkeypatch):
    """Two ways to say what she knew, said twice, with nothing making them
    agree — and the one that loses is invisible in the file."""
    _corpus(tmp_path, """
- name: says_it_twice
  entry: tag
  expect: reply
  input: hi
  knows:
    transcript:
      - hello
  snapshot:
    platform: signal
    conversation_id: the-real-group
    before_ms: 1752000000000
    culture_key: the-real-group
    unit: friends
""", monkeypatch)

    with pytest.raises(ValueError, match="states BOTH"):
        load_corpus("probe")


@pytest.mark.parametrize("dropped", ["culture_key", "unit", "before_ms",
                                     "platform", "conversation_id"])
def test_an_incomplete_snapshot_is_refused_by_name(tmp_path, monkeypatch, dropped):
    """`culture_key` and `unit` especially: `nora.infra.sql.snapshot` refuses
    to derive them, and a wrong one matches no rows and reads exactly like a
    room that had no culture and no notebook — a case that would grade a
    prompt missing the very block it is about."""
    stated = {
        "platform": "signal",
        "conversation_id": "the-real-group",
        "before_ms": 1752000000000,
        "culture_key": "the-real-group",
        "unit": "friends",
    }
    del stated[dropped]
    lines = "\n".join(f"    {k}: {v}" for k, v in stated.items())
    _corpus(tmp_path, f"""
- name: incomplete
  entry: tag
  expect: reply
  input: hi
  snapshot:
{lines}
""", monkeypatch)

    with pytest.raises(ValueError, match=dropped):
        load_corpus("probe")
