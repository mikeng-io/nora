# Private DM — shared unit understanding, curated publication

Status: proposed, **not implemented**, revised 2026-10-21. The operator clarified
that Persona and Cognition are shared within a unit; private conversations may
contribute curated understanding to Rollup/Notebook. This replaces the earlier
proposal's separate private Persona and blanket prohibition on DM-derived
shared learning. No runtime/configuration change or acceptance is implied.

Revision 2026-10-21: WhatsApp + Signal, one cross-platform transcript, private
computer work and subagents are in the first integrated PR. This reverses the
earlier computer/export/unsolicited-private-job deferral, subject to the private
publication and deferred-send rules below. The
[unit-scoped computer prerequisite](../computer-unit-scope/00-contract.md) is
satisfied: #164 shipped in v6.5.0, with the operator-verified 2026-10-21 live
migration and #168 health-gate correction recorded in the
[cutover runbook](../computer-unit-scope/01-migration.md). DMs extend that shipped
implementation; DM support itself remains unbuilt.

Implementation: [issue #124](https://github.com/mikeng-io/nora/issues/124).

## Reading order

1. This file: the operator's contract and its limits.
2. [Current flow](01-current-flow.md): inspected seams and first transport.
3. [Storage](02-storage.md): private Record binding and publication provenance.
4. [Execution](03-execution.md): curation, ordinary turns and component effects.
5. [Build and acceptance](04-build-and-acceptance.md): implementation and proof.
6. [Cross-platform work](05-cross-platform.md): transcript, workspaces and deferred sends.

`design/private-dm.md` remains a pointer, not a second specification. Every
schema/tool/test addition below is proposed; nothing in this bundle is a runtime
receipt. No new decision-index number is assigned to unimplemented behaviour.

## 1. One person, one Persona per unit

Use the existing unit-scoped account for Identity, Character and Hypotheses.
Authored names and platform bindings still come from `identity.toml`; learned
facts stay in Persona. Do not create a private Persona, a private synthetic
unit, a second Cognition engine or a new memory tier.

The person's DM uses their **default unit** as its social context. Nora may
read that unit's permitted group discussion and curated Notebook in the DM.
Cognition and the existing Persona writer may learn from the unit's groups and
its bound DMs. Raw private evidence remains protected; the shared account holds
only the understanding curated for shared use. Confidential details that cannot
be published stay in private Record and remain available in authorised DMs.

The [existing Persona contract](../unified-memory/35-persona.md) and
[writer](../unified-memory/45-persona-adaptor.md) remain the baseline. This is a
proposed extension to their evidence/disclosure contract, not a claim that the
current code already curates private sources safely. Hypotheses stay qualified
questions with their existing grounding, prediction and retirement rules.

## 2. The publication boundary

The operator permits **curated understanding** to cross into shared unit state;
this is an intentional publication decision, not an accidental retrieval leak.

- Raw DM messages, quotations, attachments, transcripts and confidential details
  never become directly readable in a group or another person's DM.
- Cognition can curate an understanding from DM evidence into the unit Persona,
  Notebook or a shareable aggregate. Its actual content must be suitable for
  that audience; shortening/paraphrasing alone does not make it suitable.
- An explicit keep-private instruction vetoes publication of that information
  and conclusions that would reveal it. Ambiguous confidentiality is held.
- Published titles, predictions, relationship targets, summaries and actions
  must not reveal private particulars, the existence of a private conversation
  or who privately supplied a fact. Private provenance remains internally
  re-derivable but is not part of the shared view.
- A Rollup is an input/aggregate, not automatic permission to publish. DM timing,
  counts and response patterns can reveal private activity too. Only a curated
  audience-appropriate result enters shared blocks or shared action premises.

A general communication preference may be a suitable curated trait. A
confidential disclosure restated as a shorter fact is still confidential.
These are acceptance scenarios, not an exhaustive list of permitted topics.
Curation must also check implications and re-identification in the destination
context, not only the presence of names or exact quoted words.

## 3. Shared Cognition, bounded operations

Reuse the unit's existing Cognition/session and Persona adaptor machinery,
locks, stores and reasoning modes. Raw-DM access is an internal curation
capability, never a blanket permission granted to a group-facing turn.
A unit session that reads raw private evidence can propose curated writes,
but cannot directly send to a group, create group reminders, publish arbitrary
files or commit ordinary unreviewed shared state. The curation boundary covers
each proposed write/revision/retirement and its side effects.

After publication, ordinary group Cognition, replies and actions may use the
curated result through their existing guards. They receive the published view,
not the raw DM batch or private session working state. This shares understanding
within the unit while preventing private reasoning from bypassing publication
through a tool call or background callback.

The semantic part is a model judgement, not a deterministic privacy theorem.
A publication receipt proves which judgement and content were committed; it
does not prove the judgement was right. External labelled evaluation and scope
proof are required before enablement, and a disclosure failure blocks it.
No extra always-on classifier or duplicate curator service is prescribed.

## 4. Default unit and private history

Propose optional `default_unit` per person in `identity.toml`. It must select a
unit whose policy explicitly lists the person; wildcard membership is not
sufficient. Infer only when exactly one unit is eligible. Zero/ambiguous
eligibility refuses the DM before side effects; invalid explicit configuration
fails startup. Identity and policy changes require validated copies/recreate.

Conversational DMs also require `[dm] enabled = true` (new, default false) and
the existing DM person allowlist. The switch prevents a previously inert
nonempty allowlist from activating on upgrade. Within that grant, every linked
platform is enabled by default; proposed per-person `dm_platforms.<platform>.enabled`
in `identity.toml` permits an explicit false opt-out. First admitted transports
are WhatsApp and Signal; Discord/Instagram remain deferred. Operator-authored
account IDs alone link identities; display names never match. Unknown senders
are never recorded. A known identity alone grants no DM or unit access.

Raw history is one transcript per person plus selected unit (`dm_scope_id`)
across verified accounts and platforms. Read pages by that scope, interleaved
with per-line platform markers, and serialize turns on it. Replies stay on
the inbound platform/target; quote/edit/touch pointers remain platform-local.
Changing the default starts/resumes that unit's private partition; it neither
replays old-unit DM history nor copies already-curated Persona into another
unit. Existing publications stay in their original unit under ordinary
correction/retirement rules. Revoking DM access stops raw private access and
new curation; it does not imply that a unit forgets an already-published trait.
Explicit forget/dispute requests use the existing subject-hide/reweigh path.
No silent deletion cascade is introduced.

## 5. Reuse and initial capability

WhatsApp and Signal text are the first transports in one integrated PR. Normal
normalisation, ingest, engagement, context-block formats, model runner and guarded delivery
remain. The audience is one authenticated human; no group roster, home room,
location opt-in or group tool grant is borrowed.

Use the existing tool registry with scoped capabilities. Text replies, safe
retrieval, curated memory, computer/export and subagents are in scope. A DM
workspace is keyed by person+unit, never globally by person. It may read the
anchor unit workspace; writing there is explicit curated publication. Groups
have no DM workspace access. Browser cookies/logins are private evidence too.
Subagent results are deferred sends to their launching platform/target with
permission rechecks and no retargeting. Computer/subagent spend has a shared
per-person DM budget across platforms/units. See [the full contract](05-cross-platform.md).
Video generation and inbound media/voice remain deferred; scoped computer file
returns are included. General unit Cognition and curated learning are **in scope**, rather than disabled as in the old proposal.

## 6. Costs and reopening

Compromise of any one admitted linked account exposes the whole person+unit
private transcript and DM workspace, including its browser state. Platform
opt-out blocks that platform; platform markers are provenance, not isolation.

Curation can omit a useful fact, or mistakenly publish a private one. Shared
Persona can change after a DM, including after a subject's dispute; that is
allowed curated learning/correction, not proof that the raw source is public.
Removing quotations does not promise zero inference about private interactions.
The accepted direction is managed curation with measured disclosure behaviour,
not complete non-interference between DM activity and shared understanding.

Keep confidential detail retrievable privately without duplicating Persona.
Reopen an engineering choice on source drift, a failed scope test or a measured
curation/disclosure failure. This document does not authorise implementation,
paid runtime evaluations, schema changes, live enablement or deployment.
