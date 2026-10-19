# Current flow and first transport

Status: inspected source, not live verification. Baseline:
`6945619b556c4089c58442a3be5efedca8f1868e` (`origin/master` when inspected,
2026-10-20). The documentation branch adds no runtime changes. The knowledge
graph supplied symbol discovery; the source in the checkout was checked because
graph entries can refer to older worktrees.

## Transport choice

Choose **WhatsApp text** for the first integrated implementation.
`nora/platform/whatsapp/parser.py::parse_event` already normalises direct chats
as `conversation_type="dm"`; `conversation_id_for` canonicalises the chat JID.
`nora/platform/whatsapp/send.py::WhatsAppSender.send` accepts a DM only when its
address belongs to the identity-derived recipient set. `nora/app/otp.py` already
uses guarded direct delivery for codes. None proves conversational DM safety.

Signal has direct-send groundwork but drops inbound non-group conversation
messages; Discord's earlier bundle deliberately deferred DMs. Expanding either
before proving private scope increases transport work without testing a new
privacy property. Reopen the choice if a synthetic WhatsApp inbound/reply probe
cannot preserve authenticated sender and direct destination. No registration,
pairing or account change is part of this design work.

## End-to-end seams and gaps

Paths below are relative to the repository root. These are existing seams,
not proposed modules or claims that the listed changes have shipped.

| Step | Current owner and observed behaviour | Required change |
|---|---|---|
| Normalise | `nora/platform/whatsapp/parser.py::parse_event` preserves sender, chat, stanza ID, edit/touch pointers | Validate direct peer resolves to sender; reject unsupported media before custody |
| Admit/record | `nora/ingest/pipeline.py::_run` calls `stages/scope.py::run` before identity/media/audit | Resolve and carry private scope before every persistent or spawned operation |
| Scope | `nora/ingest/stages/scope.py::run` refuses unknown DM senders but still requires a listed conversation | Branch to DM person allowlist + selected-unit validation; never require a fake shared conversation |
| Audit | `nora/infra/sql/audit.py::SqlRecordStore.append` inserts Record and projects its graph node in one transaction | Store private scope on inbound and outbound rows; partition transcript and point lookups |
| Stimulate/feed | `nora/ingest/stages/stimulus.py` resolves a unit from the conversation; `memory_feed.py` spawns Honcho writes | No shared stimulus, Standing wake or Honcho feed for DM; private Persona wake only |
| Engage | `nora/ingest/policy.py` has mention/text/reply/voice policies; sender permission follows Record | Admit eligible text DMs directly, with existing budgets/debounce/queue semantics |
| Assemble | `nora/engagement/context.py::TranscriptInjection.assemble` gathers transcript, Persona and unit blocks | Bind reads to the carried private scope and explicit read-only shared scope |
| Resolve audience | `nora/agent/room.py::Room.scope` normally reads a platform roster; `lineage.py::render_room` looks in policy | DM audience is its authenticated peer, never a group roster or unknown-room fallback |
| Retrieve | `nora/agent/memory_search.py::scope_for` builds unit/room/person scope; SQL applies it per hop; `SqlRecordQuery` reads supplied rooms | Give private and shared retrieval separate bounded selectors; never let a wire chat ID span anchors |
| Learn | `nora/language/persona.py::PersonaEvidence` / `PersonaPersonAdaptor` and `SqlCharacterEvidence` read rooms and watermarks | Reuse the writer for a private human account, with private evidence and independent cursors |
| Deliver | `nora/engagement/stages/deliver.py::build` constructs guarded delivery to `ConversationTarget` | Preserve wire target and private Record binding; recheck permission before send |
| Debrief | `nora/engagement/stages/debrief.py` produces model-driven Standing / working state | Skip DM model fold and working-state production/replay; retain only bound operational outcomes/traces |
| Telemetry | Engagement roots, `nora/obs/tracing.py` and `nora/agent/telemetry.py` currently use the conversation ID for trace/session attributes | Replace DM wire IDs with opaque turn/scope IDs before tracing or logging; a direct JID contains a phone number |

## Why storage changes are necessary

`messages` has `conversation_type` but no selected-unit or private-owner
binding. `SqlRecordStore.recent` takes only a conversation ID, so changing the
default unit cannot safely reuse it unchanged. A platform's DM chat ID does not
change when the operator changes the person's default unit.

`nora/infra/sql/memory_index.py::_project_message` derives `origin_room` from
platform and conversation ID. That too would combine DM partitions unless it
uses a durable private binding. `SqlCharacterEvidence._person`, `_room` and
`_mentions` use room pairs; pair-only selection is insufficient for private
history. These are real existing read boundaries, not speculative leakage.

`nora/app/composition.py::character_person_keys` builds shared person accounts
from policy conversations; `character_unit_keys` builds shared collectives.
Private conversations must not be added to either input as shared rooms.
`nora/infra/sql/persona.py` already owns atomic validation, append-only chains,
disputes and hypothesis verdicts; private learning must reuse those operations.

`nora/app/memory_routing.py::persona_scopes_of_person` and `nora/web/memory.py`
currently enumerate shared accounts. A private account must never enter a
shared portal list simply because the owner belongs to its anchor unit.

## Current rules that remain

The [contract](00-contract.md) retains the shared unit boundary, existing
listed-person/exclusion checks and fail-closed location behaviour. The current
source still contains legacy Honcho routing even though the unified-memory
design plans its removal. A design describing that removal is not evidence that
the legacy feed can safely accept private messages. DM composition bypasses
Honcho reads and writes regardless of the group memory surface.
