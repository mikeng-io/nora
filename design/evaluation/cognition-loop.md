# The Cognition loop eval

What it measures: whether one unit's Cognition session, run as production
runs it, leaves the unit's whole state the way a good member of the group
would, wake after wake. Cognition is the whole OODA loop. It observes, orients
by creating, revising and retiring every kind of memory, decides, and acts
through `remind` and `wake_at`. So the eval scores the loop's effect on all of
that, not one store.

## Why the graph-only replay cannot answer it

The memory-graph spike replays a wake in the historical lane, which offers
`memory_propose` alone and answers every read as `unavailable`. It then
judges graph statements only. That measures a session production never
runs:

- **The writes are interdependent.** One message can settle a note, move an
  event, add a profile guess and only then justify a graph relation. Scoring
  only the graph scores a session that was denied the other moves, and it
  hides the writes that should have gone elsewhere.
- **Reads shape writes.** Connect-before-create needs `memory_search`,
  `note_read` and `profile_read` to answer. A blinded read makes duplicates
  correct behaviour.
- **One wake is not the loop.** A note opened at one wake is revised or closed
  at a later one; a reminder set today is judged when it comes due. A single
  wake cannot show either.

## The parts

**Subject.** The deployment's own `CognitionSessionRunner`, composed by
`obtain` from the configuration under test and run in the live lane
(`historical=False`): every tool the live session holds, every live read, the
same doctrine, skills and context assembly. No eval-only prompt, tool list or
renderer. The configuration under test is the only thing that varies between
arms.

**Sandbox.** One isolated database per case, restored by `sandbox_as_of` at
the case's first wake. Every restorable store comes back with its own ids, and
the derived indexes are rebuilt as the spike already does. Nothing in the
sandbox reaches a person: there is no transport, no dispatcher and no
delivery worker. A reminder or wake is a row the eval reads, never a message.

**The world advances between wakes.** A case is a sequence of recorded wakes
of one unit. Before wake *n*, the Record rows (messages, observations, social
items, her conscious-turn sends) that production received between wake *n−1*
and wake *n* are copied from the source into the sandbox. Everything Nora
derives (memory, profiles, notebook, topics, lessons, Standing, reminders,
wakes) comes only from the sandbox's own earlier wakes, never from the
source. So wake *n* orients against what the *candidate* kept, as production
would. This divergence is declared: anything a conscious turn derived in
production between wakes, other than the Record rows themselves, is absent.

**The room directory is frozen at run start.** Rooms a unit expands from a
guild-level `[[conversations]]` entry resolve through the deployment's own
channel read, taken once before the first case and frozen into the run's
output as `room-directory.json`; every subject composes with a static
directory built from that copy, and `--room-directory` replays a frozen
one. The map is as of the run, not as of each wake — a channel renamed
between recorded wakes still resolves under its run-start name. A guild
the unit needs but the read cannot answer for makes the case `ERROR` at
the directory step, never a silent deny.

**The clock** is each wake's recorded time. Reads see the sandbox as of that
time. Nothing later in the source is visible.

**Completion hooks run as in production.** Lesson settlement, decay and the
unit Standing fold run on a successful close. The eval does not skip them as
a disposable replay does, because what the next wake sees depends on them.
Carry and watermark bookkeeping run too.

**State diff.** Before and after every wake, the eval reads each owned store
through the production read paths, not through SQL of its own:

- memory graph statements and corrections;
- profile lines;
- notes, memos and events, with their status and field provenance;
- topic anchors and accounts;
- lessons;
- Standing;
- pending reminders and wakes.

The diff is typed and keyed by entry id: created, revised (before and after),
retired or closed, and unchanged. It is the unit of judgement, together with
the session's full trajectory.

**Trajectory.** Every wake records:

- the assembled system prompt and context, by block;
- every model request and response, reasoning included;
- every tool call with its arguments, its result and whether it was refused;
- token spend and the stop reason.

Every verdict cites the trajectory and diff it was judged on, so a result can
be explained, and re-derived, without re-running.

**Inspection.** `--inspect-only` walks the same path and stops before the
model call: the system prompt and every context block are still assembled
through the same `_session_input` a session runs and recorded, so a dry
run shows exactly what Nora would have read. Nothing is persisted — no
session row, no outcome, no stimulus consumption.

**The report.** `report.json` is the machine-readable record; `report.html`
renders the same evidence — status and budget, the evidence receipt, then
each case's wakes in order: what arrived, what Nora read, what she did,
what changed, and the judgements — as one self-contained file written at
the end of every run and re-renderable from the frozen output alone.

**Case.** A case names a unit, an ordered list of recorded wake ids from the
source, and expectations over the sequence. An expectation is about the
state, not about tool calls. Each one is a `must` or a `must_not`, anchored
at a wake and naming the kind it concerns:

- after wake 2 the dinner note names the new restaurant, and no second dinner
  note exists;
- the event moved to Friday and keeps its told provenance;
- no profile trait rests on a single message;
- no reminder for something weeks away;
- nothing said in room A surfaces in room B's entries.

An expectation is a judgement, written in plain words, that a judge reads
against the diff and trajectory. Recorded wake ids and the expectations that
quote production content live in the private case directory on the deploy
host, never in the repository.

**Judges.** `nora-judge` and `nora-judge-2`, the OmniRoute aliases, verified
by the served model header. Each judge reads one expectation against that
wake's diff, the sandbox state the expectation names, and the trajectory, and
answers `met`, `not_met` or `cannot_tell` with its reason and the entry ids it
rests on. Two judges that disagree make the expectation `REVIEW`. A judge's
reason must cite entries that exist in the diff or state, or the verdict is
`ERROR`.

## Verdicts

Per expectation, per case and per run, fail-closed:

- **PASS**: every expectation in the case was met by both judges.
- **FAIL**: an expectation was not met.
- **REVIEW**: the judges disagreed, or one could not tell.
- **ERROR**: the case did not run as specified: a restore or advance failed, a
  wake raised or hit a limit, a judge failed or cited nothing real, or the
  served model did not match.

An ERROR is never a PASS, and a wake that wrote nothing is judged like any
other: "nothing changed" is a state the expectations can require or forbid.

## Boundaries

- **No delivery.** The subject composes with no transport and no fire path.
  The eval asserts this before the first wake and refuses the run if any
  egress path is wired.
- **No production writes.** The source is read-only; every write lands in the
  case's sandbox, which is dropped after the case. The privacy rules on
  production rows apply in full: nothing restored, diffed or judged is copied
  into the repository.
- **Cost is bounded before the run.** A run states its token budget from the
  cases' wake count and refuses to start past it, and every run begins with a
  smoke run on one case. The spend of each arm is reported with its results.
- **Arms are compared only on identical cases**, run fully recorded under
  this design. Scores from the graph-only replay are not comparable.

## What it reuses

The spike's restore, index rebuild, subject composition, inspection-to-request
check, evidence capture and grading contract stay. What changes:

- the session runs in the live lane;
- the world advances between wakes;
- the completion hooks run;
- the judgement moves from graph statements to the whole state diff.

The graph-only replay scorer is retired: this is the eval, not a second path
beside it.
