# Persona: identity, character and hypotheses as one account

Status: **ruled by the operator 2026-10-15**, not built. Supersedes
[30](30-identity-and-character.md) §2 (Identity as a fifth tier), §5 and the
dispute rule in §7. 30's schema (§3), precedence (§4), agents as actors (§6)
and the remember / correct flow (§7) stand, moved under Persona as below.

## The rulings

Operator, 2026-10-15, answering 30 §8:

| 30 §8 | ruling |
|---|---|
| 0, 1. Where per-person facts live once Honcho goes | In the Notebook, as **one account per entity that composes identity and character**. They answer one thing, who this entity is to Nora, and are not split into two tiers. |
| 2. Abduction | It is the account's **third part**, beside identity and character. The composed account is called **Persona**. |
| 3. Who may dispute a line | **Anyone**, and how much it counts depends on the context. Weighing it is what Nora learns to do; no fixed rule decides it. |
| 4. Visibility of what someone told about themselves | Today's lineage rule (§4 below), with the operator asking whether it should be the unit-wide view. Recommendation below. |
| 5. Honcho's uncited conclusions | **Dropped.** A one-off backfill script builds the new memory from Nora's own stores. |

## 1. What Persona is

One account per entity: an actor (a human, Nora, another agent) or a
collective (a room, the unit). Three parts, each with its own reasoning modes
and its own way of changing:

| part | answers | modes | changes |
|---|---|---|---|
| **Identity** | who or what it is: names, role, relationships, stable attributes, instructions; for a collective, its purpose and stated norms | `authored`, `told`, `deduced` | rarely, and only by a new statement |
| **Character** | what it is like: how they talk, what they care about, what they reliably do, who they are close to | `induced`, from many instances or a Rollup aggregate | by drift; a line decays without re-evidence |
| **Hypotheses** | why it is like that: an explanation of its behaviour | `abduced`, each with a **prediction** later Record rows confirm or refute | retired when refuted; never rendered as fact; goes out as a question; assent never upgrades it |

The sections are 30 §3's (Identity: `Names`, `Accounts`, `Role`,
`Relationships`, `Attributes`, `Instructions`; a collective's `Purpose`,
`Norms`) and cognition-loop/12 §4's (Character's sections), plus one
`Hypotheses` section. `Unsure` goes: what it held becomes either a
hypothesis or nothing.

**Where it sits.** Persona **is the Character tier, widened**, not a fifth
tier. The Notebook keeps four tiers (Register, Persona, Lessons, Standing), and
DEC-249 is amended in two places only:

- §1: the tier formerly called Character is Persona, and holds identity and
  hypotheses beside character;
- §2: per-person facts live in Persona, not in Memory (Honcho).

**One writer.** The Character adaptor, renamed the Persona adaptor, writes all
three parts (DEC-249 rule 3). The tag turn writes none of it (rule 4): a
"remember this" or "that's wrong" is a Record item plus a stimulus, as 30 §7
describes.

**Collectives.** A room's or the unit's Culture (cognition-loop/10 §5) is
already Character's machinery for a collective, so it is that collective's
Persona Character part, and its `Purpose` and `Norms` are the Identity part.
Whether Culture's tables are merged into Persona's or kept and read as
its character part is a build choice that changes nothing a reader sees; the
build takes the one with the smaller migration.

## 2. Disputes: anyone, weighed in context

30 §7 kept the cognition-loop/50 §4 rule: only the subject may dispute a line
about themselves. **Ruled otherwise.** Anyone may dispute any line, and a
dispute is evidence, not a veto:

1. It is a Record item (`persona_dispute`: who disputed which line, citing
   their message), and it raises a stimulus.
2. The disputed line **keeps rendering**, marked `disputed`, until the writer
   has weighed it. Under 30 §7 it stopped at once, which was right only
   while the subject was the only one who could dispute.
3. The Persona adaptor weighs it in its next session against what the
   line rests on: who disputed (the subject, someone close to them, someone
   who was there), what each side cites, and how old each is. It keeps,
   revises or retires the line, and the revision cites the dispute.
4. A retired or revised line still runs the retraction cascade (30 §7 step 3).

Two limits stay, because they are about what a line is, not who may
challenge it:

- **`Instructions` are told by the subject only.** "Don't call me
  Katherine" from anyone else is evidence for `Relationships` or a
  question, never an instruction.
- **The subject's own statement about themselves outranks anyone else's**
  (30 §4's precedence). A dispute by another person can retire a line that
  rests on induction; to override the subject's own told line it needs a later
  statement by the subject.

How well the adaptor weighs disputes is measured, not assumed: the eval
carries cases where the subject, a close friend and a bystander dispute the
same line, and where the dispute is wrong.

## 3. Visibility: the person's rooms, not the whole unit

The operator asked whether a told identity line should be visible across the
unit. The two readings:

| | today's lineage rule (cognition-loop/50 §5) | unit-wide |
|---|---|---|
| a line told in room A is visible in | A, then **every room of the unit the person is in** | every room of the unit, including ones the person is not in |
| "Kit is vegetarian", told in the friends room | in the friends room and the game-night room Kit is in | also in a room Kit has never joined |

**Recommendation: keep today's rule.** A unit holds rooms with different
members, and a person who told something in their rooms has not told the
rooms they are not in. The prior-art review found one person's disclosure
surfacing in another's session to be the main multi-user risk
([40](40-prior-art.md)). The unit-wide view is right for a **collective's**
Persona (the unit's purpose is the unit's in every room of it), and that is
already what a unit account's scope is.

If the operator wants unit-wide for people too, it is one lineage parameter,
not a schema change.

## 4. The one-off backfill

Honcho's conclusions are dropped, not exported. A one-off script, run once
when the new memory is first deployed, builds everything else from Nora's own
stores:

1. **Persona from Character.** Every live Character head (35 person accounts
   on 2026-10-14) keeps rendering as it does until the Persona adaptor's first
   session rewrites it into at most 5 `trait` lines citing what the head cited
   ([45](45-persona-adaptor.md) §8). Nothing is converted by code, because a
   prose account does not split into lines without a judgement.
2. **Identity's authored lines.** `Names` and `Accounts` from `identity.toml`,
   rendered rather than written (30 §3: accounts are never model-written).
3. **The memory index.** One `project()` over every row of every indexed store
   ([25](25-memory-graph.md) §7), then the embedding worker fills the vectors.
   The same command is the index's rebuild, so the backfill also proves the
   rebuild.
4. **Told identity lines** are not backfilled from Honcho. They come back as
   people say them again, or as the Persona adaptor deduces them from cited
   Record rows. Until then a person's Identity part is their authored lines
   alone, which is a visible regression from the peer card for the people
   Honcho knew: measure `<persona>` against `peer_card` in the tag turns
   before Honcho is switched off (00 «The reading that would reopen it»).

The script is idempotent and refuses to run against a database that already
has Persona rows, so it cannot run twice by accident.
