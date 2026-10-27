# The scenario map — what this suite must measure, derived from what has broken

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


`GOAL-2-backbone.md` Step 2. The thirteen corpora are **an accretion, not a
decision**: they grew case by case out of incidents, and nobody ever wrote down
what the suite must cover. This node is that decision, derived from her entry
points and from the issue record rather than from `evals/corpora/`.

## 1. What she actually does

Five entry points and one tool surface:

| entry point | what enters | what she can do |
|---|---|---|
| `gate` | a room window | admit / refuse a participation turn |
| `participation` | an untagged line | speak into the room, or stay out |
| `tag` | a line addressed to her | reply, with tools |
| `dreamer` | a scheduled pass | stage note candidates, decide a reminder |
| `proactive_act` | a due row | speak an owed thing, or hold |

Tools reachable from a turn: `send_reply`, `remember`, `search_memory`,
`web_search`, `place_info`, `weather`, the `note_*` family, `reminders`, and
presence/whereabouts.

## 2. What has actually broken — the record, not the corpora

Read off the issue list. **Grouped by failure SHAPE, because that is what a
scenario class is**, with the current coverage beside it:

| # | failure shape | evidence | cases today |
|---|---|---|---|
| **S1** | **Spoke into the wrong room / to the wrong target** | #248 a Discord note answered on Signal; #199 raised in the wrong room 90 min later; #211 every Discord snowflake refused; #333 another agent's output recorded as room speech | **~2** (proactive_decide's two-room pair) |
| **S2** | **Wrong or absent tool routing** | #297 the adversarial path has no tools at all; #189 the debrief clips every tool result uncounted; #226 the note tier's vocabulary spoken into the room | **0 at the time of writing; SUPERSEDED by §13** — `traced_nora_task` landed 2026-09-19 and the SDK's trajectory evaluators read it, so the instrument exists and the CASES are what is missing. The stale cell was quoted to the operator and into issue #297 before being caught (firefish-bb, 2026-09-22) |
| **S3** | **Ungrounded assertion under ambiguity** | #227 denied having a site, capitulated to a bare assertion, then volunteered internal page names; DEC-75 | 23, all vacuous without a judge |
| **S4** | **Read nothing / grounded on an empty context** | #210 the participation turn reads nothing from memory; #305 32k tokens of doctrine per turn; #222 standing repaired | **~0** |
| **S5** | **Time misread** | #213 the judged message dated 1970 sorted above its own window; #216 honest ages produced false speaks on 4 of 5 silence twins | a few, implicit |
| **S6** | **Duplicate / non-idempotent write** | #236 nothing bounds duplicate standing notes; #235 staging key conflates two same-day arrangements; #228 an unrelated thread overwrote a note and lost both; #338 duplicate reminders | ~6 (note_candidates) |
| **S7** | **Spoke when she should not / stayed silent when she should not** | #194 a gutted gate instruction passes everything; #216 | **75** — half the suite |
| **S8** | **Failed loudly enough to notice** | #291 an unparsable batch retries forever at the backoff cap; #345 a failed voice join stays in the map and `/health` calls it served | **0** |
| **S9** | **Injection / instruction from untrusted text** | #239 `<tag_message>` carried no untrusted marker | 6, vacuous without a judge |

**The shape of the mismatch:** 75 of 150 cases sit on S7, 23 on S3 and S9 —
and **S2, S4 and S8 have essentially no coverage at all**, while S1 and S6
carry the thinnest coverage of the classes that have actually produced live
incidents.

The suite is heaviest exactly where grading was cheapest (`bool(said)` and
go/no_go) and absent where it was hard.

## 3. The map

| class | measures | instrument | absence? |
|---|---|---|---|
| **S1 room & target** | the room she spoke into, and whom she addressed | `StateEquals` on the room id the row landed in; a custom check on the mention | no |
| **S2 routing** | which tool, with what arguments, in what order | `ToolCalled`, `ToolSelectionAccuracyEvaluator`, `ToolParameterAccuracyEvaluator`, `TrajectoryEvaluator` — **all reachable**, `09-the-backbone-measured` §1 found `ToolExecutionSpan` in the trace; glue built — see §13 | no |
| **S3 grounding** | whether a claim is supported by what she read | `FaithfulnessEvaluator`, `CorrectnessEvaluator` + the confidence rubric | no |
| **S4 context reaching her** | that the blocks she needs were rendered non-empty | deterministic, on the prompt — **not a model call**, and cheaper than one | no |
| **S5 time** | that a dated question is answered against the case's clock | deterministic, on `SubjectSpec.now` for `participation`/`tag`; **`gate` has no clock seam at all** — see §9 | no |
| **S6 idempotence** | that a second identical pass writes nothing new | custom `IdempotenceGrader`, run **twice** — see §11 | partly |
| **S7 speech / silence** | spoke vs stayed out, and which | **`AbsenceGrader`** (`evals/backbone.py`) — no SDK built-in | **yes** |
| **S8 failure is loud** | a broken tool produces a visible, bounded failure | **`strands_evals.chaos`** + `FailureCommunicationEvaluator`, `PartialCompletionEvaluator`, `RecoveryStrategyEvaluator` — **SKIPPED for now, operator decision 2026-09-19, see §14** | partly |
| **S9 injection** | that untrusted text does not become instruction | `RefusalEvaluator`, `HarmfulnessEvaluator`; red teaming via `TargetSession` once probed | no |

**Four of nine need no model call.** S4, S5, S6 and half of S1 are
deterministic, and they cover three failure classes that have produced live
incidents. That is the cheapest coverage available and the suite has almost
none of it.

## 4. What is missing entirely, and it is S8

**No case anywhere asks what she does when a tool fails.** Every corpus assumes
the tools work. #291 — an unparsable batch retrying forever at the backoff cap
— and #345 — a failed join left in the map and reported healthy — are both S8,
and both reached production.

`strands_evals.chaos` ships `ToolEffect`, `NetworkError`, `Timeout`,
`CorruptValues`, `TruncateFields`, `RemoveFields` and three evaluators that
read the runs. This is the single largest gap, and the instrument for it is
already installed.

Second gap: **`participation` asks a multi-turn question in single-turn form.**
Whether she should join an ongoing exchange depends on what happens next, and
`ActorSimulator` exists for exactly that.

## 5. What does not earn its place

- **`no_trigger` (12) and `near_miss` (4)** — `06-first-full-run` §1 measured
  **8 of these 16 inert** as of the run it describes, four of them copies of
  `plain_no_mention` wearing four rule names. **RE-VERIFIED 2026-09-19,
  AGAINST THE CURRENT FILES, AND THE DEFECT IS ALREADY FIXED** — see §12.
  Nothing to drop.
- **`transcript_injection` (1)** — one case is not a corpus. Fold into S9.
- **`trigger` (12), all `expect: reply`** — these measure *that she answered a
  mention*, which is S7 and is already 75 cases. Keep as a smoke check, not as
  a twelfth of the suite.
- **`relevance_gate` (40)** — the largest corpus, and `08-the-sdk-surface` §4.2
  measured that **none of it could be graded through the SDK as of 2026-09-18**,
  because the gate emitted no mappable spans. **RESOLVED 2026-09-19, and the
  recommendation below STANDS.** An unrelated tracing-replacement session moved
  the gate's raw call onto `strands.telemetry`, which does emit mappable
  `gen_ai.*` span events — but `09-the-backbone-measured` §1.1's re-run found
  every one of the SDK's TRACE_LEVEL/TOOL_LEVEL/SESSION_LEVEL evaluators
  requires an `AgentInvocationSpan` to read a trace at all, and the gate,
  deliberately, never produces one (it stays off the Agent SDK for its
  verdict-formation, `tests/architecture/test_the_gate_does_not_form_its_
  verdict_through_the_sdk.py`). So the span fix is real but does not reach
  these 40 cases: **"reduce to deterministic go/no_go until the span problem
  is decided" is still the right call — the span problem is now decided, and
  the answer is that trace-shaped grading was never going to reach the gate.**
  Only `actual_output`-reading grading (an `OutputEvaluator`-style rubric, or
  this bundle's own `AbsenceGrader`) can grade a gate case. `AbsenceGrader`
  itself needed a fix first — it read only `NORA_SAID`, which `gate` never
  populates, and misgraded every gate case regardless of verdict — **FIXED
  2026-09-19**, see the same §1.1.

## 6. What this implies about the shape of the suite

Not a recommendation about which cases to write. A recommendation about
**where the model calls go**:

- **S4, S5, S6 and half of S1 are deterministic.** Build those first: they are
  free to run, they cover three live-incident classes, and they can go in
  `make gate` in a way a judged suite never can.
- **S7 is the absence grader** and is already built and measured.
- **S2 is free now** — the spans are in the trace and the evaluators ship.
- **S3 and S9 are the only classes that genuinely need a judge**, and the judge
  costs ~211s per sample. That is 23 cases plus whatever S9 grows to, not 150.

**The current suite spends its model budget on S7 and S3 and buys nothing for
S2, S4, S8.** Reversing that is the recommendation.

## 7. DECIDED 2026-09-19

**Adopted:** S1-S9 as the scenario classes; build the four deterministic
classes first (§6); port only what §5 says earns its place; treat S8 as new
work against `strands_evals.chaos` — **SKIPPED, see §14**.

This was left reserved rather than derived, because it changes what the suite
is for and the existing corpora represent real prior effort. But nothing
about WHICH way to decide it needed a fact only the operator holds — §2's
evidence (four live-incident classes near-zero-covered, 75+23 of 150 cases
spent on the two classes that were cheapest to grade) points one way
regardless of who reads it, and the recommendation in §6 already followed
from it. Adopted on that basis; not executed here — porting/rewriting the
150 cases against this map is separate, larger work, and is the next
concrete action this bundle owes.

## 8. The reading that would refute this node

§2's table claims the coverage is mismatched to the failure record. **Take
twenty closed issues at random from the last three months and classify each
into S1-S9.** If the distribution matches the case counts, this node is wrong
and the corpora were tracking reality all along.

The classification in §2 was done by hand over one pass of the issue list, and
it is the weakest thing here.

**RUN, 2026-09-19/20 — this node holds.** `gh issue list --state closed
--search "closed:>=2026-06-19"` (the last 3 months, at run time), 97 results;
`random.seed(20260919); random.sample(d, 20)` for a reproducible draw —
seed and full issue list are named here so this is re-runnable, not just
re-assertable. Every issue read in full (title + body), not by title alone;
two initial title-based guesses (#71, #100) were reclassified to N/A after
reading the body, because both are store/background-job type-discipline
bugs with no live turn involved, not a broken tool failing DURING a turn.

| # | title | class | why |
|---|---|---|---|
| #248 | proactive answers a note in the home room, not the room it was born in | **S1** | the exact incident §2 already cites for S1 |
| #160 | proactive pass reads memory 3-5x when the prompt already has the answer | **S2** | over-routing — calling a tool the input didn't need |
| #227 | denied having a site, capitulated to a bare assertion, volunteered internal page names | **S3** | the exact incident §2 already cites for S3 |
| #77 | a fact verdict can cite only one source, corroboration silently discarded | **S3** | an assertion's grounding silently weakened |
| #168 | `silence.md` doctrine never reaches the `tag` occasion prompt | **S4** | context that should reach her does not |
| #144 | the gate reasons from a hand-written capability list, not the real tool registry | **S4** | reasoning on stale/wrong context about herself |
| #239 | `<tag_message>`/`<replying_to>` carry no `untrusted` marker | **S9** | exactly the shape S9 measures |
| #182 | the coordinate-leak detector counts decimal literals and a re-encoding defeats it | **not S1-S9** | a disclosure/redaction-effectiveness failure — leakage FROM her, not injection INTO her; §9 does not have a slot for this |
| #71, #100, #64, #5, #169, #10, #82, #244, #240, #314, #172, #59 | (12 issues) | **N/A** | store-type discipline, analytics pipelines, CI/tooling, docs, code organization — no live turn is involved at all |

**The result, read straight: of the 8 that are turn-behavior issues at all,
S1/S2/S3/S4/S9 each got 1-2 hits and S5/S6/S7/S8 got ZERO** — in particular
**S7, which is 75 of the CURRENT 150 cases (half the suite), produced zero
of eight real incidents in this sample**, while S2 and S4 — near-zero
corpus coverage — together account for half the real incidents found. This
is the same shape §2 already claimed, found independently by a different
method, which is what "the distribution matches" would have needed to look
like to refute it. It did not.

**Two things this run adds rather than confirms:**

1. **n=8 is small** — this is a spot-check, not a re-derivation of §2's
   count, and a different reasonable read of the four N/A-leaning issues
   (#71, #100, #169, #182) could move one or two of them into S8 or a new
   class. The headline (S7 over-covered, S2/S4 under-covered) is robust to
   that; the exact 1-2 counts per class are not.
2. **#182 names a tenth failure shape this map has no slot for**: content
   LEAVING her that should not (a redaction/disclosure-effectiveness gap),
   distinct from S9's untrusted-content-ENTERING-her. Not scoped in here —
   named so it is not silently absorbed into S9 by a future reader looking
   for the closest fit.

## 9. S4 — built, 2026-09-19

`evals/backbone.py` gained `NORA_BLOCKS_EMPTY` and `ContextGrader`. It reads
`nora.agent.prefill.build_prefill`'s own `nora.prefill.blocks_empty` span
attribute — already computed and annotated in production, not a new
instrument — via a small independent OTel exporter (`_prefill_capture`),
because `StrandsInMemorySessionMapper` (what `TracedHandler` would give you)
drops any span that is not `chat`/`execute_tool`/`invoke_agent`, and
`agent.prefill` is Nora's own plumbing span, not a model-invoke one.

Verified against the real `build_prefill()` directly (a `TagContext`, no
DB/network — it is a pure function): a seeded-empty block reads empty, a
seeded-non-empty one does not. `tests/unit/test_eval_backbone.py` pins
`ContextGrader`'s grading logic in isolation.

**One inherited claim corrected along the way**: `03-step0-results.md`'s
"`<people_in_window>` is not rendered at all (`fire.py:1122`)" is stale —
that line is a threshold guard today (`_window_speakers`), and
`_people_in_window_block`/`FirePath._recall_people` both cite issue #210 as
already fixed. Not re-litigated here since `10-the-scenario-map.md` §2 never
repeated the claim, only `03-step0-results.md` did.

Not done: actual `must_render` cases (hand-written `Case` objects per entry
point, per `GOAL-2-backbone.md`'s "no corpus format work" rule) — the
grader and signal exist, the cases are the next piece.

## 10. S5 — built, 2026-09-19, and the `gate` premise was false

**`RelevanceGate` has no clock seam at all.** `SubjectSpec.now` threads into
nine `build_dependencies` components — `FirePath` among them, confirmed:
`fire.py` reads `self._clock().astimezone()` and passes it into
`build_prefill(now=...)`, which reaches `_transcript_block`'s
`render_messages(..., now=clock)`. `RelevanceGate.decide()` is not one of
the nine: it calls `render_context(transcript, standing, window=...)` with
no `now=` argument, so `render_context` falls through to
`datetime.now(UTC)` — the real wall clock, always, regardless of what a
case's `SubjectSpec.now` states. So §3's "deterministic, on `SubjectSpec.
now`" was true for `participation`/`tag` and false for `gate` — this table
row is now corrected rather than routed around, per this bundle's own rule
for a refuted premise.

**Built for the entry points where the premise holds.** `evals/backbone.py`
gained `render_transcript_at` (a pure task calling the REAL
`nora.domain.blocks.render_messages`/`nora.domain.prompt.age` directly — no
`Subject`, no database, no network, since the computation itself is a pure
function of rows and a clock) and `SubstringGrader` (a per-case `Contains`:
the SDK's own `Contains` fixes one `value` at construction, so it cannot
vary the expected age string across a suite of cases that each expect a
different one; `Case.expected_output` carries it instead).

Verified against the real functions: a row 181 minutes old renders
`age="3h ago"`, one minute old renders `age="just now"` — `nora.domain.
prompt.age`'s own boundaries (`< 90s` "just now", `< 5400s` minutes,
`< 172800s` hours, else days). `tests/unit/test_eval_backbone.py` pins both,
plus a deliberately wrong expectation failing rather than passing silently.

**Not done, and it is a real gap rather than an eval-harness one**: giving
`RelevanceGate` a clock seam so it CAN be driven with a frozen `now` (which
would let S5 — and any case needing a reproducible transcript age — run
through the actual `gate` entry point rather than only through
`render_messages` directly). Production has no live need for this (real
deployment's "now" is always the wall clock), so it is purely a
testability gap, not a bug — naming it rather than silently building around
it, per this bundle's own discipline.

## 11. S6 — built, 2026-09-19, and it needed plumbing no other class did

Applies to `dreamer` only (`nora.subject.dreaming.dream`, both adaptors it
can select) — nothing to do with participation/gate/tag. Unlike every other
class here, S6 needs the SAME `Subject` driven **twice**: idempotence asks
whether the SECOND pass sees exactly what the first left, so a fresh
`obtain()` per run (every other case's isolation) would defeat the check
rather than perform it.

**`Spec` gained `repeat: int`.** `_drive()` calls `port.drive()` once as
before, then — only if `repeat > 1` — again on the SAME subject and the SAME
`now_ms`, no re-seed between. `NORA_WRITTEN`/`NORA_CHANGED_COUNT`/
`NORA_STAGED_COUNT` now carry the FIRST run's `Outcome.written`/
`len(.changed)`/`len(.staged)` — never surfaced to any grader before this,
for any entry point — and the `_2` siblings carry the repeat run's. `dream()`
already diffs its own notebook `before`/`after` per call (confirmed by
reading `nora/subject/dreaming.py`), so `written`/`changed` are deltas and
the repeat run's OWN values already answer "did run 2 add anything." `staged`
is the one exception, an absolute snapshot rather than a diff, so
`IdempotenceGrader` compares both runs' counts rather than reading the
second alone.

**Why a custom grader and not the SDK's `StateEquals`.** Checked first, per
this bundle's own rule against rebuilding what ships: `StateEquals(name=...,
value=...)` can pin ONE named state to a fixed value, which covers
`written_2 == 0` and `changed_2 == 0` on their own — but it cannot compare
TWO different named states against each other (`staged` before vs. after),
and it sets no `label`, so a bare `StateEquals` failure would count as
`ERROR` by this bundle's own `read()` (an unlabelled output), not the `FAIL`
it actually is. Neither gap is worked around; `IdempotenceGrader` exists for
both.

`tests/unit/test_eval_backbone.py` pins the grader logic directly (pure,
`EvaluationData` built by hand) — including the exact bug S6 exists to
catch (#236/#338: a second pass that wrote something new must FAIL, not
silently pass) and the staged-snapshot case (nothing written, but the
staged count moved, must still FAIL).

**Verified against a real live `dream()` pass, 2026-09-20.**
`note_candidates.yaml`'s `p1_group_dinner_date_set_venue_open` (a real
`create` case), driven twice (`Spec(repeat=2)`) against the deployed `nora`
alias, real local Postgres, `notes.enabled`/`notes.candidates_enabled`
overridden on (off by default — `entry.py`'s `dreamer` config only turns on
`[dreamer]`, not the note-candidates adaptor specifically, a gap this run
found and worked around rather than one `IdempotenceGrader` needed to know
about). Real result: run 1 `written=1`; run 2 `written_2=0,
changed_count_2=0, staged_count_2=0, error_2=None` — `IdempotenceGrader`:
**`PASS`, "the repeat pass added nothing."** Creating the same note twice
correctly wrote it once. Combined wall time for this run and S2's above:
~17s (see §5's own "is the suite fast enough serially" question — one real
data point, not a measurement of it).

## 12. `no_trigger`/`near_miss` — the "four copies" finding was already fixed

Re-verified 2026-09-19 against the actual current corpus files and the real
`nora.tag.is_tagged`/`strip_code_spans`/word-boundary logic (pure string
functions, no network/DB): loaded both YAML files, substituted `{trigger}`/
`{trigger_split}` exactly as `evals/rebuilt/runner.substitute_trigger` does,
and for each case checked whether disabling EITHER the word-boundary rule or
the code-span-strip rule alone would flip it from silent to tagged.

**Every one of the 16 depends on a real, distinct mechanism today:**

| case | depends on |
|---|---|
| `leading_word_char_glued`, `trailing_word_char_glued`, `underscore_glued_both_sides`, `trailing_digit_glued`, `name_inside_a_longer_word`, `near_miss_trailing_word_char`, `near_miss_leading_word_char` | the word-boundary rule |
| `inside_a_code_span`, `inline_code_span`, `fenced_code_block`, `near_miss_code_span` | the code-span-strip rule |
| `not_the_trigger_underscore_variant`, `missing_dot_typo`, `space_inside_token_mid_word`, `near_miss_broken_spacing` | neither special rule — these are LITERAL, documented in-file as deliberately not templated (a misspelling/whitespace-break of the trigger's own shape, which cannot be expressed as `{trigger}` substitution without producing nonsense — the files' own comments name this exactly) |
| `plain_no_mention` | nothing — the intentional trivial control, not a rule case |

The four cases the withdrawn `06-first-full-run` measurement named
(`norax`, `nora1`, `` `nora` ``, `Sonora`) do not exist under those names
today. `no_trigger.yaml`'s own header dates the fix: the **2026-09-18
`{trigger}` SUBSTITUTION** rewrote the corpus from hardcoded literals (where
four cases silently lost their trigger content) to rendering against the
CONFIGURED trigger, and that fix evidently landed before this bundle's own
§5 was written, which cited the pre-fix measurement without re-checking it —
exactly the "late reversal not landed until every quoting file changes"
failure mode, this time on the READING side rather than the code side.

**Better than a one-off probe: this is already a standing CI guard.**
`tests/architecture/test_the_near_miss_corpora_exercise_their_rules.py`
pins exactly this property — deletes each matching rule in turn from a live
copy of `nora/tag.py`'s source and re-scores, with the identical exemption
list this section derived independently (`plain_no_mention`,
`space_inside_token_mid_word`, `near_miss_broken_spacing`,
`not_the_trigger_underscore_variant`, `missing_dot_typo`). Green today (4
passed). §5's "drop the four copies" instruction predates knowing this test
existed and already covered the case.

`inside_a_code_span` and `inline_code_span` ARE a real, live redundancy
worth naming even though neither is a bug: both are structurally the same
near-miss (trigger wrapped in a single-backtick inline span), differing only
in their trailing sentence. Not touched here — a call about which one (if
either) to drop belongs with whoever next touches this corpus for content
reasons, not as a side effect of re-verifying a different, now-closed
finding.

## 13. S2 — built, 2026-09-19, and it needed no new grader at all

`evals/backbone.py` gained `traced_nora_task(spec)`, the whole of it:
`eval_task(TracedHandler())(nora_task(spec))`. `TracedHandler.after()` does
`processed.setdefault("trajectory", session)`, and `nora_task`'s dict never
sets that key, so this ADDS a `Session` onto the exact same `output`/
`environment_state` shape every other grader in this module reads — S2 and
S7 (`AbsenceGrader`) can grade the SAME driven case. No custom evaluator:
`ToolCalled`/`ToolSelectionAccuracyEvaluator`/`ToolParameterAccuracyEvaluator`/
`TrajectoryEvaluator` ship in the SDK and read `actual_trajectory` directly,
and `09-the-backbone-measured` §1 already measured a real `ToolExecutionSpan`
in a real `participation` case's trace.

**One thing checked and corrected along the way, not left standing wrong**:
`_prefill_capture()`'s own docstring (§9, S4) claimed it could not coexist
with `TracedHandler` in the same process — OTel's `TracerProvider` can only
be SET once, and the assumption was that whichever helper set it first would
own it exclusively. Verified empirically, both construction orders: it was
wrong. `add_span_processor` is additive, and `StrandsEvalsTelemetry.
__init__` falls back to reading the already-registered provider when its own
`set_tracer_provider` call is refused, so both exporters end up on the ONE
shared provider and both receive every span. Corrected in the code comment
directly (`evals/backbone.py`); this bundle's own "no claim without a tool
call" rule caught its own earlier claim.

**Verified mechanically at first, then live, 2026-09-20.** `traced_nora_task`
was exercised against a deliberately unreachable database — `_drive()`
raises inside `obtain()`, `nora_task`'s own catch turns that into the usual
ERROR-shaped dict, and the wrap survives it. `tests/unit/
test_eval_backbone.py` pins this, falsified by reverting.

**Then run for real**: `participation.yaml`'s `stalled-question-known`
(real `knows.transcript`/`notes`), driven through `traced_nora_task` against
the deployed `nora` alias over the SSH tunnel, real local Postgres. Real
result: `traces: 1`, `spans: [InferenceSpan, ToolExecutionSpan,
InferenceSpan, ToolExecutionSpan, InferenceSpan, ToolExecutionSpan,
InferenceSpan, AgentInvocationSpan]` — the SAME retry-loop shape
`09-the-backbone-measured.md` §1 measured before this bundle even had S2's
glue code. `ToolCalled(tool_name="send_reply").evaluate(...)` on the real
`Session`: **`test_pass=True`, "tool 'send_reply' was called"`.** S2 is
confirmed end to end, not just wired: a real driven case, graded by the
SDK's own unmodified evaluator, correctly.

## 14. S8 — SKIPPED, 2026-09-19, and one of the reasons given was wrong

Checked before building, not routed around: `strands_evals.chaos.
ChaosPlugin` only works by being passed to `Agent(plugins=[chaos])` **at
construction time** — that is how Strands' plugin/hook system works, no
seam around it.

**CORRECTED 2026-09-19, by the adversarial audit that reviewed this
decision.** This section originally claimed `grep -rn "plugins=\[" nora/`
returns nothing repo-wide, and concluded nothing passes `plugins=` to a real
`Agent` today. **The grep is accurate; the conclusion drawn from it is
false** — `nora/agent/factory.py`'s `create_agent` (the production tag-turn
agent, `nora/app/agent_runner.py`'s own caller) builds
`plugins: list[Plugin] = [AgentSkills(...)] if skills_dir is not None else
[]` and passes `Agent(model=model, plugins=plugins, ...)`. The grep pattern
missed it purely because the list is bound to a variable first — a grep
standing in for "does the property hold" is not a measurement of it, the
exact substitution this bundle's own discipline exists to catch, caught
here by someone else checking rather than by writing this section.

**What actually holds, checked properly:** `create_agent` already has a
live `plugins=` parameter and a real one flowing through it — but it is
hardcoded to build exactly one kind, `AgentSkills`, from `skills_dir`. There
is no seam for an EXTERNALLY-SUPPLIED plugin like `ChaosPlugin` to reach it
without changing `create_agent`'s own signature and its caller in
`nora/app/composition.py`. And `nora.subject.SubjectSpec` — the seam this
entire bundle is built on — still REFUSES to be a collaborator-injection
point by design; its own docstring: *"NOT a seam for collaborators... there
is no way to express `{"media_join": None}` and that is the point."*
`overrides` does already give indirect influence over which plugin gets
built (`cfg.doctrine.dir` decides `skills_dir`), which is a real caveat on
"no seam at all" worth naming even though it does not amount to arbitrary
injection.

So building S8 as `strands_evals.chaos` designs it still means adding a NEW
parameter to `create_agent`/`composition.py` that lets something reach in
and inject an ARBITRARY execution-time hook into her tool pipeline — a
different, larger thing than the one hardcoded plugin that already exists —
and that is still exactly what `GOAL-2-backbone.md`'s own "what would STOP
this" section names: *"Anything that would change what the live deployment
does."* `GOAL-2-backbone.md`'s scope statement also puts chaos explicitly
**OUT** ("OUT: ... Chaos") — this was never in the backbone goal to begin
with; §4's recommendation to build it is this LATER node's own analysis, not
something the backbone owed. The skip decision survives the correction; the
reasoning it was made on did not, until now.

**Presented to the operator as three options** (skip; add a narrow eval-only
plugin-injection seam; find a lower-risk substitute by wrapping tool
implementations before Agent construction, unchecked) — **operator chose
skip.** S8 stays at zero coverage. Revisit as its own goal, with its own
scope decision about whether a chaos-testing seam is worth the boundary
cost, rather than as a line item inside this one.

## 15 — the corpus port, done, 2026-09-20

**Operator override.** `GOAL-2-backbone.md`'s own scope statement puts
porting the 150 cases **OUT** ("porting the 150 cases against the new map is
separate work, not done here" — §7 above says the same), and left it
reserved for the operator to decide when. Asked directly whether to keep it
deferred or do it now, given item 4 (sampling) had just landed: **operator
chose full port now.**

**What was built**, in `evals/scenarios.py` and `evals/backbone.py`:

- **`load_corpus(name)`** — every `evals/corpora/*.yaml` file, as
  `strands_evals.Case` objects, reusing the existing seeded content
  unchanged (no corpus-format rewrite; "the port is mechanical" held). Every
  field the old harness's own `Case` read (`room`/`now`/`policy`/`config`/
  `knows`/`expect`/`category`) rides in `Case.metadata` under the same
  names.
- **`MeetsGrader`**, delegating to `evals.rebuilt.domain.Outcome.meets()`
  rather than reimplementing its eleven-vocabulary logic
  (`go`/`no_go`/`speak`/`silence`/`reply`/`no_reply`/`create`/`update`/
  `none`/`either`/`remind`) a second time. Used UNIFORMLY across all 13
  corpora — no per-corpus grader selection. `AbsenceGrader` (S7's own,
  already shipped) is unchanged; `MeetsGrader` is a strict superset,
  needed for S1/S6's `create`/`update`/`remind` and S3/S9's judge verdict.
- **`Spec`/`_drive()` gained per-case `room`/`now`/`policy`/`config`**,
  read off `Case.metadata`, falling through to `Spec`'s own experiment-wide
  defaults when a case states none. Necessary because `proactive_decide.
  yaml` names a real room and a real `now` PER CASE — which room she picks
  IS the S1 assertion — which one `Spec` shared across a whole `Experiment`
  batch could not express.
- **`Spec.judge`**, wiring `evals.rebuilt.grading.Judge` into `_drive()` —
  called while the subject is alive, mirroring `run_once()`'s own call —
  for S3 (grounding) and S9 (injection), which need a judge's opinion, not
  just a structural signal.
- **`experiment_for(name, database_url=..., judge=...)`** — the per-corpus
  glue: `load_corpus` + `MeetsGrader`, threading a judge only into the five
  corpora that need one (`JUDGED_CORPORA`: `being_contradicted`,
  `identity_grounding`, `source_scope`, `injections`,
  `transcript_injection`).

**Two real bugs found live, both latent since the spine was first built,
neither reachable by any hand-written S4/S5/S6/S2 case this session wrote**:

1. `_as_nora_case()` dropped a case's `category` entirely.
   `evals/rebuilt/entry.py`'s `dreamer` reads `case.category or
   "note-candidates"` to pick which adaptor a pass runs — every ported
   `category: proactive` case silently ran against an adaptor the graph
   never built.
2. `_drive()`'s policy passthrough never rewrote the policy template's own
   `workspace = "..."` to the ISOLATED workspace name (the same
   substitution `run_once()` already does) — so the composed subject's
   real `honcho.workspace_id` and its policy's declared room workspace
   disagreed, and the proactive adaptor reported "no home room" for a
   workspace whose policy plainly named one.

Both fixed, regression-tested, and falsified mechanically; both then
confirmed fixed live.

**Live-verified, real DB + the deployed `nora` alias over the SSH tunnel**:
a `no_trigger` case (PASS), a `proactive_decide` case (ran to completion —
FAIL on content, not on the mechanism — the room/policy/category plumbing
all worked), a `participation` case (ran to completion), a `relevance_gate`
case (PASS). Not live-verified: the five `JUDGED_CORPORA` (a real judged
run costs ~211s/sample × 3 samples/case per `evals/rebuilt/grading.py`'s
own measurement — expensive enough that a full live judged pass is its own
follow-up action, not folded into this one) and `note_candidates`/
`proactive_act` beyond what §11/§13 already live-verified.

**§5's fold/drop decisions, applied:**

- `no_trigger` (12) and `near_miss` (4) — kept as is, per §12's
  re-verification.
- `trigger` (12) — kept, graded the same as everything else via
  `MeetsGrader`; no separate "smoke check" carve-out was needed once
  grading stopped being per-corpus.
- `relevance_gate` (40) — needed no case-count reduction: `MeetsGrader`
  grades `go`/`no_go` off `Outcome.admitted` deterministically (no judge
  call for the GATE's own verdict, matching `JUDGED_CORPORA` correctly
  excluding it), which is exactly the "deterministic go/no_go" shape §5
  asked for — the shape was already right, only the grading mechanism was
  new.
- `transcript_injection` (1) — classified as S9 alongside `injections`
  (same `JUDGED_CORPORA` entry, same `entry: tag`), not merged into
  `injections.yaml` as a file — the fold is a classification, not a
  content rewrite.

**Not done here:** an actual full live run of all 150 cases (cost/time,
per the judge-cost note above); deleting `evals/rebuilt/` (its own decision,
`GOAL-2-backbone.md`'s stated release condition — the port existing does
not by itself mean the old harness's *report/rubric/CLI* plumbing is safe
to delete without checking who else still calls it directly).

## 16 — `evals/rebuilt/`'s trim, 2026-09-20, and the premise was wrong

**The premise, corrected before acting on it.** "Delete `evals/rebuilt/`"
(GOAL-2's own release condition) reads as "delete the package once the port
replaces it." Checked against the actual import graph before touching
anything: `evals/backbone.py` and `evals/scenarios.py` import
`entry.py`/`domain.py`/`grading.py`/`judge.py`/`rubric.py` from this package
DIRECTLY — `MeetsGrader` delegates to `domain.Outcome.meets()`, `_drive()`
routes through `entry.ENTRY_POINTS`, `Spec.judge` wires `grading.Judge`. None
of that is "old," all of it is load-bearing for the spine this session just
finished. Deleting the package would have broken the port on the same
commit that finished it.

**What was actually dead**, checked by grep against every caller (tests
included) before removing anything, not assumed from the file's name:

- `evals/rebuilt/__main__.py` (the old CLI) and `evals/rebuilt/report.py`
  (its renderer) — nothing outside each other referenced them once
  `python -m evals` existed. Deleted.
- `runner.py`'s `run_once`/`run`/`load`/`_case`/`_check_the_corpus_
  assertions`/`_scrubbed`/`substitute_trigger`/`_source` — **NOT deleted**:
  `tests/integration/test_two_entry_points_one_runner.py`,
  `tests/unit/test_the_judge_can_remove_a_pass.py`, and
  `tests/architecture/test_the_near_miss_corpora_exercise_their_rules.py`
  all drive these directly, on purpose, to test properties the new spine's
  own unit tests do not cover the same way (an architecture-level generality
  claim, a corpus-assertion function's own edge cases, DSN-scrubbing). A
  function with a real caller is not dead code because its package's name
  says "rebuilt."
- `tests/integration/test_the_rebuilt_suite_runs.py` — one of its five tests
  (`test_a_run_with_no_model_reports_incomplete_and_fails`) called the now-
  deleted `render()` to check the run's OVERALL pass/fail text; its
  substantive assertions (per-entry-point `gradable`/`errors`/`hits`) never
  needed `render()` at all and are unchanged. `test_every_corpus_loads` was
  dropped as redundant with `evals.scenarios`'s own count test. The other
  three tests (proactive-path induced-failure guards) needed no change —
  they never touched `report.py`.
- `Makefile`'s four `eval-*` targets — migrated from
  `python -m evals.rebuilt evals/corpora/<name>.yaml` to
  `python -m evals <name>`. Found and dropped along the way:
  `eval-note-candidates`'s `DREAMER_DIR`/`EFFORT`/`PER_FIRE` conditional
  flags named CLI options that do not exist in EITHER the old or the new
  `__main__` — a pre-existing, unrelated drift, not something this port
  introduced or broke.
- `docs/runbook.md`'s operator-facing CLI example — updated to
  `python -m evals`. `docs/eval-sop.md` was found to already be stale from
  an EARLIER supersession (it still shows `python -m evals.run --suite`,
  deleted 2026-09-18, predating even `evals.rebuilt`) — out of scope for
  this trim, not touched.

**`evals/rebuilt/__init__.py`'s own docstring corrected** to match: six
modules, not four; no "lives beside the old one until Step 5" framing (this
package IS the surviving one now).

**What this means for GOAL-2's release condition**: there is no longer a
"delete the package" condition to satisfy, because that was never the right
description of what needed to happen. The condition is closed by this trim,
not deferred further. Full suite: 7967 passed (one fewer than before this
node — the one redundant test dropped).
