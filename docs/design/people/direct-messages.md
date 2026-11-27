---
class: design
subject: a person's private conversation with Nora across their linked accounts
owns: [direct message scope, DM admission]
area: people
links:
  - {to: docs/design/people/rooms.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Direct messages

A direct message is a conversation between Nora and one person. It is a **room of
the primary unit**, and what it reads, what it keeps and what may leave it are the
rules in [boundaries](../cognition/boundaries.md). This file owns only how a DM is
admitted and kept continuous across a person's accounts.

## Admission

A DM needs an eligible identity, a supported platform, and the identity listed in
the primary unit.

- The sender and the direct peer each resolve to the same identity
  ([identity](identity.md)). An unknown peer, or an identity the primary unit does
  not list, is refused before anything is recorded or run.
- A platform is admitted by being supported for DMs, not by having a parser or a
  send route. Unsupported media is refused before custody.
- A platform can be opted out per identity, which applies to inbound work and to
  later sends alike.

## One scope across accounts

A **DM scope** is the identity paired with the primary unit. Every admitted
account of that identity on every admitted platform shares it.

- One transcript interleaves platform-marked lines from all linked accounts in
  arrival order. Independent per-platform pages are never joined and presented as
  one conversation, and arrival order claims no agreement between transport clocks.
- One turn slot per scope covers assembling the transcript, the pass, the guarded
  delivery and the state it updates. A message arriving on another platform is
  recorded and waits; it cannot run a simultaneous turn over the same transcript.
  Another person's scope is independent.
- A reply goes only to the inbound platform and the original direct target. Scope
  is a key for reading and serialising, never a destination or a fallback account;
  retries never choose another linked account.
- Quotes, edits, reactions and de-duplication keep platform-local keys, so a
  colliding id or time on another platform cannot cross.
- Operational identifiers in logs and traces are opaque; the native target stays
  inside the transport.

## Change and revocation

Revoking a grant or opting a platform out stops private reads and later sends. It
does not erase what was already disclosed. A person's request to forget or dispute
something hides it at once and keeps the request and its reason private.
