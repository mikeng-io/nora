# Unified memory: social understanding that continues across interactions

Nora's memory supports a continuing understanding of people, relationships,
shared experiences and unfinished affairs. Its purpose is social intelligence:
use relevant experience to understand the present, participate appropriately,
and revise that understanding when evidence changes. Personal assistance can
use the same foundation; tools and execution supply its additional capabilities.
This is a design purpose, not a claim of measured social competence.

## Read the design

Read these four documents in order. Each owns one part of the current design;
specialist contracts are linked where needed, rather than repeated here.

| Document | Owns |
|---|---|
| [Memory map](15-memory-map.md) | Layers, graph/index ownership, profiles, memory kinds and scope |
| [One Cognition session](29-one-cognition-session.md) | Understanding, connected writes, decisions and retry ownership |
| [Revision and learning](50-learning-from-acts.md) | How corrections and outcomes change understanding and later practice |
| [Build and acceptance](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31) | Delivery order, bounded journeys, evidence limits and switch acceptance |

The [graph contract](27-the-memory-is-a-graph.md) owns storage, vocabularies,
admission and traversal details. [Notebook kinds](../notebook-memory-types/00-design.md)
own entry lifecycles. Neither is an additional prerequisite reading path for
understanding the whole system. The bundle index lists specialist references.

## Purpose and boundary

Preserve what eligible sources state, including information whose future use
is unknown. Spend additional organization, retrieval and revision effort where
it can support understanding a person, a relationship, a shared situation or
an ongoing responsibility. Retention, retrieval and preemptive context injection
have different purposes; retaining a statement does not put it into every turn.

People, rooms, units and Nora are all subjects of understanding. Places, events,
works, organisations and topics connect their experiences; the graph is not
limited to person profiles. Extend its vocabulary for a concrete representational
need, under the existing reviewed vocabulary contract, rather than attempting
an exhaustive ontology or allowing model-invented predicates.

Personal assistance reuses preferences, constraints, open matters, decisions
and outcomes. Memory supplies continuity; planning chooses what to do; tools
execute within their grants. External systems remain authoritative for their
current state. Intent, attempted execution and confirmed result stay distinct,
and actual results return through Record before updating understanding.

## Architecture

Records and computed Rollups ground Cognition's judgements. The graph connects
items across their stores and owns persistent entities and semantic relations
with evidence and validity. Its search index is a rebuildable projection.
Profiles synthesize understanding of a subject; other Notebook entries retain
open matters, interests, practices and reasoning. Scoped Views select what each
reader needs. No store becomes a second owner of another store's truth.

Cognition understands and connects new evidence in one wake session, maintains
relevant profiles and affairs, then decides whether to act. Later observations,
corrections and execution outcomes can revise any affected understanding.
Learning need not create a lesson; its test is supported change and appropriate
later use, not the number of stored entries or graph edges.

## Sequence

[Build and acceptance](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31)
is the single owner of current delivery order, quality evidence and outstanding
acceptance. Earlier fixed sample/parity switch formulas are historical. Source
implementation, semantic acceptance and operational activation remain separate.
[Cutover](70-honcho-cutover.md) owns the authorized switch, observation and staged
removal of Nora's Honcho dependency; this document authorizes no model spending,
backfill, flag change or deployment. The [Honcho audit](10-honcho-audit.md) records
the migration's original inventory; git retains superseded overview sequences.

## The reading that would reopen it

A bounded journey showing unsupported interpretation, lost correction, wrong
scope or a missing representation reopens the relevant contract. If connected
retrieval or maintained synthesis adds cost without improving the measured
journey over simpler reads, simplify that path. A successful write, passing
source checks or a quiet operational period establishes no semantic improvement.
