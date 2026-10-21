# Private Record and canonical unit state

Status: proposed. The [contract](00-contract.md) replaces separate private
Persona accounts with curated publication into the existing unit stores.
Names below are proposed additions, not existing symbols. Allocate migration
revisions from the actual Alembic head when implementing.

## 1. Keep the raw conversation binding

Retain proposed `dm_scope`: `id UUID PRIMARY KEY`, `person_id TEXT NOT NULL`,
`anchor_unit TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL`, with nonblank
text and `UNIQUE(person_id, anchor_unit)`. Insert-on-conflict/read makes two
verified accounts reuse the same binding. Configuration is file-backed, so
there is no fake foreign key to a policy unit or identity file.

`DmScope` carries scope ID, owner and anchor unit through NormalizedMessage,
EngagementRequest, TagContext, queues and retained work. **Remove** the old
`private_unit = dm:<uuid>` field and private Persona key scheme. The wire
ConversationTarget remains the actual direct chat; scope is never a destination.

Add nullable `messages.dm_scope_id UUID REFERENCES dm_scope(id)`, a check that
a bound message is a DM, and `(dm_scope_id, id)` paging index. New conversational
DM input/output requires the binding; OTP templates retain their independent
bounded path. Older unbound DM rows are not guessed into an anchor. Restore
and schema-parity checks must cover the table/field/index/history registration.

This binding is the transcript key across platforms, not just an ownership tag.
Proposed selector: `recent_for_dm_scope(dm_scope_id, before_id, limit)` on
SqlRecordStore, with scope-bound authorization before returning text. Page by
stable Record ID so same-time deliveries interleave deterministically; render
each row with time, speaker and platform. Do not concatenate two independently
paged wire-chat histories or call `SqlRecordStore.recent` by conversation ID.
Deduplication and quote/edit/touch hydration include platform plus the original
wire target and original DM scope, never a timestamp/remote ID alone.

Raw group reads explicitly exclude DMs. Owner-authorised private reads require
the exact scope ID, including quote/edit/touch/citation hydration in SQL before
text is returned. Same wire chat, guessed Record ID or timestamp is insufficient.
A default change never rebinds an old message/redelivery. Old-anchor edits and
queued work cannot be applied under the new anchor.

## 2. One existing account and one writer

Persona stays `character_key(anchor_unit, person_id)` in the existing
`persona`/`persona_line` stores. Identity, Character and Hypotheses keep existing
canonical typed values, precedence, append-only chains, disputes and verdicts.
Notebook remains its current tiers; curation does not create a second Notebook.

The person writer's bounded evidence reader may include the subject's own
bound DMs in that same unit. A DM lists its owner for the existing listed-room
rule: discussing somebody else privately does not make that DM evidence for
that other person's Persona. Another unit, another owner's private DM scope or
a sender who cannot be resolved is never eligible. Existing shared evidence
and social rules remain otherwise unchanged.

Use the existing account watermark/settlement machinery; extend its input
selector with scoped DM pages rather than minting a competing private writer.
Track DM review progress separately from group source progress within that
existing account's input bookkeeping so a group count cannot consume private
pages. Failed/unsettled review does not advance its cursor. Reuse the existing
conflict handling and atomic account write; refresh actual cursor names at build
rather than copying historical count-domain assumptions.

## 3. Curation publication provenance

Every DM-derived shared mutation goes through an explicit publication check
before the existing native store commits it. This includes Persona/Notebook
adds, revisions, retirements, hypothesis verdict changes and derived summaries.
A raw-DM-capable context cannot call the ordinary unguarded write operation.

Propose one append-only `curation_publication` receipt with UUID ID, unit,
source DM scope ID, native target kind/ID/revision, canonical visible-content
SHA-256, private source refs, confidentiality/curation judgement, writer session
and written time. A unique native target revision prevents duplicate receipts.
This is provenance for the real private-to-shared boundary, not a generic entry
store or an additional reasoning tier. Private rationale/source fields are not
included in shared DTOs, model blocks or explanation tools.

The write boundary resolves sources server-side, checks unit/owner permissions,
explicit confidentiality restrictions and typed grounding, and binds the exact
visible payload to the judgement. Caller-supplied "curated" flags, a receipt for
an older revision, or merely citing a private message are insufficient. A
semantic judgement must address what the entire payload reveals, not only a
selected safe sentence. Hold/refuse/ERROR writes no shared mutation or receipt.

The subject's existing immediate-hide/forget operation is a restricted safety
withdrawal, not semantic promotion. Check the authenticated subject, target and
bound request immediately and record only the native status change with private
provenance; do not defer hiding for a publication judgement. No replacement
text or reason becomes shared through that exception. Subsequent replacement,
reweighing and ordinary retirement still follow the curation write boundary.

Commit the native mutation and its receipt atomically through the existing
store transaction seam. The hash covers every exposed field, including title,
conditions, predictions, status/replacement, attribution and aggregate values.
No post-commit projection or callback may add unreviewed visible content. A
later change needs its own publication decision if it uses raw DM evidence.
Unchanged group-only writes retain their existing path and native validation.

Curation decisions are model-produced and can be wrong; deterministic checks
measure permission, binding and provenance, not semantic safety. Do not label
receipt existence as a privacy PASS. Acceptance independently judges the input,
proposed visible content and committed readback.

## 4. Indexes, Rollup and evidence reads

Raw DM graph nodes use an opaque scope-derived origin distinct from the wire
chat and shared unit. Private seeds, hops, by-ID reads, projection retries and
rebuilds require the owner or an explicit internal unit-curation capability.
The ordinary unit reader cannot acquire that capability from a model argument.
Person/room hubs keep their existing non-traversal rules.

Curated targets index normally in their canonical unit, with the usual subject,
exclusion and location restrictions. Their source DM nodes never become shared
neighbours or returned provenance. Reading a published target does not grant
its citation text, IDs, session log or private rationale. Explain from curated
content only; direct private evidence requests remain unavailable/refused.

Raw DM Rollup counts/episodes remain scoped working inputs. Initial shared
Rollup/Notebook blocks receive only the curated result, not private event
counts, timestamps or per-person activity. Any materialised DM-derived shared
aggregate must use the same publication receipt and source-private projection.

The earlier `dm_shared_refs` blanket replay-envelope proposal is removed.
Authorised private conversation history is not retroactively unpublished when
a source group message is pruned; already-disclosed knowledge is not erased.
Fresh shared reads still enforce current exclusions/location policy. Unit/DM
revocation denies private history; a default change selects another partition.
Published unit understanding stays put until normal dispute/revision/retirement.

## 5. Private computer and deferred-job binding

[Cross-platform work](05-cross-platform.md) defines the DM workspace keyed by
`dm_scope_id`, its own 0o700 Unix-user boundary and explicit unit-read/publish-write
access. Keep filesystem/browser data private like raw Record; private copies do
not become shared merely because their input came from the unit. A default-unit
change selects a distinct scope/workspace; no transcript or file rebinding.

Deferred job payloads retain the launching owner, DM scope, anchor unit, platform
and direct target durably beside the existing job identity. Workspace identity
is not a destination. Budget admission/spend is keyed by person across DM
platforms and anchors, rather than reset by choosing another linked account.
