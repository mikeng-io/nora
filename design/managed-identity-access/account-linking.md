# Bilateral account linking

## Authentication, authorization and consent

Linking asserts that two platform accounts belong to one person. Both holders
authenticate, are authorized to act for their respective accounts and explicitly
approve the same link. Authentication proves account control; consent approves
the identity change. Completing a login or observing an account does not itself
approve a link.

The initiating side approves the exact target account and intended canonical
person. The receiving side independently authenticates and approves that same
association. A scoped operator may facilitate a request, but cannot supply
either holder's approval or use registration to attach an account to an existing
person. Imported, operator-maintained file bindings are an explicit backend
handoff, not a bypass offered by the managed linking API.

The request binds the two accounts, the selected canonical person and the effect
being approved. Changing any of these invalidates the approvals. Approval is
short-lived, single-use and bound to the specific request. Refusal, cancellation
or expiry leaves the accounts unlinked; a forwarded challenge or a replay cannot
approve another association. Account proofs and approvals are delivered privately
to the holders, not posted into a shared room.

## One pending request, one published result

The link service owns the pending association. Neither a partial approval nor an
Ory callback publishes ownership. At commitment it revalidates account control,
current ownership, identity state, approvals and caller authority. Conflicting
requests cannot move one account between people: the binding store serializes
the ownership change and rejects a candidate whose approved inputs have changed.
Repeating a completed request returns its result without applying it again.

The result is reported as linked only after canonical resolution and the
authorization consequences are consistent. An unavailable identity, binding or
permission service leaves the operation incomplete and blocks access dependent
on the candidate association. A database commit in one service is not a claim
that Kratos, Nora and Keto committed a distributed transaction.

## Independently registered identities

Two accounts may already have separate canonical people. Linking then unifies
their account ownership under the explicitly approved surviving person. References
to the other person remain resolvable through a canonical alias; they are not
silently discarded, rewritten as new historical authors or duplicated into a
second set of records. New writes use the surviving identity.

The same consent covers the resulting ability to act through either account
within existing grants. The link service reconciles grants and canonical
resolution before enabling that access. Narrowed revocations and explicit
restrictions remain effective; an old alias is not a route around a denial.
Account-bound sessions cannot keep acting under an independent retired subject.
Keto and all application consumers resolve through the same canonical identity.

Unifying identity does not copy transcripts, merge units or expose another unit's
profile. Historical author and source provenance remains intact, and every read
continues through its original unit/audience boundary. The service records the
association and consent needed to explain identity resolution, without making
the holder's other accounts visible to room administrators.

An unlink is a separately authenticated, authorized ownership change. It
invalidates affected sessions and account-derived access without deleting
historical provenance or restoring an arbitrary pre-link permission snapshot.
Splitting historical data ownership is not an incidental effect of removing a
login method.

## Why discovery is insufficient

A platform authenticates an account on that platform, not a person across all
platforms. Matching names, entering a phone number, sharing a server or accepting
an operator's suggestion does not prove joint ownership. Ory's social-login
linking is useful authentication machinery, but it does not provide Nora's
bilateral consent protocol for arbitrary messaging accounts.

[Ory account linking](https://www.ory.com/docs/kratos/social-signin/link-multiple-provider-account)
describes provider-login linking. Nora preserves the stronger bilateral approval
contract regardless of which provider or interface supplies an account proof.
