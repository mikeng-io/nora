# Admission, context, learning and delivery

Status: proposed. [Storage](02-storage.md) defines the durable scope; this file
defines which existing owner enforces it. No part depends on a prompt asking
the model to keep a secret.

## Bind before ingest side effects

Extend the identity file schema (`nora/persons_file.py`) with optional
`default_unit`; load it into `Persons` through `load_persons`. Composition
validates explicit defaults against the current policy. Add `[dm] enabled`
to policy with default false; it gates conversational DMs, not OTP templates.
`Policy.dm_persons` is the per-person grant; eligibility comes from explicit
person listings in shared
unit rooms, excluding wildcard-only membership and the reserved private prefix.
Missing default: infer exactly one eligible unit, otherwise decline that DM.
Invalid explicit configuration is a startup error. Identity/policy are loaded
once, so changes take effect through validated files and recreate, not an
imagined hot-reload mechanism. Each operation checks the current process's
authoritative objects; no unbounded cached permission decision is reused.

At the pre-audit scope stage, only WhatsApp ordinary text DMs enter this path.
Resolve the sender and direct peer independently through `Persons`; require
both to be the same known person and the direct address to be identity-bound.
Reject unknown/mismatched/unsupported peers before persistence, custody,
projection, media comprehension, model creation or spawned feeds. Never use a
push name, model argument or client-supplied `person_id` as sender authority.

For known but denied/ambiguous DMs, record only the existing content-free
refusal counter/log, not message text. Reject media-bearing messages before
custody in this slice. Known text edits/touches may operate on an existing
same-scope target through existing author checks, but never become fresh text
turns by inventing a parent. Empty/control events remain non-conversational.

The admitted binding travels with every subsequent stage. Give DM engagement
its deterministic direct-address policy ahead of mention/relevance policies.
The default cannot be re-resolved independently in later stages. Existing
wire-conversation slots serialize replies on the same chat; cross-account
Persona contention uses the existing expected-revision conflict handling.

## Compose two read authorities, one write authority

Use `TranscriptInjection.assemble` and the existing block builders. The DM's
actual audience is a complete, authenticated singleton of its owner; it is
not `Room.scope`'s degraded group-roster fallback. Construct an explicit DM
audience view and render scope rather than registering a synthetic conversation
in shared policy. Its location flag is false and its tools grant is empty.

Shared context uses the anchor's existing unit boundary with this audience's
person/exclusion/disclosure constraints. The shared subject list remains the
owner alone for person-bound Persona/social/presence reads; group discussion
Record content is not limited to messages authored by that owner. Do not borrow
the whole group's listed roster to expose another person's restricted account.
This preserves the existing rules rather than widening personal data access.

The private transcript uses the exact scope ID, including cross-account
continuity; provenance names its source platform without leaking identifiers.
Render shared Persona and private Persona as separate labelled blocks. Shared
facts do not get rewritten into the private account just to compose a prompt.
Where they conflict, keep source/age/uncertainty visible; a private correction
affects the private view and does not erase the shared line. Hypotheses remain
labelled questions under the existing rendering rules.

Omit location-bearing blocks and mixed-unit Nora self accounts that existing
`Policy.shows_self_in` / `shows_self_to_unit` would withhold. Never substitute
the home room. Shared notes, memos, Standing and lessons may be read only
through their existing disclosure filters. Blocks without a suitable DM
read predicate remain absent, counted as unavailable when requested.

Collect shared exposure references for prefill and every tool result as
specified in storage. Private transcript replay checks those dependencies
before returning bodies. Tool caches, lookup expansions and citation hydration
include scope in their cache key/selector; raw source IDs alone grant nothing.

## Closed tool set

The initial DM registry permits only `send_reply`, `memory_search`,
`record_query`, `load_transcript`, `about`, `remember` and `persona_dispute`, using their existing
names. Read tools receive bound private/shared readers, not arbitrary units or
wire rooms. `about` and `remember` resolve only the owner; remembering a fact
about somebody else is refused. In DM mode `remember` queues the owner's
bound Record item for the private-only writer, ignoring model-supplied facts
as evidence and applying no direct Persona mutation. The writer reads only
private owner-authored text, not hydrated quotes, shared material, Nora's
replies or mixed-turn working state. A successful queue is not a saved-fact
receipt; its result and doctrine must say processing is pending. This is a
DM-specific routing/result change to measure under `nora-tool`/`nora-eval`,
not a claim that the existing judge verifies a fact against cited text.

`persona_dispute` binds the subject and target account to the private owner
before looking up a line; a shared or another private account's line is refused.
Keep the existing subject-dispute immediate hide for that private line, then
queue private-only reweighing. Persist the bound owner Record citation and
private target, not a model-written replacement fact, quoted shared text or
freeform reason as new evidence. It cannot hide a shared line. All ordinary
incoming private correction/forget text also reaches the private adaptor; an
immediate-hide claim requires the actual scoped dispute write receipt, while
later reweighing is explicitly asynchronous.

Required tool discovery, if used by
the current registry, may reveal/activate only this same closed set.

All other tool specs and budgets are excluded. Both discovery and execution
enforce the set through the existing registry/ReplyGuard seam, so a guessed
call cannot reach a disabled tool. No relay/send-to, reminders, group writes,
computer/subagent/export, external MCP tools, location/presence, social reads,
generated/fetched media or asynchronous jobs. This is a narrow initial slice;
expanding it requires scope and destination proof for the added operation.

## Consumer inventory and write disposition

| Existing consumer | DM disposition in the first implementation |
|---|---|
| Ingest audit + graph projector/rebuild | Persist bound Record; private graph scope only; same transaction |
| `memory_feed`, media comprehension feeds, lazy adoption (`app/adoption.py`) | No DM input to Honcho, including replay/backfill; DM never reads a peer card |
| Ingest stimulus, Standing folds, Cognition wake/agenda, group participation | No DM-derived shared stimulus, activity count, wake, agenda or turn |
| `PersonaEvidence`, `SqlCharacterEvidence`, person-key enumeration | Private owner key, private Record counts/pages; shared enumeration excludes all private rows |
| Persona write, dispute, hypothesis/verdict polling | Same private account and private evidence; no shared line mutation; no global unscoped pending-row scan |
| Collective Persona, Culture, Rollup response/play/tie, Lessons, Nora self | No DM evidence or unit-wide aggregate input; group readers explicitly exclude DM rows |
| Notes, memos, Register, Standing writers | No private write in this slice; no shared write from DM prompts or background evidence |
| Memory exact/lexical/vector seeds, hops, rebuild retries | Persisted private scope applies on each read/projection; no hub traversal into a private partition |
| Model debrief, Standing folds, conversational working state | Skip production and replay for DMs; this slice persists Record and private Persona, not mixed-context summaries |
| Tool-call logs and retained operational traces | Private binding carried; no shared learning/output consumer may read it; telemetry uses opaque IDs as below |
| Portal account/history/citation/dispute endpoints | Exclude private namespaces in shared payloads; private portal UI/API is deferred and refuses private IDs |
| Analytics and operational evidence | Content-free aggregate counters may count DMs; shared social/cognition metrics must not ingest DM content or person activity |

Paths are verified starting points, not a substitute for implementation-time
caller tracing. The implementing PR must attach an executed consumer census:
every changed selector and every intentionally disabled path, with its proof.

In particular, `nora/engagement/stages/debrief.py` is a model-driven Standing /
working-set writer, not just operational bookkeeping. Do not call that fold for
a DM or replay its mixed-context working set. Retain ordinary delivery/outcome
and trace recording separately. This avoids adding a second private summariser
or another summary dependency mechanism to the initial slice.

## Telemetry identity before the first span

A WhatsApp direct chat ID contains a phone number. The existing group pipeline
uses `message.conversation_id` in engagement roots; `nora/obs/tracing.py` and
`nora/agent/telemetry.py` propagate it to provider trace metadata, `session.id`
and `gen_ai.conversation.id`. Private storage binding alone does not stop that
identifier export.

Before any ingest/root span, logger context, queue annotation or provider
metadata is created, DM paths use an opaque turn UUID; once resolved, they may
use the opaque scope UUID for session correlation. An unresolved DM is named
`dm`, never its wire chat/sender ID. No phone/JID, account ID or identity-index
key enters trace/session attributes, exception messages or ordinary logs.
Keep wire identifiers only in the transport and explicitly authorised Record
provenance. This sanitisation must not rewrite the actual delivery target or
break edit/deduplication keys. Group telemetry semantics are unchanged.

Check all export routes, not just one root span: queued turns, model retries,
fallbacks, tool-call recording, error paths and retained trace serialisation.
Operational evidence may correlate opaque scope/turn IDs; raw message content
remains subject to the existing private evidence handling rules.

## Deliver and refuse safely

Keep `deliver.build` → `Deliverer` → platform sender as the only reply path.
Before the first and each subsequent send, recheck the owner, DM grant, active
anchor and identity-bound wire target. The outbound Record uses the original
scope and exposure envelope even on failure. Retain ordinary egress scanning,
audit-first delivery, budgets and strict send-reply semantics.

Queued work retains its original scope. After restart/default change, discard
or refuse work whose original anchor is not active; never rebind it to the new
one. The initial slice starts no delayed tools or unsolicited DM tasks, but
the queue, fallback and delivery callbacks still need this check. No OTP path
is routed through conversational DM scope; requested login codes retain their
existing independent bounded template and guards.
