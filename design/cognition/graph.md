# The graph

## Rows, edges and two axes

The Cognitive Graph is rows and the typed edges between them. Every row and
every edge carries two axes and nothing else classifies it:

- **basis** — how the row is proven, which decides who may write it and how it
  is checked;
- **scope** — who the row concerns and where it may be read or go.

What the row says is its kind and content. Whether it is a belief or a
commitment, curated or raw, operator-facing or room-facing, is answered by
basis or scope, never by a third axis.

## Basis

| basis | means | proven by | written by |
|---|---|---|---|
| **authored** | a human wrote it | the file is the truth | the operator; it lives outside the graph as doctrine and configuration |
| **observed** | it happened | the recorded row is the proof | code, at intake |
| **computed** | code derived it from observed or computed rows | re-deriving it gives the same result | code |
| **inferred** | Nora judges it to be so | never proven; supported or contradicted by what it cites | the model proposes, code admits |
| **committed** | Nora decided it | its own admitted write is the proof that she decided; its outcome is proven by an observed row | the model proposes, code admits |

An **inferred** row carries how it was reached and how strongly it is held:

- **told** (someone said it), **deduced** (it follows necessarily),
  **induced** (it generalises several instances) or **abduced** (it is the best
  explanation);
- confidence **low**, **medium** or **high**.

An abduced value leaves Nora as a question, never as a settled fact. A
frequency or routine rests on a computed aggregate, not on one memorable item.
A perception reading — what an image, voice note or video shows — is inferred.
Nora's own words are observed as her act; they ground only that she said them.

**Citation rules.** An inferred row cites only observed or computed rows; a
claim may *guide* a search for another, but it never supports it. A computed
row derives only from observed or computed rows, and from the conditions and
rests-on edges of the intentions it evaluates; never from a claim's content.
A committed row may rest on rows of any basis, because resting is what makes
its reasons reconsiderable.

**Edges take a basis by the same rule.** An edge recorded with an item
(authored by, replied to) is observed; an edge code derives (co-located with,
closed by) is computed; an edge Nora judges (likes, knows) is inferred; an
edge from her own decision (rests on, serves) is committed.

Three edge families never stand in for each other:

| edge | from → to | is evidence |
|---|---|---|
| **cites** | a row → the observed or computed rows it rests on | yes, the only evidence |
| **about** | a row → the entities it concerns | no: an index for retrieval |
| **draws on** | a claim or intention → another claim or intention | no: context or premise |

The edge vocabulary is closed and reviewed: each type names its legal
endpoint kinds. A new meaning is a reviewed edge type, never a new store.

## Scope

- **unit** — the read boundary. A row is read only inside its unit.
- **origin room** — where the row was established, which governs where it may
  render and where an act about it lands.
- **persons** — the people it concerns, by id.
- **excluded persons** — people it must not be revealed to or through.
- **lineage** — the source restrictions inherited from what it cites.
- **exposure** — **external** (may reach a room through the guards),
  **internal** (Nora reads it, it never egresses), or **operator** (Nora never
  reads it).

A boundary is drawn by scope, never by a filter applied inside it.

## Planes every row carries

| plane | holds |
|---|---|
| identity | the row's id and its kind |
| provenance | the writer, the occasion, the session or turn, the trace, the model and instruction revision |
| time | when it occurred, when it was asserted, and the span it is valid for |
| revision | the revision it replaces; a stale base is refused, never overwritten |
| evidence | its edges to what it cites or rests on |
| lifecycle | its state in its kind's lifecycle |

Provenance is stamped by code, never written by the model. A correction adds a
revision or a retirement; it never erases the history that made the earlier
row inspectable.

## Five kinds

| kind | basis | is | lifecycle |
|---|---|---|---|
| **entity** | computed when code resolves it; inferred when its identity is judged | an anchor: a person, room, unit, Nora, a game, a place, a topic | admitted, merged, retired |

People, rooms, units and Nora are pointers to authored identity: code resolves
them from identity and policy, and no model proposes, merges or retires them.
A person code cannot resolve stays unresolved on the item, never a new person
entity. Other entities are inferred and exist only as the endpoint of an
admitted, evidenced edge.
| **item** | observed | something that happened: a message, link, reaction, presence change, fetched page, watch result, tool outcome, Nora's own send | append-only |
| **aggregate** | computed | a derivation over items: episodes, rollups, counts and baselines, verdicts, track records, support tallies | recomputed, never edited |
| **claim** | inferred | a belief about an entity or a relation | revised, retired, or ended by its valid-to time |
| **intention** | committed | what Nora has committed to | open, then closed by an outcome or dropped |

### Intentions

An intention names its **owner** (Nora, or the person whose request she holds)
and who it is **owed to**, and has one of two **forms**:

- **achieve** — closes when its **done-when** condition is met by a recorded
  item;
- **maintain** — holds while its **holds-while** condition stays true, is
  reviewed at its **review time**, and advances through child achieve
  intentions that serve it.

Either form carries:

- **drop-when** — the condition under which it is abandoned;
- **rests-on** — the claims, lessons and aggregates it depends on; when one of
  them is revised, the intention is due for reconsideration;
- **serves** — the parent intention a child advances;
- **watches** — for a maintain intention, the outside sources code polls on its
  behalf for as long as it holds;
- **delivery** — *choose a moment* (a scheduled act, re-judged at delivery) or
  *when it comes up* (raised only in a natural opening);
- **closed-by** — the item or aggregate that ended it, and its outcome.

**Outcomes.** An achieve intention ends with a verdict: **achieved** (its done-when was
met), **dropped** (its drop-when was met), **leaked** (its time passed with
neither, while every source that could have closed it was healthy) or
**unknown** (its time passed and a source it depended on was not healthy).
An outage is never a leak.

A maintain intention ends **ceased** (its holds-while was observed false) or
**dropped** (its drop-when was met); its watches stop when it ends. While its
condition cannot be evaluated because a source is unhealthy, it stays open and
its review records unknown. It has no verdict of its own: its track record is
its children's verdicts.

A condition that code cannot evaluate is untyped: the intention may still be
held, but it cannot close mechanically, and it is counted as loose.

An intention has at most one scheduled delivery at a time, and every delivery
passes the unit-wide arbitration in [the loop](loop.md). An intention grants
no delivery authority: whatever it leads to still passes the fire path, the
disclosure rules and the cap on unsolicited proposals.

## Presentations

A presentation is a read over rows, assembled for an occasion. Nothing writes a
presentation.

- **profile** — the claims about one entity, in the parts [claims](claims.md)
  defines. A room's or unit's profile is its culture; Nora's is how she has
  come across.
- **Standing** — Nora's open intentions toward a room, unit or person, with
  their state, age and what they rest on.
- **lessons** — the claims about Nora's own practice in a unit.
- **likes** — the claims about what people enjoy, for a fixed destination
  audience.

## The existing names, in these kinds

| name | is |
|---|---|
| Record | items |
| Rollup, episode | aggregates |
| memory-graph entity and relation | entity; claim, or an observed or computed edge |
| note (an open matter) | achieve intention |
| memo (a settled matter) | a closed intention, kept with its verdict |
| commitment | achieve intention owed to a person |
| decision | intention whose deliberation recorded the options; "reopen if" is a rests-on condition |
| reminder | the scheduled delivery of an intention |
| event, a gathering | an item once it happens; before it, a claim about the plan, and an intention for Nora's own part |
| topic | entity, with the claims about it |
| live thread | its state is a claim with a valid-to time; its question is an achieve intention |
| Character, culture | profile presentation |
| lesson | claim about Nora's practice |
| Standing, Register | presentations of intentions |
| assess report, tension | aggregate or claim with operator exposure |
