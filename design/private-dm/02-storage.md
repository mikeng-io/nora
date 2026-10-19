# Scope, keys and persistence

Status: proposed implementation design. Depends on the [contract](00-contract.md)
and [current seams](01-current-flow.md). Names below are proposed additions,
not symbols already present in the repository. Do not allocate a migration
revision until implementation, from the actual current Alembic head.

## One durable private partition

Add `dm_scope` with `id UUID PRIMARY KEY`, `person_id TEXT NOT NULL`,
`anchor_unit TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL`, and
`UNIQUE(person_id, anchor_unit)`. Both text fields must be nonblank. Creation
uses insert-on-conflict/read in one transaction, so concurrent verified
accounts reuse one partition. Identity/policy live in files, so these names
cannot have database foreign keys to configuration.

Derive the private storage unit deterministically from the scope ID:
`dm:<uuid>`. Reserve that prefix against operator-configured shared unit names.
It is a private namespace in the existing unit-keyed stores, **not** a new
policy unit or a conversation in `policy.conversations`. A single helper owns
the encoding and reverse validation. Never derive it from a phone, display
name or string concatenation of an ambiguous person/unit pair.

The proposed immutable domain value `DmScope` carries the scope ID, owner,
anchor unit and private storage unit. Resolve it once before recording; carry
it through `NormalizedMessage`, `EngagementRequest` and `TagContext`, including
queued work. The wire `ConversationTarget` remains the actual WhatsApp direct
chat. Scope and destination are separate values, neither chosen by the model.

## Record binding and point reads

Add nullable `messages.dm_scope_id UUID REFERENCES dm_scope(id)` without a
default. Existing rows remain unbound. Add a check that a bound row has
`conversation_type='dm'` and an index on `(dm_scope_id, id)` for private paging.
New admitted DM writes, including Nora's replies and failed/pending outbound
rows, require the binding in `SqlRecordStore`; absence is a refused write.
An older unbound DM is not guessed into a partition or fed to private learning.
Group readers require group rows/unbound scope; a DM reader requires the exact
scope ID. Migration/metadata parity and history/snapshot restoration must cover
the new table, field, check and index through the existing history machinery.

Do not replace `messages.conversation_id` with a synthetic ID: the original
platform/chat/stanza identifiers still back edits, deduplication and delivery.
Private transcript APIs take scope ID explicitly, and include the admitted
scope across verified accounts. Existing group transcript APIs exclude DMs.
No private call may fall back to the old conversation-ID-only API.

Quote, edit, reaction, delete and citation hydration resolve the target within
the authorised scope **in SQL**, before returning its body. Same wire chat,
timestamp, handle or guessed Record ID is insufficient. An edit/touch naming
an old anchor is refused while another anchor is active, with no model turn.
Persisted redeliveries cannot be reinterpreted under a new default: check
existing platform message identity before binding, and keep its original scope.

## Persona and cursors

Reuse `persona`, `persona_line`, disputes, verdicts and `SqlPersonaStore`.
The private human account is `character_key(private_unit, owner)`; its `unit`
is the private storage unit, never the anchor. Existing typed identity,
precedence, induction/abduction, decay and append-only validation still apply.
The first slice writes only the owner's private human Persona. It creates no
private collective account, private Persona about somebody else, notes, memos,
Standing or lessons. Those stores remain shared-read-only or unavailable.

The existing person adaptor runs with private Record evidence and private
account heads. It does not ingest group history, shared Persona heads or social
streams into its citable writer input in this first slice. Shared context is
available to the conversational reader separately. This avoids importing a
shared hypothesis into a private account's same-account premise chain or
creating a second writer. In a DM, `remember` only queues the bound owner's
Record item for this private-only adaptor; it never commits a fact selected
by the mixed-context conversational model. Model-supplied fact text, shared
tool results, hydrated quote bodies, Nora's sends and conversational working
state are not writer evidence. Other subjects are refused. A bare "remember
this" pointing only to shared/tool context cannot establish a private fact;
the tool reports queued processing, never that a fact has already been saved.

Namespace every recent/history boundary, history cursor, pending dispute and
verdict job by the private account key. Use the existing settlement-before-
cursor-advance contract and conflict retry semantics. A private failed or
unsettled batch cannot advance any shared cursor. Shared person/unit key
enumeration never includes private namespaces; a separately scoped key list
enumerates eligible existing `dm_scope` rows for the same adaptor loop.

## Retrieval and copied context

Project bound messages and private Persona under the private unit. Private
message `origin_room` uses one synthetic room key from its scope ID, not the
wire chat ID. Source IDs stay ordinary Record/Persona IDs; shared graph hubs
remain non-traversable exactly as today. All projection retries, rebuilds,
backfills and vector/exact/lexical reads honour the persisted binding.

Run private and anchor-shared retrieval as separate bounded reads and merge
under the existing overall result/prompt budget. Their scope objects remain
distinct; do not union them into a writer authority. A derived item written
by a DM stays private regardless of which read supplied the idea.

For replay of Nora's replies, add nullable `messages.dm_shared_refs JSONB`:
an array of typed source references for **all** shared material exposed to the
turn, not just citations the model chose. Include inherited references from
earlier private replies read by that turn. Scope-aware context/tool reads
collect these references; guarded delivery stores them with each outbound row.
An empty array means no shared input; NULL on an older outbound DM means
unknown dependencies and excludes that reply from replay. Each reference must
resolve through the same current shared read/disclosure predicate before replay;
a missing, unreadable or newly excluded source withholds that dependent reply.
Do not compare text or ask a model whether it copied anything. This small
dependency envelope is justified by the real boundary: a filtered shared source
can otherwise return through an already-authored DM reply. It is not a deletion
cascade and does not mutate shared sources. Incoming plain text remains the
person's own statement; verified quote hydration is checked independently.
The first slice creates no extra summariser that would discard provenance.
Historical private partitions remain stored but unreadable after loss of unit
membership or DM permission. A default change selects another partition;
there is no data migration and no rewrite of older scope bindings.
