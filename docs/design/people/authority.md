---
class: design
subject: admission, scoped grants and the one management boundary that changes them
owns: [admission, scoped authority, management operation, operation outcomes, roster synchronisation, live policy change]
area: people
links:
  - {to: docs/design/people/identity.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Authority and admission

## Who may talk to her, and where

Policy is **deny by default**: Nora refuses to start if it admits nothing. It has
two independent grants, because a direct conversation has no room to list:

- **principals**, the identities that may talk to her directly;
- **conversations**, the rooms she takes part in, and for each, which identities
  may speak or that any sender may.

Both room admission and the sender's permission must hold. A room that admits any
sender admits unresolved ones too, which widens neither its read nor its
disclosure scope. Grants name identities, never raw platform ids, so adding an
account to an identity grants it everywhere the identity is already allowed.

A platform's own roles and rosters are observations. Nora grants nothing from
being a guild owner or platform administrator; an installation may delegate
bounded authority to a role explicitly, and removing the role removes what it
carried. Tool use, delivery, background work and web reads check current
authority, not only the inbound gate.

## Scoped authority

Authority is scoped by containment: **workspace** (a group of units under one
operator), **unit**, **room**. Containment is verified from Nora's own
configuration, never from a caller-supplied name. The reading boundary stays the
unit ([boundaries](../cognition/boundaries.md)).

- **Membership and management are different.** Room membership grants speaking and
  viewing in that room only; membership never carries an operator power.
- Grants and restrictions inherit downward. **Deny always wins**, and a
  restriction applies at its scope and below.
- Registering, granting access and delegating power are separate operations.
  Adding a member does not permit appointing an operator or editing the
  operator's own authority.
- Holders approve their own links and ownership changes
  ([linking](linking.md)); room and unit administration cannot.
- Installation authority sets the backend, the first management authority and the
  global identity lifecycle. It is established out of band, never by the first
  identity to issue a command in a room.
- A local nickname is descriptive and Nora's own, set by scoped operators. A scoped
  name view uses the nearest nickname up the room-to-workspace chain, then the
  global name; siblings never borrow each other's. It follows the disclosure
  boundary of the context that uses it.

## One management boundary

Platform adapters, in-room requests and the web app change identities, accounts,
membership and grants through the same private operations. None of them writes a
backend or a file on its own.

- Every operation carries a **typed actor** resolved once from the authenticated
  invocation: a service acting within its approved sources, a holder, a scoped
  operator or the installation. A model argument or a quoted message never names
  the actor or widens a service's scope.
- An explicit command and ordinary language express the same intent. A prefix
  distinguishes a command from conversation and confers no authority. An ambiguous
  target or an unstated consequential scope asks first; a quoted or forwarded
  request is not a fresh instruction from its author.
- Operations carry an idempotency identity and the revisions they observed. Authority
  is checked again when the effect applies, so a revoked operator cannot act on an
  earlier interpretation, and a revocation invalidates cached decisions.
- The web app proves provider login, then asserts the holder's identity bound to
  the exact request. Authentication supplies neither consent nor installation
  authority, and each data path still checks its own unit audience.

### Outcomes

Every mutation ends in one of four outcomes:

| outcome | means |
|---|---|
| **complete** | the effect committed and was read back |
| **denied** | the actor lacked authority, or the request was invalid |
| **unavailable** | a store could not be reached; nothing committed |
| **incomplete** | part committed; reconciliation is owed |

An explicit refusal and an inability to check are different, and neither
authorises. An unreachable authority is unavailable, never an implicit grant.
Because the identity store, the binding store and the grant store do not share a
transaction, the management service journals each change and resumes it at startup
and periodically. Dependent access stays blocked until the stores agree, and
success is claimed only on read-back.

## Roster synchronisation

An operator approves which platform collections may be onboarded. A bridge reports
accounts and membership with the provenance to interpret them; it cannot grant
beyond that scope. Join, leave and role events keep membership current and
reconciliation repairs missed events.

- A roster states its own completeness. Only a complete, fresh observation of a
  collection establishes absence from it; a partial, stale or failed one is
  unavailable and removes no one.
- The newest accepted observation per source and collection wins. An older one
  never supersedes it, and a replay returns its recorded result.
- A server roster is not a channel audience: a room's membership and disclosure use
  the channel's own visibility. Leaving a collection retracts only the grants
  derived from that collection.
- Registering an observed account grants no access and claims no other identity's
  account.

## Live policy change

Rooms, admission, people, accounts, names, labels, tools and trust flags change
without recreating any process. Each resident process keeps its last validated
snapshot.

- A candidate runs the same loaders and cross-file checks as startup and replaces
  the snapshot whole, so no reader sees a half-applied file. A broken edit leaves a
  working process unchanged and reports why.
- Content, not a timestamp, defines a change. A refused file is reconsidered only
  when its content changes again.
- A turn in flight keeps the context it captured; later admissions read the new
  snapshot.
- Workspace structure and external workspace bindings change only at a restart. A
  candidate that touches them is refused in full, because applying it partly could
  send one person's speech or understanding to the wrong tenant.
- Missing, malformed or allow-nothing policy refuses startup. Health reads stale
  while the visible file differs from the applied snapshot.
