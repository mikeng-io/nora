---
name: nora-test
description: Decides whether a Nora test needs to exist and at which level, then writes it honestly - what it proves, where it lives, which shared fakes, builders and fixtures it reuses, how it handles time and collaborators, and how it is proved not decorative. Load before adding or editing any file under tests/. Not for proving an existing fix is guarded (nora-prove-fix) or running evals (nora-eval).
---

# nora-test

`tests/README.md` owns the map: what each directory means and the three
placement rules with their gates. This skill is the order for writing one test
and the check after each step. A test is evidence; a test that cannot fail is
decoration that reads as coverage.

## 0. Does this test need to exist?

A test must exist for:

| change | the test |
|---|---|
| a fix | the one that goes red with the fix's production line reverted |
| a behavioural rule, design invariant or acceptance statement | one named test per rule, listed where the rule is |
| a trust boundary: scope, disclosure, egress, credentials, fail-closed | the negative case: what must not happen, does not |
| a new implementation of a port | a parametrization of the port's contract suite, not a new file |
| non-trivial logic: a branch, a computation, a state transition | the smallest test that fails if it breaks |

No test is written for:

- redundant pass-through code, plain data classes, or what the type checker
  already proves — composition, configuration reaching its consumer and
  pipeline order are properties of the wiring and do get tests;
- the framework's or a library's own behaviour;
- implementation shape — which helper was called with which arguments —
  instead of the result;
- a behaviour another test already proves at the level that matters;
- a speculative case nothing in the design or an incident names;
- model behaviour: that is an eval case (`nora-eval`), not a pytest.

**Pick the cheapest level that can prove it.** `unit/` when the property is in
pure logic; `contract/` when it must hold for every implementation of a port;
`integration/` only when the property exists in the wiring itself — a scope
across stores, a pipeline order; `architecture/` when it is a property of the
source. A higher level never stands in for a missing lower one, and a lower one
never claims what only the wiring shows.

**A new test file** exists only when no module already owns the area or the
behaviour group. **A test is deleted** when the behaviour it guards is removed,
when it is decorative (step 5), or when another test proves the same thing.

Check: name the row of the first table this test answers to. None means no test.

## 1. Name the property

State, in one sentence, the behaviour the test proves and the claim it backs:
a rule, a fix, an acceptance statement.

- For a fix: the test must go red with the fix's production line reverted.
- For new behaviour: the test must fail with the feature deleted.
- Assert what the code does, observable from outside: a returned value, a
  written row, a sent message, a three-state result. Not how it does it.

Check: you can say what wrong code this test would catch.

## 2. Choose its home

Pick the directory by what makes the test true (`tests/README.md`): `unit/`
has no I/O; `contract/` runs one suite against every implementation of a port;
`integration/` and `infra/` own real Postgres, servers and containers;
`architecture/` reads the source.

- Never a new file in the `tests/` root or `tests/domain/`.
- Add to the test module that already covers the code's area. A module past
  about a thousand lines takes no new group of behaviour: start a sibling
  module named for that behaviour.
- A change that rewrites most of a legacy root file moves it to its directory.

Check: the file's directory matches what the test needs to run — a `unit/`
test that needs Postgres is in the wrong place.

## 3. Reuse before writing

Search before creating any helper:

| need | use |
|---|---|
| a `Config` | `example_config` / `example_config_path`, or `tests/builders.py` |
| a fake for a port in `nora/domain/ports.py` | `nora/testing/`; a new one goes there with its `contract/` suite |
| a double that is not a port: a clock, a sender, a model source | `tests/fakes.py` |
| a database | `clean_pg_url` for ordinary SQL tests; `pg_url` and `pg_module_url` only for the cases `tests/README.md` names |
| data | generated values, or placeholders from `identity.example.toml`; files in `tests/fixtures/` |

- Never paste production rows, even "just six people"; the hooks refuse them.
- An inline class is local scaffolding only: a fault injector, or a shared fake
  with one twist. A second copy of the same fake is the shared fake missing.

Check: `grep -rn "class _\?Fake<Name>" tests nora/testing` finds no existing
double you are about to rewrite.

## 4. Write it

- **Name** the behaviour as a sentence: `test_<subject>_<does>_<when>`. The name
  is the failure message a reader sees first.
- **One behaviour per test.** Arrange, act, assert; parametrize cases that
  differ only in data.
- **Collaborators come through `__init__`.** `monkeypatch` is for the process
  boundary only — an environment variable, a module with no seam — never to
  replace logic inside the code under test. Needing it there is a missing seam:
  add the parameter.
- **Time is a value.** Use the fake clock and explicit events or latches.
  Never sleep to wait for work; a sleep is a race that passes until it doesn't.
- **A fake behaves like the real thing,** including its wrong-call shapes,
  errors and the exact values the real SDK returns. A fake that answers every
  request the same way proves nothing.
- **Keep the three states apart.** Found, nothing found and unavailable are
  different assertions; an error is never a pass.
- **Pair every zero with a positive control.** `== 0`, "not called" or "empty"
  is also true of code that never ran: assert something only a run that ran
  produces.

Check: read only the test name and assertions. Do they state the property from
step 1 and nothing else?

## 5. Prove it can fail

- For a fix, follow `nora-prove-fix`: revert the production line, watch the
  named test go red, restore it green.
- For new behaviour, break the behaviour once by hand — delete the branch,
  invert the condition — and watch the test fail.
- A test that stays green under that change is decorative: fix it, or delete it.

Check: you saw it red for the right reason, in its own assertion, not a setup
error.

## 6. Run it

```bash
uv run python -m scripts.pytest_gate <file-or-node>    # the test you wrote
make test-affected                                     # what the change touches
```

Read the exit code, not the tail. A refused test host is unavailable, not a
pass.

Check: the named test ran and passed, and the affected selection is green.

## Signs a test is not honest

| sign | fix |
|---|---|
| green with the feature deleted | assert the observable result |
| `monkeypatch` on the code under test | inject the collaborator through `__init__` |
| `sleep` before an assertion | fake clock, events or latches |
| a fake redefined in another test file | move it to `nora/testing/` or `tests/fakes.py` |
| only "not called" / `== 0` assertions | add a positive control |
| a real person's name, phone or room id | generate it |
