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
Each wake is offered exactly the batch the recorded wake covered: its
non-self stimuli as the sandbox holds them, and a recorded stimulus that
never arrived is an `advance` ERROR naming its ids, never silently dropped.
Stimuli pending in the sandbox but outside the recorded batch stay pending
for the wake that took them in production — or remain, if none did inside
the case window. Production's `self` stimuli are never copied: the
candidate's own self-wakes take their place, so a self stimulus its earlier
wakes left due joins the batch it is due at. Every wake records the recorded
wake's stimulus ids beside the ids the sandbox session was offered; the
report states whether the non-self batches match and names the self-stimulus
difference beside them.

**The room directory is frozen at run start.** Rooms a unit expands from a
guild-level `[[conversations]]` entry resolve through the deployment's own
channel read, taken once before the first case and frozen into the run's
output as `room-directory.json`; every subject composes with a static
directory built from that copy, and `--room-directory` replays a frozen
one. The map is as of the run, not as of each wake — a channel renamed
between recorded wakes still resolves under its run-start name. A guild
the unit needs but the read cannot answer for makes the case `ERROR` at
the directory step, never a silent deny. A directory read that itself
fails — a live read that errors or a corrupt `--room-directory` file —
still freezes `report.json` and `report.html`, `ERROR` naming the
`room_directory` step.

**The clock** is each wake's recorded time. Reads see the sandbox as of that
time. Nothing later in the source is visible.

**Completion hooks run as in production.** Lesson settlement, decay and the
unit Standing fold run on a successful close. The eval does not skip them as
a disposable replay does, because what the next wake sees depends on them.
Carry and watermark bookkeeping run too.

**State diff.** Before and after every wake, the eval reads each owned store
through the production read paths, not through SQL of its own, and every
read covers each status a session write can land in — a note created and
retired inside one wake is still read, never filtered out by a live-only
view:

- memory graph statements and corrections;
- profile lines on every account the session can write — the unit's
  people, Nora, its culture, and each room's own account;
- notes, memos and events, with their status and field provenance;
- topic anchors and accounts;
- lessons;
- Standing;
- reminders and self-wakes at every state, folded to each wake
  reference's latest transition.

The diff is typed and keyed by entry id: created, revised (before and
after), retired or closed, and unchanged. A write that retires or cancels
reads as a revision carrying the closing state, never a vanished entry.
It is the unit of judgement, together with the session's full
trajectory.

**Every successful write lands in the diff.** After each wake's diff is
computed, the eval walks the trajectory's tool calls: a call whose result
states `ok` (the tool envelope succeeded and the answer wrote) must be
accounted for by the diff — each entry id the answer names lands in its
kind's entries, and an answer that names no id requires a changed entry
of its kind. A successful write the diff cannot place means the state
read missed a store, and the wake is `ERROR` at the `diff` step naming
the call — never judged on a partial picture. A `refused`, `unresolved`,
`conflict` or `unavailable` answer wrote nothing and asks nothing of the
diff.

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

**No session is an outcome, never a silent pass.** A wake whose agenda is
empty runs no session, live or inspected: the wake records `no_session`
with the reason (the outcome row's own detail in a live run) next to
whether the recorded wake ran one. Under `--inspect-only`, a wake where
production ran a session but the sandbox ran none is `ERROR` at the wake
step — a dry run cannot claim it inspected that wake. In a live run the
wake's status follows the same derived rule as any other.

**The report.** `report.json` is the machine-readable record; `report.html`
renders the same evidence — status and budget, the evidence receipt, then
each case's wakes in order: what arrived, what Nora read, what she did,
what changed, and the judgements — as one self-contained file and
re-renderable from the frozen output alone. Both are written in every
outcome: a run that fails — a model call the evidence capture raises on,
a crashed case — still freezes both, run status `ERROR` with the
evidence cause named at the top. The HTML renders every `wake-*.json`
the output directory holds: a frozen wake the report does not list shows
its trajectory under its own heading, never an empty page. An expectation
judged `ERROR` shows the judge's error type and message.

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
by the served model header. Each judge reads one expectation against a
bounded, deterministic digest of the wake — never the trajectory itself,
whose model requests repeat the whole session context and overflow any
context window. The digest carries the expectation; the wake's typed diff
for every kind (created, revised and retired entries in full, unchanged
as counts only); the sandbox state of the kind the expectation names;
every tool call in order with its arguments, result status and refusal;
the session's last assistant output; and its stop reason and tokens. It
is assembled through the same declared occasion as every model call. A
digest over the judge's input budget makes the expectation `ERROR` with
reason `judge input over budget` naming the size — never a silent
truncation. The judge answers `met`, `not_met` or `cannot_tell` with its
reason and the entry ids it rests on. Two judges that disagree make the
expectation `REVIEW`. A judge's reason must cite entries that exist in
the diff or state, or the verdict is `ERROR`.

## Verdicts

Per expectation, per wake, per case and per run, fail-closed:

- **PASS**: every expectation in the case was met by both judges.
- **FAIL**: an expectation was not met.
- **REVIEW**: the judges disagreed, or one could not tell.
- **ERROR**: the case did not run as specified: a restore or advance failed, a
  wake raised or hit a limit, a judge failed or cited nothing real, or the
  served model did not match.

A wake's status is derived the same way: a failed step (named on the wake)
or an expectation `ERROR` makes it `ERROR`, otherwise the roll-up over its
expectations — a wake with none is `PASS`. An ERROR is never a PASS, and a
wake that wrote nothing is judged like any other: "nothing changed" is a
state the expectations can require or forbid.

## Boundaries

- **No delivery.** The subject composes with no transport and no fire path.
  The eval asserts this before the first wake and refuses the run if any
  egress path is wired.
- **No production writes.** The source is read-only; every write lands in the
  case's sandbox, which is dropped after the case. The privacy rules on
  production rows apply in full: nothing restored, diffed or judged is copied
  into the repository.
- **Cost is bounded before the run.** A run states its token budget —
  wakes × the per-wake figure plus the judge allowance, two judge calls
  per expectation — and prints it before any model call, refusing to
  start past the armed cap. The default per-wake figure is the measured
  live figure. `--stop-after-tokens` is refused at startup: a session's
  model calls are parallel, and a measured cutoff only works against
  sequential calls — the flag would start the run only to fail it
  mid-wake. The spend of each arm is reported with its results.
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
