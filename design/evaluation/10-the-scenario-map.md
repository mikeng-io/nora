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
| **S5 time** | that a dated question is answered against the case's clock | deterministic, on `SubjectSpec.now` for `participation`/`tag`; **`gate` has no clock seam at all** — see §9 | no |
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
work against `strands_evals.chaos`.

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
