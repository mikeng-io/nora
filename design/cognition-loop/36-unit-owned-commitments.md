---
type: design
title: "Unit-owned commitments — one subject across delivery rooms"
description: "Unit-owned reminders, a complete pending view, derived subject identity, and fire-time premise and repeat checks. One integrated change; general cognition room binding remains a separate decision."
tags: [nora, cognition, reminders, commitments, unit, dedupe]
timestamp: '2026-10-30'
edges:
  - {dst: 30-cognition, rel: amends, provenance: asserted}
  - {dst: 35-the-proactive-turn-reviews-the-situation, rel: amends, provenance: asserted}
  - {dst: ../unified-memory/27-the-memory-is-a-graph.md, rel: depends_on, provenance: asserted}
---

# 36 · Unit-owned commitments

## 1. Boundary and reason

Operator ruling, 2026-10-30, option A: knowledge and decision belong to the
**unit**. Every reminder and stimulus composed for a cognition session has
unit scope. A room is a **delivery address**, never a second decision boundary.
This applies even when the wake has items from only one room. Another unit's
rows never enter the session. Lineage and disclosure remain per entry at the
chosen delivery room, including R-31, R-18 and R-28 and the current reversible
content-check setting. Unit knowledge is not permission to broadcast it.

This extends the unit boundary of DEC-231, not its old per-item binding rule.
The broader removal of that binding is **Job 2**, separately undecided (§9).
This node is a revisionable design, not a claim of implemented behaviour.
Decision index: **DEC number at merge**, under `nora-decision`; any new rule
uses **R number at merge**, with an enforcing test when behaviour lands.

The operator supplied the completed read-back of the 2026-10-30 incident.
Four creating sessions scheduled the same unresolved social question, using
different evidence sets in two rooms of one unit. One fire stayed silent,
two sent about 2.5 hours apart, and one remained pending. The premise and
scope are settled; no new production read is required to reconsider them.
Evidence handles supplied by the operator: cognition trace prefixes
`cfcfee2e`, `938a34fc`, `b1245a67`, `f9f4b5aa`; proactive trace prefix
`25577f46`; tag turns 830, 832, 834; `reminders` and `decision_log` rows.
These are historical evidence references, not fixture data or live reads by
this author. Private message text and member names are deliberately omitted.

Source read at base `cb07cccb0067aa3911a78286824f3d9c92d4d451` confirms:

- `nora/app/cognition_session.py`, `remind`: only a cited `note:<id>` or
  recognised bare note id sets `payload.note_id`. Message-only citations leave
  the row unlinked. `_reminder_row` stores the selected platform and room.
- `nora/infra/sql/reminders.py`, `SqlReminderStore.create`, and
  `nora/infra/sql/schema.py`, `uq_reminders_proactive_open_note`: only linked
  open proactive rows collide. `DuplicateProactiveNoteError` cannot protect
  an absent key.
- `nora/app/cognition_grounding.py`, the note-index `held` map, and
  `nora/language/cognition_render.py`, `reminds`: pending visibility requires
  both a note link and a delivery room on the agenda. `_own_sends` reads per
  message-item room. `_own_reminder` refuses off-agenda rooms.
- `nora/domain/cognition_remind.py`, `check_remind`: equality means the same
  platform, conversation and exact set of subject rows. Three, four, three
  and five citations across two rooms are not one key. `_pending_reminds`
  reads `pending_of(ai_peer)` across units before this narrow predicate.
- `nora/app/cognition_wiring.py`, `COGNITION_READ_CATALOGUE`: no reminder-list
  read. `REMINDERS` has no stored unit. `ProactiveScreen.refusal` in
  `nora/app/proactive_screen.py` admits a missing note link after the flag
  check; its renamed-unit comment documents deriving scope at fire time.
- `nora/app/fire.py`, `_read_decisions`, already reads unit-wide decisions.
  The operator's fire input contained verdicts and ages, without a subject.
  `nora/agent/prefill.py`, `_decisions_block`, cannot supply an absent subject.

One absent link therefore removes four safeguards together: store uniqueness,
pending visibility, premise checking and a meaningful decision subject.
This is a **Correctness** failure, compounded by **Completeness** of input.

## 2. Stored ownership and conversion

Add a stored `unit` to reminders, resolved from policy at write, never accepted
from model arguments. Keep platform, conversation and conversation type as
separate delivery attributes. Carry unit through normalisation, SQL and fake
stores, recurrence successors, move, claim, fire and audit. All reminder
writers, including human-requested reminders, resolve ownership once; human
reminder ownership and cancellation permissions otherwise stay unchanged.
Stimuli retain their stored unit; composing a wake selects by unit and does
not discard stimuli because their origin room is absent from the agenda.
Origins remain provenance. This does not expand the general tool bindings.

Backfill runs with dispatch paused and a captured validated policy revision.
For cognition rows, compare the unit in `payload.by` with the policy mapping
of the delivery address. A disagreement is an ambiguity, not permission to
move the row. Use a recorded historical mapping only when it proves ownership;
otherwise retain the row and mark it unavailable for dispatch. Other rows use
the proven policy mapping, with unresolved addresses treated the same way.
Terminal historical rows may retain null ownership explicitly as unknown;
new rows and dispatchable rows require stored unit. Report converted,
ambiguous and unresolved counts separately; unknown is not another unit.

A later policy rename never silently rehomes old reminders. Fire checks that
the delivery address still belongs to the stored unit. Mismatch is unavailable
and no send. An intentional rename needs an explicit validated conversion of
ownership and subject references together, or cancellation and recreation.
Do not overwrite units by re-running the current policy mapping.

## 3. A complete commitments view

Every cognition session receives a declared context-assembly block of **all
pending reminders in its unit**, not just its own linked notes or agenda rooms.
Each line carries id, resolved subject key and readable subject title, text,
intent, due time with zone, delivery room label, written-at and written-by
(person or cognition session). Include a person's reminders for awareness,
but label them as not editable by cognition. Include unresolved legacy rows
with the reason they cannot fire. Empty and unavailable are distinct states.
A failed read fails the session; it is never rendered as an empty set.

Read once from the scoped store and reuse that typed view for rendering and
verb checks, refreshing under the unit lock when a verb mutates it. The note
index may keep a shortcut, but is no longer the source of pending visibility.
The complete set is not truncated silently: if it cannot fit the existing
context budget, fail unavailable rather than make a decision on a partial set.
No new reminder-list tool or parallel context builder is needed.

Unit knowledge is assembled as unit knowledge, not intersected with every
agenda room's audience. Each entry keeps its lineage and exclusions. No
commitment text becomes authorised for a destination merely by being visible
to cognition. The delivery projection below applies the live audience checks.

## 4. Subject identity and the verbs

A subject is one unresolved commitment, not an evidence set or a sentence.
Keep `subject_rows` as evidence references. Code resolves their existing
Register citations to exactly one open note in this unit, following note merges
to the survivor. A direct note citation and message citations which that note
cites resolve alike. Zero matches returns `refused: subject_unresolved`, with
the instruction to create or identify the open note first. Multiple matches
return `refused: subject_ambiguous`; no first-match guess. A failed store read
is `unavailable`. Validate cited evidence as usual. Do not derive identity
from text similarity, hashes of prose, room, intent or exact citation sets.

Store a typed subject reference, initially `note:<survivor-id>`, plus the
revision and title at decision time. This link is mandatory for cognition's
outbound commitments and produced by code, never an optional model courtesy.
Two independently created notes for the same matter still require the existing
note merge; this mechanism does not claim semantic equivalence it cannot prove.

`# ponytail: one open note is the interim subject; replace the resolver target with the graph commitment/event entity and project reminders serves / notes concerns, without changing remind's evidence-based verb.`

This matches [the graph model](../unified-memory/27-the-memory-is-a-graph.md)
§6: reminders `serves` an event entry or commitment, notes `concerns` entities.
Do not implement that graph migration here. The future conversion maps old
note keys to canonical entity keys and resolves collisions before switching.

`remind`, `remind_move` and `remind_cancel` operate on cognition-owned pending
reminders of the session's stored unit, regardless of agenda or delivery room.
The store enforces that unit and owner in the mutation predicate, as well as
pending state. Another unit's id behaves as absent. A person's reminder stays
read-only except the already documented event-cancellation authority.

`room` on `remind` is an optional delivery choice among this unit's permitted
rooms, including off-agenda rooms. Default to the single origin room of the
resolved subject's supporting evidence; with mixed or absent origins use the
unit home room. If that default cannot receive the content, refuse with a
reason; never silently reroute private content. Explicit room choice is still
checked against unit membership, proactive grant and per-entry disclosure.
Moving a reminder changes time, retaining delivery and identity; changing the
delivery uses a new `remind` decision with the same subject.

The comments in `fire.py` `_read_decisions` and `prefill.py` `_decisions_block`
saying proactive rows fire only into the home room are stale. The actual row
address drives `proactive_event`. Home is a fallback here, not a routing law.

For an existing pending subject, `remind` **supersedes** it atomically, like
`wake_at`: one replacement, with old id and reason recorded, not two open rows.
The same request with the same due, intent, text and delivery returns the existing
id without another row. A move also replaces atomically. If the prior row is
already firing, refuse `already_due`; never create its replacement. The old
subject decision time remains available when moving or superseding, so a move
cannot erase a prior send from the fire-time comparison.

Enforce one open proactive row per `(unit, canonical_subject)` in SQL for both
pending and firing states. A unit lock alone cannot protect another writer or
process; the existing index is evidence that this store already needs the
constraint. Replace its note-only key and make new proactive keys non-null.
All proactive writers route through the same resolver and constraint. Cancel
old and insert replacement in one transaction; failure rolls both back. The
SQL and in-memory implementations share the contract. A concurrent losing
write returns a named refusal, not an apparent success. Merging notes must
canonicalise pending keys and keep one row atomically, refusing to displace a
firing row. Unrelated subjects and other units do not collide.

## 5. Fire-time premise and prior sends

The proactive screen remains before model composition. It validates stored
unit, delivery permission, reachable subject, current premise and disclosure.
Legacy unlinked rows go through the same subject resolver: a unique result can
be converted and checked; no subject, ambiguity or a failed read is unavailable
and **does not fire**. Remove the `if not note_id: return None` escape.
A retired or settled premise cancels; an unreadable premise fails unavailable.
Keep the existing distinction between a real settlement and a paraphrase, and
the merged-note survivor handling. Absence never proves settlement.

Read actual successful sends about the canonical subject across **all rooms
of the stored unit since its subject decision**, not only room transcripts or
`proactive_act spoke` verdicts. Use recorded send receipts linked by code to
reminder, subject, unit, delivery address and trace. A silent decision is not
a send. A same-subject send cancels the pending repeat with a named outcome;
an unavailable send-history read prevents fire. A new decision after a prior
send may deliberately ask again; the previous-subject window remains visible.
Hold the existing open subject reservation through terminal fire resolution,
so concurrent claims cannot both pass an empty-history check. Preserve existing
at-most-once recovery after uncertain egress; do not retry unknown delivery.

`<loop_decisions>` carries subject key, subject title, delivery room label,
time and verdict. Use the resolved subject snapshot even if the note later
closes. Apply lineage when projecting this unit history into a speaking room;
withheld subject text must not be replaced by a misleading claim of no prior
activity. Cognition sees the unit view. The fire turn still makes the final
choice to speak, act or stay silent from fresh context, as node 35 requires.

## 6. Audit of outbound paths

This is a source audit, not a runtime health claim. “Unverified” means the
property has not been established, not that it failed.

| Path and source evidence | System-derived key | Decider sees open set | Premise checked at fire | Repeat measured |
|---|---|---|---|---|
| Cognition `remind`; session, grounding, `check_remind`, screen (§1) | No mandatory subject; optional note | Only linked agenda-room notes | Linked notes only | Unverified |
| Version announcements; `schema.py` `VERSION_ANNOUNCEMENTS` and retired-ledger comment | Historical `(version, platform, conversation)` claim before send | Historical atomic claim, not a model view | Unverified | Unverified |
| Compute; `schema.py` `COMPUTE_JOBS`, `SqlComputeJobStore.claim` | Job id + pending-to-firing predicate | Store pending query; model view unverified | Unverified | Unverified |
| Recurring reminders; `reminders.py` `plan_successor`, SQL `claim` | `uuid5(series.id, occurrence)` | Model view unverified | Subject premise unverified | Unverified |
| Self-wakes; `cognition_pace.py` `check_wake_at`, session `_pending(unit)` | Minted ref, supersedes overlapping evidence; not semantic subject identity | Unit pending set | Reopens cognition, no direct send | Unverified |
| Participation nudge; `participation_window_state` | **Unaudited** | **Unaudited** | **Unaudited** | **Unaudited** |

Version announcements are retired on this base; they demonstrate a historical
system key, not a currently active safe path. Compute's state predicate proves
a job claim, not semantic dedupe of two independently created jobs. The
unguarded class is model-decided outbound acts whose subject the model names.
Participation nudge changes and memory-graph implementation remain out of scope.

## 7. Independent measurement and one replay

Add a send-outcome counter, proposed spelling
`nora_commitment_send_checks_total{unit,outcome}`, with outcomes `first`,
`repeat`, `unavailable`. Declare and increment together; derive vocabulary
from one typed constant and validate at runtime. Unit is the bounded policy
unit label; subject ids and private text are never metric labels. A repeat
means a second successful send for the same canonical subject in a unit within
24 hours. This window measures unwanted repetition; it does not itself ban
an intentional new decision. Failure is unavailable, never zero repeats.

Compute from durable send receipts at delivery acknowledgement, outside the
producer's choice of “spoke” or “silent”. Reconcile receipts after restart;
a process-local counter alone is not proof. Read-back includes receipt count,
checked sends and unavailable checks. This measures keyed commitments; an
unrelated tag reply without a grounded subject is not silently classified.
A positive control injects two acknowledged sends on one generated subject in
different rooms and must count one repeat; a different subject and another
unit must count first. The protected replay must still deliver its first valid
ask, so zero repeats cannot pass through a crashed or permanently silent path.

**One integrated replay case**, based on the four-wake causal shape, using
new generated identities, rooms, dates, text and ids, never copied production
rows. Generate one open question, evidence arriving in sets of 3, 4, 3 and 5
rows with partial overlap, two delivery rooms, four creating wakes, a silent
fire, a later successful ask and a subsequent due opportunity in the other
room. Keep an unrelated pending subject and a second unit as controls in the
same case. Drive the actual cognition and fire paths, not a scripted “duplicate”
answer. Record the declared context blocks, verb results, stored replacements,
fire outcomes, successful sends, counter checks and Langfuse traces per wake.

Baseline must reproduce the missing-link blind spot. Candidate must expose
all pending commitments, resolve or explicitly refuse each message-only
subject, keep one open row, allow the first valid ask and prevent the later
cross-room repeat. The other subject must remain actionable. A timeout or
missing subject/history read is ERROR, never PASS. Compare baseline and the
whole integrated candidate once under the existing eval machinery; no shadow
week or separate unmeasured pieces. This is a specified case, not a run result.

## 8. Acceptance and expected falsification

An implementation issue can cite **Design:**
`design/cognition-loop/36-unit-owned-commitments.md` §2–§8. Ship ownership,
conversion, context, verbs, screen, decision subjects and measurement together.
The following names are **proposed tests**, not existing or executed checks.
Place them in the existing appropriate unit/integration/contract modules;
no new root-level test files. At implementation time record exact selectors
and production lines under `nora-prove-fix`: baseline green, exact clause
reverted gives assertion failure, restore green. These are expected catches:

| Proposed test name | Production clause whose removal must turn it red |
|---|---|
| `test_pending_commitments_span_unit_rooms_and_keep_other_units_out` | Unit store predicate; independent block rather than note/agenda filter |
| `test_cognition_can_move_and_cancel_own_off_agenda_reminder` | Unit/owner mutation predicate replacing agenda membership |
| `test_message_citations_resolve_one_subject_or_refuse` | Resolver and mandatory-key refusal, including zero and ambiguous matches |
| `test_delivery_defaults_to_evidence_then_home_and_checks_audience` | Delivery-only default and per-entry permission check |
| `test_same_unit_subject_supersedes_atomically_across_rooms` | Subject uniqueness and transactional replacement; gathered SQL race plus rollback failure |
| `test_renamed_unit_and_unresolved_legacy_rows_do_not_fire` | Stored-unit comparison, conversion and no missing-link escape |
| `test_subject_sent_in_another_room_since_decision_cancels_fire` | Unit-wide acknowledged-send check; silent and different-subject controls |
| `test_loop_decisions_names_subject_and_delivery_room` | Subject propagation and rendered subject fields |
| `test_send_measure_counts_injected_repeat_and_first_send` | Independent receipt observation and counter increment |

The store contract runs against SQL and the shared fake. Fakes must distinguish
unit, subject, state, id and wrong-call shapes, including read errors; no fake
returns the same answer for every request. The replay in §7 proves the joined
journey; focused clause tests prove the failure cannot return unnoticed.

The interim key holds unless generated replay shows an existing open subject
cannot be resolved reliably from its citations, or legitimate distinct
commitments collide on one note. That evidence reopens the resolver design,
not the operator's unit boundary. Costs accepted: unresolved new subjects are
refused, ambiguous legacy rows need conversion, and one note permits only one
open proactive commitment. No claim of global semantic dedupe is made.

## 9. Job 2 — general cognition room binding (not yet decided)

Separate follow-up, **DEC number at merge / R amendment at merge**, with its
own recorded operator decision and eval. Remove room binding from cognition
reads, writes and tools: this would reverse DEC-244 (3)'s agenda-room tool
selection/refusal, the cognition application of R-21 and DEC-231's per-item
binding, DEC-250's agenda-resolved writes and the per-item room agenda model.
It must preserve the unit as knowledge boundary, provenance of origin rooms,
withdrawal, per-entry lineage and live disclosure at every delivery. It must
not turn room-scoped speaking tools into workspace-wide reads by accident.

The choice is whether each cognition tool gains a unit-native binding or is
replaced by an existing unit read while room remains only an output address.
Measure a generated mixed-room journey and a forbidden cross-unit read before
choosing. Scope A changes reminder/stimulus composition and reminder verbs
only; it does not claim to have removed those general bindings. This follow-up
lives here for a later design-anchored issue; no task status or number is
reserved. Participation nudges and the graph migration are separate work.
