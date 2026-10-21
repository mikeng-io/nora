# Current flow and first transport

Status: inspected source, not live verification. Original flow inspection:
2026-10-20; transport/computer seams refreshed against origin/master
`5856cd9f08cb16f273e7d992e7e9bc42d9606a36`, 2026-10-21. The documentation
branch adds no runtime changes. The knowledge graph supplied symbol discovery; the source in the checkout was checked because
graph entries can refer to older worktrees.

## Transport choice

The operator selected **WhatsApp + Signal text** for the first integrated PR.
`nora/platform/whatsapp/parser.py::parse_event` already normalises direct chats
as `conversation_type="dm"`; `conversation_id_for` canonicalises the chat JID.
`nora/platform/whatsapp/send.py::WhatsAppSender.send` accepts a DM only when its
address belongs to the identity-derived recipient set. `nora/app/otp.py` already
uses guarded direct delivery for codes. None proves conversational DM safety.

Signal has direct-send groundwork but
`nora/platform/signal/parser.py::parse_event` returns None when groupId is absent
(lines 419–421). Add authenticated inbound DM normalisation in this slice;
preserve group, self-send, edit and touch handling. Discord still drops DMs at
`nora/platform/discord/parser.py::parse_discord_message`; Instagram defaults unknown
is_group to group in `nora/platform/instagram/parser.py::parse_event`. Those
transports are deferred. No registration, pairing or account change is part of
this design work. [Cross-platform contract](05-cross-platform.md) fixes identity,
reply routing and serialization for the two initial transports.

## End-to-end seams and gaps

Paths below are relative to the repository root. These are existing seams,
not proposed modules or claims that the listed changes have shipped.

| Step | Current owner and observed behaviour | Required change |
|---|---|---|
| Normalise | `nora/platform/whatsapp/parser.py::parse_event` preserves DM identity/pointers; `nora/platform/signal/parser.py::parse_event` drops DMs | Add Signal inbound DMs; validate direct peer/sender; reject unsupported media before custody |
| Admit/record | `nora/ingest/pipeline.py::_run` calls `stages/scope.py::run` before identity/media/audit | Resolve and carry private scope before every persistent or spawned operation |
| Scope | `nora/ingest/stages/scope.py::run` refuses unknown DM senders but still requires a listed conversation | Branch to DM person allowlist + selected-unit validation; never require a fake shared conversation |
| Audit | `nora/infra/sql/audit.py::SqlRecordStore.append` inserts Record and projects its graph node in one transaction | Store private scope on inbound and outbound rows; partition transcript and point lookups |
| Stimulate/feed | `nora/ingest/stages/stimulus.py` resolves a unit from the conversation; `memory_feed.py` spawns Honcho writes | Bound private stimulus wakes unit curation; no raw Honcho feed or ordinary shared agenda text |
| Engage | `nora/ingest/policy.py` has mention/text/reply/voice policies; sender permission follows Record | Admit eligible text DMs directly, with existing budgets/debounce/queue semantics |
| Assemble | `nora/engagement/context.py::TranscriptInjection.assemble` gathers transcript, Persona and unit blocks | Page one dm_scope transcript across platforms, mark each line, serialize turns by scope; shared reads stay bounded |
| Resolve audience | `nora/agent/room.py::Room.scope` normally reads a platform roster; `lineage.py::render_room` looks in policy | DM audience is its authenticated peer, never a group roster or unknown-room fallback |
| Retrieve | `nora/agent/memory_search.py::scope_for` builds unit/room/person scope; SQL applies it per hop; `SqlRecordQuery` reads supplied rooms | Give private and shared retrieval separate bounded selectors; never let a wire chat ID span anchors |
| Learn | `nora/language/persona.py::PersonaEvidence` / `PersonaPersonAdaptor` and `SqlCharacterEvidence` read rooms and watermarks | Reuse the same unit account/writer; admit subject-bound DM evidence through curation before shared mutation |
| Deliver | `nora/engagement/stages/deliver.py::build` constructs guarded delivery to `ConversationTarget` | Preserve wire target and private Record binding; recheck permission before send |
| Debrief | `nora/engagement/stages/debrief.py` produces model-driven Standing / working state | Scope private working state; shared Standing/debrief output requires curation and protected provenance |
| Telemetry | Engagement roots, `nora/obs/tracing.py` and `nora/agent/telemetry.py` currently use the conversation ID for trace/session attributes | Replace DM wire IDs with opaque turn/scope IDs before tracing or logging; a direct JID contains a phone number |

## Why storage changes are necessary

`messages` has `conversation_type` but no selected-unit or private-owner
binding. `SqlRecordStore.recent` takes only a conversation ID, so changing the
default unit cannot safely reuse it unchanged. Nor can a single wire chat
selector provide cross-platform continuity: the proposed reader pages by
`dm_scope_id`, with a stable Record cursor and each row's platform/target intact.
A platform's DM chat ID does not change when the operator changes the person's
default unit.

`nora/infra/sql/memory_index.py::_project_message` derives `origin_room` from
platform and conversation ID. That too would combine DM partitions unless it
uses a durable private binding. `SqlCharacterEvidence._person`, `_room` and
`_mentions` use room pairs; pair-only selection is insufficient for private
history. These are real existing read boundaries, not speculative leakage.

`nora/app/composition.py::character_person_keys` builds shared person accounts
from policy conversations; `character_unit_keys` builds shared collectives.
Private conversations must not be added as raw shared rooms. Their bound
evidence enters the existing unit writer through an explicit curation input.
`nora/infra/sql/persona.py` already owns atomic validation, append-only chains,
disputes and hypothesis verdicts; curated DM-derived learning must reuse those operations, with a publication
boundary for mutations that can become shared.

`nora/app/memory_routing.py::persona_scopes_of_person` and `nora/web/memory.py`
currently enumerate shared accounts. They should continue showing the same
canonical unit account, while private source citations/reasons remain protected
even when the curated line is visible.

## Current rules that remain

The [contract](00-contract.md) retains the shared unit boundary, existing
listed-person/exclusion checks and fail-closed location behaviour. The current
source still contains legacy Honcho routing even though the unified-memory
design plans its removal. A design describing that removal is not evidence that
the legacy feed can safely accept private messages. DM composition bypasses
Honcho reads and writes regardless of the group memory surface.

## Revised interpretation (operator clarification, 2026-10-20)

The earlier bundle treated all DM-derived learning as private and excluded DM
Cognition. That is superseded: one unit Persona and shared Cognition curate
suitable understanding from group and DM evidence. Raw Record access and shared
publication are separate permissions. Existing validators check structure and
citations; they do not establish that a paraphrase is safe to share. Current
Cognition native verbs can commit immediately, so their raw-DM-capable mode
needs guarded publication, not just a changed objective or rendering filter.

This is a proposal about changed behaviour, not evidence that today's writers,
Rollup or Notebook already implement a confidentiality publication judgement.
