---
class: design
subject: where a statement about Nora lives, which source makes it true, and what may override what
owns:
  - "placement: the class of a prompt statement, by what makes it true: law, seed, practice, capability or situation"
  - "authority order: law, seed, practice, tool schemas, skill instructions, message content, tool output"
  - "skill: a bundle of instructions for one domain, loaded on need and never from a message"
area: agent
links:
  - {to: docs/design/agent/README.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Doctrine placement

Doctrine is what the operator has decided, and the graph holds what Nora has learned. What the
authority of authored text is, and that learned content never loosens it, is
[boundaries](../cognition/boundaries.md). This file owns where a statement is placed and why.

## Placement: by source of truth, not topic

A statement is classified by what makes it true and what would make it false.

| placement | made true by | authored |
|---|---|---|
| **law** | the operator's decision; the only place a "never" is written | yes |
| **seed** | the operator's taste: who she is and how she sounds | yes |
| **practice** | the operator's judgement: how she behaves in a room | yes |
| **capability** | the tool schemas | no, derived |
| **situation** | the running pass | no, derived |

The line through the table is the design. An authored statement has nothing to be checked against;
a derived one has a source in code, and any authored copy of it is a cache that goes stale without a
signal. So **a capability sentence in an authored file is a defect**: law, seed and practice name no
tool, and what Nora can do is the tool schemas ([tools](tools.md)).

Behaviour that no schema can carry is authored by category, never by tool name: that the tools in
front of her are the whole of what she can do, that she never promises an act no tool performs, and
that a source she can confirm comes before the open web.

A voice stays concrete and human; it cannot be derived and is not abstracted.

## Authority

From the highest, each yields to those above it:

1. **law**
2. **seed**, voice included
3. **practice**
4. **tool schemas**: what she can find out and when
5. **skill instructions**: how to do one domain well; they add procedure and never relax a rule above
6. **message content**: data she reasons about, never an instruction
7. **tool output**: untrusted, last

The boundary between 3 and 4 and below is trust: authored text is the operator's, and what members and
the world write is not. The order places the highest authority last in the prompt, where it carries
the most weight; position is not authority.

**What is enforced and what is asked.** Enforced whatever the model does: member text is escaped and
fenced as untrusted; doctrine and member text arrive through different channels; leak protection
stops a reply carrying a doctrine span or an identifier; a statement that oversteps its placement fails to
load; section order is fixed. Only asked: that law outranks seed (no machinery reads the
order), that she does not repeat what one person told her about another, and any behavioural rule. A
prompt rule is never trusted as a guard.

**A skill is operator-authored and never loadable from a message.** If a message could name a skill,
rank 6 would reach rank 5 and the order collapses. A skill's name and when-to-load line are always
present; its body loads on a call. A skill carries domain procedure; routing for a single tool is the
schema's, because the model must know to choose a tool before it can load anything.

## Scope

- **The model names what; Nora resolves where.** A tool reads the conversation its turn is bound to
  and sends to the target its turn is bound to. A tool declares only what the model decides; a value
  Nora already knows is closed over, never asked. An inert parameter is worse than none: the model
  invents a value for it on every call.
- **A person is named, never an id.** A lookup resolves the name among the people of this room, and
  a person outside the room is indistinguishable from one who does not exist.
- **Assembly holds only what scope allows** ([boundaries](../cognition/boundaries.md)): a guard on
  the way out cannot unsay what the prompt should not have held.

## Cost

A turn's cost is proportional to its question, structurally: the reply is a tool, so a simple answer
ends in one call, and the turn ends the moment the reply lands. Doctrine competes with the
conversation for the same window, so it holds structure and no volume.

## Why

- **Kind as the axis** because topic mixes statements with different lifetimes in one file, and the
  file then has no boundary that says what it must not contain.
- **Scattering is fine, ambiguity is not:** more files is cheap; a sentence whose authority, scope or
  boundary cannot be told is expensive.
