# Learning from what she does: every act gets an outcome

Status: **proposed**, 2026-10-15. Nothing is built. Belongs before the social
phase (cognition-loop migration step 5), so its first act is learned from.
**Amended 2026-10-15 (operator)** on an audit: proactive sends already have
episodes, Instagram acts need their own reader and table, feedback has a
named writer, and the measures are made not self-attested.

## What the operator asked

Operator, 2026-10-15: when Nora engages socially and answers summonses, she
should learn from it, both about herself and about the person.

## 1. What exists, and the gap

- **Room sends have outcomes.** Each send is a `response` episode (the Rollup's
  response kind, `nora.infra.sql.rollups`): the replies, reactions, edits and
  unquoted follows inside its window, or `silent` where an answer was expected.
- **Two adaptors learn from them.** Lessons (DEC-300, DEC-301, DEC-311) writes
  what works in a unit; `character-nora` writes how she comes across
  (DEC-292).
- **Proactive sends already have outcomes.** A fire turn's send is a
  response episode like any room send, carrying the brief's `intent`
  (`nora/infra/sql/rollups.py:384`, `_send_intent` at `:704-712`).
- **Social acts have no outcome.** The Instagram summons ledger
  (`instagram_summons`, migration 0066) records that a comment-mention was
  answered and how the write went, and nothing records what followed. The
  social acts planned for step 5 have no episode either.
- **Nothing learns about the person from how they answer her.** The
  Character adaptor reads a person's own messages only.

## 2. The loop

> 2026-10-21 (18 / ADR 0026): the Persona labels in the loop below are historical. [ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md) names these Character entries, with profile, traits and hypotheses; [18](18-message-readings.md) keeps them bounded and places source statements in Record message readings.

```
 ACT       a room send · a proactive message · an Instagram comment reply ·
   │       a summons answer                                (Record row)
   ▼
 OUTCOME   one episode per act, computed by code: replies, likes, reactions,
   │       deletions, silence, explicit feedback               (Rollup)
   ▼
 LEARN     about HERSELF   → Lessons: what works, per unit and per platform
   │                       → Persona · Nora, character: how she comes across
   │       about THE PERSON → Persona · person ([45](45-persona-adaptor.md)):
   │                            induced trait, from ≥ 3 of their episodes
   │                            hypothesis, explaining a trait, with a prediction
   ▼
 USE       the next act reads Lessons and that person's Persona (push, pre-fetch)
   ▼
 MEASURE   per act kind: reply rate, hypothesis confirmation rate,
           lesson re-support rate
```

## 3. What is built

1. **An episode per act.** Room and proactive sends already have one
   (`rollup_response`). Instagram acts cannot reuse it: a response episode is
   per build and unit and keyed by an integer `message_id`; a summons writes
   no audit row and has no unit; `instagram_summons` has a uuid `id` and a
   nullable `person_id`; and nothing ingests replies or likes on her
   comments. So the Instagram half needs three new parts:

   - **an outcome reader**: a comment poller that reads, inside the window,
     the replies, likes and deletion on each comment she wrote;
   - **a unit-assignment rule for a roomless act**: the unit of the
     summoner's resolved person when `person_id` is set and that person is in
     exactly one unit; otherwise the act has no unit, and its episode informs
     Lessons per platform only, never a person's Persona;
   - **its own episode table**, keyed by the summons' uuid, not
     `rollup_response`.

   | act | window | outcomes | episode |
   |---|---|---|---|
   | room send | today's | today's | `rollup_response`, built |
   | proactive message | today's room window | as a room send, with its `intent` | `rollup_response`, built |
   | Instagram comment reply / summons answer | 72 h | reply to her comment, like on it, the thread continuing without her, deletion, nothing | new table, from the comment poller |

   An outcome that cannot be read (a platform outage, an expired token) is
   `unknown`, never `silent` (R-27, today's rule).

2. **Explicit feedback as a Record item.** A `feedback` item, linked to the
   act by `feedback_on` ([25](25-memory-graph.md) §2). **Its writer is named**
   (2026-10-15): it is recorded only when the speaker addresses Nora's act
   explicitly, by replying to or quoting her act ("that was weird", "good
   one") or reacting to it, which ingestion sees in the message's own reply
   and reaction columns; or by the operator's review of an act in the portal.
   A model never classifies an arbitrary message into a feedback item. It is
   the strongest evidence an episode can carry, and a lesson or a Persona
   line resting on it says so.

3. **One new edge relation, `outcome_of`** (episode → act), now in the closed
   set in [25](25-memory-graph.md) §2 and projected by the Rollup builder
   (25 §7). "How did people react when I did this" becomes one hop.

4. **The Persona adaptor reads the person's episodes** ([45](45-persona-adaptor.md)
   §4): how they answer Nora is evidence about them, cited as `response:<id>`.

5. **Lessons read social episodes**, scoped per platform as well as per unit,
   so what works in a group chat is not applied to a public comment thread.

## 4. Two limits

- **Scope.** The unit is the boundary, and evidence about a person counts
  only if it was said in a room that person is listed in
  ([35](35-persona.md) §3). A roomless act's episode is evidence about a
  person only when §3's unit-assignment rule gives it a unit that lists
  them, and what follows from it renders only in that unit's rooms that list
  them.
- **Rewarded is not right.** Learning what a person rewards can drift into
  saying what they want to hear (30 §8 item 5: a stored profile measurably
  raises a model's agreeableness). The Lessons and Persona objectives say a
  reaction measures reception, not correctness, and the eval carries a case
  where a person rewards agreement and the expected Nora does not mirror
  them. A lesson that says "agree with X" is refused by the doctrine check
  (DEC-311: a lesson never goes ahead of doctrine's law).

## 5. Measure

- **Per act kind:** the reply rate and the explicit-feedback balance, before
  and after Lessons and Persona render, read from the episodes themselves.
- **Hypotheses:** the share confirmed, over every hypothesis ever written,
  per adaptor, with `expired` and `unknown` reported apart
  ([45](45-persona-adaptor.md) §3).
- **Lessons:** the share re-supported by new episodes before their horizon.

How each avoids self-attestation (amended 2026-10-15; the first version
claimed none was, which was not yet true): reply rates and feedback balance
are computed by code from the episodes, which no adaptor writes. A
hypothesis's verdict is set by code or a separate judge when its window
closes, never by the adaptor that wrote it (45 §3). Lesson re-support is
decided by the Lessons adaptor itself, so it is reported as that adaptor's
claim until a separate judge regrades a blind sample of it.
