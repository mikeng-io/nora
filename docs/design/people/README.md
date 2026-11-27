---
class: design
subject: people, units and rooms, who Nora recognises and where she speaks with them
owns: [people area, the parts and connections of identity, authority and rooms]
area: people
links:
  - {to: docs/design/cognition/graph.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# People

Nora recognises people, and she speaks in rooms. This area answers three
questions before any pass runs: **who is this**, **what may they do here**, and
**which room is this**. Cognition owns what she knows about them; this area owns
who they are, where they are admitted and how a room is found.

## Parts

| part | answers | defined in |
|---|---|---|
| **identity** | who a sender is: one identity, many platform accounts | [identity](identity.md) |
| **linking** | how two accounts become one identity, only by both holders | [linking](linking.md) |
| **authority** | who may talk to her, where, and who may change what | [authority](authority.md) |
| **rooms** | the unit, room and conversation each message belongs to; how a room is addressed and listed | [rooms](rooms.md) |
| **direct messages** | one person's private conversation across their accounts | [direct messages](direct-messages.md) |

## Connections

Platform bridges report authenticated account observations. Identity resolves an
account to an identity; authority decides what that identity may do in the room's
scope; rooms place the message in its unit. The graph then holds a unit-scoped
entity for the identity ([graph](../cognition/graph.md)), and the engagement loop
runs with the resolved sender, room and unit. Nothing downstream resolves a
sender again from raw text.

Each unit is one boundary. A person in two units has independent state in each,
and recognising an account never reveals their other accounts, memberships or
another unit's understanding.

## Invariants

- **One identity, many accounts.** An identity is one registered subject, whether
  a person, an agent or a bot; a new platform adds an account to it, never another
  identity.
- **A display name never authenticates.** A name, handle or nickname is shown,
  never matched, and never proves ownership of an account.
- **Ownership is proven, never inferred.** Matching names, a shared room or an
  operator's suggestion do not link accounts; only both holders do.
- **One authoritative writer per fact.** Identity and grants come from one
  selected backend; an unavailable backend is unavailable, never a permissive or
  stale fallback.
- **Refused and unable-to-check are different answers**, and neither authorises.
- **The actor comes from the authenticated invocation**, never from a model
  argument or quoted text.
- **A room is addressed by identity, not by guess.** Room names are unique within
  a unit; when two rooms fit, she asks.
- **Her own situation is generated, not authored.** The rooms, transports and
  handles she states come from live policy and adapters at the time she speaks.

## Where it lives in the code

| part | owner |
|---|---|
| identity, linking, authority, admission | `people/` |
| the room directory and the live policy it reads | `people/` |
| the graph's unit-scoped entities for identities | `memory/` |
