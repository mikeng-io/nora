# Identity and character, for actors and collectives

Status: **proposed**, 2026-10-14. The tier question in §2 and the questions in
§8 are the operator's to rule on.

## What the operator asked

Operator, 2026-10-14: a proper schema across two audiences: **actors** (a
human, Nora, another AI agent) and **collectives** (the room, and the unit).
Forming a character is not only saying *what it is like*; it is also saying
*what its identity is*. The new memory should build up a person's identity the
way Honcho's peer card did. Today's Character reads to him as half abduction,
or plain derivation.

## 1. What exists, and the half that is missing

**Character is live and describes behaviour only.** DEC-290; 35 person
accounts on norty, the latest written 2026-10-14. Its sections
([cognition-loop/12](../cognition-loop/12-schemas.md) §4) are all about
behaviour: `How they talk`, `What they care about`, `How they decide`,
`What they reliably do`, `Who they are close to`, `Unsure`. Culture is the
same machinery for a room or the unit (cognition-loop/10 §5). The `Unsure`
section is where the operator's "half abduction" lives: what the evidence
suggests but does not carry, with no mode saying so.

**Identity is scattered over four places, and none is a tier.**

| where | holds | written by | problem |
|---|---|---|---|
| `identity.toml` | names, platform accounts per person | the operator, by hand | covers who may be resolved, not who someone is |
| Honcho peer card | `IDENTITY:` / `ATTRIBUTE:` / `RELATIONSHIP:` / `INSTRUCTION:` lines | Honcho's dream pass | uncited, unscoped within its workspace (cognition-loop/50 §5), leaving with Honcho ([10](10-honcho-audit.md)); in 164 of 200 live tag turns |
| doctrine `identity` | who Nora is | the operator | authored; correct as it is |
| `<self>` | her Character · Nora account (DEC-292) | the Character adaptor | what she is like, not who she is |

Nothing holds, with a citation, "Alex is Sam's sister", "call me Kit, not
Katherine", "Kit is vegetarian since August", "this room is for the
Tuesday game night", or "the bot in this room is X, run by Y".

## 2. Identity answers its own question, so it is its own tier

DEC-249's rule: each tier answers exactly one question, and no question is
answered by two. Character answers *what is this entity like?* Identity
answers **who or what is this entity?**, which no tier answers. The two also
differ in every property a tier is cut by:

| | Identity | Character |
|---|---|---|
| question | who or what is it? | what is it like? |
| comes from | mostly **listening**: what was authored or told | mostly **watching**: what is done over many instances |
| reasoning modes | `told`, `deduced` (from authored and told lines) | `induced`; `abduced` only if §5 allows it |
| changes | rarely, and by a new statement | by drift, and lines decay without re-evidence |
| written by | the tag path on a told statement, and the Character adaptor for deductions | the Character adaptor only |

**Recommendation:** Identity is the Notebook's fifth tier. The alternative
is Identity sections inside a Character account, which mixes two
reasoning modes and two decay rules in one row. That is the distinction the
operator is asking for.

## 3. The schema

The entry columns are cognition-loop/05 §3's (home, entity, body, citations,
reasoning, confidence, visibility). The body is fixed-heading Markdown under
cognition-loop/12's grammar. **One line per fact**, each ending in its
citations and its mode.

**Actor** (`kind`: `human`, `nora`, `agent`):

| section | holds | source |
|---|---|---|
| `Names` | names, preferred name, nicknames and who uses them | authored, told |
| `Accounts` | platform identities | authored (`identity.toml`) only, rendered, never written by a model |
| `Role` | operator, member, guest; for an agent, who runs it and what it is for | authored, told |
| `Relationships` | to other actors ("Alex's sister") | told, deduced |
| `Attributes` | stable facts: where they live, work, diet, languages | told |
| `Instructions` | how they want to be treated ("don't call me Katherine") | **told by the subject only, never inferred** (Honcho 3.3's rule) |

**Collective** (`kind`: `room`, `unit`):

| section | holds | source |
|---|---|---|
| `Names` | the room's name, nicknames, how they refer to themselves | told |
| `Purpose` | what it is for | authored, told |
| `Origin` | how and when it started | told |
| `Members` | who is in it and their role here | authored + Record (the roster), rendered |
| `Norms` | rules someone **stated** ("no work talk here") | told |

How a room *actually* behaves stays in its Culture. A stated norm in Identity
and an observed practice in Culture may disagree, and the disagreement is
itself worth seeing.

**Rules taken from Honcho's peer card** (3.3.0, `src/dreamer/specialists.py`):

- **Stable only.** A fact likely to change within months does not belong in
  Identity; it is a note or a Character line.
- **A hard cap.** Honcho caps a card at 40 lines; Identity caps each section
  and says what is evicted (Kindroid's per-section caps,
  [40](40-prior-art.md)).
- **No behaviour.** Behaviour is Character's.
- **Instructions are never inferred.**

## 4. Precedence and contradiction

Authored > told by the subject > told by someone else about the subject >
deduced. A lower line never overrides a higher one; it sits beside it. Two
cited lines that conflict are both kept, marked `current`, `superseded` or
`disputed` (Cognee's conflict nodes, cognition-loop/80 §4's
contradiction-as-a-row), until a later statement settles them.

**Speech acts are kept.** "Thinking about going to Japan" is not "went to
Japan". ChatGPT's memory records exploration as fact, and that is its most
reported failure ([40](40-prior-art.md)). A told line keeps the verb it was
told with.

## 5. Character, with the modes made explicit

The sections stay. Two changes:

- **`Unsure` becomes explicit modes.** Each line carries `induced` (with the
  Rollup aggregate or the instances it rests on) or, if the operator allows
  it, `abduced`.
- **Abduction in Character, as a hypothesis** (operator decision, [00](00-intent.md)
  part 3). An abduced line carries a prediction that later Record rows can
  confirm or refute. It never renders as fact, and it is retired when refuted.

## 6. Other AI agents are actors

An agent in a room gets an Identity (`kind = agent`, who runs it, what it is
for) and a Character like any person. Its messages are already recorded (the
room allowlist gates answering, not recording), so its output is evidence
about it. It is never evidence about the person who runs it.

## 7. "Remember this" and "that's wrong"

**Remember.** A told statement is written on the tag path, with the message as
its citation, to the tier its question names:

| said | goes to |
|---|---|
| who someone is ("I'm vegetarian now", "call me Kit") | Identity, `told` |
| a settled fact of the unit ("our usual place is X") | Register · memo |
| something open or dated ("Japan in March") | Register · note or event |

`remember` (today a Honcho `conclude`) becomes the Identity write.

**Correct.** Nothing is overwritten:

1. The correction is a Record item (`character_dispute`, or the told line
   itself), and the disputed line stops rendering at once.
2. The writer's next session re-evidences the line or removes it
   (cognition-loop/50 §4).
3. Everything whose `cites` edges reach it is marked `unsupported` and goes
   back for re-derivation (the retraction cascade, [25](25-memory-graph.md) §1).
4. The revision chain keeps what was believed and when it changed.

**Only the subject may dispute a line about themselves** (cognition-loop/50
§4). Anyone else can add their own told evidence, which sits beside it.
**Assent never upgrades an abduction**: "yeah" to "is it because evenings are
short?" does not make the hypothesis a fact (cognition-loop/10 §4). It takes a
statement with content.

**Seeing it.** The portal shows each person the Identity and Character about
them, with the evidence behind each line (cognition-loop/50 §4).
`character_dispute` and the citation-edge table are designed and **not
built**. They are this bundle's build, not new design.

## 8. For the operator

1. Identity as a fifth Notebook tier (recommended), or sections inside
   Character.
2. Abduced lines in Character as marked hypotheses, or abduction kept to
   Register and Standing.
3. Does the operator override "only the subject may dispute"?
4. Visibility of a told Identity line: today's lineage rule (its origin room,
   then the person's rooms in the unit, cognition-loop/50 §5). The prior-art
   review found one person's disclosure surfacing in another's session to be
   the main multi-user risk ([40](40-prior-art.md)).
5. A watch, not a decision: a stored profile measurably raises a model's
   agreeableness (Jain et al.). In a group the risk is mirroring whoever Nora
   knows best. The eval should carry a case for it.
