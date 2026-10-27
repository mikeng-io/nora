# 11 — The sandbox rebuild: one Nora, rebuilt from production, run isolated

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


**Status: §1.2's rule and §7.1's three states are BUILT (2026-09-21, see
§1.3). §2–§6's replay/rebuild machinery is not.**

## 0. The rule

> **Every case runs a COMPLETE Nora, rebuilt from production data by
> production's own code, into isolated storage. No tier is missing, no tier is
> authored, and no case is assembled differently from any other.**

One rule, no exceptions, nothing per-case. If a tier cannot be rebuilt, the case
does not run — it is never quietly assembled without it.

**The rule is not an optimisation to be justified case by case. It is the only
defensible default, because the thing that would license any exception cannot be
measured.** See §1.1.

> **Amendment, 2026-10-15 (design/unified-memory step 0a): a second assembly,
> chosen per CORPUS.** A corpus in `evals.scenarios.FULL_RESTORE_CORPORA`
> (today `conversational_recall` alone) is seeded by `sandbox_as_of`, every
> restorable store as of the instant with its own ids, instead of
> `sandbox_database` + `sandbox_from_snapshot`'s four tiers, and its subject
> composes with the presence READER on (`evals.subject_config.
> FULL_RESTORE_OVERRIDES`: no collector, no geocoder, R-31 untouched). Why:
> the memory journeys recall a picture's stored reading, a note's revisions
> and the Rollup's outings, which the snapshot path does not reconstruct, so
> on it those cases could not run at all. The choice is the corpus's, never a
> case's: every case of such a corpus is assembled the same way, every other
> corpus keeps the snapshot path byte for byte
> (`tests/unit/test_eval_full_restore.py`), and the rule above still binds
> within each. Honcho is still not replayed (§3): both assemblies start each
> draw on an empty workspace, which the corpus declares.
>
> **And its rooms have members (2026-10-15, DEC-312).** The sandbox has no
> signal-cli daemon or Instagram bridge, so every roster read answered
> `available=False` and every lineage tool (`where_been`, `rollup_read`) said
> `unavailable`: a Nora who could not look, which production is not. A
> full-restore subject now reads each room's members from the restored
> `messages` rows: the people who spoke there in the 90 days before the
> instant (`nora.subject.sandbox_roster`, `SubjectSpec.recorded_rosters`). The
> divergence is declared both ways: a member silent for 90 days is missing (a
> subset, which lineage already treats as safe), and one who spoke and has since
> left is present.
>
> **And on the graph surface its memory index is built (2026-10-16, design
> unified-memory 65 V3).** The index (`memory_node`, `memory_edge`, the
> queue, the state row) is DERIVED, so it is never restored
> (`nora.infra.sql.history.NOT_RESTORED`), and the blank template holds none:
> every graph-surface sandbox, on either assembly, read an empty index, and
> every `memory_search` answered `partial` with nothing (PR #32's
> `tool_selection` run). Now, when the arm is the graph surface
> (`--memory-index`, or the deployment's own flag), each sandbox, after it
> is seeded and before the drive, is projected by production's `rebuild`,
> then given the source's vectors where a node's embedded input is identical
> (`nora.subject.sandbox_index`, `memory_index.adopt_worker_state`): a vector
> is a function of the model and its input alone, so the copy equals
> re-embedding, and a node edited after the instant gets none. The worker
> is never the subject's (`obtain` composes none), so no sandbox writes a
> vector through the live embedder; the subject queries it at its loopback
> publish (`EMBEDDER_FROM_HOST`, compose's `embedder_host`). Fail-closed: the
> run is refused before any draw when the embedder does not serve the
> configured file or the source holds under 0.95 of its owed vectors
> (a clone older than 0106, or a backfill still running), and a draw is
> refused (ERROR) when its sandbox holds under 0.95 of the source's coverage.
> Each sandbox prints its coverage. A node the copy misses stays owed, so a
> search over its scope answers `partial`, as production does for a node its
> worker has not reached.

### Private Persona writer stimuli (2026-10-19)

`persona_identity` is a synthetic source-grounding experiment, not a historical
reconstruction or a reply-routing score. Its six committed contracts have no
invented anchors and remain visibly unanchored without `NORA_EVAL_PERSONA_CASES`.
The private file input supplies an existing real room/instant, a roster person,
explicitly invented source arrivals and a declared read clock. The loader counts
all six as `persona_sources` COUNTERFACTUAL, restricted to this corpus. The source
snapshot is unchanged background; no private source text is committed as history.
The subject appends the invented arrivals using the roster's actual account, and
seeds held birthday state through the canonical append-only Persona write API.
A later stimulus clock is explicit in `sandbox_from_snapshot`; the ordinary
exact-anchor clock guard remains unchanged. This exception measures the six
invented vectors requested by design/unified-memory/75, and does not license
synthetic edits to any other anchored corpus. Procedure: `evals/PERSONA_IDENTITY.md`.

## 1. What this replaces, and why twice was not enough

> **Re-baseline, throughout this document (2026-09-25):** a re-baseline is the before arm of the next confirming run, never a standalone run. See the nora-eval skill, "An eval confirms a fix; it never finds one".


Two earlier drafts of this document both failed the same way, and the failure is
worth recording because it is the suite's own historical disease.

- **Draft 1** proposed a per-case `requires:` listing which tiers a case depends
  on, filling only those. It was justified by a count: 24 of 150 cases declare a
  `standing:`, 126 do not.
- **Draft 2** kept one assembly but accepted Honcho as "a uniform divergence,
  stated rather than hidden."

Both are the same mistake at different sizes: deciding, per tier or per suite,
that something production ALWAYS supplies may be absent here. A case assembled
without standing is not a case that does not need standing — it is a case
running a **different prefill than production**, measuring a different Nora,
with the difference invisible in the report. That is exactly what the rebuild
exists to end: the previous suite's cases were each patched together their own
way and collectively measured nothing coherent.

Draft 2's Honcho concession was also simply **wrong on the facts** — see §3.

But the deeper error is Draft 1's, and it is not that the count was wrong. The
count is accurate and it is **not evidence of anything about sensitivity**.

## 1.1 Why sensitivity cannot be measured, and what follows

"24 of 150 declare a `standing:`" measures what the cases' AUTHORS WROTE. It does
not measure what changes the output. Those are different propositions, and no
experiment available here converts one into the other:

- **The negative is unfalsifiable.** Running the suite with a block present and
  absent and observing no difference establishes nothing. It is n samples of a
  STOCHASTIC process, on the m inputs that exist today, under one model, one
  doctrine and one instruction revision. Absence of observed effect is not
  absence of effect. (This suite has already measured its own noise floor: an
  identical `proactive_decide` prompt scored 0.20–0.50 recall at n=5, and a
  40-case gate suite scored 0 to 2 errors run to run on an unchanged build.)
- **The scope is open, not closed.** Even a sound result for today's 150 cases
  says nothing about the 151st, about a new input, or about the same input after
  a doctrine change. A subtraction justified once would have to be re-justified
  on every change to anything — which is the same as never being justified.
- **The judgement is semantic.** Whether the room's standing changes how a line
  should be read is not arithmetic. It is the model reading a whole context.
  Every block is part of that reading by construction.

So the asymmetry is total: **adding a tier that turns out not to matter costs a
model call. Omitting a tier that does matter silently produces a number that
looks exactly like a valid one.** One is a bill; the other is a false reading
that nothing downstream can detect.

This is why R1 (§6) is unconditional rather than evidence-gated. There is no
evidence that could gate it. A rule requiring proof that cannot exist is a rule
with no exceptions, which is the only kind that cannot drift.

**Consequence for this document's own history:** every sensitivity count
produced during its drafting — "24 standing-sensitive", "0 culture-sensitive",
"0 of the 54 converted cases are standing-sensitive" — is hereby retired as a
basis for any decision. The last of those was used to retract a warning that the
54 cases converted on 2026-09-21 assemble without standing. **The retraction was
wrong and the warning stands** (§7).

## 1.2 The subject itself diverges, and that is upstream of every tier

Seeding the right context into the wrong Nora measures nothing. Measured
2026-09-21, the eval subject is not the production subject:

**Config.** `nora/subject/obtain.py:92` builds every subject from
`config.example.toml` — the repo's EXAMPLE — not from the deployment's
`config.toml`. **65 values differ.** Not trivia:

| | example (what every eval ran) | production |
|---|---|---|
| `llm.model` | `ollamacloud/deepseek-v4-flash:0731` | `nora` |
| `llm.reasoning_effort` | `low` | `none` |
| `relevance_gate.enabled` / `.shadow` | **False** / **True** | True / False |
| `participation.enabled` | **False** | True |
| `standing.enabled` | **False** | True |
| `culture.enabled` | **False** | True |
| `notes.enabled` | **False** | True |
| `dreamer.enabled` / `.model` / `.reasoning_effort` | **False** / flash / `none` | True / `nora-dreamer` / `low` |
| `search` `online` `weather` `route` `preview` | **all False** | all True |
| `doctrine.canary` | `CHANGE-ME-PER-DEPLOYMENT` | the real canary |

So every case to date evaluated a DIFFERENT MODEL at a DIFFERENT REASONING
EFFORT, with the relevance gate off and shadowed, with participation, standing,
culture, notes and the Dreamer disabled, and with most tools switched off.

This also explains a finding blamed on the entry point: `RelevanceGatePolicy` is
never wired not only because `entry: tag` omits the flag, but because
`relevance_gate.enabled` is False for the whole suite.

**Policy.** `default_policy()` emits one room with `persons`/`unit` and NO
per-room flags. Production's `policy.toml` carries 6 conversations with
`relevance_gate = true` on 3, `home_room = true` on 3, `voice = true` on 2. So
even with the gate enabled in config, the isolated room is not opted in.

**The per-case `config:` overrides are the symptom.** 22 cases set
`notes.enabled` and 8 corpora set `engagement.trigger.text` — cases compensating
by hand for a baseline that was wrong underneath them. Under the rule below they
all disappear.

### The rule

> The subject composes **the deployment's own config and the real room's own
> policy entry**. The ONLY permitted differences are the isolation keys: the
> room id, and the unit/workspace. Nothing else is settable, per case or
> per corpus.

An example file is by definition not production and drifts from it by design, so
it cannot be the baseline. The config is COPIED from the deployment into the
sandbox workdir per run, and the run records its digest — so what a number was
produced under is re-derivable rather than assumed.

**Consequence to settle (operator).** Production has
`engagement.trigger.enabled = false` — the text trigger was disabled
2026-09-01. `trigger.yaml` (12), `no_trigger.yaml` (12) and `near_miss.yaml` (4)
assert the behaviour of a mechanism production no longer runs. Under this rule
they do not merely change config; they test a path that does not exist in the
deployment. What those 28 are for is a question about the corpus, not about the
harness.

### 1.3 What landed 2026-09-21 — one path, isolated by database

**Built.** `evals.backbone._drive` has ONE path. Every case names a real room
and a real instant (`snapshot: {platform, conversation_id, before_ms}`) and
nothing else about what she knows. There is no `knows:`, no `config:`, no
`policy:`, no `room:`, no `now:`; `evals.scenarios.load_all` refuses them by
name. The three states of §7.1 are live: `census()` counts active / parked /
unanchored per corpus and `python -m evals` prints them beside every result.

**Isolation is by DATABASE, not by room.** The previous design shared one
database across cases and minted a hashed room per case (`room_for`,
`workspace_for`, `sandbox_room_for`). That is gone. Each case composes
against a sandbox database of its own, cloned from a blank migrated template
BESIDE the source (`nora.subject.sandbox.sandbox_database`, the same
`CREATE DATABASE … TEMPLATE` the test suite already used per test), and is
dropped when the case ends. So:

- the room is the REAL room's own id, and the deployment's `policy.toml` and
  `identity.toml` are copied VERBATIM — no swapped entry, no Discord-shaped
  minted id, no one-person roster;
- the unit and the culture key are RESOLVED from the room's policy entry at
  `obtain()` (`Subject.unit`, `Subject.culture_key`) and are not a case's to
  state. An earlier check that `unit == honcho.workspace_id` is deleted: the
  deployment itself fails it on every room, because `honcho.workspace_id` is
  Honcho's per-deployment workspace and a unit is per room;
- the subject's clock is frozen AT `before_ms` and every seeded row keeps its
  real `ts`. Nothing is shifted. The clock is a port (`01-boundaries` §3), so
  the shift the earlier sandbox needed is no longer needed;
- **the source is the deployment's clone**, `nora_eval` on the deploy host
  (`scripts/refresh_eval_clone.sh`, `make eval-source`), opened read-only.
  Production is never opened by the suite, and `python -m evals` refuses a
  source whose name is not a clone's (`*_eval`).

**Honcho** is the one store a database does not isolate. `_baseline()` points
`honcho.workspace_id` at a fixed `<deployment>-eval` sibling — stable, not
per case, so §3's replay can one day fill it. Until then every sandbox's peer
card is EMPTY. That is a stated tier gap, not a hidden one.

**SUPERSEDED 2026-09-24 (#462), see §1.9.** The paragraph above was the
design, and the shared sibling did not hold it: every draw's ingest fed it and
every later draw read it back, so peer cards were not empty, they were every
earlier eval's. Each draw now gets a Honcho workspace of its own, created
empty; the sibling remains only as the default for a subject composed outside
a draw.

**What a case may still say about its input.** `input` is the line that
arrives at `before_ms`; `person` is who says it (a `person_id` the roster
knows — the room's `persons` gate would otherwise refuse the line silently);
`mention: true` marks it as @-mentioning her, structurally
(`NormalizedMessage.mentions`). With the text trigger off since 2026-09-01,
a mention IS how the inbound path engages her, so every `tag` case expecting
a reply states one. Discord's adapter is off in the sandbox, so those cases
anchor to Signal rooms.

**Decided here, where the operator question above was open.** The
trigger/no_trigger/near_miss cases keep their inputs (a trigger-shaped line
is still what arrived); the reply-expecting ones engage her by mention.
What they measure is therefore "a mention with this text gets a grounded
reply" and "trigger-shaped text with no mention gets silence", both of which
the deployment does run.

**Still empty in the sandbox** (the extraction is `snapshot.py`'s four tiers):
reminders, note candidates, debounce/window state, Honcho. A `proactive_act`
case, which fires a due reminder row, has no anchor until reminders join the
extraction; those cases are parked P-A with that return condition. R1 is not
yet enforced mechanically — a case whose tier is empty runs — because the
extraction cannot yet tell "empty then" from "not extracted"; the counts in
this section are what to read until it can.

### 1.4 The first live run, 2026-09-21 — what it measured and what it found

Run on the deploy host, against the clone, with the deployment's own
environment. **The machinery works end to end**: the blank template is built,
a sandbox database is cloned per case, the real room is reconstructed at the
named instant, the deployment's own model answers, and the case is graded.

**Two wiring defects, both found by running and both fixed.** Neither could
have been found by a test, because both are properties of the DEPLOYMENT's
configuration and the repo's tests compose from the example:

1. **`settle()` waited for the process to be idle.** All four `near_miss`
   cases ERRORed with "the turn never settled". Composition spawns ENDLESS
   loops into the same `BackgroundTasks` set a turn is spawned into —
   `ComputeJobPoller.run()`, `VersionAnnouncer.run()`, `heartbeat.run()` —
   and the deployment enables the first. Waiting for that set to reach zero is
   waiting for the process to exit. It now waits for the DELTA the case added.
2. **A turn cannot run in a room whose adapter the sandbox disables.**
   Three `participation` and two `proactive_act` cases anchored in Discord
   rooms and died with a `KeyError` from `fire.py:1615`: `FirePath._turn`
   resolves the room's adapter before speaking. `EntryPoint.drives_a_turn`
   now carries the fact, `_drive` refuses by name, and an architecture test
   catches it before a run.

**The numbers are n=1 and are NOT a rate.** Recorded so the next run has a
baseline, and for no other purpose:

| corpus | passed | failed | errored |
|---|---|---|---|
| near_miss | 4 | 0 | 0 |
| no_trigger | 12 | 0 | 0 |
| trigger | 12 | 0 | 0 |
| injections | 5 | 0 | 0 |
| relevance_gate | 17 | 2 | 0 |
| participation | 6 | 4 | 3 |

The `participation` errors are the Discord cases above; `proactive_act`'s is
the same fault, and both are fixed rather than carried.
| note_candidates | 14 | 4 | 1 |
| proactive_act | 4 | 5 | 1 |
| proactive_decide | 4 | 1 | 0 |

**Read the failures with §1.1 in hand.** Twelve of the eighteen are the same
direction — she acted or spoke where the case expects silence — and there are
two explanations this run cannot separate:

- the behaviour is real, and it is the FIRST honest reading these cases have
  had: every earlier run had `participation`, `relevance_gate`, `standing`,
  `culture`, `notes` and the Dreamer switched OFF by the example config;
- the anchors do not carry the cases' premises. Every one was chosen in a
  single automated pass on 2026-09-21, and a case asserting "nothing is open
  here" is exactly the kind whose premise a newly-chosen window can fail to
  hold.

**Separating them is a measurement, not a reading.** `--sample N` on the
failing subset, and a human reading of a handful of anchors. This suite has
already measured its own noise floor at 0.20–0.50 recall on an identical
prompt at n=5; treating any of the table above as a behavioural verdict would
be the exact error §1.1 exists to refuse.

**Three `participation` cases became UNANCHORED rather than parked.** Their
premise exists only in a Discord room, so the adapter divergence blocks them
— and that divergence is a seam THIS REBUILD OWES, which §7.1 rejected as a
parking reason in as many words. They load, run and ERROR until a Discord
room can hold a turn. Proposed as P-A parkings and corrected, which is the
same correction §7.1 records being made once before.

**One error is a production-shaped reading rather than a defect.**
`p7_cantonese_only_solo_plan` came back `unavailable` with a
`PartialReplyError` — the deployment's own per-call `max_tokens`, which its
Dreamer runs under too. It is one sample of one case; a ceiling is never sized
on n=1.

### 1.5 The counterfactual state — DECIDED 2026-09-22 (operator)

A fifth state, and the first deliberate hole in §0's rule. It exists because
the culture arm's `-chronicle` case cannot be a real moment: a chronicle and
an account are the SAME FACTS IN TWO REPRESENTATIONS, and no room ever held
both at once. The operator ruled the case stays.

**Why it earns the hole, and this is the opposite of the argument for
dropping it.** "An account beats a chronicle" is the premise
`design/culture-tier` is BUILT ON, not a question it asks — which was the
case for dropping the arm. It is the better reason to keep it: a premise
nobody has measured is the one most worth measuring. If it comes back null
the finding is about whether the tier should EXIST, and that is a finding
worth being able to get.

**What makes the hole safe is the COUNT, not the argument.** A counterfactual
is a manipulation, not a reconstruction. `census()` reports it as its own
number beside total/active/parked/unanchored, and never folds it in, because
an aggregate that averaged the two would let §0's rule erode by ARITHMETIC
rather than by anybody deciding to relax it. A corpus with none says nothing
about the state, so the column cannot make it look ordinary.

**The state holds ONLY what cannot be reconstructed.** The same arm's
`-deviant` case is NOT in it: another real room's REAL account, attached
here, is a declared swap of real data and stays anchored. Putting it here too
would be the cheap economy that makes the number stop meaning anything — one
arm in the hole, not two.

A case states `counterfactual: {varies, why}` beside a real `snapshot:`, and
`evals/scenarios.py` refuses: a counterfactual with no anchor (that is
authored content, which this corpus has no path for), a `varies` outside a
CLOSED set (`culture`; `transport` since 2026-09-24 — see §1.7), a missing `why`, and a case that is both
parked and counterfactual (a parked case does not run, so varying a tier in
it asserts nothing). Widening the set is an edit to that file, which is the
point.

**Three controls, one hole.** W3 of the culture arm inverts its expectation
(`-nocult` silence, `-account` speak), which guards presence-versus-content
structurally; `-deviant` guards it by content using real data; `-chronicle`
tests the representation claim the tier is named for. Only the last costs the
rule anything.

### 1.6 What is owed, and what it is waiting on (2026-09-22)

Written down because three sessions were working on this at once and two of
them ended. Anything below that is not started is not started by ANYONE; the
peer sessions that raised these items are gone, and their branches, not their
memories, are the record.

| owed | waiting on | who raised it |
|---|---|---|
| the four culture arms + the owed `blocks_empty` test | the block, on `mikeng-io/culture-step5-production` — they land in ONE merge, never separately (#294 reverted #284 for shipping the block alone) | culture session |
| S2 wiring — a traced task through `experiment_for`, the SDK trajectory evaluator, expected tool in `metadata` | nothing. `traced_nora_task` EXISTS and has one caller: its own unit test | tool-catalogue session |
| tool-selection cases, incl. a no-tool-at-all case | S2 wiring above. Anchor the pairs against the catalogue branch — the `where_is` line only reads "RIGHT NOW" there, and master's still says "was last seen", which is the defect rather than the fix | tool-catalogue session |
| the tool-result seam (§7.2) — 13 unanchored cases | its hook is in `nora/agent/`, the catalogue work's own area | this bundle |
| the Discord adapter divergence — 3 unanchored cases | an adapter present without a live `GET /users/@me` | this bundle |
| R1 made mechanical | an extraction that can say a tier is EMPTY rather than UNREAD. Reminders landed 2026-09-22; Honcho and note candidates have not | this bundle |

**The owed `blocks_empty` test must pin BOTH directions.** A room whose culture
head was folded before the instant means `culture` is NOT in
`nora.prefill.blocks_empty`; a room with no head means it IS. Asserting only
the first passes on a build where the block is unconditionally present — a
different defect that fails the same way, which is what #294 was about.

**One baseline caveat for whoever re-measures.** `[culture] enabled` is one
flag with two effects and has a READ side as of that branch. Only
`participation.yaml` is affected — the tag assembler contains `culture` zero
times and `FirePath` gates the read on `kind == "participation"` — so the
re-baseline is one corpus of thirteen, not the whole suite. Take it on that
branch so before and after are one measurement.

### 1.7 The transport tier is HELD, not reconstructed (2026-09-24)

`<presence>` (design/self-awareness/02) renders each network's live
`health()` and her own live-read handle. Measured on the first presence
probe: the sandbox rendered Signal and Instagram «reconnecting» (their
listeners never start), Discord and WhatsApp «not running in this
deployment» (forced off, §1.2), and no Instagram handle (the bridge does not
resolve from the host). Production, read back off Langfuse the day before,
rendered «Every one of them is up right now». That is a prompt the deployment
does not send (`01-boundaries.md` §5(4)).

Health at an anchor's instant is recorded nowhere — the same class as the
`presence.` location feed. So `nora.subject.transport` HOLDS it on the one
reader (`Dependencies.self_presence`): every network the deployment's own
`config.toml` enables is `up`, Instagram's live handle stands in at the value
production last rendered (`evals/situation.py`, dated), and a case that needs
anything else varies `transport` as a counterfactual. The held adapters sit in
the reader's map only, never the registry, so no send is rerouted.

### 1.8 The judge's source names who said what (2026-09-24, #458)

`Sandboxed.transcript` is the SOURCE every judged corpus grades against
(`evals.backbone._drive` joins it into `judge.grade(source=...)`). Until
#458 it was one message BODY per line: no speaker, nothing marking her own
rows. The judge is told to fail any attribution the data does not link, so
a reply that correctly said «you asked that twice yesterday» was graded a
fabrication — measured re-judging the presence corpus, where every safety
FAIL or REVIEW on the ordinary-question case gave that reason.

It is now the window as SHE was shown it: `nora.subject.sandbox.judge_source`
reads the sandbox's rows back through the store her turn reads, joins their
media as every transcript builder does (#116), names them
with `Persons.name_transcript`, and renders them with
`nora.agent.prefill.render_transcript` — the body of `<group_transcript>`,
extracted so there is one renderer, not a judge's copy. One `<m>` element per
row, `from=` the identity's name (her own rows under `display_name`, decided
by the flag), `age=` against the moment's clock. No account id is rendered;
the judge is a third-party call and R-18 applies to it.

**Judged numbers from before and after this change are NOT comparable.** Every
judged corpus's verdicts move — attribution checks that could not pass now
can, and ones that passed on an unlabelled line may not. Re-baseline each
judged corpus on a build that has this before comparing an arm against it;
never set a pre-#458 judged number beside a post-#458 one.

**Residual, closed 2026-09-25 (#459).** The source WAS still EVERY row up to
the instant (`snapshot` is unbounded), while her turn is shown only the
newest `context_window`, plus the quote chase (`nora.domain.quote_window`).
So the judge could credit a line she was never shown.

Closed by binding the judge's source to the window her turn actually
rendered, READ BACK FROM THE TURN rather than re-derived from
`context_window`: `evals.backbone._shown_transcript` scans the driven turn's
own captured `chat` spans — the same mechanism `_shown_now` already used for
the clock line — for the `<group_transcript window="N">…</group_transcript>`
block `nora.agent.prefill._transcript_block` puts in front of the model, and
hands its exact body to `_judge_source` in place of `Sandboxed.transcript` —
byte-identical to what she read, proven (not merely claimed) by
`tests/unit/test_tool_route_grader.py::
test_shown_transcript_decodes_the_real_strands_serialized_span`, which
round-trips a realistic window through the real renderer, the real
`strands.telemetry.tracer.serialize`, and this function, and asserts
equality.

**The span carries JSON, not plain text (review, 2026-09-25).** Strands
writes a model call's messages onto its span through
`json.dumps(…, ensure_ascii=False)`, so a real body's newline is the two
characters `\`/`n`, not one, and its quotes are `\"`. Matching the block
pattern over that raw attribute value found `</group_transcript>` a line
early on a multi-row window and left a stray escaped newline at each end of
the captured body. `_shown_transcript` now `json.loads`s each attribute
value first and walks every string leaf of the decoded structure
(`_strings_in`/`_texts_in`) before searching — legacy and latest GenAI
message conventions both nest the real text somewhere in that tree, and
walking rather than naming the shape is what keeps this from breaking
silently on the next convention change. It needed no new seam through
`nora.subject` or `nora.domain`: the render was already observable off the
spend-capture exporter `_drive` builds for every other `NORA_SHOWN_*` value.

**The gate renders its own `<group_transcript>` too (review, 2026-09-25).**
`nora.ingest.relevance_gate.RelevanceGate` builds one at its own read bound
(40, `transcript_limit`) on its own `chat`-operation span — it is a raw model
call, not an "entry point that renders none". `[participation]` and
`[relevance_gate]` are one subsystem, so a `participation` drive can carry
the gate's admission call BEFORE the turn's own reply call, both `chat`
spans — the earliest-span-first scan would otherwise hand the judge the
GATE's window, read at a different bound, answering a different question.
`_shown_transcript` is scoped to spans reachable from the subject's own
`invoke_agent` span (`_under_agent`, the same mechanism `_model_calls(...,
agent=...)` already used for per-turn spend): the gate's raw call "never
constructs an agent" (its own docstring) and so has no such ancestor,
excluding it by construction. Currently no JUDGED corpus drives
`participation`, so this was not yet observed live, but the seam is general
and a future one would have hit it silently.

**Blind is not fail-closed (review, 2026-09-25).** `Judge.grade` still
answers with `source=None` — "meaningful with or without source data",
DEC-75 — which is correct for a case that carries no source BY DESIGN (an
injection case) but is indistinguishable, to the judge, from a source that
should have existed and did not because the capture broke. `evals.backbone.
_drive` now checks: a turn that SPOKE always ran `build_prefill`, which
renders `<group_transcript>` unconditionally, so `shown_transcript is None`
on a judged, spoken draw is the CAPTURE breaking, not a legitimately
blockless entry point. That draw's `Outcome.error` is set instead of calling
the judge at all — `Outcome.meets()` reads any `error` as the third answer,
never PASS or FAIL, so it ERRORs, counted and visible in the run's report,
rather than spending a judged draw on a blind verdict nobody can tell apart
from a grounded one.

`Sandboxed.transcript` itself is UNCHANGED and still unbounded — it is what
`sandbox_from_snapshot` seeds the sandbox with, read back before the turn
runs, and the turn has not happened yet at that point. The bound is applied
downstream, in `evals.backbone._drive`, once the turn's own render exists to
read back from.

**Judged numbers from before and after this change are NOT comparable
either**, for the same reason #458's were not: a source that no longer
contains a line she was never shown changes which attribution checks can
pass. Re-baseline each judged corpus again on a build that has this fix
before comparing an arm against it.

### 1.9 Honcho is isolated per DRAW (2026-09-24, #462)

**What was wrong.** §1.3 said every sandbox's peer card was EMPTY. The shared
`<deployment>-eval` workspace made that false. Ingest feeds every arriving
line and her reply into Honcho (`memory_feed`), the deriver turns them into
conclusions, and the tag path reads them back as `<peer_history>` and
`<self_card>`. So draw 3 of a case read what draws 1 and 2 had said, ranked by
similarity to the question being asked again, and answered «你問咗第三次喇».
The judge, grading against a window with no earlier ask, called that a
fabrication. Draws were correlated, two arms running at once read each
other's notes, and `miles_novak` (the default `person`) had a card built from
every corpus any session had run. Read back on the probe below: Nora's
representation of `miles_novak` in the shared workspace opened with «miles_novak asked
about splitting 3480蚊 among 4 people», three copies, each from an earlier
draw of the very case being probed.

**What it is now.** `evals.backbone._drive` opens
`nora.subject.memory_workspace.draw_workspace` around each draw:

- a workspace named `<deployment>-eval-<run>-<case>-<draw>-<nonce>` is
  created, marked `nora_eval_draw` in its metadata, BEFORE the subject is
  composed, and the subject composes against it (`honcho.workspace_id`,
  applied last so no `Spec` override can redirect it). A create that fails
  is an ERROR: a draw with no workspace of its own is not isolated;
- it is deleted when the draw ends, raising or not, after the subject is
  closed and its background work cancelled. Sessions first, because Honcho
  answers 409 to a workspace delete while a session is active;
- it is created with Honcho's derivation OFF (`configuration.reasoning.enabled
  = false`, which also stops peer cards and dreams, and `summary.enabled =
  false`). A draw can never read what the deriver would derive from it, since
  derivation lags minutes to tens of minutes behind a feed, so those tasks were
  pure load on the Honcho production's memory runs on. Read back live
  2026-09-24: 13 feeds into such a workspace enqueued zero work units, and a
  seeded conclusion (`conclude`, written directly) still came back through
  `representation`;
- `python -m evals` sweeps, at start, any marked draw workspace older than six
  hours: what a killed run left behind;
- nothing creates, deletes or composes against a workspace outside
  `<deployment>-eval[-…]`. `build_config` refuses it on the composed config and
  the admin refuses it before any request, so production's `nora` cannot be
  reached from this path.

The composition root builds ONE `HonchoClient`, from `honcho.workspace_id`,
and no caller passes a `workspace_id=` to a verb, so the config IS every read
and write. `tests/integration/test_eval_draws_isolate_honcho.py` holds that on
a composed subject against a fake shaped after Honcho's OpenAPI.

**Measured on the deploy host, `tool_selection` `route_calculate` at
`--sample 3`:** three workspaces appeared, one per draw; each draw's prefill
rendered `self_card`, `peer_card` and `peer_history` EMPTY, draw 3 included.
Create cost 9–12 ms per draw and the delete call 20–46 ms: noise beside a
turn. The delete is ACCEPTED at once and carried out by Honcho's own queue,
behind the deriver's backlog, so a workspace can stay listed for TENS of
minutes after its draw: measured ~40 minutes on 2026-09-24 (accepted
13:53–13:54Z, gone 14:30–14:34Z, behind a 43-task production backlog, on a
build that still let draws derive). Nothing reads it in that time.

**Numbers from before and after this change are NOT comparable** for any
corpus whose entry point reads memory: every `tag` corpus (trigger,
no_trigger, near_miss, injections, transcript_injection, being_contradicted,
identity_grounding, source_scope, tool_selection, presence), `participation`
and `proactive_act` through the fire path's cards, and `dreamer` corpora
wherever a pass calls a memory verb. Re-baseline on a build that has this
before setting an arm against it.

**OPEN, not decided here: §4's replay workspace per anchor.** §4 wants one
rebuilt workspace per anchor, shared by every case at it. A draw WRITES into
its workspace, so a shared replayed workspace would bring #462's correlation
back. When replay is built it has to land in each draw's own workspace (a
replay per draw, or a copy, which Honcho's API does not offer), and §4's cost
argument has to be re-made on that basis. Replaying into a draw workspace
also needs derivation ON for the replay's own feed, so the derivation-off
setting above is right for today's empty workspaces and has to be revisited
with it.

**First baselines after the fix (2026-09-25, master `3c93cfc7`).** Run on the
deploy host from a fresh `git archive` of master, sequentially, with
`--sample 3 --judge --judge-samples 1 --reasons` and no `--deployment-config`,
so production's `[llm] defer_tools = true` is what they measure. These replace
the pre-fix numbers in #444 (`tool_reporting`) and #452
(`being_contradicted`) as the reference; the two are not comparable.

| Corpus | Draws | Pass / fail / ERROR | Pre-fix, for direction only |
|---|---|---|---|
| `tool_reporting` | 8 cases × 3 | 24 / 0 / 0 | 22/0/2 and 22/2/0 (#444) |
| `being_contradicted` | 5 graded × 3 (+1 unanchored) | 6 / 4 / 5 | 5/7/3 and 4/7/4 (#452) |

The "you've typed this N times" replies that #452 graded as invented history
did not appear in any draw: the log holds no such line. All 42 draws created
their own workspace and none was left listed afterwards. The 5 ERRORs are
judge timeouts at 300 s, not verdicts on her; n=3 is a direction, not a rate.

### 1.10 The source and the template are SHARED, and a run must never migrate either (2026-09-25)

**What was wrong.** `nora.subject.sandbox._blank_template` called
`run_migrations` on `<source>_blank` unconditionally, every time it was not
already cached in the calling process. `<source>_blank` (`nora_eval_blank`)
is not this run's — it is the ONE template every session on the host clones
its sandboxes from (§1.3). A run from a branch whose migration head was
ahead of it (`0065_standing_working_set`, on `mikeng-io/standing-working-set`
— #541, not yet merged) upgraded the shared template to that head.
Every OTHER session's run — from master, at an older head — then failed at
seeding with `Can't locate revision '0065_…'`: `alembic upgrade head` has to
find the database's CURRENT revision in its OWN script directory to plan a
path from it, and master's script directory has never heard of a revision
only the ahead branch defines. Fixed by hand with a downgrade at the time.

**What changed.** `nora/subject/sandbox.py`:

- `_blank_template` upgrades the shared template only the FIRST time it is
  ever created (nothing depends on it yet), or leaves it untouched when it is
  already at the calling process's own head. Any other relationship — ahead
  or behind — is a DIVERGENCE: a throwaway PRIVATE copy
  (`<template>_run_<nonce>`) is cloned from the shared one at its own,
  unmodified revision, migrated to the process's head, and dropped by
  `close_private_blank_templates` when the run ends (`evals.__main__.main`'s
  `finally`) — one copy per RUN, reused by every case in it, the same shape
  the per-case sandbox already uses for its own drop, one level up.
- `refuse_if_source_diverges_from_head` refuses the whole run, before any
  case, if the READ-ONLY source's alembic revision differs from the
  checkout's head, in either direction. The source cannot be migrated through
  this module at all (`create_readonly_sql_engine`), but a branch whose
  migration adds a column `snapshot()` selects would otherwise misread an
  older source deep inside a case, with a message naming a column rather
  than a reason. Chosen over teaching `snapshot()` to read only the columns
  an old source has (with defaults for the rest): one comparison with no
  exceptions is smaller and cannot itself be wrong about which gap is safe.
- `shared_database_versions`/`assert_shared_databases_unchanged` are an
  independent, second check, wired into `evals.__main__.main`: both shared
  databases' alembic versions are recorded before anything runs and
  re-read after, success or failure, and a run whose OWN two rules above
  somehow did not hold ERRORs loudly rather than reporting a clean exit.

Tests: `tests/unit/test_sandbox_shared_database_guard.py` (offline, a fake
engine and mocked Alembic calls — a branch-ahead-or-behind run never issues
`run_migrations` against the shared name), `tests/unit/
test_eval_main_shared_db_guard.py` (the CLI wiring: refusal order, the
guard surviving a raising case, never folding a caught mutation into `rc`),
and `tests/integration/
test_shared_eval_database_survives_a_branch_ahead_run.py` (two real
Postgres databases, revert-proved: the shared one is provably unmoved by
`_blank_template`, and provably moved by calling `run_migrations` on it
directly — the exact call the old code made).

**Review, same day: three follow-ups, all in the same shape.**

1. **`refuse_if_source_diverges_from_head` refused too much.** "In either
   direction" refused every ordinary run from master too: production's clone
   trails its own deploy by however long between deploys, so a routine
   `source at 0063, branch at 0064` (an index-only migration) refused
   EVERY run, not just an ahead branch's. Narrowed to refuse ONLY when the
   source is AHEAD — `nora.infra.sql.migrations_runner.revision_is_known`
   answers this with no database connection: this repo keeps ONE linear
   alembic chain, so a revision the checkout's own script directory
   recognizes at all is, by construction, an ancestor of head, never a
   sibling branch. A source BEHIND head now runs; `nora.infra.sql.snapshot.
   snapshot` is the fail-closed second layer, catching an asyncpg/psycopg
   `UndefinedColumnError`/`UndefinedTableError` (SQLSTATE `42703`/`42P01`)
   from any of its five reads and re-raising the SAME
   `SourceRevisionMismatchError` (moved to `nora.infra.sql.migrations_runner`
   so both raisers — `nora.subject.sandbox` and `nora.infra.sql.snapshot` —
   can share one type without `nora.infra.sql` reaching upward for it),
   naming both revisions and `make eval-source`.
2. **A `kill <pid>` (SIGTERM) skipped the `finally`.** Neither
   `close_private_blank_templates` nor `assert_shared_databases_unchanged`
   ran, and a leaked `nora_eval_blank_run_*` outlived the process — which
   `scripts/refresh_eval_clone.sh`'s own sweep did not know to look for
   either (it only swept `_sb_%` and the bare `_blank`). Fixed three ways:
   `evals.__main__._raise_on_sigterm` turns a SIGTERM into an ordinary
   `SystemExit` so the `finally` runs like any other exception path; the
   refresh script's sweep now also matches `_blank_run_%`; and
   `nora.subject.sandbox.sweep_stale_private_blank_templates` runs at every
   `python -m evals` start, dropping a leaked private copy ONLY when it is
   both older than a grace period (`STALE_PRIVATE_BLANK_AFTER`, six hours —
   the same reasoning `nora.subject.memory_workspace.STALE_AFTER` uses) AND
   holds no connection in `pg_stat_activity` right now — the name itself
   carries its creation instant (`_blank_template` now builds it from
   `nora.subject.memory_workspace.new_run_id`, not a bare uuid), since
   `pg_database` has no creation-timestamp column to read instead.
3. **Both a case's own error and a caught shared-database mutation must
   surface.** They do, with no code change needed: Python chains an
   exception raised inside a `finally` onto whatever was already
   propagating (`__context__`), so the mutation (which fires last, and is
   what `main()` re-raises) carries the case's own error as its context
   rather than discarding it. Pinned in `tests/unit/
   test_eval_main_shared_db_guard.py::
   test_a_case_raising_and_a_shared_mutation_both_surface`.

### Where the suite runs — DECIDED 2026-09-21: on the deploy host

Not a preference. Measured: with the deployment's features on, composition
**refuses to start** outside its environment.

| blocker | why it cannot be faked |
|---|---|
| `NORA_DATA_HOST_ROOT` | a HOST-side docker bind-mount path. `[compute]` refuses to start without it, because an in-container path "silently resolves to an empty host directory" |
| Discord `GET /users/@me` | the adapter authenticates for real at startup; a placeholder token is HTTP 401 |
| signal-cli `127.0.0.1:18080` | unreachable off-host; self-id silently falls back |
| Honcho `host.docker.internal:8000` | same — every peer write fails |

Beyond startup, a long tail DEGRADES rather than fails: `route`, `search`,
`online` and `image_gen` each warn "enabled but $KEY is unset" and
carry on with the tool unavailable. **That degradation is a divergence**:
production's Nora holds `web_search`; a subject composed without its key does
not, and a case whose turn would have reached one measures a Nora missing it.

The mechanism is `os.environ.setdefault` in `_baseline()`, and it is ONE
composition path in two environments rather than two paths: on the deploy host
the real values win and the tools are production's; anywhere else placeholders
fill in, composition still succeeds, and the tools degrade. The repo's own
integration tests keep working on a laptop for exactly this reason — they test
the harness rather than evaluate Nora.

`NORA_DATA_HOST_ROOT` is the interesting one: outside a container there is no
mount, so the host path IS the local path. The workdir is not a placeholder
there — it is the correct answer.

**A run states where it ran.** `python -m evals --deployment-config` defaults to
the deployment's own `config.toml`; `materialise()` rebuilds
`config.evaluation.toml` from it before any case and REFUSES when the two
disagree outside `ALLOWED_TO_DIFFER`. A missing deployment config stops the run
rather than silently falling back to the example.

### A declared divergence that is not yet closed

**Discord is disabled in the sandbox.** Signal's adapter is neutralised by a
placeholder account and stays enabled as the deployment has it; Discord's cannot
be, because it resolves its own identity with a live `GET /users/@me`.

The cost is real and is stated rather than hidden: the registry's
`self_aliases()` feeds `self_ids`, which `MentionPolicy`/`ReplyToNoraPolicy`
match on. With Discord off, a Discord room's mention-and-reply engagement cannot
fire — and since the text trigger was disabled 2026-09-01, mention-and-reply IS
the deployment's engagement path. No corpus exercises it today, so nothing is
measurably wrong yet. A mention/reply corpus on Discord is blocked on closing
this.

## 2. Every tier has a source. There are no exceptions.

A tier is either RECOVERED (history recorded it) or REPLAYED (production's own
producer regenerates it from the recovered transcript). Nothing is authored.

| tier | mode | source |
|---|---|---|
| transcript | recovered | `messages` (2026-08-05→) |
| notes | recovered | `note` + `note_revision` (2026-09-07→) |
| standing | **replayed** | `standing_event.Wiring` — `composition.py:1815` |
| culture | **replayed** | `CultureAdaptor` — `composition.py:3208` |
| peer card / memory | **replayed** | `MemoryStore.feed()` — `ingest/stages/memory_feed.py:47` |

Recovery has a floor: `standing_revision` begins 2026-09-20 14:13 (4 rows, one
room) because `0059` deliberately did not backfill, and the pre-0059 schema held
only the current value — there is nothing to backfill FROM. `culture_revision`
begins 2026-09-17. Those floors are why standing and culture are replayed rather
than recovered, and replay has no floor: it needs only the transcript, which
goes back to 2026-08-05.

## 3. Honcho is replayed, not conceded

Draft 2 called Honcho unrecoverable because it holds no point-in-time history
and its representations drift. The first half is true and the conclusion does not
follow: **you do not snapshot Honcho, you replay it**, exactly as with standing.

Production's own write path is one ingest stage:

```python
await memory.feed(peer_id=..., text=..., conversation_id=..., metadata={"ts": ts})
```

Every field it needs is on a recovered transcript row — `person_id` → peer,
`body` → text, the room, and `ts`. The read path back into the prompt is
`peer_card()` (`prefill.py:538`). So a rebuild feeds the real transcript through
the real stage into the isolated workspace and Honcho derives its own peer cards
from it — the same producer on the same data.

A representation cannot be written directly, only derived. That does not matter,
because of §4: the rebuilt WORKSPACE is the frozen artifact, not a file
extracted from it.

## 4. The rebuild is the artifact: pre-built, content-addressed, frozen

The unit is not a per-tier file. It is **one rebuilt workspace per anchor**,
which every case at that anchor shares.

- **Pre-built.** Constructed ahead of any run, never lazily during one. Replay
  costs model calls (the fold) and derivation time (Honcho); paying it per run
  makes a case's input vary per run, and the suite measures noise instead of
  behaviour — the `proactive_decide` recall-at-n=5 finding.
- **Content-addressed.** Keyed by the anchor, so sharing is by construction
  rather than coordination.
- **Frozen.** Built once, reused thereafter. A changed objective or model does
  not silently rebuild; a human moves it.

```
anchor-key = sha256(platform | conversation_id | before_ms | folded_to_ms)[:16]
```

```
evals/corpora/rebuilds/
  _index.yaml         # anchor-key -> room, instants, built_at, digests, provenance
```

The index row carries what makes the rebuild re-derivable and falsifiable:

```yaml
anchor_key: <sha256[:16]>
platform: signal
conversation_id: "<real room>"
before_ms: <T2>                 # the transcript window
folded_to_ms: <T1>              # T1 <= T2; where the folds were replayed to
transcript_digest: "<sha256 of the rows replayed>"
producers:
  standing: {agent: standing.event_fold, model: "...", instruction_sha256: "...",
             fold_batch: <n>, rollup_max_folds: <n>, fold_count: <n>}
  culture:  {agent: "...", model: "...", instruction_sha256: "..."}
  memory:   {peers: <n>, fed: <n>}
built_at: "<ISO-8601>"
```

`transcript_digest` is the falsifiable binding: re-read the same rows and get the
same digest, or the anchor no longer denotes what was built.

Cost scales with **distinct anchors, not cases** — one agent already reused 3
anchors across 33 cases.

## 5. The one knob: staleness is a timestamp

`before_ms` is the transcript window. `folded_to_ms` is where the folds were
replayed to, and `T1 <= T2`.

`T1 == T2` (the default) gives standing current with the room. `T1 < T2` gives
standing that lags it — not exotic: production folds every `fold_batch`
messages, so standing ALWAYS trails the newest lines. That is what
`a10_the_transcript_outranks_her_own_summary` is about, and it is expressed by
choosing T1, not by hand-writing a stale summary.

This is a property of the anchor. It is not a per-case choice about which tiers
to inject, and it is the only dial a case turns.

## 6. Refusals (fail-closed)

Every one is an ERROR. An ERROR is never a PASS.

| # | condition | why |
|---|---|---|
| R1 | a case assembles with ANY tier empty | the rule: no case runs a reduced prefill |
| R2 | `folded_to_ms` > `before_ms` | folded from messages the room had not yet said |
| R3 | a rebuild's `transcript_digest` ≠ the rows read now | the anchor no longer denotes what was built |
| R4 | a referenced anchor-key is absent from `_index.yaml` | a typo reads as an empty tier |
| R5 | any producer in `producers:` is missing its model or instruction sha | unprovenanced, so not re-derivable |
| R6 | a replay reports fewer peers fed than the transcript has speakers | a silently partial memory rebuild |
| R7 | a case states any window, limit or cap of its own | a tier PRESENT but REDUCED is the same divergence as one absent |

**R7 names a defect already in the tree.** `snapshot:` as shipped on
2026-09-21 accepts a per-case `transcript_limit:`. Production does not choose a
window per message — it reads one from config per entry point
(`cfg.relevance_gate.transcript_rows`, `composition.py:2871`). A case setting its
own is choosing an assembly production never chooses, which is R1's disease
expressed as a number rather than an absence. The knob must be removed from the
case surface; the window is whatever the composed config says for that entry
point.

This generalises: **completeness is not only about which tiers are present, but
about every tier being assembled the way production assembles it.** Any future
per-case knob over assembly is the same defect wearing a new name.

**R1 is the hinge**, and it needs no per-case declaration precisely because it is
unconditional — which is what makes quiet divergence impossible. It turns
"assembled a stripped room and passed" into "refused to run."

## 7. What this costs, stated honestly

- **All 150 cases need a rebuild**, not 24.
- The 54 cases converted 2026-09-21 currently assemble without standing, and most
  without culture or memory. Under R1 they refuse to run until rebuilt. That is
  the correct verdict, not a regression.
- A rebuild costs the fold's model calls plus Honcho derivation latency, once per
  distinct anchor.
- Hand-authored cases cannot satisfy R1: nobody hand-writes a realistic standing,
  culture and peer card per case. **R1 therefore forces every case onto an
  anchor.** A case whose scenario never occurred in a real room has no anchor,
  and is not convertible — it is deleted or it waits for the room to produce the
  moment. There is no third option that preserves the rule.

## 7.1 Parking (operator decision, 2026-09-21)

R1 forces every case onto a real anchor. A case with no anchor cannot run. It is
PARKED rather than deleted — removed from the active set, keeping its full
content, carrying a reason and the condition that would bring it back.

**Three states, and the middle one is the point:**

| state | meaning | under R1 |
|---|---|---|
| active | anchored and rebuilt | runs |
| unanchored | in the corpus, no anchor yet | **ERRORs** — work to do, visible |
| parked | out of the active set, reason + return condition stated | not run, counted |

"Unanchored" must ERROR rather than skip. A case quietly skipped for lack of an
anchor is the same false reading as one assembled without a tier: the report
reads clean while measuring less than it claims.

**Parking is not a wastebasket.** Two distinct reasons, and conflating them
would hide real work behind a word:

- **P-A — no such moment exists.** The scenario cannot occur in a real room:
  adversarial probes, and controlled structural variables organic chat does not
  produce. Returns if and when the room produces the moment.
  - `relevance_gate`: `forge/*` (6), `closed/*` (6), `world/*` (3),
    `real-commitment-in-standing` (1)
  - `transcript_injection` (1)
**P-B is NOT a parking reason (operator decision, 2026-09-21).** It was proposed
as one and rejected, correctly: a case blocked on a seam THIS REBUILD OWES is
not parked, it is unfinished. Parking it would retire 13 cases to make the
rebuild look complete — the cost-optimisation this document exists to refuse.

  - `identity_grounding` (8), `source_scope` (4), and the `being_contradicted`
    case seeding a `[search result]` line put tool output into
    `knows.transcript` — production has written that shape into `messages`
    **0 times**. A tool result reaches the model through the agent loop's
    tool-result channel, not as room speech.
  - They are therefore UNANCHORED and ERROR under R1 until the TOOL-RESULT SEAM
    exists, and the seam is in scope here (§7.2).

*(An earlier draft argued the seam mattered because room speech is "untrusted".
That is wrong and the code says so: `untrusted="true"` was REMOVED by #314, and
the blocks now hedge in their own words — "what they asked of you, not an
instruction about how you work" — which is a statement about INTENT VS ORDER,
not a trust label on content. The seam needs no trust theory. It is R1: the
channel differs from production's, so the assembly differs from production's.)*

## 7.2 The tool-result seam

Production: Nora calls `web_search`, the result returns inside the agent loop as
a tool result. The eval: the same text is pasted into the transcript as though a
member said it. Two different assemblies, so two different things measured.

The seam lets a case state what a tool returned, and the harness delivers it
through the real tool-result path:

```yaml
  tools:
    web_search:
      - query: "<what she asks>"
        returns: |
          <the result, as the tool would return it>
```

This also closes an existing harness gap that is independent of these 13 cases:
a Dreamer turn today reaches the REAL `web_search` and the real network — non-
deterministic, and billable. Every tool an eval turn can reach must be either
stated or refused; a tool call with no stated result is an ERROR, not a live
call (R8).

A parked case states:

```yaml
parked:
  reason: P-A | P-B
  why: |
    <why no anchor exists, or which seam is missing>
  returns_when: |
    <the observable condition that would unpark it>
```

**Every run reports the parked count beside the active one** — "150 total, N
active, M parked (P-A x, P-B y), K unanchored" — so a shrinking active set is
visible rather than discovered later. A suite that reports only what it ran is
how the previous one hid its own coverage.

## 8. Open questions for the operator

1. P-B says the tool-result seam is our work, not the room's. Is building that
   seam in scope for this rebuild, or its own piece of work with those 13 cases
   parked meanwhile? (They are parked either way; this decides who picks them
   up and when.)
2. Honcho replay is asynchronous and its derivation is not instant. How long
   must a rebuild wait before its peer cards are considered complete, and what
   does R6 measure to know?
3. Rebuilds are frozen. Does a changed doctrine/objective invalidate every
   existing rebuild at once, or do they coexist with their recorded
   `instruction_sha256` until moved by hand?
