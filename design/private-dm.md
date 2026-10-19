# Private conversations anchored to a person's default unit

Status: **proposal, not implemented**. Written 2026-10-20 from the operator's
design discussion. This document specifies the intended boundary; merging it
does not enable DMs, change configuration or establish runtime acceptance.

Implementation tracking: [issue #124](https://github.com/mikeng-io/nora/issues/124).

## 1. Purpose and agreement

Nora should support a personal, persistent one-to-one conversation while
understanding the group discussions the person shares with her. Information
flows from an authorised group into that person's DM, never from a DM into a
group or another person's DM. This includes what Nora learns, not just the
original messages.

The operator proposed a **default unit per person** to anchor the DM to one
social context. This replaces the earlier suggestion to combine every unit
the person belongs to. The direction and default-unit concept are the agreed
design inputs. The configuration rules, partitioning on default changes and
initial capability limits below are proposed implementation defaults.

## 2. Existing shape and the change required

[Persona](unified-memory/35-persona.md) §3 makes the unit the read boundary:
Identity, Character and Hypotheses are one account per entity per unit. The
same human in another unit has a separate learned account. Authored names and
account bindings from `identity.toml` establish who the person is across
platforms; they do not make learned knowledge universal. Hypotheses remain
questions, never established identity facts.

[The Persona adaptor](unified-memory/45-persona-adaptor.md) specifies the
shared writer and account keys. Its existing unit boundary cannot distinguish
a private conversation from group knowledge if both are written into the same
unit account. Simply adding a DM room to the shared unit is therefore unsafe.

The current `policy.example.toml` has a DM person allowlist, but says Signal
non-group messages are dropped. The one-time-code substrate in `nora/app/otp.py`
and the direct-address restrictions in the Signal/WhatsApp senders are useful
delivery groundwork; they are not proof of conversational DM support.

[The earlier Discord scope](discord-integration/00-overview.md) deferred
Signal and Discord DMs in favour of future portal chat. This proposal reopens
the private-conversation scope; it does not retrospectively change that
historical bundle or claim any platform is already supported.

## 3. Selecting the default unit

Proposed configuration: an optional `default_unit` on each person's entry in
`identity.toml`. Policy continues to own membership and permission; the
identity setting only selects among units the person is authorised to use.

Resolve the person from the authenticated platform account, then resolve the
unit once for the turn:

1. An explicit default must name an existing unit in which policy explicitly
   lists the person. A wildcard room does not establish personal membership.
2. With no explicit default, exactly one eligible unit may be inferred.
3. With no eligible unit or several and no default, refuse conversational DM
   engagement before a model call. Do not guess, choose the first unit or
   silently use the deployment's generic fallback unit.
4. A known identity is necessary but insufficient: the separate DM allowlist
   must also admit the person. Unknown identities are refused before recording
   an inbound DM, preserving the existing direct-message identity rule.

Reject an invalid explicit default during configuration validation/startup.
Re-evaluate authorisation at each turn and before delayed delivery; a removed
identity, DM grant or unit membership cannot retain access through a cached
binding. An unavailable permission check refuses access rather than treating
the person as having no remembered information.

No in-conversation unit switching in the initial implementation. The operator
changes the default through validated configuration and the normal recreate
procedure. A default is not a home room, a tool grant or a location opt-in.

## 4. Read scopes and private learning

There are two separate scopes in one DM turn:

- **Shared reads:** the selected unit under its existing unit-wide boundary,
  with the current person visibility, exclusions and disclosure checks. This
  proposal does not introduce a narrower source-room membership rule or
  re-enable lineage. Rooms whose talk must not meet remain in separate units.
  A DM audience contains its authenticated person; do not borrow a group's
  roster to make another person's Persona or restricted material visible.
- **Private reads and writes:** the authenticated person paired with the
  selected unit. This scope contains DM Record, private Persona and any other
  derived state that DM processing creates. A different person, a group and a
  different selected unit cannot read it.

Use existing stores and writer/reader seams, extended only where needed to
represent these scopes. This is a behavioural contract, not a requirement for
a new generic permission framework, a new tier or a duplicate pipeline. Exact
schema/key encoding is an implementation choice, to be checked against every
real caller before coding.

The DM prompt may compose authorised shared knowledge with private knowledge,
retaining their provenance and distinguishing private corrections or conflicts.
A private statement may change Nora's private understanding without revising,
retiring, disputing or hiding the shared account's line. A group-visible
correction requires evidence authorised for that group through its normal
write path. This extends Persona's current subject-dispute behaviour: a DM
dispute cannot itself mutate the shared account and reveal private activity.

Every result derived from a DM turn stays private, including a hypothesis
based on both group and private evidence. It cannot be promoted because it
sounds harmless, agrees with a shared fact or has had the original quote
removed. Independent group evidence can establish the same fact separately.

The boundary covers all consumers, not only prompt rendering: transcript and
media retrieval, memory graph seeds and hops, Persona writers and prediction
judges, Rollup, Cognition, Register, notes, memos, lessons, Standing, collective
accounts, portal views and background jobs. If a subsystem has no private
scope yet, it must not consume DM evidence or create shared output from it.
Private DM activity must not influence shared scheduling or collective state.

## 5. Changing the default and permissions

Private history is partitioned by **person and selected unit**, across the
person's verified accounts. Cross-platform continuity does not combine
different people's accounts or bypass the DM allowlist.

Changing the default starts or resumes the private partition for the new unit.
Old DM messages, summaries, attachments, learned traits and pending questions
are not replayed into it. They may contain old group context as well as private
statements, so keeping one unpartitioned DM history would bridge units.
Switching back may resume the old partition only after current authorisation
is checked. There is no automatic copy, migration or sharing action.

Revoking the person's unit membership or DM permission makes that private
partition unavailable, including history and derived state. Existing narrower
exclusions/disclosure restrictions must also hold for copied or derived
material; when access cannot be proved, omit the affected item rather than
replaying it. This does not create retroactive per-room membership tracking
inside a shared unit. No deletion cascade is specified here.

Pending work retains its original person, unit and private destination.
After a default change, do not deliver or resume a task under the new anchor;
refuse/cancel it unless the original anchor is active and still authorised.
Never retarget it to a group's home room or the new default.

## 6. Engagement, tools and delivery

Reuse the normal normalisation, ingest, engagement, context assembly and
guarded delivery flow. An admitted DM addresses Nora directly and needs no
group mention or ambient relevance judgement. It must still obey the existing
reply budgets, deduplication, egress checks and audit-first delivery rules.

The initial capability is user-initiated text conversation and persistent
private learning. It grants no authority to post to a group, relay a message,
create group reminders, initiate unsolicited DMs or perform cross-room writes.
Tools with those effects are absent/refused in DM turns; do not rely on the
model to keep their destinations private.

Do not inherit a group's tool grants, `location` flag, home-room roster or
presence permissions. Location/presence reads remain unavailable until a
separate DM audience check is implemented and measured. An authenticated
one-person audience is not permission to disclose another person's location.
Other enabled tools must bind their reads and effects to this turn's actual
scope, and their delayed results must preserve it.

Read failures remain `unavailable`, never `nothing_found`. Unknown audience,
scope or destination refuses the affected operation before model access or
delivery. A platform-specific identifier is transport provenance, not a
model-selected destination.

## 7. Acceptance before enabling

Use synthetic people, units, rooms and private facts. Build the smallest
integrated slice through the existing pipeline, then prove these properties:

1. A known, DM-allowed person with a valid explicit or unambiguous inferred
   default can converse without a mention. Unknown, denied, ambiguous and
   invalid-default cases fail before provider creation and obey recording rules.
2. An authorised group fact reaches that person's DM. A fact from another unit,
   an excluded source or another person's DM does not. Shared reads retain the
   existing unit boundary and person visibility/disclosure checks.
   Include positive controls so an empty reader cannot pass the scope test.
3. A private fact and a private-only hypothesis persist for subsequent DMs but
   never reach group prompts, group retrieval, shared writers, shared background
   inputs, portal views for others or another person's DM. Mixed-evidence
   derivations stay private. A private dispute leaves the shared account intact.
4. Changing the default does not replay old-unit DM context; changing back
   respects current membership. Revoked unit access cannot return through
   cached Persona, summaries, prior DM replies, retrieval or queued work.
5. Reply and delayed-result destinations stay private. Attempts at group sends,
   group writes and inherited location access are refused at the tool boundary.
6. Inspect actual before/after assembled inputs offline, including background
   batches, before bounded live-model acceptance. Model wording alone cannot
   prove isolation. A timeout/error remains ERROR, not a privacy PASS.

Conformance: preserve the existing identity and egress restrictions. Consistency:
extend existing stores and the one writer. Coherence: one fixed anchor per turn,
separate private writes. Correctness: executed scope and delivery checks.
Completeness: inventory and check every consumer of DM evidence, including
background and delayed paths. No implementation is accepted on this document's
assertions alone.

## 8. Delivery scope, alternatives and reopening

This change is design documentation only. Implementation needs one integrated
change with scope proof before enabling conversational DMs. No live identities,
policy/config files, database rows or deployment are changed by this proposal;
no new decision-index number is allocated for unimplemented behaviour.

Choose one supported text transport for the first implementation after checking
its actual inbound and delivery paths. Signal and WhatsApp have direct-send
groundwork; neither is selected or claimed complete here. A portal chat UI,
Discord DMs, media/voice, self-service identity provisioning and explicit
private-to-group sharing are outside the initial slice.

Rejected: one shared unit account for DM learning (private facts leak into group
Persona); one DM combining all units (social contexts merge); a prompt-only
instruction to keep secrets (writers/retrieval still cross the boundary); and a
stateless DM (does not meet persistent personal-conversation intent).

Costs accepted by this proposal: duplicated facts across private/shared scopes,
potentially different understandings of a person, omitted restricted material
whose access cannot be proved, and loss of immediate DM continuity when the
default changes. Explicit sharing and cross-unit DM use would reopen the scope
contract; an observed cross-scope read/write blocks enabling until corrected.
