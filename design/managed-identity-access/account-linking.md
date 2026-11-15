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

- **Signal** has no OIDC or OAuth, and on Signal account proof and consent
  stay two separate acts. A challenge delivered privately to the exact
  account's own direct address — the bound matchable identifier (`phone`
  or `uuid`), never the person's preferred direct target — proves control
  of that account only. It is proof, not authority: redeeming it cannot
  mint an approval-capable session, and a code forwarded to the initiator
  gives the initiator nothing the receiver did not perform — a forwarded
  text replayed from the initiator arrives under the *initiator's* account
  identity and is refused. The issuer scope is Nora's own Signal service
  account: the proof is delivered by the installation's signal-cli, not a
  third-party provider. A holder's approval or refusal — on either side —
  is an explicit, request-bound private message sent from the exact
  account the request names to Nora's service account, authenticated by
  signal-cli's transport provenance (the envelope's source identity, never
  quoted body or claimed sender text) and bound to the request nonce
  delivered with the challenge plus the visible pair, surviving person and
  effect. Group, quoted, forwarded-content and reaction messages are not
  approvals; a mismatched account, a replayed, expired or changed request
  refuses; an unavailable transport fails closed. This is a narrow
  management-consent envelope at the transport, not a direct-message
  feature: the v2 Signal parser currently drops non-group envelopes, so
  the consent handler is the link service's own seam, not room chat.
  The private challenge displays the exact pair, survivor and effect, then
  supplies the request-specific reply `!!!link approve <request-id> <nonce>`
  (or `refuse`). The transport parser retains typed sender identifiers and
  message timestamp; the link service checks their current binding and the
  pending request before recording a decision. The parser alone never
  approves a link. A legacy envelope without a typed sender identifier is
  unavailable for consent rather than guessing an identifier kind.
- **Discord** supplies provider login: OAuth2 `identify` plus
  `GET /users/@me` yields the account's snowflake — the provider-global user
  id, bound as the ownership key `discord.user_id`. Nora's OAuth client id
  is the proof's audience: it records which application vouched for
  control, not a second namespace of the account identifier. A `discordId`
  captured on the JWT at initial sign-in is stale provenance, not consent
  for a new link: the approval surface requires a fresh authenticated
  confirmation bound to the request — re-authentication or an approval
  action that re-derives the account — while the session proves that exact
  snowflake. Discord direct messages are not a consent channel either: the
  v1 adapter drops guild-less messages.
- **Telegram** supplies provider login independent of any Nora messaging
  adapter: the Telegram Login OIDC flow (authorization code with PKCE,
  registered allowed URLs, bot client id/secret, `openid` plus `profile`
  scopes) returns a signed ID token and the profile surface's user object.
  The token is validated against Telegram's published keys — signature,
  issuer, audience (the bot's client id), expiry, nonce and state — and
  the issuer/audience/subject triple is proof provenance: the OIDC `sub`
  is the login subject, *not* the Bot API user id, and is never used as
  the account key. The ownership key is the profile surface's `id`, bound
  as `telegram.user_id`; a response missing it is an invalid or
  unavailable proof, never a guessed mapping. The same explicit,
  request-bound approval then applies on the authenticated surface.

A platform with none of these — no provider login and no private delivery to
the exact account — is unavailable for linking, not approximated: registration
and room admission never double as ownership proof.

Whatever supplies the proof, the approval records which exact account —
platform, identifier kind, value and issuer scope — proved control, and
which authenticated surface carried the consent. Today's OTP challenge
retains only a person and platform and targets the person's preferred
direct address; it cannot name which account of that person proved
control, so it grounds neither a session nor a message-bound approval
under this contract. Link consent therefore requires account-exact
provenance the current person-level challenge does not carry: the
challenge, its delivery and every holder-visible approval name the exact
account, and the surface that carries consent — provider session or
transport-authenticated message — binds that same account.

## One pending request, one published result

The link service owns the pending association. Neither a partial approval nor an
Ory callback publishes ownership. At commitment it revalidates account control,
current ownership, identity state, approvals and caller authority. Conflicting
requests cannot move one account between people: the binding store serializes
the ownership change and rejects a candidate whose approved inputs have changed.
A reused `operation_id` carrying a different effect is a conflict, not a retry;
a competing ownership change invalidates approvals observed before it. Repeating
a completed request returns its recorded result without applying it again.

Every link-lifecycle operation resolves to one of the four outcomes:

| Operation | complete | denied | unavailable | incomplete |
|---|---|---|---|---|
| `request_link` | request recorded, approvals pending | actor not bound to the claimed initiating account; invalid pair or survivor | binding/identity store unreachable | n/a — nothing committed |
| `approve_link` / `refuse_link` | side's decision recorded; the link commits only once both sides approved the identical request | wrong account for the claimed side; expired, mismatched or changed request | request or session state unreadable | n/a — a decision either records or does not |
| `cancel_link` | request withdrawn | actor is not the initiating side | request state unreadable | n/a |
| commit (inside `approve_link`) | binding, canonical alias and normalized grants published and read back | revalidation refuses: account moved, survivor changed, identity suspended | Kratos, binding store or Keto unreachable | part committed — dependent access stays blocked |
| `unlink_account` | binding, derived sessions and account-derived access revoked and read back | actor is not the authenticated holder of that exact account | session, binding or Keto revocation cannot be confirmed | partial revocation — affected authority stays blocked locally until reconciliation agrees |

An `incomplete` result is never reported as `complete`: dependent access
remains blocked until the required stores agree, and a failed revocation
blocks the affected authority locally while reconciliation completes.
A database commit in one service is not a claim that Kratos, Nora and Keto
committed a distributed transaction.

## Independently registered identities

Two accounts may already have separate canonical people. Linking then unifies
their account ownership under the explicitly approved surviving person. References
to the other person remain resolvable through a canonical alias; they are not
silently discarded, rewritten as new historical authors or duplicated into a
second set of records. New writes use the surviving identity.

The same consent covers the resulting ability to act through either account
within existing grants. The link service reconciles grants and canonical
resolution before enabling that access: each side's positive grants normalize
to the surviving person inside the exact scopes they were granted in, carrying
their provenance, and explicit restrictions normalize the same way — deny
always wins. Neither union expands a grant's scope, containment or audience.
Where both sides held the same grant the surviving record keeps both
provenances (or an idempotent equivalent), so a later revocation cannot
silently discard an independently supported grant. Narrowed revocations and
explicit restrictions remain effective; an old alias is not a route around a
denial. Account-bound sessions cannot keep acting under an independent
retired subject. Keto and all application consumers resolve through the same
canonical identity.

Unifying identity does not copy transcripts, merge units or expose another unit's
profile. Historical author and source provenance remains intact, and every read
continues through its original unit/audience boundary. The service records the
association and consent needed to explain identity resolution, without making
the holder's other accounts visible to room administrators.

An unlink is a separately authenticated, authorized ownership change made by
the holder of that exact account — not an installation power: installation
authority suspends a global identity, but never asserts account ownership. It
detaches the account binding and revokes the sessions and account-derived
access the binding carried. The canonical alias and historical author
references remain — unlink restores no arbitrary pre-link permission snapshot
and performs no implicit split — while the person's remaining verified
accounts keep the current canonical grants and restrictions. Splitting
historical data ownership is a separate, explicitly scoped operation, not an
incidental effect of removing a login method.

## Why discovery is insufficient

A platform authenticates an account on that platform, not a person across all
platforms. Matching names, entering a phone number, sharing a server or accepting
an operator's suggestion does not prove joint ownership. Ory's social-login
linking is useful authentication machinery, but it does not provide Nora's
bilateral consent protocol for arbitrary messaging accounts.

[Ory account linking](https://www.ory.com/docs/kratos/social-signin/link-multiple-provider-account)
describes provider-login linking. Nora preserves the stronger bilateral approval
contract regardless of which provider or interface supplies an account proof.
