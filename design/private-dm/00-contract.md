# Private DM — shared unit understanding, curated publication

Status: **revised proposal, not implemented**, 2026-10-20. The operator clarified
that Persona and Cognition are shared within a unit; private conversations may
contribute curated understanding to Rollup/Notebook. This replaces the earlier
proposal's separate private Persona and blanket prohibition on DM-derived
shared learning. No runtime/configuration change or acceptance is implied.

Implementation: [issue #124](https://github.com/mikeng-io/nora/issues/124).

## Reading order

1. This file: the operator's contract and its limits.
2. [Current flow](01-current-flow.md): inspected seams and first transport.
3. [Storage](02-storage.md): private Record binding and publication provenance.
4. [Execution](03-execution.md): curation, ordinary turns and component effects.
5. [Build and acceptance](04-build-and-acceptance.md): implementation and proof.

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
nonempty allowlist from activating on upgrade. Unknown senders are never
recorded. A known identity alone grants no DM or unit access.

Raw history is bound to person plus selected unit across verified accounts.
Changing the default starts/resumes that unit's private partition; it neither
replays old-unit DM history nor copies already-curated Persona into another
unit. Existing publications stay in their original unit under ordinary
correction/retirement rules. Revoking DM access stops raw private access and
new curation; it does not imply that a unit forgets an already-published trait.
Explicit forget/dispute requests use the existing subject-hide/reweigh path.
No silent deletion cascade is introduced.

## 5. Reuse and initial capability

WhatsApp text is the proposed first transport. The normal normalisation,
ingest, engagement, context-block formats, model runner and guarded delivery
remain. The audience is one authenticated human; no group roster, home room,
location opt-in or group tool grant is borrowed.

Use the existing tool registry with scoped capabilities. Text replies, safe
retrieval and curated memory operations are in scope. Video generation,
media/voice and unsolicited private sends are deferred engineering choices.
They are not inherent incompatibilities with DMs. General unit Cognition and
curated learning are **in scope**, rather than disabled as in the old proposal.

## 6. Costs and reopening

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
