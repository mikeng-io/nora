# Reuse the unit pipeline; curate before sharing

Status: proposed. [Storage](02-storage.md) defines raw scope and publication
provenance; this file assigns enforcement to existing components.

## 1. Identity, admission and audience

Extend `nora/persons_file.py` (`_PersonEntry`) and `nora/persons.py` (`Persons`)
with optional default_unit and proposed per-platform
`dm_platforms.<platform>.enabled` (omitted means true); compose validation
against explicit policy membership. Add `[dm] enabled` default false beside the
person allowlist. Enabling requires graph/Persona readers and the curation write
boundary to be wired; never fall back to a legacy Honcho feed. Identity/policy
are startup-loaded; validate copies and recreate for changes.

At the pre-audit scope stage, resolve the WhatsApp or Signal sender and direct peer
independently to the same known person, validate direct target and anchor, then
bind DmScope once. Unknown/denied/mismatched/ambiguous/unsupported input is
refused before Record, custody, projection, feeds or model calls. The initial
slice rejects media before custody. Redeliveries and edit/touch targets retain
original scope and existing author checks.

Reuse the deterministic engagement policy, with an eligible DM addressing Nora
without a mention. Preserve budgets, deduplication and queue semantics; add one
turn slot keyed by `dm_scope_id` across platforms before transcript assembly. Hold it through
reply audit/delivery and private working-state update so two platforms cannot
run concurrent turns over one transcript. Retain wire keys for deduplication,
edits/touches and actual delivery; they do not define the DM turn lock. Build
the audience as one authenticated human, not a degraded roster fallback or a borrowed group audience. The default unit grants context,
not location permission or another room's tool grants.

## 2. Context and canonical Persona

Reuse TranscriptInjection and existing block formats, with scope-paged,
interleaved platform-marked transcript rows. The DM assembles its own private
history plus the default unit's permitted group context and canonical curated
Persona/Notebook. There is no second private Persona block. Confidential
self-details remain available through private Record, not an uncurated shared
Identity line. Existing listed-subject, exclusion and location rules remain;
never widen a DM to the group's whole roster to expose restricted accounts.

Group turns assemble group Record plus published unit views. They never get raw
DM history, private curation drafts, review rationale or source citations through
prefill, tools, lookup expansion, caches, graph neighbours or portal payloads.
Private evidence reads are a code-bound capability for the owner/curation task,
not a public tool parameter or a consequence of unit membership alone.

`remember` in a DM queues the bound source Record item for the existing unit
writer's curation path. It does not directly store a fact invented by a model
that has read mixed context. A queued response says processing is pending; only
an actual curated commit is a saved/shared understanding receipt. Hypotheses
still need the existing trait premises and prediction machinery.

A subject's explicit dispute/forget of an existing unit Persona line retains
the existing immediate-hide semantics, including when requested privately.
The request and its reason remain private; no automatic group announcement or
private replacement text is published. Reweighing/replacement uses curation.
This deliberately replaces the earlier private-account-only dispute design.

## 3. Cognition and Rollup

Keep one unit's existing Cognition runner, agenda, locks, budgets, native stores
and Persona adaptor. A private stimulus may wake that unit for **curation**;
its text, activity counts and agenda description are not exposed to a normal
group-facing situation. In the initial implementation, a curation batch opens
one owner/anchor's raw DM scope alongside permitted shared context; it does not
pool every member's private transcripts into one unbounded prompt.

Use an explicit internal session capability for this phase. It may read its
bound raw evidence and propose curated native writes. All mutations pass the
publication boundary in storage. It cannot use group send, proactive/remind,
pace/self-wake, export, computer or asynchronous callbacks to express private
material directly. Expiring/holding a draft leaves no shared agenda text or
work item that bypasses publication; failed sessions retain unconsumed evidence.
Private review timing may affect internal scheduling, but normal group agendas
must not expose a member's DM activity or source-specific reasons.

After a successful curated commit, ordinary unit Cognition and person writers
read the canonical result normally and can act through existing audience guards.
They do not inherit the raw-DM session's transcript, logs, tool cache or working
state. Same engine and unit understanding; different read/effect capabilities.

Rollup can supply bounded private patterns to curation. Neither a deterministic
count nor a model summary is automatically shared. Curated roll-up understanding
may inform unit Persona, Lessons, Standing, notes, memos or collective accounts
through their existing writers and the same publication boundary. Curation of
person facts still honours the subject's listed-source rule. Do not reinterpret
a Notebook write as permission to publish arbitrary information about others.

DM debrief/working-state production is permitted as private session bookkeeping,
with DmScope propagated to storage/replay and no ordinary unit reader access.
Do not run the old unscoped Standing fold. A shared Standing/working summary is
a separate curated write, not the private debrief copied into the shared unit.
If a private artifact has no scoped implementation, keep it transient/omit it;
that does not disable unit Cognition or canonical Persona learning.

## 4. Tools and other consumers

Reuse the registry's capabilities rather than building a separate agent.
DM text tools include send_reply, memory_search, record_query, load_transcript,
about, remember and persona_dispute with the bound readers/writers above.
Required tool discovery can activate only permitted tools; execution/ReplyGuard
also enforces the set. Computer use, scoped export and subagents are enabled
in the first slice as [cross-platform work](05-cross-platform.md) specifies.
This reverses their earlier deferral for ordinary authorised DM turns; internal
raw-capable curation still cannot use computer/export/callbacks to bypass
publication. Direct DM relay/unguarded group writes, location, video generation
and inbound media/voice remain deferred; scoped computer file returns are included.
Private job results are allowed deferred sends, not a grant for arbitrary
unsolicited private jobs.

| Existing consumer | Required behaviour |
|---|---|
| Record and graph projection/rebuild | Raw DM binding persists; no raw DM node/text enters group reads |
| Stimulus, Cognition agenda and queue | Private cause can schedule bound internal curation; only published results enter ordinary shared situations |
| Persona evidence/write/dispute/verdict | Same unit account; author/subject scoped source inputs; publication check on raw-DM-derived mutations |
| Rollup, collective Persona, Lessons, Standing, notes, memos | Reuse native writers; private inputs/drafts stay protected, curated shared results are allowed |
| Debrief and retained working state | Scope private artifacts; shared folds require publication, not a session label |
| Memory feeds, media feeds, lazy adoption | Never feed raw DMs to Honcho or other uncurated legacy surfaces, including backfill |
| Portal, evidence, citations and exports | Published view only for shared reads; private source bodies/IDs/reasons are not inherited from target visibility |
| Trace/log/provider metadata | Opaque turn/scope IDs; phone-bearing direct chat IDs remain transport/authorised Record provenance |

The implementation must trace every caller of these native writes and reads;
this table is the census starting point, not an assertion of runtime coverage.

## 5. Publication is a semantic decision

The existing writer must judge whether the exact visible result reveals a
private statement, confidential implication, source identity or interaction.
Keep-private instructions and uncertain confidentiality cause hold/refusal;
notes, memos and predictions cannot be loopholes. Semantic fidelity is also
required: do not turn a private anxiety into an unsupported universal trait.

Code enforces source scope, restrictions, exact candidate/target binding,
transactions and audience projection. It cannot prove from a hash or citations
that the model's paraphrase is safe. The proposed model acceptance suite must
independently label both safe useful publication and unsafe publication; a
system publishing nothing fails usefulness. A model/reviewer outage is ERROR
and leaves the candidate uncommitted. No extra always-on review agent is required.

## 6. Delivery and operational privacy

Retain deliver.build → Deliverer → sender, audit-first delivery and egress guards.
Recheck active owner, DM grant, anchor, launching platform opt-out and direct
target before each DM send, including subagent results. Pin the actual platform
and target from inbound admission; another linked account is not a fallback.
Queued work cannot retarget after a default change. Deferred results enter the
same scope turn slot before reading transcript/composing their DM reply.
Ordinary group sends can consume curated premises through existing guards, never raw curation output.

Before any root span, logger context or provider metadata, replace DM wire IDs
with an opaque turn ID; resolved sessions may correlate with the opaque scope
ID. Sanitize retries, queues, error paths and retained traces too. Do not alter
actual wire targeting or edit/deduplication keys. OTP template delivery remains
independent. Operational traces/review inputs containing private content require
private evidence access; a shared publication receipt does not expose them.
