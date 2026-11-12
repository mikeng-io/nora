# Entities, relations, evidence and retrieval

## Ownership

Source and entry stores retain their content, heads and lifecycles. The graph
adds persistent entities, semantic relations and evidence links. Its index
projects searchable pointers, text, vectors, times and scope from those owners;
it never becomes another source of truth. Raw presence fixes are never indexed;
only computed episodes — stays, trips — are a read surface.

A persistent entity is a person, room, unit, Nora, place, event, work,
organisation or topic. People and collectives are pointers to authored identity,
not model-created identities. Other entities are created only as the endpoint
of an admitted, evidenced relation, never as orphaned mentions.

Relations have a closed, reviewed vocabulary and legal endpoint kinds. Record
relations express source structure such as authorship, attachment and replies;
Rollup relations express computed episodes and aggregates; Notebook relations
express judgements such as plans, attendance, preferences and associations.
A new meaning requires a reviewed definition and endpoint contract, not a new
per-kind store or an autonomous ontology writer.

The entity is the hub, not its profile. Citations support a claim, `about`
indexes its subjects, and links supply typed context or premises. These are
different promises and cannot substitute for one another.

## Evidence and admission

The model points at meaning, shown endpoints and shown sources. Code allocates
identity, connects the statement to its sources, derives scope and bookkeeping,
and admits or refuses each statement independently. Valid siblings do not fail
because one proposal is malformed.

Admission checks the relation vocabulary, endpoints, layer, evidence custody,
quoted spans, scope and correction target. Only source bodies actually rendered
or explicitly returned by reads are citable; a graph label alone grants no
source body and no authority to correct an unseen relation. Notebook evidence
is refused, and scope can narrow but never widen beyond its evidence.

Each relation records its writer and supporting or contradicting evidence.
Quoted evidence must occur in the cited source. A session's receipts preserve
what it was shown, while its model, instruction and execution provenance make
the write re-derivable. Rendering never decides validity; admission never
formats presentation.

## Time and correction

Assertion time, build time and validity in the world are distinct. Unknown event
time stays unknown. A later contradiction closes or supersedes a relation rather
than deleting it. A plan does not become attendance without independent evidence
in the relevant window, and a cancelled plan does not prove an event happened.

Hidden source nodes leave the index and source-tied readings lose eligibility.
Other judgements retain their own history and citations, including that a source
was later deleted; there is no automatic withdrawal cascade. Subject disputes
have their own immediate visibility rule in [profiles](profiles.md).

## Retrieval

A read first computes the unit, person, room and evidence eligibility inside
the query. Hybrid search finds seeds, and bounded traversal follows meaningful
Rollup and Notebook relations. Structural Record edges support evidence lookup
but do not turn a person into an unlimited fan-out to everything they wrote.

Results return nodes with their relations, layers, validity and evidence handles.
Current content is hydrated from the owning store, so stale index text cannot
revive an old claim. Depth, fan-out, nodes, time and result size are bounded;
hitting a bound reports partial, and failure reports unavailable.

The same retrieval semantics serve automatic prefetch and explicit reads. Push
is limited to a small fixed core, code prefetch starts from structural anchors,
and tools provide depth on demand. Preview rows carry kind, identity, time,
state, layer and evidence availability so the model can tell a cancelled plan
from a supported current fact.

A public social item scoped to a person is readable in any room that lists
that person.

## Named places

A household place is a persistent named entity, not a person's live position.
Resolution checks saved local places before public lookup within the room's
permitted circle. Unknown or unpermitted scope falls through to an ordinary
public name search without revealing that a guarded place exists.

The server may substitute a saved coordinate internally for a route the person
requested. The model sees only the place name and its evidence. A person's live
position never becomes a routing endpoint, and coordinates never enter model
prose or public enrichment.

## Public enrichment

A fetched public page used by the loop is retained as a Record item with its
fetch and content provenance, so a later claim remains re-derivable. It can
support public attributes of a place, work, event or organisation. It cannot
establish a private person's attendance, traits or relationships.

Outbound queries contain only the minimum public question. A private person's
name, handle, private address, identifier, coordinate or raw trace never leaves
for enrichment. Egress screening is independent of whether internal content
filters are enabled.

## Why relational storage

Postgres already holds the owners, transactions and scoped recursive walks.
Entity and evidence writes can share the source transaction, and a hidden source
can leave the index atomically. A separate graph engine must earn its cost on
measured query semantics and plans, not on vocabulary preference.
