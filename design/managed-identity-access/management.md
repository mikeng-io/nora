# Private management and platform synchronisation

## One application boundary

Nora's managed identity capability exposes a private service API for account
registration/resolution, profile updates, link requests and approvals,
membership synchronisation and scoped grant changes. Platform adapters, in-room
management and the Web App use those same operations. They do not write Kratos,
Keto or a private TOML file independently.

The API authenticates service callers using Nora's service-authentication
boundary and authorizes each caller's permitted sources and resources. Network
privacy alone is not caller authentication. Ory administrative endpoints and
credentials are accessible only to the management service and authorized
installation administration; they are not model tools or public browser APIs.

Service provisioning uses `POST /app/v1/management/operations`. Each configured
credential identifies one service, its permitted platform sources and an exact
set of approved scopes. Its JSON body supplies the typed operation, never the
actor. This service entry point admits registration and roster synchronisation;
it cannot supply holder consent or scoped human authority. The server refuses
duplicate credentials and service identities, and every response is private
and non-cacheable. The use case returns the same four mutation outcomes; HTTP
success means `complete`, not merely that a request reached an Ory endpoint.

Bulk onboarding uses `POST /app/v1/management/rosters` under the same service
credential and approved scope. A snapshot names its platform, collection,
audience kind, completeness and explicitly classified accounts. Unknown account
types make a claimed complete roster unavailable; bots are not registered as
people. A collection audience cannot grant room membership. Each new account
starts an independent person without a registration-sourced membership; the
collection sync supplies its retractable support. The durable batch identity
binds the exact effect, and a retry resumes unfinished account correlation and
membership without recreating people or attaching accounts to existing people.

Automated work carries its service identity and approved onboarding scope;
human-triggered work also carries the verified human actor. Every operation
therefore carries a typed actor — a service principal acting within its
approved sources, or an authenticated holder — resolved once from the
authenticated invocation; neither a model argument nor a quoted message can
nominate another actor or enlarge the service's scope. Registration of an
observed account is not permission to claim it for an existing person. Repeated
events and retried requests reuse the same registration and operation identity.

The shared contract lives in `nora/domain/management.py`: the closed operation
vocabulary, the four actor kinds (`service`, `holder`, `operator`,
`installation`), the typed targets (account, person, membership, scope) and the
outcome set — `complete`, `denied`, `unavailable`, `incomplete`. Every
operation carries an idempotency `operation_id`, the actor, the target, a
per-kind typed payload — never an arbitrary mapping — and the
identity/ownership/authority revisions its submission observed. The
constructed operation is an immutable snapshot: a caller cannot change a
validated effect by mutating the structures it was built from, and reusing an
`operation_id` for a different effect is a conflict, not a retry. The
vocabulary encodes the rulings above: ownership operations belong to the
authenticated holder of the exact account — a service principal, a scoped
operator or the installation cannot approve, refuse or unlink an account
binding (installation suspends the global identity but never asserts
ownership) — while membership, grant and delegation changes belong to scoped
operators, and registration and roster synchronisation belong to service
actors within approved sources. An incomplete result means part of the effect
committed and reconciliation is still owed; it is never reported as complete,
and access depending on the unapplied remainder stays blocked.

## Explicit and semantic in-room requests

An explicit command prefix and ordinary language are alternate expressions of
the same management intent. A prefix distinguishes a command from conversation;
it confers no authority. A model can interpret an intent, but cannot nominate the
authenticated actor or decide whether authorization succeeded.

Context assembly supplies the verified actor and current room/unit/workspace
context once. Target resolution uses platform mentions or authoritative account
bindings. An ambiguous display name or an unspecified consequential scope needs
clarification before a write; quoted messages, forwarded requests and transcript
content are not fresh management instructions from the quoted author.

The resulting typed operation passes through the private boundary's permission
check. Identity linking always uses the [bilateral protocol](account-linking.md),
even when the initiating sentence was explicit. Routine scoped membership changes
need the delegated operator's authorized request, not an invented bilateral
account-link ceremony. A successful response states the applied effect and scope;
unavailable or refused results cannot be narrated as completion.

## Platform discovery and membership

An operator approves which platform collections Nora may onboard and which
membership rules apply. A bridge supplies accounts, membership observations and
the provenance necessary to interpret them; it cannot grant authority outside
that approved scope. Platform adapters normalize these observations into the same
domain concepts rather than teaching each consumer a separate identity model.

An initial roster supports bulk registration. Join, leave and role events keep
membership current, and reconciliation repairs missed events after reconnects.
An authenticated newly observed sender can be registered under the same approved
rule without waiting for a full roster. Neither route guesses a cross-platform
link. Bots and other non-person accounts are not silently classified as humans.

Roster completeness is explicit. Only a successful, complete observation of the
relevant collection can establish absence from it; a partial page, lost intent,
timeout or unavailable roster does not remove every missing member. Failed
synchronisation is visible as unavailable, and fresh privileged decisions do not
rely on membership evidence known to be incomplete or stale.

The shared mutation boundary owns the one freshness policy every roster path
answers to: the producer stamps each observation with its own upstream-read
instant, a complete roster may establish absence only inside one sync interval
of that instant (clock disagreement bounded), and missing, stale or future
evidence is unavailable rather than an empty synchronisation. One durable mark
per authenticated service, collection and scope records the newest accepted
instant and its effect: an older observation never supersedes it, a replay of
the same instant and effect returns its recorded outcome, and the same instant
carrying different content is refused. A newer accepted observation — including
a partial one — orphans an older journaled effect's pending steps, which close
as superseded instead of applying.

A guild/server roster is not a channel audience. Room membership and disclosure
use the actual channel visibility and the existing roster/read contract. Leaving
one collection revokes the grants derived from that membership while preserving
other memberships, independently granted access and canonical identity. Sync
support is keyed by the authenticated service and its collection identifier,
inside the approved target scope. A complete snapshot retracts only that
source; another collection mapped to the same unit retains its own support.
Journal replay retains the same source and stable account ordering. Direct
operator restrictions remain effective when automatic synchronisation runs.

## Web App

Discord authentication and the existing Signal/WhatsApp code flow resolve to the
same canonical person as messaging. Possession of a phone-number string is not
authentication; the delivered challenge must prove control of the account.
The Web App can initiate and present linking, but both sides approve the exact
association independently. An existing Web App session is not consent to a new
link, and a challenge redeemed in the initiating browser is not a substitute
for the receiving holder's explicit approval — on Signal no challenge mints an
approval-capable session at all: consent there is the request-bound private
message the link contract describes.

Session identity, account ownership and permissions are checked against current
state. Disabled identities, revoked bindings and canonical aliases cannot leave
a session acting as an obsolete independent person. The portal's pages and API
routes share this enforcement rather than relying on a page-level login gate.

The Web App acts as a configured authentication gateway after verifying provider
login. A separate private key signs short-lived assertions bound to the exact
HTTP method, path and body; provisioning and OTP bearers cannot sign them. The
assertion retains the original account/person binding and revisions, not roles
or an actor chosen by request JSON. Scoped human mutations revalidate that
binding and current permission inside the same write gate as their effects.
Browser writes also require the portal's own origin. Authentication never
supplies bilateral consent or installation authority.

## Rationale and integration references

The design uses the provisioning pattern of a stable application principal and
shared registration logic, rather than copying an application's user/tenant
schema. Financial-account provisioning, single-default-tenant constraints and
wildcard capability grants are not Nora's person/unit/room model.

The existing [platform bridge architecture](../platform-bridges/00-contract.md) owns
authenticated event handoff, and [file reload](../policy-reload/README.md) owns
file publication. The managed service connects to these boundaries rather than
building another event transport or rewriting file-mode reload semantics.

[Discord's member API](https://github.com/discord/discord-api-docs/blob/main/developers/resources/guild.mdx#list-guild-members)
illustrates why adapters must report capabilities and completeness: full member
enumeration needs the privileged members intent and pagination. Other platforms
have different discovery limits; universal identity does not imply universal
roster visibility.
