"""The rebuilt evaluation layer.

The SDK-native spine (`evals/backbone.py`, `evals/scenarios.py`) imports
`domain.py`/`entry.py`/`grading.py`/`judge.py`/`rubric.py` from HERE: they
are shared primitives. `runner.py`'s `run_once`/`run`/`load`/`_case` stay live
because tests drive them directly (`tests/integration/`).

Six parts; the judge's (`grading.py`/`judge.py`/`rubric.py`) live here
because they must not live in `nora`:

    domain.py    Case, Knowledge-as-stated, Expectation, Outcome, Verdict
    entry.py     the entry-point port — `gate`, `participation`, `tag`,
                 `dreamer`, `proactive_act`
    runner.py    corpus loading + the per-case driver (`run_once`/`run`)
    grading.py   the LLM judge (`Judge`), built once per run
    judge.py     what a judge's answer MEANS — pure, no client in it
    rubric.py    the criteria a judge grades against

`evals.rebuilt` composes nothing. Its only route to Nora is
`nora.subject.obtain`, and there is no way to hand that function a
collaborator.
"""
