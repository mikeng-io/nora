# Admission and continuity

## Authority before side effects

A conversational DM requires the feature grant, an eligible declared person, a
supported platform and a valid anchor unit. The sender and the direct peer must
resolve independently to that same person through operator-authored account
identifiers. Display names, profile names and text claims never match.

The anchor must explicitly list the person. An operator may choose it; inference
is allowed only when exactly one unit is eligible. Zero or ambiguous eligibility
or an unknown direct peer refuses before Record, media custody, memory feeds,
stimulus, provider creation or filesystem work; an invalid explicit choice is an
invalid configuration and fails startup.

Linked platforms are admitted within that grant by default, with an explicit
per-platform opt-out applying to both inbound work and later sends. A supported
transport is not implied by a direct-send route or an OTP template. Unsupported
media is refused before custody rather than retained without a scoped reader.

## One scope across accounts

The DM scope binds the person and anchor unit once. Every admitted account on
every admitted platform uses that scope for transcript, workspace and turn
serialization. Its opaque identity replaces phone-bearing direct chat IDs in
logs, traces and provider metadata; native targeting remains intact in the
authorized transport and Record.

The transcript pages by this scope with a stable Record cursor, interleaving
platform-marked lines from all linked accounts. Arrival order does not claim
that two transport clocks agree. Independent wire-chat pages are never
concatenated and called one conversation.

A turn slot keyed by the scope covers transcript assembly, model work, guarded
delivery and private state updates. Another platform can record an inbound
while waiting but cannot run a simultaneous turn over the same transcript.
Deferred result composition takes the same slot; another person's scope remains
independent.

Delivery uses only the inbound platform and original direct target. Quotes,
edits, reactions and deduplication retain platform-local wire keys, scope and
author checks, so a colliding remote ID or timestamp cannot cross platforms.
Retries and fallbacks never select another linked account.

## Reading across units

The DM may read the default unit's permitted context and, when the topic calls
for it, the context of another unit that explicitly lists the person. It does
not render every unit indiscriminately; unclear topic scope asks rather than
guesses.

Each source unit gets a DM audience projection containing only this person.
Every room, item and nested lineage entry must pass for that audience in its
own unit. A room the person is not listed in is unreadable even when its unit
is shared. Ambiguous or unresolved origins withhold the whole item.

Another person's DM is never readable. Group turns keep the ordinary same-unit
resolver and gain no cross-unit content. Unit membership alone never grants raw
DM access to a group-facing turn or to a model argument.

## Change and revocation

Changing the default unit selects a different DM scope and workspace. Old
messages, redeliveries, files and queued work remain bound to their original
partition. Existing publications remain in their original unit until ordinary
correction or retirement.

Revoking the grant or platform stops private reads, new curation and deferred
sends. It does not erase evidence already disclosed or published. A subject's
forget or dispute uses the existing immediate-hide path while keeping the
request and reason private.
