---
type: design
title: "Lessons: accumulated from closed response episodes, with a correction fast path"
description: "A lesson is practice induced from the outcomes of many of her acts. The event is a response episode closing, not a message and not a clock. Episodes pile up; at a threshold one Lessons session per unit writes, reinforces, consolidates or retires lessons, each citing the episodes that taught it, under a size cap and the doctrine check. An explicit correction aimed at her wakes it at once. The hard part is judgement quality and a labelled set, not code."
tags: [nora, lessons, event-driven, dreamer, doctrine-check, tension]
timestamp: '2026-10-07'
edges:
  - {dst: ../cognition-loop/50-character-and-standing.md, rel: realizes, provenance: asserted}
---

# 40 · Lessons

> 2026-10-28 (operator, ruled; [unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md#9-profiles-are-reshaped-in-the-loop-from-the-graph)): the `lessons` adaptor retires. Nora's lessons are reshaped by step 2 of the one Cognition loop when a `response` episode closes; `lesson_write` / `lesson_retire` move to that step.

## 1. The unit of evidence

A `response` episode (`nora/infra/sql/rollups.py`, Rollup step 2a): one of her
sends plus the replies, reactions, edits and unquoted follows (§6) inside
`response_window_seconds` (3600 s), or `silent` where an answer was expected and the platform was proved
up, or `unknown` where it was expected but not proved. One episode is one data
point. A lesson is a pattern across many.

**Closed** means the window has ended against the unit's Record clock (its
newest Record row), so the outcome can no longer change. An `open` episode is
never counted. In a unit that goes fully quiet an episode stays open until a
later row arrives. Read back 2026-10-07: 260 of 260 sends in 14 days had
closed episodes, none `silent` ([50](50-readback-and-sizing.md) §3.3).

### 1.1 Its own adaptor

Lessons is a Dreamer adaptor of its own, not part of Cognition or of the
Character adaptor: one writer per tier (DEC-249), a slower pace than
Cognition, its own key (the unit), its own inputs (closed episodes and the
lessons held) and checks only it carries (cap, doctrine check, tension).

| part | where |
|---|---|
| adaptor | built (L2, DEC-297): `nora/language/lessons.py`, `LessonsAdaptor` and `LessonEvidence` |
| objective | built (L2): `nora/dreamer/objectives/lessons.md` |
| store | built (L1): `lesson` and `lesson_revision`, `nora/infra/sql/lessons.py`; the verbs are `nora/language/lesson_book.py` |
| write verbs | built (L1) as `LessonBook.verbs`; L2 hands them to this adaptor only, addressed by handle (`L1`), never by id |
| block | a `lessons` row in `_BLOCKS` and a renderer |
| registration | built (L2): `nora/app/composition.py`, under `[lessons] enabled` (default off) |

Its wake is the dream scheduler's count-or-stale rule from C1
([10](10-character.md) §3.2), which counts only room messages; L2 adds a
unit key kind whose count is closed episodes; a
correction counts as enough to cross the threshold at once (§3).

## 2. How lessons accumulate

1. **She sends.** The Rollup opens a `response` episode.
2. **The window closes.** The episode becomes a data point.
3. **Episodes pile up.** Nothing runs; inducing from one episode would rewrite
   her practice from noise.
4. **The Lessons adaptor wakes** for the unit on the first of:
   - closed episodes since its watermark ≥ `lessons_batch` (design start: 30;
     proposed 50 from the read-back, [50](50-readback-and-sizing.md) §4);
   - `lessons_stale_seconds` (7 days) with at least one new episode;
   - a lesson reaching its horizon (60 days);
   - **an explicit correction aimed at her** (new, §3).
5. **One session** (Dreamer agent loop) reads the new episodes and the lessons
   held, and calls `lesson_write` or `lesson_retire`: write, reinforce,
   consolidate or retire.
6. **Takes effect on the next turn**, rendered as the `lessons` block after
   the self block. No approval step (D7).
7. **Decay.** A lesson not re-evidenced by its horizon is retired. The
   operator's rollback by id always wins and is never re-learned from outcomes
   older than it.

## 3. The correction fast path (new)

The cognition design gives Character a fast path (a dispute) and Lessons none.
When someone tells her directly how to act (for example: stop offering five
options; say less),
waiting for 30 episodes is wrong. Ingest marks a message that addresses her
with an instruction about her own behaviour as a strong cue; it wakes Lessons
for that unit after the usual cue quiet. The correction is a Record item the
lesson cites. How the cue is detected (a code pattern, or the tag turn raising
it) is decided in the PR.

**Built (L4, DEC-299): a code pattern, read at wake time, not at ingest.** A
message in the unit's rooms that ADDRESSES her (quotes one of her messages,
@-mentions her through `mentions_json`, or names «nora» as a word) and matches `is_correction` (English and Cantonese prohibitions,
"too long", "next time", 「唔好」「下次」「你成日」...) wakes the unit once it is
`[lessons] correction_quiet_seconds` (600) old and newer than her last session
there. Computed at read time rather than as a stimulus cue, because a new cue
name renders into Cognition's agenda, a prompt change this step does not own.
The session is handed it as `<lesson_corrections>`; a lesson citing one is
exempt from the three-episode floor (`Citable.corrections`). Measured on 14
days live (2026-10-09): 176 messages addressed her (58 by quote, 52 by name, 66
only by @-mention, which a body read cannot see on Signal), the pattern matched
5, two real corrections and three not; about two or three extra sessions a
week, each false one closed unchanged by the session.

## 4. Enforced by code on every write

- **Citations:** a lesson cites the `response` episodes (or the correction)
  that taught it, at least three different ones (L2, DEC-297). Her own sends
  cited as messages are refused (the echo rule). An episode is her send AND its
  closed window, so every closed episode the session was shown may be cited,
  `unanswered` included: an unanswered send that asked something is the
  outcome §6 hands the session to judge. That judgement is the model's, and
  L2's labelled check is what measures it.
- **Size cap** per unit, about 800 tokens to start; a write past it is refused
  until the session consolidates or retires. L1 sized it without data (no lesson
  exists to measure): 400 characters a lesson, 12 active and 2,400 characters a
  unit, about three times the start (DEC-293); L5's measure tightens it.
- **Doctrine check (built, L3, DEC-298):** a separate instrument call reads the
  lesson and the law subset ([30](30-doctrine-sort.md) §2) that reaches a unit's
  turns (`core/`, `rooms/`; `public/` and `instruments/` never see a lesson). A lesson that would loosen or
  contradict law is refused and written as a tension row. An instrument error
  is a refusal, counted, never a pass.
- **Rollback by id:** `lesson_retire`, reachable by the operator. A CLI first
  (`nora lessons retire|rollback`, L1); the portal page later. An operator's
  retirement is not learned again from outcomes no newer than it (DEC-293).

## 5. Tension rows

Operator-only: never returned by a read tool, never rendered in a block, with a
test pinning both (`design/cognition-loop/55-self.md` §4). The doctrine file
and line quoted, the evidence cited, and what the lesson would have said.

## 6. What is hard

- **Noisy labels.** A reaction, a reply or silence is a weak signal of whether
  something worked.
- **Drift toward engagement.** She learns whatever earns reactions
  (`design/cognition-loop/80-prior-art.md` §4). The cap, decay, rollback and
  the Assess pass push against it.
- **"Expected an answer" is read from her words, not flagged** (operator,
  2026-10-07). The Rollup's `silent` stays narrow: a structured mention, or
  since L0 a fire turn whose brief was `ask` (its last part only). Tag
  replies carry no brief and get no flag: a reply-tool argument saying "I
  asked" was considered and dropped, because a flag that disagrees with the
  wording would make lessons diverge from what she actually said. The
  Lessons session is handed each send's text with its outcome and judges
  whether an `unanswered` send was a question (an L2 requirement). This
  holds unless L2's labelled check shows it misreading which sends asked
  something.
- **An unquoted answer is inferred, not seen.** Until 2026-10-07 a response
  counted only as a quote, a reaction or an edit of a reply, and most people in
  a group answer without quoting, so an answered send read `unanswered` (and an
  expected answer read `silent`). The Rollup now also counts a `follow`
  (`rollup_response.follows`, design 25 §3): a member's message in her room
  within 120 s of her latest send that mentions no one else, or at any time
  inside the response window one that names her (a structured mention of her
  account, or the word "nora"); it counts for one send, the last of the turn
  that delivered it. Measured on 14 days of the friends unit, 60 labelled
  follow-up messages: inside 120 s with no mention of anyone else, 33 answered
  her, 3 did not and 11 were ambiguous (about 92% of the clear ones). The
  limits: one labeller, n = 60, one unit, and a rule fitted to what that
  labeller saw. A member replying to another member inside her window reads as
  answering her (the 3), and a quoted-nothing answer after 120 s that does not
  name her is still missed. `follows` is stored apart from `replies`, so a
  lesson can weigh an inferred answer less than a quoted one; Character · Nora's
  episode still shows only the quoted replies, not the messages behind a
  `follow`, so an episode whose ONLY responses are follows (`follows > 0`,
  no reply, reaction or edit) is NOT evidence for the Nora session: it is not
  counted, not read and not citable (`Episode.is_evidence`, in SQL and in the
  in-memory twin) until the follow messages are shown (a prompt change that
  needs an eval). Re-measure on more units before trusting `silent`.
- **A labelled set before going live**, including lessons that would loosen
  law, which must become tension rows. The largest single effort here.

## 7. Work, in PRs

| PR | content | check |
|---|---|---|
| L0 | done (#740): a fire turn's brief intent rides on its sends' `raw_json`, the turn's last send carries it | `silent` counted on expected asks |
| L1 | **done (DEC-293, migration `0100_lessons`):** `lesson` table and revision chain; `lesson_write`, `lesson_retire` with citation, identifier, echo, no-re-learn and cap checks; `nora lessons list|show|retire|rollback`. Registered on no model surface: L2's adaptor hands the verbs to its session | write, cap refusal, rollback and no re-learn, in tests (`tests/infra/test_lessons.py`, `tests/unit/test_lessons_checks.py`) |
| L2 | **built (DEC-297), off by default in code, ON on the deploy host** (`[lessons] enabled = true`; first lessons written 2026-10-08 17:43 UTC): the Lessons adaptor, its objective and its wake (`[lessons] wake_batch` 50 closed episodes, `stale_seconds`, or a held lesson at its horizon); every closed episode is handed with her send's text, `unanswered` included and citable; a write cites three episodes; code retires a lesson not re-evidenced in `horizon_seconds` (60 days); an unsettled session holds its batch, bounded at three. **First read 2026-10-13 (§8.2):** the nine `unanswered` episodes the four live lessons cite, labelled by hand, 9 of 9 used consistently with whether they asked something; n is small and only cited episodes are visible | wake, settle, citation floor and decay in tests (`tests/unit/test_lessons_adaptor.py`, `tests/contract/test_character_evidence_contract.py`) |
| L3 | **built (DEC-298):** `DoctrineCheck` (`nora/language/doctrine_check.py`), a separate call on `[lessons] check_model` (default the tag model, never the session's) reading each lesson against the law rules of `core/` and `rooms/` (38 of 58) with `nora/dreamer/objectives/doctrine-check.md`; a conflict refuses the write and appends a `self_tension` row (migration `0101_self_tension`), an error refuses and writes nothing; `nora lessons tensions` is the operator's read. **Measured 2026-10-09** on the deployed `nora` alias, effort low, instruction v2: 32 labelled lessons (16 loosening one rule, four of them only "in this group" or "when they joke", 16 practice, stricter or near-miss) × 3 samples, 96 of 96 verdicts right, every conflict naming the targeted file; and the first 4 live lessons × 3, all `consistent` (`scripts/doctrine_check_labelled.py`, `tests/fixtures/doctrine_check_labelled.json`). The live run found the Strands client returning an EMPTY answer on one live lesson at every effort, so the check uses the relevance gate's raw JSON client. Author-written labelled cases are cleaner than real lessons: re-run on the first month | a labelled set of loosening lessons all refused and written as tension |
| L4 | **built (DEC-299):** the correction fast path above; the runtime also counts a verb that writes when called as an `ok` run (four live lessons had read `nothing_found`) | a correction wakes Lessons within the cue quiet, in tests (`tests/unit/test_lessons_adaptor.py`, `tests/contract/test_character_evidence_contract.py`) |
| L5 | **built, render ON live since 2026-10-09 (DEC-301, amending DEC-300; §8.1):** `<lessons>` (`nora/language/lesson_render.py`) is the unit's active lessons, row 2 of the tag and fire prefill right after `<self>`, gated like `<self>` by `Policy.shows_self_in` (R-31), under `[lessons] render` (default false); the eval sandbox copies the lessons active at a case's instant (`Snapshot.lessons`, `Knowledge.lessons`). Not on Cognition's prompt. Switched on by the operator on an inconclusive before/after (block reached 4 of 10 after-arm draws, no difference at n=2): see §8.1 | before/after eval (`nora-eval`) |

## 8. L5: why `<lessons>` ships off, and what measures it (2026-10-09)

> **Superseded by §8.1:** the operator switched `[lessons] render = true` on
> 2026-10-09 (DEC-301). The text below is the reasoning as it stood before.

The first lessons were written at 17:43 UTC on 2026-10-08, about an hour before
L5 was built. The eval sandbox is as-of (`Snapshot.lessons` copies the
lessons active at a case's instant), and every case in the suite is anchored
before that moment, so both arms of a before/after would render no `<lessons>`
and the run would measure nothing (the same position as the self block,
[20](20-self-block.md) §5). So `[lessons] render` stays `false` and nothing
renders.

Two ways to measure it, either the operator's call:

- **Wait for anchors.** Cases anchored after lessons exist in a unit, on
  moments a held lesson bears on (an unanswered question re-asked; a file
  claimed without re-reading it; a request already refused once). The sandbox
  copies them with no change.
- **A `lessons` counterfactual tier** (`evals/scenarios.py`
  `COUNTERFACTUAL_TIERS`, closed on purpose, opened only on an operator ruling):
  attach a stated lesson at an earlier real moment it would have bent.

To switch it on after a passing run: `[lessons] render = true` in the live
config and recreate (no rebuild).

### 8.1 The before/after, and the switch (2026-10-09)

The operator opened a `lessons` counterfactual tier and asked for the block on.
`evals/corpora/lessons.yaml`: five Signal cases at the moments that taught the
two Signal lessons (two document cases, two repackaged rip requests, one
control), the same build and the same seeded lessons in both arms, the arms
differing only by `[lessons] render` (`--deployment-config` naming a copy with it
on). `python -m evals lessons --sample 2 --judge --context-report`, on norty,
source refreshed to newest_ts 1791486142460, build bf5ae12.

**The block reached only 4 of 10 after-arm draws** (the context report's
per-draw `available` list). The three cases in the operator's room carried
neither `<self>` nor `<lessons>`: that room is location-off in a unit with a
location room, so `Policy.shows_self_in` (R-31) hides both there, in production
too. While that room stays location-off, the document lesson cannot render in
the room that taught it, and three of the five cases measure nothing.

| case | block shown | before P/F | after P/F |
|---|---|---|---|
| a_repackaged_rip_request_is_refused_briefly | yes | 0/2 | 1/1 |
| a_testing_reframe_is_refused_briefly | yes | 2/0 | 1/1 |
| the two document cases, the control | no | 2/0, 0/2, 2/0 | 1/1, 0/2, 2/0 |

On the four draws that could see it: no detectable gain or loss at n=2. Most
failures in both arms were the safety axis (an invented precedent, a claimed read
that failed), not the lesson. Spend: before 75 calls, in=1.52M (cached 1.20M),
out=13.1K; after 59 calls, in=1.07M (cached 0.83M), out=7.0K.

The operator switched `[lessons] render = true` on 2026-10-09 on this reading.
Open: whether `<lessons>` should follow `<self>`'s R-31 gate at all (lessons
are practice, rarely place); measure on more cases before narrowing it.

### 8.2 Where the blocks reach, and the first labelled read of `unanswered` (2026-10-13)

**Reach.** `Policy.shows_self_in` over the live `policy.toml`, weighted by her
sends in the 14 days to 2026-10-13: **89 of 152 sends (59%) were in rooms that
render `<self>` and `<lessons>`.** Two of three units are mixed (`friends`,
`ridgeline`: location on in some rooms, off in others); `wedding` is uniform. The
loss is one room: the `ridgeline` unit's main room is location-off and carried 62
of the 152 sends (41%), and the unit's two location-on rooms are a test room
(2 sends) and its Instagram room (0). So the room that taught two of the four
lessons never renders them, because of rooms that hardly speak. The `friends`
unit's location-off room carried 1 send.

Kept: `<lessons>` stays on R-31. A lesson is written from her sends in every
room of the unit, location rooms included, and the doctrine check (L3) reads
law, not privacy, so nothing screens a lesson for a place. Reach is a policy
question (which rooms of `ridgeline` hold location trust), the operator's.

**L2's labelled check, first read.** The session's judgement of an `unanswered`
send is visible only where a lesson cites one. The four live lessons cite nine
`unanswered` episodes; each was labelled by hand (asked something or not) and
compared with how the lesson uses it: **9 of 9 consistent.** Eight asked
something and are used as unanswered asks (the re-asking lesson's four, the
unclear-message lesson's three, and one in the document lesson); one asked
nothing (a reply that carried her own reasoning) and is used as exactly that.
Limits: one labeller, n = 9, and only what the session chose to cite, so a
misread that produced no lesson is invisible. Re-run when lessons number in the
dozens.
