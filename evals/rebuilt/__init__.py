"""The rebuilt evaluation layer (`design/evaluation/02-the-rebuild.md`).

Lives beside the old one until Step 5 deletes `evals/*.py`, because deleting
the instrument before its replacement drives a real case means a period with
no instrument at all.

Four things and nothing else (`01-boundaries` §2):

    domain.py   Case, Knowledge-as-stated, Expectation, Outcome, Verdict
    entry.py    the entry-point port — `gate` and `participation`
    runner.py   one function: case in, outcome out

`evals.rebuilt` composes nothing. Its only route to Nora is
`nora.subject.obtain`, and there is no way to hand that function a
collaborator.
"""
