# Private direct messages

A private DM is one authenticated person's conversation with Nora across their
verified accounts. It can use the understanding of the units that person belongs
to without making the raw conversation, credentials or private provenance
visible to those units.

**Status:** designed

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fprivate-dm%2F%22)

## Parts

- **Admission and scope** resolve the sender and direct peer to the same
  declared person, choose an explicitly eligible anchor unit and bind one
  person-plus-unit DM scope. [Admission](admission.md) defines continuity,
  platform routing and cross-unit reads.
- **The private Record** holds one interleaved transcript across linked
  platforms, with native targets preserved for delivery, edits, reactions and
  deduplication. It is not a synthetic shared room.
- **Curation and publication** let the existing unit Cognition and profile
  writers learn suitable understanding without exposing raw DM evidence.
  [Curation](curation.md) defines the semantic check, receipts and laundering
  rule.
- **Private computer work** uses a person-plus-unit workspace and browser
  boundary with read-only unit access and explicit publication for writes.
  [Workspaces](workspaces.md) defines files, jobs, spend and deferred sends.
- **Ordinary engagement and delivery** reuse declared context, tools, guarded
  sends and per-scope serialization, with opaque operational identifiers.

## Terms and promises

There is no separate private persona, private synthetic unit, second Cognition
engine or automatic shared-memory feed. The canonical person profile remains
one per unit. A DM may read every unit that explicitly lists its owner, but only
through the per-room disclosure predicates; publication remains anchored to the
default unit.

Raw messages, quotations, attachments, transcripts, activity patterns, cookies
and private source reasoning never become directly readable in a group or
another person's DM. Only an audience-appropriate curated result can cross.
Paraphrase alone is not publication safety, and a keep-private instruction or
ambiguous confidentiality holds the candidate.

Replies stay on the admitted inbound platform and direct target. Scope is a
read and serialization key, never a destination or a fallback account.
Changing the default unit selects a different private partition without moving
old history, workspaces or queued delivery.

## Change and failure

The design starts with Signal and WhatsApp text. Other transports and media do
not become admitted by having a parser or a direct-send helper. Platform
opt-out, DM revocation or an unresolved owner/unit binding stops raw access and
new curation before side effects.

A refused or unavailable publication judgement commits no shared mutation.
Private read failures never fall back to the anchor unit's files or a group
roster. Already-published understanding remains in its original unit under
ordinary dispute, correction and retirement; revocation is not a silent delete
cascade.
