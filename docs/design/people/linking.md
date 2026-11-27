---
class: design
subject: how two platform accounts become one identity, by both holders' consent
owns: [account link, bilateral consent, canonical alias, unlink]
area: people
links:
  - {to: docs/design/people/identity.md, rel: uses}
  - {to: docs/design/people/authority.md, rel: uses}
---

# Linking accounts

A **link** asserts that two accounts belong to one [identity](identity.md).
Discovery cannot establish it: a platform authenticates an account on that
platform, not a person across platforms. Matching names, a phone number typed in,
a shared server or an operator's suggestion prove nothing.

## Consent is bilateral

- **Both holders** authenticate as their own exact account and explicitly approve
  the same link. Authentication proves control; approval is a separate act that
  consents to the identity change.
- An operator may facilitate a request. An operator cannot supply either
  approval, and registering an account never attaches it to an existing identity.
- Accounts under one controller can still be different identities. A link is
  neither delegation nor attribution of an agent's acts to its controller.
- The request names the exact accounts by platform and identifier, the surviving
  identity and the effect. Changing any of these, or the identity, ownership or
  authority state seen at approval, voids the approvals.
- An approval is short-lived, single-use and bound to its request. Refusal,
  cancellation or expiry leaves the accounts unlinked. A forwarded challenge or a
  replayed message approves nothing.
- Proofs and approvals reach the holders privately, never in a shared room.

## Proof differs by platform, the contract does not

Each platform proves control its own way: a private challenge delivered to the
exact account on Signal, provider login on Discord and Telegram. The proof
records which exact account, which identifier and which authenticated surface
carried it. A platform with neither a provider login nor private delivery to the
exact account cannot link; it is unavailable, never approximated.

## One request, one published result

The link service owns the pending request. At commitment it revalidates account
control, current ownership, identity state, both approvals and the caller's
authority, then publishes the binding, alias and grants together.

- Conflicting requests cannot move one account between identities.
- Repeating a completed request returns its recorded result. Reusing a request
  identity for a different effect is a conflict, not a retry.
- Each step ends **complete**, **denied**, **unavailable** or **incomplete**
  ([authority](authority.md#outcomes)). Part-committed work is never reported
  complete, and access depending on the unfinished remainder stays blocked.

## After a link

When both accounts already had identities, linking unifies ownership under the
approved survivor. The other identity stays resolvable as a **canonical alias**;
history keeps its original authors and is never rewritten.

- Each side's grants and restrictions carry to the survivor inside the scopes
  they were granted in, with their provenance. Deny always wins, and neither side's
  union widens a scope.
- Linking copies no transcript, merges no unit and exposes no other unit's
  understanding. Every read stays behind its original unit boundary.
- Account-bound sessions cannot keep acting as a retired subject.

## Unlinking

Only the authenticated holder of that exact account can unlink it; installation
authority can suspend an identity but never asserts or removes ownership. Unlinking
detaches the binding and revokes the sessions and access that binding carried. The
alias and historical authors remain, no earlier permission state is restored, and
no data is split. Splitting history is a separate, explicitly scoped operation.
