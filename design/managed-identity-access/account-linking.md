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

The request binds the exact initiating and receiving accounts — platform and
matchable identifier, never a display name or handle — the surviving canonical
person, the approved effect, and the identity, ownership and authority
revisions observed at approval. Changing any of these invalidates the
approvals. Approval is short-lived, single-use and bound to the specific
request. Refusal, cancellation or expiry leaves the accounts unlinked; a
forwarded challenge or a replay cannot approve another association. Account
proofs and approvals are delivered privately to the holders, not posted into a
shared room, and a challenge redeemed by the initiating side is not the
receiving holder's approval.

## Platform account proofs

Each side's approval is expressed on a private surface while authenticated as
the exact account named in the request. The authentication proves control of
that account; a separate explicit approve action, bound to the request's id
and revisions, supplies consent. The mechanisms differ per platform because
the providers do:

- **Signal** has no OIDC or OAuth. Account control is proven by a challenge
  delivered privately to the exact account's own direct address — the bound
  matchable identifier (`phone` or `uuid`), never the person's preferred
  direct target. The issuer scope is Nora's own Signal service account: the
  proof is delivered by the installation's signal-cli, not a third-party
  provider. The holder then approves or refuses the request on the private
  management surface under a session minted by that same account's proof.
  Inbound direct messages are not a consent channel: the v2 Signal parser
  drops them (`conversation_type` is group-only), and a forwarded code
  redeemed by the initiator proves nothing about the receiver. The small
  private transport addition — delivering the challenge to a candidate
  account's direct address and minting the account-bound session on
  redemption — is part of the link service implementation, not a room
  feature.
- **Discord** supplies provider login: OAuth2 `identify` plus
  `GET /users/@me` yields the account's snowflake, scoped to Nora's own
  Discord OAuth application (client id). A `discordId` captured on the JWT at
  initial sign-in is stale provenance, not consent for a new link: the
  approval surface requires a fresh authenticated confirmation bound to the
  request — re-authentication or an approval action that re-derives the
  account — while the session proves that exact snowflake. Discord direct
  messages are not a consent channel either: the v1 adapter drops
  guild-less messages.
- **Telegram** supplies provider login independent of any Nora messaging
  adapter: the Telegram Login OIDC flow (authorization code with PKCE,
  registered allowed URLs, bot client id/secret) returns an ID token whose
  audience is the bot's client id and whose subject is the Telegram user id.
  The token is validated against Telegram's published keys; the issuer scope
  is the specific Telegram bot registration. The same private-surface
  explicit approval then applies.

A platform with none of these — no provider login and no private delivery to
the exact account — is unavailable for linking, not approximated: registration
and room admission never double as ownership proof.

Whatever supplies the proof, the approval records which exact account —
platform, identifier kind, value and issuer scope — proved control. Today's
OTP challenge retains only a person and platform and targets the person's
preferred direct address; a session minted from it cannot name which account
of that person proved control. Link consent therefore requires account-exact
provenance that the current person-level challenge does not carry: the
challenge, its redemption and every holder-visible approval name the account,
and the session binds that same account for its lifetime.

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
