# Learning from what she does: every act gets an outcome

Status: **proposed**, 2026-10-15. Nothing is built. Belongs before the social
phase (cognition-loop migration step 5), so its first act is learned from.

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
- **Social acts have no outcome.** The Instagram summons ledger
  (`instagram_summons`, migration 0066) records that a comment-mention was
  answered and how the write went, and nothing records what followed.
  Proactive and social acts planned for step 5 have no episode either.
- **Nothing learns about the person from how they answer her.** The
  Character adaptor reads a person's own messages only.

## 2. The loop

```
 ACT       a room send · an Instagram comment reply · a summons answer ·
   │       a proactive message · a DM                      (Record row)
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

1. **An episode per social act.** The response episode generalises from room
   sends to every act, keyed by the act's own id (a message id, a summons row,
   a proactive send). Its window and outcome set are per platform:

   | act | window | outcomes |
   |---|---|---|
   | room send | today's | today's |
   | Instagram comment reply / summons answer | 72 h | reply to her comment, like on it, the thread continuing without her, deletion, nothing |
   | proactive message | today's room window | as a room send, plus `ignored` when the room moved on |

   An outcome that cannot be read (a platform outage, an expired token) is
   `unknown`, never `silent` (R-27, today's rule).

2. **Explicit feedback as a Record item.** "That was weird", "good one", or
   the operator's review of a summons answer is a `feedback` item that cites
   the act. It is the strongest evidence an episode can carry, and a lesson or
   a Persona line resting on it says so.

3. **One new edge relation, `outcome_of`** (episode → act), added to the
   closed set in [25](25-memory-graph.md) §2 by schema change. "How did people
   react when I did this" becomes one hop.

4. **The Persona adaptor reads the person's episodes** ([45](45-persona-adaptor.md)
   §4): how they answer Nora is evidence about them, cited as `response:<id>`.

5. **Lessons read social episodes**, scoped per platform as well as per unit,
   so what works in a group chat is not applied to a public comment thread.

## 4. Two limits

- **Scope.** A person's social episodes inform their Persona under the same
  lineage as everything else ([35](35-persona.md) §3): what someone did on
  Instagram renders only in rooms that person is in.
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
- **Hypotheses:** the share later confirmed, per adaptor.
- **Lessons:** the share re-supported by new episodes before their horizon.

None of these is self-attested: each is read from later episodes, which
the adaptor that wrote the line did not produce.
