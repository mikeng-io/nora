# The scenario map — what this suite must measure, derived from what has broken

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
| **S2** | **Wrong or absent tool routing** | #297 the adversarial path has no tools at all; #189 the debrief clips every tool result uncounted; #226 the note tier's vocabulary spoken into the room | **0** — `Outcome` carries no tool calls |
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
| **S2 routing** | which tool, with what arguments, in what order | `ToolCalled`, `ToolSelectionAccuracyEvaluator`, `ToolParameterAccuracyEvaluator`, `TrajectoryEvaluator` — **all reachable**, `09-the-backbone-measured` §1 found `ToolExecutionSpan` in the trace | no |
| **S3 grounding** | whether a claim is supported by what she read | `FaithfulnessEvaluator`, `CorrectnessEvaluator` + the confidence rubric | no |
| **S4 context reaching her** | that the blocks she needs were rendered non-empty | deterministic, on the prompt — **not a model call**, and cheaper than one | no |
| **S5 time** | that a dated question is answered against the case's clock | deterministic, on `SubjectSpec.now` | no |
| **S6 idempotence** | that a second identical pass writes nothing new | `StateEquals` on counts, run **twice** | partly |
| **S7 speech / silence** | spoke vs stayed out, and which | **`AbsenceGrader`** (`evals/backbone.py`) — no SDK built-in | **yes** |
| **S8 failure is loud** | a broken tool produces a visible, bounded failure | **`strands_evals.chaos`** + `FailureCommunicationEvaluator`, `PartialCompletionEvaluator`, `RecoveryStrategyEvaluator` | partly |
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
  **8 of these 16 inert**: they passed with both of `nora/tag.py`'s matching
  rules deleted, and four contained no trigger at all, being copies of
  `plain_no_mention` wearing four rule names. A case that passes against
  deleted code measures nothing. **Drop the four copies; re-derive the rest
  against the rules they name.**
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
  this bundle's own `AbsenceGrader`) can grade a gate case, and
  `AbsenceGrader` itself needs a fix first — see the same §1.1 for why it
  currently misgrades every gate case regardless of the SDK question.

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

## 7. DECISION RESERVED

**Proposed:** adopt S1-S9 as the scenario classes; build the four deterministic
classes first; port only what §5 says earns its place; treat S8 as new work
against `strands_evals.chaos`.

**Not taken here.** It changes what the suite is for, the existing corpora are
the operator's, and the numbers in §2's last column are a count of cases rather
than a judgement of them.

## 8. The reading that would refute this node

§2's table claims the coverage is mismatched to the failure record. **Take
twenty closed issues at random from the last three months and classify each
into S1-S9.** If the distribution matches the case counts, this node is wrong
and the corpora were tracking reality all along.

The classification in §2 was done by hand over one pass of the issue list, and
it is the weakest thing here.
