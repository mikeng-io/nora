# Persona: identity, character and hypotheses as one account

Status: **ruled by the operator 2026-10-15**; the person account is built
since 2026-10-18 (45, in shadow: schema, the person adaptor, disputes, the
verdict judge). Supersedes
[30](30-identity-and-character.md) §2 (Identity as a fifth tier), §5 and the
dispute rule in §7. 30's schema (§3), precedence (§4), agents as actors (§6)
and the remember / correct flow (§7) stand, moved under Persona as below.
**Amended 2026-10-15 (operator)** on an audit: disputes split by who disputes
(§2), the unit is the read boundary (§3, which described lineage wrongly), a
deleted message makes nobody forget (§5), and shadow changes no prompt (§4).

## The rulings

Operator, 2026-10-15, answering 30 §8:

| 30 §8 | ruling |
|---|---|
| 0, 1. Where per-person facts live once Honcho goes | In the Notebook, as **one account per entity that composes identity and character**. They answer one thing, who this entity is to Nora, and are not split into two tiers. |
| 2. Abduction | It is the account's **third part**, beside identity and character. The composed account is called **Persona**. |
| 3. Who may dispute a line | **Anyone**, and how much it counts depends on the context. Weighing it is what Nora learns to do; no fixed rule decides it. **Except** (2026-10-15): the subject's own dispute hides the line at once (§2). |
| 4. Visibility of what someone told about themselves | **Ruled 2026-10-15: the unit is the read boundary**, lineage stays off, and evidence about a person counts only if said in a room they are listed in (§3). |
| 5. Honcho's uncited conclusions | **Dropped.** A one-off backfill script builds the new memory from Nora's own stores. |
| 6. The workspace (DEC-214) | **Ruled 2026-10-16: retired; the unit is the only boundary.** A workspace exists to put several units' people in one Honcho tenant, and the unit-boundary ruling in row 4 already stops anything crossing units, so nothing would enforce it. It is removed at step 9 of [00](00-intent.md) «Sequence», with Honcho. Removed with it: `policy.toml` `[units.<u>] workspace` and its startup check, `identity.toml` `[workspaces]`, and the routing; the portal's viewer scope (`workspaces_of_person`) moves to the unit. Cost, accepted: what a person says about themselves in one unit no longer reaches another unit their workspace shares today; only turning lineage on for Persona would bring that back. |

## 1. What Persona is

One account per entity: an actor (a human, Nora, another agent) or a
collective (a room, the unit). Three parts, each with its own reasoning modes
and its own way of changing:

| part | answers | modes | changes |
|---|---|---|---|
| **Identity** | who or what it is: names, role, relationships, stable attributes, instructions; for a collective, its purpose and stated norms | `told`, `deduced`; authored lines (names, accounts from `identity.toml`) are rendered with no mode, never written | rarely, and only by a new statement |
| **Character** | what it is like: how they talk, what they care about, what they reliably do, who they are close to | `induced`, from many instances or a Rollup aggregate; `deduced` | by drift; a line decays without re-evidence |
| **Hypotheses** | why it is like that: an explanation of its behaviour | `abduced`, each with a **prediction** later Record rows confirm or refute | retired when refuted; never rendered as fact; goes out as a question; assent never upgrades it |

The sections are 30 §3's (Identity: `Names`, `Accounts`, `Role`,
`Relationships`, `Attributes`, `Instructions`; a collective's `Purpose`,
`Norms`) and cognition-loop/12 §4's (Character's sections), plus one
`Hypotheses` section. `Unsure` goes: what it held becomes either a
hypothesis or nothing.

**The modes table that holds is [45](45-persona-adaptor.md) §3** (amended
2026-10-15). This table said Character was `induced` only; 45 allows
`deduced` in character too, and 45's is kept. `authored` is not one of
cognition-loop/10 §4's six modes, so an authored line carries no mode.

**Where it sits.** Persona **is the Character tier, widened**, not a fifth
tier. The Notebook keeps four tiers (Register, Persona, Lessons, Standing), and
DEC-249 is amended in two places only:

- §1: the tier formerly called Character is Persona, and holds identity and
  hypotheses beside character;
- §2: per-person facts live in Persona, not in Memory (Honcho).

**One writer.** The Character adaptor, renamed the Persona adaptor, writes all
three parts (DEC-249 rule 3). ~~The tag turn writes none of it (rule 4): a
"remember this" or "that's wrong" is a Record item plus a stimulus, as 30 §7
describes.~~ **Amended 2026-10-17 (operator, DEC-317):** Honcho's `remember`
exists because it updated her understanding of a person, so on the graph
surface `remember` returns as the Persona write. When someone tells her
something about a person, the turn writes a `told` line (who told it, about
whom, citing the message) through the same write path and judge the adaptor
uses ([45](45-persona-adaptor.md) §3), so its rules hold for a turn's line
as for a session's. It writes `told` lines only, never a deduction,
induction or hypothesis, and never retires or revises: weighing stays the
adaptor's. "That's wrong" is still a Record item plus a stimulus (§2).

**Collectives.** A room's or the unit's Culture (cognition-loop/10 §5) is
already Character's machinery for a collective, so it is that collective's
Persona Character part, and its `Purpose` and `Norms` are the Identity part.
Whether Culture's tables are merged into Persona's or kept and read as
its character part is a build choice that changes nothing a reader sees; the
build takes the one with the smaller migration.

## 2. Disputes: anyone, weighed in context; the subject's hides at once

30 §7 kept the cognition-loop/50 §4 rule: only the subject may dispute a line
about themselves. **Ruled otherwise.** Anyone may dispute any line. Amended
2026-10-15 (operator): what happens next depends on who disputes.

1. It is a Record item (`persona_dispute`: who disputed which line, citing
   their message), and it raises a stimulus.
2. **A dispute by the subject hides the line at once** (a hard privacy rule,
   not a weighing): the line is marked `hidden`, stops rendering everywhere,
   and its index node is dropped, before the adaptor runs. The same holds
   when the subject says "forget that" about a line. The adaptor's next
   session may write a revised line, which renders only if it no longer says
   what was disputed.
3. **A dispute by anyone else** marks the line `disputed`, which has one
   meaning: awaiting weighing. It keeps rendering, marked as disputed, until
   the next session.
4. The Persona adaptor weighs it in its next session against what the
   line rests on: who disputed (someone close to the subject, someone who was
   there), what each side cites, and how old each is. It keeps, revises or
   retires the line, and the revision cites the dispute.
5. Nothing else is re-marked. The retraction cascade was deleted 2026-10-03
   (migrations 0092/0093); lines that drew on a retired line keep their own
   citations and are re-judged at their next session.

Two limits stay, because they are about what a line is, not who may
challenge it:

- **`Instructions` are told by the subject only.** "Don't call me
  Katherine" from anyone else is evidence for `Relationships` or a
  question, never an instruction.
- **The subject's own statement about themselves outranks anyone else's**
  (30 §4's precedence). A dispute by another person can retire a line that
  rests on induction; to override the subject's own told line it needs a later
  statement by the subject. A line that loses on precedence is `outranked`,
  its own state, never `disputed` ([45](45-persona-adaptor.md) §3).

How well the adaptor weighs disputes is measured, not assumed: the eval
carries cases where the subject, a close friend and a bystander dispute the
same line, and where the dispute is wrong.

## 3. Visibility: the unit is the boundary (operator, 2026-10-15)

The first version of this section compared "today's lineage rule" with a
unit-wide view, and recommended lineage. Both halves were wrong about today:
lineage is built but **switched off**, `[lineage] mode = "off"`, marked "do
not enable" (operator, 2026-10-03, cognition-loop/10 §3), and its table here
described the rule wrongly. A recommendation resting on a filter that is off
protects nothing.

**Ruled (option b):**

1. **The unit is the read boundary**, as it is today: a Persona account
   renders in the unit's rooms that list the person
   (`nora/language/character.py:40-47`), and never in another unit. Rooms
   whose talk must not meet belong in different units (DEC-231). Lineage is
   **not** re-enabled.
2. **Evidence about a person counts only if it was said in a room that person
   is listed in.** This governs the inputs that are new in this bundle:
   messages that mention the person ([45](45-persona-adaptor.md) §4), lines
   someone else told about them, and their social episodes
   ([50](50-learning-from-acts.md) §4). Talk about Kit in a room Kit is not in
   is not evidence about Kit, is not read by Kit's session, and is not
   reached from Kit's node ([25](25-memory-graph.md) §4).

| | was proposed (lineage) | ruled (unit boundary + listed-room evidence) |
|---|---|---|
| "Kit is vegetarian", told by Kit in the friends room | the friends room, then Kit's other rooms | every room of the unit that lists Kit |
| "Kit is moving", said by Sam in a room Kit is not in | rooms whose members are a subset of that room's | not evidence about Kit at all |
| the same person in a second unit | — | a separate account, from that unit's evidence |

The unit-wide view is right for a **collective's** Persona (the unit's
purpose is the unit's in every room of it), which is already a unit account's
scope.

**Person-input extension (proposed 2026-10-20):**
[45 §4a](45-persona-adaptor.md#4a-proposed-person-input-extension--2026-10-20)
is the single contract for wider Record/Rollup inputs and Register context
into the one person writer. Register entries are links/context, never
independent evidence; Persona is itself Notebook. The current exclusion of
person location remains: unit scope does not authorize presence across rooms.
That extension is not a receipt of shipped ingestion or model acceptance.

## 4. The one-off backfill

Honcho's conclusions are dropped, not exported. A one-off script, run once
when the new memory is first deployed, builds everything else from Nora's own
stores. **It runs in shadow** (amended 2026-10-15): Persona is written under a
shadow key, the live Character head keeps rendering, and Honcho's feed keeps
running, until the switch (00 «Sequence» step 8). Nothing a prompt carries
changes before then.

1. **Persona from Character.** Every live Character head (35 person accounts
   on 2026-10-14) keeps rendering as it does; the Persona adaptor's first
   shadow session rewrites it into at most 5 `trait` lines citing what the
   head cited ([45](45-persona-adaptor.md) §8). Nothing is converted by code,
   because a prose account does not split into lines without a judgement.
2. **Identity's authored lines.** `Names` and `Accounts` from `identity.toml`,
   rendered rather than written (30 §3: accounts are never model-written).
3. **The memory index.** One `project()` over every row of every indexed store
   ([25](25-memory-graph.md) §7), then the embedding worker fills the vectors.
   The same command is the index's rebuild, so the backfill also proves the
   rebuild. It reports how many items it could not project, and until it
   finishes a search answers `partial`, never `nothing_found`. The media part
   is counted first and runs under a throttle ([20](20-embedding-bakeoff.md)).
4. **Told identity lines** are not backfilled from Honcho. They come back as
   people say them again, or as the Persona adaptor deduces them from cited
   Record rows. Until then a person's Identity part is their authored lines
   alone, which is a visible regression from the peer card for the people
   Honcho knew: measure `<persona>` against `peer_card` in the tag turns
   before Honcho is switched off (00 «The reading that would reopen it»).

The script is idempotent and refuses to run against a database that already
has Persona rows, so it cannot run twice by accident.

**Built (2026-10-17), for step 1:** there is no separate script. The Persona
adaptor's first session on an account is the backfill: until a session first
settles the account (`persona.settled_at` is NULL) it is handed the Character
head and the rows that head cited, and never again after, which is what
"cannot run twice" asks for, per account rather than per database. A turn's
`told` line landing first does not skip it. Turning on `[persona] enabled`
runs it: a key that never ran wakes on its first tick with any evidence.

## 5. A deleted message makes nobody forget (operator, 2026-10-15)

A message deleted on the platform sets `messages.hidden_at`. It is hidden:
never quoted, never a search hit, and its index node is dropped
([25](25-memory-graph.md) §7). **Lines and entries derived from it stay**, and
cite it as "a message since deleted". There is no retraction cascade and
nothing is purged: the cascade, `nora person forget` and the `citation` edge
table were deleted on 2026-10-03 (migrations 0092/0093; cognition-loop/10 §2
rule 7, cognition-loop/05 §3, cognition-loop/70 §9), and this ruling does not
bring them back.

The one exception is the subject's own: a dispute or "forget that" by the
person a line is about hides that line at once (§2).


## Typed Identity and recovery amendment, 2026-10-19

[75](75-typed-identity.md) supersedes the first-session/newest-page coverage
claim above. A first successful session does not establish historical coverage.
Named typed Identity values share the Persona transaction with Character and
Hypotheses. The separate frozen historical pass is resumable and explicitly
rerunnable; old prose is retained as unresolved history rather than parsed
into authoritative facts. See [45](45-persona-adaptor.md)'s typed amendment
for the cursor and operator receipt commands. Cutover still requires grounded
coverage comparison and the budgeted behavioral gates.
