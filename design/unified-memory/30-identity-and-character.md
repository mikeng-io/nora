# Identity and character, for actors and collectives

Status: **proposed**, 2026-10-14. **Ruled 2026-10-15 in [35](35-persona.md):**
identity, character and abduced hypotheses are one account, Persona, which
widens the Character tier; there is no fifth tier. §2, §5, §7 step 1 («the
disputed line stops rendering at once», now only for the subject's own
dispute), §7 step 3 (the retraction cascade, deleted 2026-10-03) and the
«only the subject may dispute» rule in §7 are superseded there; §8 is
answered there; §3, §4, §6 and the rest of §7 stand under Persona.
**Amended 2026-10-15:** the schema, modes and caps that hold are
[45](45-persona-adaptor.md)'s; this node's tables are kept as the reasoning
that led to them.

**This node proposes amending DEC-249.** [cognition-loop/05](../cognition-loop/05-data-model.md)
§1 fixes the Notebook at four tiers, and §2 rules that "per-person facts live
in Memory" (Honcho) and "the Notebook stores no statement-level provenance".
With Honcho retired, per-person facts need a home. This node puts them in a
fifth tier, Identity, which is a change to that ruling, not a reading of it.
It needs the operator's ruling and its own decision row when it lands
(nora-decision). *(Superseded by [35](35-persona.md) §1: no fifth tier;
Persona widens Character, and DEC-249 is amended in §1 and §2 only.)*

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

Nothing holds, with a citation, "Rin is Sam's sister", "call me Kit, not
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
| written by | the Character adaptor, its sole writer (DEC-249 rule 3: one writer per tier) *(superseded by 35: one tier, the Persona adaptor)* | the Character adaptor |

One adaptor writing two tiers is the existing pattern: Cognition writes both
Register and Standing. **The tag turn never writes Identity** (DEC-249 rule 4:
speaking turns only read Views, and change the Notebook by raising a
stimulus); see §7. *(Amended 2026-10-17, DEC-317: on the graph surface the
turn's `remember` writes a `told` line through the Persona write's judge;
[35](35-persona.md) §1.)*

Identity also needs `told` and `deduced`, which cognition-loop/10 §4 allows
only in Register and Standing. Its "allowed in" column is amended by this
node. Whether the six modes stay as stored detail is itself open
(cognition-loop/05 §10 Q2); this node assumes they do.

**Recommendation:** Identity is the Notebook's fifth tier. The alternative
is Identity sections inside a Character account, which mixes two
reasoning modes and two decay rules in one row. That is the distinction the
operator is asking for.

## 3. The schema

> **Decided (operator, 2026-10-22):** [47](47-character-dimensions.md#decided-operator-2026-10-22) admits evidence-supported location claims, including residence, at named-place level. Code-set `location` exists to preserve the current per-room location read setting, not to restrict storage. Sensitive categories follow normal modes/floors with no special handling. Dedicated keys may be deduced under 45 §3; fallback attributes stay told-only. Gender/sex/birthday retain 75's existing contract. The historical instruction rules below are unchanged.

> 2026-10-22 (operator brief; proposed): [47](47-character-dimensions.md) defines named prose slots for traits and hypothesis explanations in phase 4, alongside ADR 0026’s profile/traits/hypotheses names. Profile typed facts remain [75](75-typed-identity.md)’s; citations, modes and state remain columns, not the fixed-heading markup described historically below. Reading claim slots belong to phase 2 in [18](18-extraction.md).

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
| `Relationships` | to other actors ("Rin's sister") | told, deduced |
| `Attributes` | stable facts: where they live, work, diet, languages | told |
| `Instructions` | how they want to be treated ("don't call me Katherine") | **told by the subject only, never inferred** (Honcho 3.3's rule) |

**Collective** (`kind`: `room`, `unit`):

| section | holds | source |
|---|---|---|
| `Purpose` | what it is for | authored, told |
| `Norms` | rules someone **stated** ("no work talk here") | told |

Two things a collective's identity might hold are already answered elsewhere,
and stay there so no question has two homes: its names and their origins are
Culture's `Names and where they came from` section (cognition-loop/12 §4), and
its members are the roster, already rendered as the `room_members` block.
*(Amended 2026-10-15: a collective's names now live in Persona as `name`
identity lines, [45](45-persona-adaptor.md) §2; Culture's `Names and where
they came from` section is retired into them. The roster stays.)* How
a room *actually* behaves stays in its Culture. A stated norm in Identity
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

Two proposed changes to Character's schema (cognition-loop/12 §4, DEC-290),
both the operator's to rule on (§8):

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

**Remember.** The tag turn writes nothing to the Notebook (DEC-249 rule 4).
`remember` records a Record item marking the message as a statement to keep,
and raises a stimulus. That wakes the tier's own writer, which writes the
entry citing the message. The tier is the one the statement's question names:

| said | goes to | written by |
|---|---|---|
| who someone is ("I'm vegetarian now", "call me Kit") | Identity, `told` *(now Persona's identity part, 35)* | the Character adaptor *(now the Persona adaptor)* |
| a settled fact of the unit ("our usual place is X") | Register · memo | Cognition |
| something open or dated ("Japan in March") | Register · note or event | Cognition |

`remember` (today a Honcho `conclude`) becomes that Record item and stimulus.
*(Superseded for the first row on 2026-10-17, DEC-317: on the graph surface
`remember` writes the `told` identity line itself, through the Persona
write's judge; [35](35-persona.md) §1. The memo and note rows stand.)*

**Correct.** Nothing is overwritten:

1. The correction is a Record item (`persona_dispute`, or the told line
   itself), and the disputed line stops rendering at once. *(Superseded by
   [35](35-persona.md) §2: only the subject's own dispute hides a line at
   once; anyone else's marks it `disputed` for the next session.)*
2. The writer's next session re-evidences the line or removes it
   (cognition-loop/50 §4).
3. ~~Everything whose citations reach it is marked `unsupported` and goes
   back for re-derivation (the retraction cascade).~~ *(Superseded: the
   cascade was deleted 2026-10-03, migrations 0092/0093. Nothing derived is
   re-marked; [35](35-persona.md) §5.)*
4. The revision chain keeps what was believed and when it changed.

**Only the subject may dispute a line about themselves** (cognition-loop/50
§4). Anyone else can add their own told evidence, which sits beside it.
**Assent never upgrades an abduction**: "yeah" to "is it because evenings are
short?" does not make the hypothesis a fact (cognition-loop/10 §4). It takes a
statement with content.

**Seeing it.** The portal shows each person the Identity and Character about
them, with the evidence behind each line (cognition-loop/50 §4).
`persona_dispute` (formerly `character_dispute`) is designed and **not
built**; it is this bundle's build. The citation-edge table is not: it was
dropped 2026-10-03 (migration 0093), and the memory index's `memory_edge` is
read by the retrieval service, not by a cascade ([25](25-memory-graph.md) §1).

## 8. For the operator

*(All answered 2026-10-15 in [35](35-persona.md).)*

> 2026-10-21 (18 / ADR 0026): [18](18-extraction.md) amends the historical answers below: source statements live in Record extraction, while Character entries are bounded Notebook judgements with profile, traits and hypotheses, following [ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md).

0. The DEC-249 amendment above: per-person facts move from Memory into the
   Notebook. Without it, Identity has no lawful home once Honcho goes.
   *(Ruled: into Persona, 35 §1.)*
1. Identity as a fifth Notebook tier (recommended), or sections inside
   Character. *(Ruled: neither; one Persona account, 35 §1.)*
2. Abduced lines in Character as marked hypotheses, or abduction kept to
   Register and Standing.
3. Does the operator override "only the subject may dispute"?
3a. `Unsure` replaced by explicit per-line modes (§5): a Character schema
   change.
4. Visibility of a told Identity line: today's lineage rule (its origin room,
   then the person's rooms in the unit, cognition-loop/50 §5). *(Ruled: the
   unit is the boundary and lineage stays off, 35 §3.)* The prior-art
   review found one person's disclosure surfacing in another's session to be
   the main multi-user risk ([40](40-prior-art.md)).
5. A watch, not a decision: a stored profile measurably raises a model's
   agreeableness (Jain et al.). In a group the risk is mirroring whoever Nora
   knows best. The eval should carry a case for it.
