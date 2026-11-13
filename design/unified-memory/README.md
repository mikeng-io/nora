# Unified memory

Nora's memory is a continuing, revisable understanding of people,
relationships, shared experiences and unfinished affairs. It preserves eligible
sources without putting everything into every turn, and spends synthesis and
retrieval effort where they improve social understanding.

**Status:** building

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Funified-memory%2F%22)

## Parts

- **Source stores** own messages, media, social observations, fetched pages,
  presence, acts and outcomes. They preserve author, scope and time.
- **The memory graph** connects those sources to persistent entities and
  evidence-backed relations. Record and Rollup relations are projected by code;
  Notebook relations are admitted judgements.
- **The retrieval service** scopes before ranking, finds seeds through a
  rebuildable index, walks meaningful relations and hydrates current content
  from its owner. A query returns a subgraph, not a flattened fact list.
- **Cognition** interprets new evidence, resolves entities, proposes relations
  and corrections, maintains affected profiles and affairs, and decides whether
  to act. The [Cognition session](../cognition-loop/session.md) owns execution.
- **Profiles** are bounded syntheses of facts, recurring traits and falsifiable
  guesses for a person, room, unit or Nora. They are not transcripts or a second
  store of every source statement.
- **Revision and learning** use later evidence, disputes and actual outcomes to
  change affected understanding. A lesson is one possible result, never the
  required result.

The subjects are [the graph](graph.md), [understanding and
revision](understanding.md), and [profiles](profiles.md).

## Invariants

- Only Record and Rollup independently support a judgement. Notebook entries
  supply context and typed premises, never fresh evidence for themselves.
- The unit is the read boundary. An entity belongs to one unit; a person in two
  units has independent pointers, relations and profiles.
- An intention, attempted act, confirmed execution and later outcome remain
  different facts. External systems stay authoritative for their current state.
- The graph owns semantic understanding; the search index is disposable. Losing
  the index costs retrieval infrastructure, not source truth.
- Retrieval never crosses scope, and index text never revives a superseded or
  hidden source. Partial or unavailable retrieval is not an assertion of absence.
- A correction adds a revision, closure, dispute or retirement with provenance.
  It does not delete the history that made the earlier judgement inspectable.
- Authored identity, doctrine and policy remain human-owned. Derived memory
  cannot override them.

## Why this shape

Social meaning depends on connections: an acknowledgement can change an earlier
plan, and the same words can mean something different after a relationship or
outcome changes. A graph keeps those connections and their evidence visible.
One session keeps interpretation, association and decision in one context.
Bounded profiles make durable understanding usable without turning every
recollection into prompt text.
