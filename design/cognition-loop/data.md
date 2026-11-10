# Data layers and the Notebook

## Layers by authority

**Authored** is what people deliberately give Nora: doctrine, objectives,
skills, identity, policy and reference material. It configures every other
layer and is not a claim she derived about the world.

**Record** is what was observed: raw events and their revisions, source health,
perceptions, Nora's acts and their outcomes. Source-tied readings of raw input
also live here, because they record what was said rather than asserting it as
true. Record is append-only history and is the root of every evidence chain.

**Rollup** is what code computes from Record: episodes and aggregates over a
logical watermark. It is rebuildable and never a model summary.

**Notebook** is Nora's cited judgement, divided into Register, Character,
Lessons and Standing. These are the only tiers; Record and Rollup are layers,
not tiers. An entry is one Notebook row, with a home, entity, kind, body,
citations, reasoning, visibility, lifecycle, revisions and provenance.

**Views** are slices assembled for a reader at read time: a turn's context, a
Cognition situation, a tool result or a portal page. A View may be captured as
provenance but is not a store to consult as fresh input.

Third-party memory can feed and inform Views. It is not another source of
authority inside Nora's evidence flow and cannot be cited as Record.

## One direction of flow

```text
Authored -> configures every layer
Record -> Rollup -> Notebook -> Views
   ^                                  |
   +---- Nora's and people's outcomes-+
```

Nothing writes backwards. What Nora says and does becomes new Record rather
than immediate proof of a Notebook judgement. Code owns Record and Rollup;
Cognition owns the Notebook's judged writes. Assessment and operator reports
sit outside the Notebook and never silently rewrite it.

Every row belongs to a person, room, unit or workspace. A person appearing in
two units has independent Notebook state in each. Shared person-scoped
observations do not merge those judgements.

## Notebook vocabulary

Register answers what is happening in the unit. A **note** is open and has a
closing condition; a **memo** is settled and retained; an **event** is dated
and attended. Notes may track events and close into memos, while events may
leave settled consequences behind. Reminders are execution machinery, not a
fourth Register kind.

Character answers what an entity is like. Its entries distinguish established
profile, supported traits and hypotheses; an account means a platform login,
not a Character entry. Culture is the Character of a room or unit. Nora's
Character describes how she has come across and never overrides doctrine.

Lessons answer what Nora has learned about acting in a unit. A lesson is a
bounded, reversible prescription supported by outcomes, not a new law and not
an authored runtime skill.

Standing answers where Nora stands toward a room, unit or person. It holds her
current position and working set, not a platform status or queue position.

## Citations, links and search

A citation points from an entry, field or section to Record or Rollup evidence.
It carries the provenance needed to re-derive the claim and to decide where it
may render. A typed link points between Notebook entries and supplies context,
not evidence. Links do not let a higher-level judgement become the root of its
own support.

Storage remains relational. Entity, citation and link references support graph
queries without requiring a graph database or letting model-generated triples
become a parallel source of truth.

Views apply scope, disclosure and freshness at assembly. Prompt blocks carry
small indices and enough time and identity information for a model to interpret
them; bodies and wider history are pulled when needed. An unknown or unavailable
slice says so rather than rendering as an empty world.

## Time and correction

System time records when Nora held a row; valid time records when the thing it
describes held in the world. The distinction is intrinsic to Rollup episodes
and Register affairs, while Character, Lessons and Standing primarily carry
support and age.

Current heads may be efficient projections or mutable pointers, but revision
and transition history preserves earlier states. A restore reads history as of
its requested moment together with the authored and configured inputs then in
force. External mutable memory is not presumed reconstructable unless it has
its own equivalent history.

Correction is a new revision, never a rewrite of the past. People can dispute
what is held about them, and unsupported or stale lines lose authority rather
than being silently reinforced. Removal from future reads is soft wherever
possible; it does not falsify an earlier snapshot by destroying its history.

## Identity and growth

Nora's identity is the state about her: authored law, observed acts, digested
Character, learned Lessons and held Standing. Derived blocks render below the
authored doctrine. Evidence pulling against that law becomes an operator-only
tension report, never an agent-authored doctrine change.

Growth must remain inspectable. Her Character and Lessons need responses to
her acts, not merely her own previous prose. Every learned line has evidence,
history and a way to retire it.
