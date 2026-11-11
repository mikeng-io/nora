# The memory map: content, understanding and connections

This is the current conceptual map. It describes design responsibilities,
not installed flags or completion of every storage migration.

## Layers and ownership

| Layer | Holds | Authority and correction |
|---|---|---|
| Authored | Human identity mapping, doctrine, policy and configuration | Humans own these; derived memory cannot override them |
| Record | Messages and edits, source readings, media/social observations, fetched pages, disputes, feedback, Nora's retained exchanges and actual results | Preserve source, author and time; edits/deletions change source eligibility |
| Rollup | Computed stays, outings, co-locations, response episodes and aggregates | Rebuild from Record; a model does not edit the result |
| Notebook | Evidence-grounded judgements and ongoing affairs | Revise, dispute, supersede or retire through each kind's writer and lifecycle |
| Views | Profiles as rendered, context blocks, Standing and portal presentations | Assemble for the reader from eligible current sources; not new evidence |

A stored profile is Notebook; its bounded rendering is a View. Layer answers
who owns a statement and how it is derived. It does not determine its subject,
usefulness or certainty. Only Record and Rollup provide independent evidence;
Notebook conclusions may provide context and typed premises, never evidence for
another conclusion.

## Graph, ontology and index

The graph connects concrete items across layers and stores. The ontology is
the vocabulary and constraints that give those connections meaning. The search
index finds entry points; traversal returns a scoped subgraph with provenance.

| Component | Owns |
|---|---|
| Existing source and entry stores | Their content, current heads and lifecycles |
| Graph entity store | Persistent people/room/unit/Nora pointers, places, events, works, organisations and topics |
| Relation and evidence stores | Connections, layer, validity, writer, status, supporting/contradicting sources |
| Memory index | Rebuildable pointers, matching text, vectors, times and scope projected from owners |

Losing the index loses retrieval infrastructure, not source truth. Losing an
owned semantic relation loses stored understanding: the graph is not wholly a
cache. [The graph contract](27-the-memory-is-a-graph.md) owns the exact schema,
reviewed vocabularies, admission rules, temporal model and bounded traversal.
Adding a relation value needs a reviewed meaning and allowed endpoints, not a
new per-kind table or autonomous ontology writer.

The entity is the hub, not its profile. Entries about the same person or event
meet there; they need not feed a single growing profile. **Cites**, **about** and
**link** retain different meanings: source support, subject indexing and typed
context/premises respectively. [Relations](../notebook-memory-types/10-relations.md)
owns the permitted entry links.

## Profiles: understanding people and their social context

A profile is Nora's current, revisable understanding of a person, room, unit or
herself. It is a bounded synthesis, not a complete transcript or the subject's
own self-description. The [profile contract](27-the-memory-is-a-graph.md#9-profiles-are-reshaped-in-the-loop-from-the-graph)
keeps **facts**, **traits** and **guesses (unconfirmed)** distinct:

- Facts retain source attribution and temporal qualifications; the graph design
  renders them from current relations rather than maintaining a competing copy.
- Traits generalize supported recurring observations, retaining the conditions
  in which a pattern holds. An isolated occasion is not a durable trait.
- Guesses explain observations tentatively and carry an observable prediction
  and window; an independent verdict confirms/refutes that prediction, not the
  whole person or every causal claim.

Social understanding includes relationships and shared history, not just personal
attributes. Collective profiles describe observed group practice, purpose and
norms; they are not an average of private member traits. Nora's own profile
concerns how she comes across; lessons concern how she should act. New evidence
can change either without changing the other.

Authored identity mapping stays human-owned. A person's own statements,
third-party reports, observations and Nora's inference retain their different
attribution and precedence. [Disputes](35-persona.md#2-disputes-anyone-weighed-in-context-the-subjects-hides-at-once)
owns the subject's immediate hide and subsequent weighing. [Profile dimensions](47-character-dimensions.md)
and [typed identity](75-typed-identity.md) retain their detailed accuracy contracts;
current naming follows [ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md).

## Notebook kinds and situation

[Notebook kinds](../notebook-memory-types/00-design.md) owns note, topic, lesson,
profile and decision. Their distinctions organize use and lifecycle; they are
not an exhaustive ontology of everything Nora can remember. Memo is a note's
kept phase in the target model; published events/documents are Record postings.
Legacy stores remain until their explicit migrations. Standing is the common
derived presentation, not another memory kind or substantive judgement writer.

A current situation combines relevant profiles with recent observations, open
matters, decisions and canonical pending/execution state. No profile alone can
supply what is happening now. [Context assembly](../context-assembly/05-the-cognition-context.md)
owns which decision earns each injected block; the tools reach further without
injecting every retained item. Current availability and external state are read
from their owners, not inferred from an old profile or a summary of an attempt.

## Three rules the index keeps

- **Scope before ranking and traversal.** The unit is the read boundary;
  every node, endpoint and evidence source must pass its current eligibility
  checks. Person evidence retains the listed-room rule. Presence/social grants
  and any private scope are independent of merely sharing a unit.
- **Hydrate from the owner.** Index hits point to current source/entry heads;
  stale matching text cannot revive a superseded claim. An incomplete search
  is partial or unavailable, never a confident assertion of absence.
- **Hide the source without inventing a cascade.** Hidden source nodes leave
  the index in their write; source-tied readings lose eligibility when any of
  their sources is hidden or edited. Other derived judgements keep their own
  history and citations, including the indication that a source was deleted;
  there is no automatic withdrawal cascade.

Media is indexed per attachment under its message's scope. Raw presence fixes
are reached through episodes; aggregates remain available by key. Stickers are
a send catalogue. Tension remains operator-only and unreadable by Nora. Authored
configuration/doctrine are not indexed. Shared external files retain their link
and discussion under the [external-file contract](../external-files/00-design.md);
a bounded fetched web page is separately retained as evidence for what the loop
actually read. Store-specific tools keep their typed reads and operations.
