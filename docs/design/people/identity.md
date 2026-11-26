---
class: design
subject: identity, platform accounts and identifiers, and the two backends that hold them
owns: [one identity, many accounts, a display name never authenticates, identifier kind, identity backend, identity suspension, Nora's own accounts]
area: people
links:
  - {to: docs/design/people/README.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
---

# Identity

## One identity, many accounts

An **identity** is the stable registered subject Nora recognises across every
platform, room and unit of an installation. Humans, agents and bots share one
model: registration, resolution and authority never classify the subject or how
its accounts are operated. A platform's bot flag describes an account; it neither
makes a second kind of identity nor decides permissions.

| term | meaning |
|---|---|
| **identity** | the canonical subject to which admitted accounts resolve |
| **platform account** | one provider-side registration, with one or more typed identifiers |
| **identifier** | a matchable value inside its platform and kind |
| **binding** | the verified association of an account with an identity |
| **account control** | what authentication evidence proves about one account |
| **principal** | the authenticated security identity kept for a request |

- An identity holds any number of accounts on one platform: a second phone, a
  second Discord. Many accounts per platform is the baseline, not an edge case.
- A grant to an identity covers every account it holds. Allowing an identity
  allows all its accounts.
- A controller who operates an agent's account and their own does not make those
  one identity; delegation never merges.
- Rooms and units refer to the same identity. Joining one allocates nothing.
- A sender that code cannot resolve stays unresolved on the item; she may still
  record and, where the room admits it, reply, without a person behind it.

## A display name never authenticates

A display name, profile name, handle or local nickname is descriptive. It may be
shown and rendered in a transcript. It is never matched, never indexed to resolve
a sender and never evidence of ownership. A handle is re-assignable: its owner
renames at will and the next holder of the freed name would otherwise resolve as
them, while everything downstream trusts the identity completely.

## Identifiers

- An identifier's **kind** is fixed per platform, so a typo is a startup error and
  not a silently ignored key: a phone or an account uuid on Signal and WhatsApp
  (alongside the WhatsApp linked id), a numeric user id on Discord and Telegram.
- A kind carries its issuer scope. Two different numbers for one account on one
  provider are two kinds, not interchangeable values.
- An identifier has exactly one owner, enforced where the binding is written.
  Repeated and concurrent discovery returns the existing registration.
- Resolving an identifier returns the exact account it names, never a preferred
  account of the identity, because that account is what a proof binds to.
- The identifier Nora receives and the one an operator can read are often
  different values. Nora therefore surfaces discovered identifiers; the operator
  or the holder confirms them against something they recognise. Discovery
  suggests and never binds.

## Two backends, one writer

| responsibility | file backend | managed backend |
|---|---|---|
| identity roster and descriptive profile | the authored identity file | a managed identity record, correlated to the canonical identity |
| account ownership | the validated identity file | Nora's verified binding store |
| grants and management roles | the authored policy file and operator administration | a scoped relationship store with a declared permission model |
| link consent | operator-maintained bindings | Nora's [bilateral link service](linking.md) |
| rooms, destinations and topology | Nora configuration | Nora configuration |

- The file backend suits one person or a small group on any number of platforms.
  Its writes are operator edits; management in a room reports that management is
  operator-maintained and edits nothing.
- A deployment selects one backend at startup. Managed grants are never merged
  with file allowlists, and a failed managed service never falls back to a
  permissive or stale file. Returning to files is an explicit validated handoff.
- The canonical identity is stable under either backend. The managed record is
  correlated to it, and historical references keep their original identity.
- Each admitted identity is protected from its first durable publication; it is
  unavailable to dependent access until the import that created it is complete.
- Read projections, such as a unit's roster of names, are never independent
  writable authorities. A failed refresh invalidates the projection and reuses no
  earlier one.

## Descriptive traits

An identity carries descriptive traits only: a display name, non-matchable labels
and the social opt-out. The opt-out is set explicitly at registration and read
across the identity and every retired identity it absorbed, so an opt-out recorded
on either applies after linking. A missing or malformed trait is unavailable to the
reader, never inferred as enabled, and a changed preference stops later input from
reaching a model. No path lets anyone clear another holder's preference.

## Lifecycle

Leaving a room or unit ends that membership, not the identity. Revoking a binding
removes that account's ability to act as the identity. Suspending an identity is a
separate authorised operation enforced at every entry point, including existing
web sessions; a login-state change alone is not assumed to stop messages that
arrive on another platform.

## Nora's own accounts

Nora's accounts follow the same typed-identifier model, so her own identifiers and
a person's are one concept with one matching rule. Her accounts come from the live
adapter identity, not a copy kept in prose. A degraded discovery is counted, never
silently reduced to one account. Her public handles are display text: they answer
questions about her accounts, never join sender matching or her own aliases, and a
missing handle is omitted rather than invented.
