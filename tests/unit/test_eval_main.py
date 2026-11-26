"""`python -m evals` — corpus selection and the default source URL, no DB/network.

A one-line change must not cost a full run, so `--corpus`/`--class`/`--all` must actually narrow
what runs, and picking nothing must refuse rather than silently running everything.
The source URL section pins `source_url_for` against generated credentials:
the default resolves the deployment's credential source the way the host
tooling does (process env > `.env` > `secret.toml`), an explicit URL reads
none of them, and the clone-name guard never relaxes.
"""

from __future__ import annotations

import argparse

import pytest

from evals.__main__ import CORPUS_CLASS, _corpora_for
from evals.scenarios import CORPUS_ENTRY, JUDGED_CORPORA


def _args(**kwargs: object) -> argparse.Namespace:
    defaults = {"all": False, "corpus": [], "cls": []}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_corpus_class_only_names_corpora_that_exist():
    assert set(CORPUS_CLASS) <= set(CORPUS_ENTRY)


def test_no_selection_is_empty_not_everything():
    """The reason this script exists: a one-line change must not have to
    cost a full run by default."""
    assert _corpora_for(_args()) == []


def test_explicit_corpus_names_are_selected():
    result = _corpora_for(_args(corpus=["no_trigger", "near_miss"]))
    assert result == ["near_miss", "no_trigger"]


def test_class_expands_to_every_corpus_in_it():
    result = _corpora_for(_args(cls=["S7"]))
    expected = {name for name, c in CORPUS_CLASS.items() if c == "S7"}
    assert set(result) == expected
    assert len(expected) > 1, "a meaningful smoke test needs a class with >1 corpus"


def test_all_ignores_narrower_selections_and_returns_everything():
    result = _corpora_for(_args(all=True, corpus=["no_trigger"]))
    assert set(result) == set(CORPUS_ENTRY)


def test_an_unknown_corpus_name_is_refused_not_silently_dropped():
    with pytest.raises(SystemExit) as exc_info:
        _corpora_for(_args(corpus=["not_a_real_corpus"]))
    assert exc_info.value.code == 2


def test_judged_corpora_are_reachable_by_class():
    """Every judged corpus must be selectable by class, not only by exact
    corpus name — otherwise `--class S3` silently runs nothing judged."""
    s3 = set(_corpora_for(_args(cls=["S3"])))
    s9 = set(_corpora_for(_args(cls=["S9"])))
    # `standing` (the fold) is judged and sits in S6,
    # the class of what a WRITE keeps.
    s6 = set(_corpora_for(_args(cls=["S6"])))
    # `participation` is judged on its opted-in criteria and sits in S7,
    # the trigger class its cases belong to.
    s7 = set(_corpora_for(_args(cls=["S7"])))
    assert s3 | s9 | ((s6 | s7) & JUDGED_CORPORA) == JUDGED_CORPORA
    assert "standing" in s6
    assert "participation" in s7


def test_positions_prints_each_draws_written_position_and_its_length(capsys):
    """The judge's reason cannot say WHY a finding was left out, and the
    position's length against the cap is the first thing that does. The row
    is the `EvaluationData` dump, so the position rides on its
    `actual_environment_state` — read by NAME, so a sibling state cannot be
    printed in its place."""
    from types import SimpleNamespace

    from evals.__main__ import _print_positions
    from evals.backbone import NORA_POSITION, NORA_SHOWN

    body = "我應承咗幫佢睇場地。\n搵到三間，send 咗一間。"
    report = SimpleNamespace(
        cases=[
            {"name": "residue-x#1", "actual_environment_state": [
                {"name": NORA_SHOWN, "state": "the fold's own input"},
                {"name": NORA_POSITION, "state": body},
            ]},
            {"name": "residue-x#2", "actual_environment_state": []},
        ],
        detailed_results=[
            [SimpleNamespace(label="FAIL")],
            [SimpleNamespace(label="ERROR")],
        ],
    )
    _print_positions(report)
    out = capsys.readouterr().out
    assert f"[FAIL] residue-x#1  ({len(body)} chars)" in out
    assert "      | 搵到三間，send 咗一間。" in out
    assert "the fold's own input" not in out
    assert "[ERROR] residue-x#2  (none)" in out


def test_print_input_composes_the_graph_surface(monkeypatch, capsys):
    """The render probe is not a memory-arm escape hatch: its default Spec
    carries `graph`, which is what activates sandbox indexing in the draw."""
    from types import SimpleNamespace

    from strands_evals import Case

    import evals.__main__ as main

    seen = []

    def fake_task(spec):
        seen.append(spec)

        def run(case):
            return {"environment_state": [
                SimpleNamespace(name=main.NORA_SHOWN, state="rendered input"),
            ]}

        return run

    monkeypatch.setattr(main, "load_corpus", lambda name: [Case(name="case", input="x")])
    monkeypatch.setattr(main, "nora_task", fake_task)
    assert main._print_input("standing", "case", source_url="postgresql://u@h/generated") == 0
    assert [spec.memory_surface for spec in seen] == ["graph"]
    assert capsys.readouterr().out.strip() == "rendered input"


def test_the_estimate_counts_draws_and_bounds_their_cost():
    """Per corpus, cases x samples x that corpus's measured per-draw cost."""
    from evals.__main__ import JUDGED_DRAW_SECONDS, UNJUDGED_DRAW_SECONDS, estimate

    est = estimate([(2, False), (1, True)], 3)
    assert (est.cases, est.sample, est.draws) == (3, 3, 9)
    assert est.seconds == 6 * UNJUDGED_DRAW_SECONDS + 3 * JUDGED_DRAW_SECONDS
    assert est.line() == (
        f"estimate: 3 cases x 3 samples x 1 arm = 9 draws, ~{est.seconds / 60:.0f} min"
    )
    # The judge's share scales with --judge-samples; at the default it is 211 s.
    one = estimate([(1, True)], 1, judge_samples=1).seconds
    assert estimate([(1, True)], 1, judge_samples=5).seconds > JUDGED_DRAW_SECONDS > one
    assert estimate([(1, False)], 1, judge_samples=5).seconds == UNJUDGED_DRAW_SECONDS


def test_the_run_that_held_the_lock_all_night_is_refused_unless_allowed():
    """tool_selection on the graph surface, every case x 3, holds the host's
    one eval lock for hours: refused, naming the way out. The same run starts
    with --allow-long, and two cases x 3 start without it."""
    from evals.__main__ import BUDGET_MINUTES, estimate, over_budget
    from evals.scenarios import selected_cases

    cases = len(selected_cases("tool_selection"))
    full = estimate([(cases, False)], 3)
    assert full.minutes > BUDGET_MINUTES
    why = over_budget(full, allow_long=False)
    assert why is not None and "--case" in why and "--allow-long" in why
    assert over_budget(full, allow_long=True) is None
    assert over_budget(estimate([(2, False)], 3), allow_long=False) is None


def test_the_budget_edge_is_inclusive():
    from evals.__main__ import BUDGET_MINUTES, UNJUDGED_DRAW_SECONDS, Estimate, over_budget

    at = Estimate(cases=1, sample=1, draws=1, seconds=BUDGET_MINUTES * 60)
    assert over_budget(at, allow_long=False) is None
    over = Estimate(cases=1, sample=1, draws=1, seconds=BUDGET_MINUTES * 60 + UNJUDGED_DRAW_SECONDS)
    assert over_budget(over, allow_long=False) is not None


def test_a_case_the_run_cannot_draw_is_refused_before_the_estimate():
    from evals.scenarios import selected_cases

    with pytest.raises(ValueError, match="no active case named"):
        selected_cases("tool_selection", only=frozenset({"no-such-case"}))


# --------------------------------------------------------------------------
# source_url_for: generated credentials only; every value is synthetic.
# --------------------------------------------------------------------------


def _deployment(tmp_path, *, secret=None, env=None):
    if secret is not None:
        (tmp_path / "secret.toml").write_text(
            f'[credentials]\nPOSTGRES_PASSWORD="{secret}"\n'
        )
    if env is not None:
        (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD={env}\n")
    return tmp_path


def test_source_url_default_accepts_secret_only(tmp_path, monkeypatch):
    """A deployment whose credential lives ONLY in secret.toml still gives
    the eval its clone URL — the .env-only default refused exactly this."""
    from sqlalchemy.engine import make_url

    from evals.__main__ import EVAL_CLONE, source_url_for

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    parsed = make_url(
        source_url_for("", _deployment(tmp_path, secret="synthetic-secret"))  # noqa: S106 -- synthetic fixture
    )
    assert (parsed.drivername, parsed.username, parsed.host, parsed.port,
            parsed.database, parsed.password) == (
        "postgresql+asyncpg", "nora", "127.0.0.1", 5435,
        EVAL_CLONE, "synthetic-secret")


def test_source_url_host_precedence_and_empty_override(tmp_path, monkeypatch):
    """Process env > .env > secret.toml; a PRESENT empty value wins the layers
    below it, so an operator override is a refusal, never the file's value."""
    from sqlalchemy.engine import make_url

    from evals.__main__ import source_url_for

    deployment = _deployment(
        tmp_path, secret="synthetic-secret", env="synthetic-legacy"  # noqa: S106 -- synthetic fixture
    )
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert make_url(source_url_for("", deployment)).password == "synthetic-legacy"  # noqa: S105 -- synthetic fixture
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-process")
    assert make_url(source_url_for("", deployment)).password == "synthetic-process"  # noqa: S105 -- synthetic fixture
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    with pytest.raises(ValueError, match="no POSTGRES_PASSWORD") as error:
        source_url_for("", deployment)
    assert "synthetic-legacy" not in str(error.value)


def test_source_url_explicit_skips_credentials_and_refuses_live_name(tmp_path):
    """A stated URL reads no credential file at all — this deployment dir is
    broken on purpose and would raise if read — and the clone-name guard is
    unchanged."""
    from evals.__main__ import source_url_for

    (tmp_path / "secret.toml").write_text("malformed = [")
    stated = "postgresql+asyncpg://u:p@h/generated_eval"
    assert source_url_for(stated, tmp_path) == stated
    with pytest.raises(ValueError, match="not a clone"):
        source_url_for("postgresql+asyncpg://u:p@h/nora", tmp_path)


def test_source_url_password_reserved_characters_roundtrip(tmp_path, monkeypatch):
    """The URL is built with `URL.create`, not string interpolation, so a
    password full of reserved characters parses back to itself."""
    from sqlalchemy.engine import make_url

    from evals.__main__ import source_url_for

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    password = "syn:thetic/p@ss?w#rd%&="  # noqa: S105 -- synthetic fixture
    url = source_url_for("", _deployment(tmp_path, env=password))
    parsed = make_url(url)
    assert parsed.password == password
    assert parsed.host == "127.0.0.1" and parsed.database == "nora_eval"


def test_source_url_default_refuses_missing_and_broken_credentials(
    tmp_path, monkeypatch,
):
    from evals.__main__ import source_url_for

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="no POSTGRES_PASSWORD"):
        source_url_for("", tmp_path)
    (tmp_path / "secret.toml").write_text("malformed = [")
    with pytest.raises(ValueError, match="unreadable"):
        source_url_for("", tmp_path)


# --- the journey selection ---------------------------------------------------

_JOURNEY_PLAN = {
    "version": 2,
    "expected_wakes": 0,
    "steps": [{"send": "the venue is Cedar Hall"}, {"until_delivery": True}],
    "expectations": [{"turn": 0, "polarity": "must", "kind": "behavior",
                      "text": "says where the outing was held",
                      "check": "Cedar Hall"}],
    "judges": ["alpha", "beta"],
}


def _journey_inputs(tmp_path):
    """The declared cohort and services manifest a journey selection binds."""
    import json

    from evals.services import load_services

    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps({
        "version": 1, "judges": ["alpha", "beta"],
        "cases": [{"name": "the_outing", "plan": dict(_JOURNEY_PLAN)}]}))
    services_path = tmp_path / "services.json"
    services_path.write_text(json.dumps({"version": 1, "corpora": {
        "autonomous_journey": {"the_outing": {
            "kind": "journey_world", "policy": "[rooms]\nallow = []",
            "judges": ["alpha", "beta"]}}}}))
    return argparse.Namespace(
        journey_cohort=cohort_path, services=services_path,
        service_inputs=load_services(services_path), run_budget="10",
        sample=1, subject_overrides=None, judge=None,
        measure="protocol", memory_surface="graph")


def test_the_journey_corpus_is_selectable_by_name_never_by_class_or_all():
    assert _corpora_for(_args(corpus=["autonomous_journey"])) == ["autonomous_journey"]
    everything = _corpora_for(_args(all=True))
    assert "autonomous_journey" not in everything, (
        "--all stays the yaml corpora; a live-clock journey needs its "
        "declared inputs and never rides a bulk selection")
    classes = set().union(*(
        _corpora_for(_args(cls=[c])) for c in set(CORPUS_CLASS.values())))
    assert "autonomous_journey" not in classes


def test_a_journey_selection_binds_the_declared_inputs(tmp_path):
    from evals.journey_cohort import bound_selection

    cohort = bound_selection(_journey_inputs(tmp_path), ["autonomous_journey"], None)
    assert cohort.judges == ("alpha", "beta")
    assert [c.name for c in cohort.cases] == ["the_outing"]


@pytest.mark.parametrize("damage", [
    pytest.param({"journey_cohort": None}, id="no-cohort"),
    pytest.param({"services": None}, id="no-services"),
    pytest.param({"run_budget": None}, id="no-budget"),
    pytest.param({"sample": 2}, id="sampled"),
    pytest.param({"subject_overrides": "x.json"}, id="subject-overrides"),
    pytest.param({"judge": "some-judge"}, id="eval-judge-on-journey-only"),
    pytest.param({"memory_surface": "off"}, id="memory-off-arm-not-composable"),
])
def test_a_journey_selection_refuses_an_absent_or_promoting_input(tmp_path, damage):
    """Every clause refuses BEFORE composition or model dispatch, and no
    flag promotes the finite world into a real-model baseline."""
    from evals.journey_cohort import bound_selection

    args = _journey_inputs(tmp_path)
    for key, value in damage.items():
        setattr(args, key, value)
    with pytest.raises(ValueError):
        bound_selection(args, ["autonomous_journey"], None)


def test_a_journey_selection_refuses_an_undeclared_world(tmp_path):
    """A services manifest naming another corpus, a wrong-kind plan, or
    aliases the cohort did not declare all refuse."""
    import json

    from evals.journey_cohort import bound_selection
    from evals.services import load_services

    args = _journey_inputs(tmp_path)
    empty_services = tmp_path / "empty.json"
    empty_services.write_text(json.dumps(
        {"version": 1, "corpora": {"other": {"c": {
            "version": 1, "bindings": {"llm.base_url": "x"}, "services": {"x": []}}}}}))
    args.service_inputs = load_services(empty_services)
    with pytest.raises(ValueError, match="no plans"):
        bound_selection(args, ["autonomous_journey"], None)


def test_the_cohort_flag_on_an_ordinary_selection_is_refused(tmp_path):
    """A declared input that binds nothing is a selection error, not a
    silently ignored flag."""
    from evals.journey_cohort import bound_selection

    with pytest.raises(ValueError, match="no selected corpus"):
        bound_selection(_journey_inputs(tmp_path), ["standing"], None)


def test_an_ordinary_selection_binds_no_cohort(tmp_path):
    from evals.journey_cohort import bound_selection

    args = _journey_inputs(tmp_path)
    args.journey_cohort = None
    assert bound_selection(args, ["standing"], None) is None


# --------------------------------------------------------------------------
# --measure: the off-default behavioral claim for the journey corpus.
# --------------------------------------------------------------------------


def test_the_measure_flag_is_off_default_and_refuses_an_unknown_claim():
    from evals.cli import eval_parser

    assert eval_parser().parse_args(
        ["autonomous_journey"]).measure == "protocol"
    assert eval_parser().parse_args(
        ["autonomous_journey", "--measure", "behavioral"]
    ).measure == "behavioral"
    with pytest.raises(SystemExit):
        eval_parser().parse_args(
            ["autonomous_journey", "--measure", "live"])


_GATEWAY = {"upstream": "http://127.0.0.1:9/v1",
            "models": ["nora", "alpha", "beta"]}


def _gateway_manifest(tmp_path):
    """A manifest whose journey world declares the gateway `behavioral` needs."""
    import json

    from evals.services import load_services

    path = tmp_path / "services-gateway.json"
    path.write_text(json.dumps({"version": 1, "corpora": {
        "autonomous_journey": {"the_outing": {
            "kind": "journey_world", "policy": "[rooms]\nallow = []",
            "judges": ["alpha", "beta"],
            "model_gateway": dict(_GATEWAY)}}}}))
    return path, load_services(path)


def test_a_behavioral_selection_binds_the_declared_live_world(tmp_path):
    """`--measure behavioral` meeting a per-case plan that declares its
    own model gateway is the only way the journey's claim changes."""
    from evals.journey_cohort import bound_selection

    args = _journey_inputs(tmp_path)
    args.measure = "behavioral"
    args.services, args.service_inputs = _gateway_manifest(tmp_path)
    cohort = bound_selection(args, ["autonomous_journey"], None)
    assert cohort.judges == ("alpha", "beta")
    assert [c.name for c in cohort.cases] == ["the_outing"]


def test_a_behavioral_selection_cannot_promote_a_generated_world(tmp_path):
    """A plan with no model gateway answers every request through
    CausalProtocol/GeneratedJudge — finite responses, never model
    behavior — so `--measure behavioral` refuses before any case
    composes or a model is dispatched."""
    from evals.journey_cohort import bound_selection

    args = _journey_inputs(tmp_path)
    args.measure = "behavioral"
    with pytest.raises(ValueError, match="cannot promote"):
        bound_selection(args, ["autonomous_journey"], None)


def test_a_gateway_fixture_keeps_the_declared_protocol_measurement(
        tmp_path):
    """A declared gateway never promotes a protocol claim to behavior."""
    from evals.journey_cohort import bound_selection

    args = _journey_inputs(tmp_path)
    args.services, args.service_inputs = _gateway_manifest(tmp_path)
    cohort = bound_selection(args, ["autonomous_journey"], None)
    assert [case.name for case in cohort.cases] == ["the_outing"]
    assert args.bound_measurement.mode == "protocol-rehearsal"


def test_a_behavioral_measurement_binds_the_journey_only(tmp_path):
    """The off-default choice names the journey corpus: a behavioral
    claim on an ordinary corpus refuses rather than silently bind."""
    from evals.journey_cohort import bound_selection

    args = _journey_inputs(tmp_path)
    args.measure = "behavioral"
    args.journey_cohort = None
    with pytest.raises(ValueError, match="no journey corpus"):
        bound_selection(args, ["standing"], None)


# --------------------------------------------------------------------------
# --run-input-bytes: the existing wire-input bound, exposed on the CLI.
# --------------------------------------------------------------------------


def _parsed_budget(*argv: str):
    """The real flag path: argv -> parser -> run_budget_for -> RunBudget."""
    from evals.cli import eval_parser, run_budget_for

    args = eval_parser().parse_args(list(argv))
    return run_budget_for(args.run_budget, input_bytes=args.run_input_bytes)


def test_run_input_bytes_reaches_the_run_budget():
    """The parsed `--run-input-bytes` value must land on the budget's real
    `max_input_bytes` field — a flag that parses but binds nothing gates
    nothing, which is exactly the silent-ignore this surface exists to
    refuse."""
    budget = _parsed_budget(
        "tool_selection", "--run-budget", "10", "--run-input-bytes", "9000")
    assert budget is not None
    assert budget.max_input_bytes == 9000
    assert budget.max_requests == 10


def test_run_input_bytes_without_run_budget_is_refused_before_dispatch():
    """`--run-input-bytes` alone would install no bound at all, so
    `run_budget_for` refuses (the caller turns it into `refusing to run`,
    before `active_run_budget` is ever set) rather than silently ignore it."""
    with pytest.raises(ValueError, match="requires --run-budget"):
        _parsed_budget("tool_selection", "--run-input-bytes", "9000")


def test_run_input_bytes_admission_is_cumulative_with_a_permitted_control():
    """The bound is on CUMULATIVE serialized request bytes: a first request
    under the cap admits (the positive control — only a budget that actually
    ran can admit), and the request that would carry the cumulative size over
    it refuses before dispatch."""
    from nora.domain.run_budget import RunBudgetError

    budget = _parsed_budget(
        "tool_selection", "--run-budget", "10", "--run-input-bytes", "9000")
    reservation = budget.admit(input_bytes=7000, declared_output_tokens=10)
    assert budget.requests == 1 and budget.input_bytes == 7000
    with pytest.raises(RunBudgetError, match="input size"):
        budget.admit(input_bytes=3000, declared_output_tokens=10)
    assert budget.requests == 1          # the over-cap request never counted
    budget.release(reservation)


@pytest.mark.parametrize("bad", ["0", "-5", "1.5", "many"])
def test_run_input_bytes_must_be_a_positive_integer(bad):
    """A zero, negative or non-integer byte bound is an argparse refusal
    (exit 2), never a bound that admits nothing for an unreadable reason."""
    from evals.cli import eval_parser

    with pytest.raises(SystemExit) as exc_info:
        eval_parser().parse_args(
            ["tool_selection", "--run-budget", "10",
             "--run-input-bytes", bad])
    assert exc_info.value.code == 2


def test_no_run_input_bytes_leaves_the_wire_bound_unset():
    """No flag, existing behaviour: `--run-budget` alone still parses and the
    byte bound stays uninstalled — and no flag at all still builds no budget."""
    assert _parsed_budget("tool_selection", "--run-budget", "10") \
        .max_input_bytes is None
    assert _parsed_budget("tool_selection") is None
