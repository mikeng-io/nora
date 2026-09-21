"""The rebuilt evaluation layer (`design/evaluation/02-the-rebuild.md`).

**STATUS, 2026-09-20** (`design/evaluation/10-the-scenario-map.md` §16):
this is no longer "the old harness, deleted once its replacement lands." The
SDK-native spine (`evals/backbone.py`, `evals/scenarios.py`) imports
`domain.py`/`entry.py`/`grading.py`/`judge.py`/`rubric.py` from HERE — they
are shared primitives, not a layer awaiting replacement. What WAS the old
harness's own driver/report/CLI (`__main__.py`, `report.py`, and
`runner.py`'s `run_once`/`run`) is trimmed as its callers are ported, not
deleted wholesale; `run_once`/`run`/`load`/`_case` are still live because
real tests still drive them directly (`tests/integration/`).

Six things, not four (`01-boundaries` §2 named three; `grading.py`/`judge.py`/
`rubric.py` were added for the judge, which needed somewhere to live that is
not `nora`):

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
