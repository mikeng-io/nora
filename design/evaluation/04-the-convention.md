# The convention — what exists, written down

Written LAST, on purpose. Attempted first on 2026-09-17 (PR #307) and withdrawn
the same day: a convention written before the code describes the variations that
already exist and legitimises them.

Everything below is true of the tree as of 2026-09-17 and is held by a test.
Where something is NOT yet true, §6 says so rather than aspiring.

## 1. The rule

> **Different evaluation suites. Different input. One Nora.**

## 2. How to add a suite

**Write a corpus file. That is the whole procedure.**

```yaml
# evals/corpora/<name>.yaml
config:                       # optional — what this SUITE needs turned on
  engagement.trigger.text: "nora.000"
policy: |                     # optional — the room set, when the default won't do
  default = "deny"
  [[conversations]]
  platform = "signal"
  id = "g1"
  persons = "*"
cases:
  - name: real/weather
    entry: gate               # which entry point the input enters at
    expect: go
    sample: 5
    knows:                    # what she KNOWS when the case starts
      transcript: ["今日好似好凍", "深水埗而家幾多度？"]
```

Then `python -m evals.rebuilt evals/corpora/<name>.yaml`.

No module, no driver, no registration. `tests/architecture/
test_the_entry_point_is_a_port.py::test_adding_a_suite_adds_no_code` is what
keeps that true — it holds that the runner reaches an entry point only through
`ENTRY_POINTS`, so there is no name a new suite could need it to import.

## 3. The four things

| | what it is | where |
|---|---|---|
| **Case** | input + expectation + entry point | `evals/rebuilt/domain.py` — pure, imports nothing from `nora` |
| **Subject** | Nora. Obtained, never built | `nora/subject/obtain.py` — one function |
| **Runner** | case in, outcome out | `evals/rebuilt/runner.py` — one function, no suite branch |
| **Outcome** | what happened, one shape | `evals/rebuilt/domain.py` |

## 4. The four rules, and the test that holds each

| rule | held by |
|---|---|
| **The eval layer never composes a subject.** | `test_the_eval_layer_composes_nothing` — AST, refuses a call to any of the seven assemblers |
| **Adding a suite adds no code.** | `test_adding_a_suite_adds_no_code` |
| **`nora.app` never learns it is being measured.** | `test_the_subject_is_obtained_not_assembled.py` — AST walk over `nora/app/**` |
| **One way to compose.** | `test_obtain_is_the_only_thing_that_composes` — every public name that returns a `Subject` |

All four were falsified before being trusted: each was attacked, each went red,
each was restored. A pin that has never failed is a pin nobody has checked.

## 5. The rules that are not tests, and why they still bind

### An input the graph cannot honour is an ERROR

Not a FAIL, and never a PASS. `Outcome.meets` returns `True`, `False` or
**`None`**, and the third answer is the whole discipline. Three places produce
it today:

- **Honcho `conclusions:`** — Honcho DERIVES a conclusion from messages, so
  there is no way to state one without substituting the client. Two
  participation cases.
- **`now:`** — the graph has no clock seam, so a case stating a time would be
  judged at wall-clock time instead. Twenty-one proactive cases.
- **any unreachable model** — on all four entry points, each through its own
  signal (§5.1).

The alternative in every case is a suite that scores an un-run case. For the
`silence`-expecting half of a corpus that is not a near miss: **every one of
them passes**, and nothing reports it.

### 5.1 Each entry point fails closed through its OWN signal

There is no shared one, because the deployment does not offer one:

| entry point | the signal | why nothing else works |
|---|---|---|
| `gate` | `GateVerdict.source == "fail_closed"` | the gate never raises; a timeout, a refusal and an unparseable body all arrive as `no_go` |
| `participation` | `speak`'s token is not `sent`/`silent` | a failed turn writes no row, which reads as silence |
| `tag` | `nora_silent_failures_total` rose | `Dispatcher.on_message` returns `None` and spawns the turn — there is nothing else to read |
| `dreamer` | `DreamOutcome.status == "unavailable"` | an un-run pass has an empty notebook diff, byte-identical to a correct `none` |

Every one of those four was found by RUNNING the entry point with no provider
and reading what came back. Three of the four returned a verdict.

### A number without its subject is not a number

`subject=` is printed on the report's last line (#310). There is one subject
now, and naming it is what keeps that true: the moment a second appears, that
line is where it shows up.

## 6. What is NOT true yet

- **`evals/*.py` still exists** and is still where a live number comes from.
  Step 5 deletes it after the replacement has driven a live run. Deleting
  first would mean a period with no instrument at all, and it is what this
  bundle would be repeating rather than fixing.
- **The judge and rubric graders have not moved.** Structural grading —
  did she reply, what did the pass write — is in place. Content grading
  (`expected_output:`, carried on every case that had one) is not.
- **Four assertions in the note-candidates corpus are parked**, not graded:
  `must_cite`, `anchor_date`, `creates`, `updates`. They need the staged
  candidate's `message_ids`, which the notebook diff does not carry.
- **Twenty-one proactive cases and two participation cases do not run.**
  §5 says why, and both reasons are missing seams in `nora.app`, not in this
  layer.

## 7. The seams `nora.app` does not have

Four found by building this, all the same shape — **the deployed graph could
not be asked for something it does**, so each driver built its own:

| | found by | closed? |
|---|---|---|
| `RelevanceGate` not on `Dependencies` | trying to reach the gate | **yes** — a field |
| `FirePath` not on `Dependencies` | trying to reach the turn | **yes** — a field |
| no clock seam | the proactive corpora | **no** — needs its own decision |
| no Honcho write seam | two participation cases | **no** — may not be fixable |

The first two cost one line each and no behaviour change. That they were
missing at all is most of the mechanism behind seven assemblies: building a
second `RelevanceGate` was genuinely easier than reaching the deployed one.
