# The graph

## Rows, edges and two axes

The Cognitive Graph is rows and the typed edges between them. An **entity** is
a neutral anchor. An **identity** is a registered subject, using the same model
for humans, bots and agents. Humanity is a claim about the subject.

Every row and edge carries two axes:

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
closed by) is computed; an edge Nora judges (about a topic) is inferred; an
edge from her own decision (rests on, serves) is committed.

Evidence, indexes and typed relations each have their own edge:

| edge | from → to | is evidence |
|---|---|---|
| **cites** | a row → the observed or computed rows it rests on | yes, the only evidence |
| **about** | a row → an entity it concerns. Admission computes links from claims and intentions to their resolved identity pointers. Nora proposes inferred links from claims, intentions and recorded items. | no: an index for retrieval |
| **draws on** | a claim or intention → another claim or intention | no: context or premise |
| **grades** | a grade claim → the guess or lesson it assesses | no: the grade cites its evidence separately |

**Support and dependency stay apart.** `cites` is evidential support: it gives
a claim its strength and must trace to observed or computed rows. `draws on` is
inferential dependency: claim B built on claim A's premise. It is recorded so
revision can follow the chain, and it adds no strength. When a row is revised,
retired or contradicted, every claim that draws on it and every intention that
rests on it is due for reconsideration.

The edge vocabulary is closed and reviewed: each type names its legal
endpoint kinds. A new meaning is a reviewed edge type, never a new store.

## Scope

- **unit** — the read boundary. A row is read only inside its unit. Each Nora
  has one **primary** unit, her home circle; other units are **affiliates**,
  helper or ad hoc circles, each still its own boundary. An identity's DM with
  Nora is a room of her primary unit.
- **origin room** — where the row was established, which governs where it may
  render and where an act about it lands.
- **identities** — the registered subjects it concerns, by ID.
- **excluded identities** — registered subjects it must not be revealed to or through.
- **lineage** — the source restrictions inherited from what it cites: what a
  row may be rendered as, where, and to whom. Exclusions and exposure always
  inherit. For DM-origin evidence the raw items stay in the DM, while a claim
  citing them may be shown to the model in any room of the primary unit, marked
  with its DM origin ([boundaries](boundaries.md)).
- **exposure** — **external** (may reach a room through the guards),
  **internal** (Nora reads it, it never egresses), or **operator** (Nora never
  reads it).

A boundary is drawn by scope, never by a filter applied inside it.

## Planes every row carries

| plane | holds |
|---|---|
| identity | the row's id and its kind |
| provenance | the writer, the occasion, the pass, the trace, the model, and the revisions in force: code, instruction and skill, configuration, identity and policy |
| time | when it occurred, when it was asserted, and the span it is valid for |
| revision | the revision it replaces; a stale base is refused, never overwritten |
| evidence | its edges to what it cites or rests on |
| lifecycle | its state in its kind's lifecycle |

Provenance is stamped by code, never written by the model. When replay or restore
re-evaluates recorded passes and graph rows, it uses the revisions recorded
with them or returns unavailable. Observed items are read as recorded.
A correction adds a
revision or a retirement; it never erases the history that made the earlier
row inspectable.

## Five kinds

| kind | basis | is | lifecycle |
|---|---|---|---|
| **entity** | computed when code resolves it; inferred when its identity is judged | an anchor: an identity, room, unit, Nora, a game, a place, a topic | admitted, merged, retired |
| **item** | observed | something that happened: a message, link, reaction, online-status change, fetched page, watch result, tool outcome, Nora's own send | append-only |
| **aggregate** | computed | a derivation over items: episodes, rollups, counts and baselines, verdicts, track records, support tallies | recomputed, never edited |
| **claim** | inferred | a belief about an entity or a relation | revised, retired, or ended by its valid-to time |
| **intention** | committed | what Nora has committed to | open, then closed by an outcome or dropped |

Registered [identities](../managed-identity-access/identity.md#one-identity-many-accounts),
rooms, units and Nora have code-resolved pointers to authored identity and policy;
no model proposes, merges or retires those canonical records. Each unit has its
own graph pointer to a canonical identity, preserving that unit's disclosure
boundary. A sender code cannot resolve stays unresolved on the item. Other
entities are inferred and exist only as endpoints of admitted, evidenced edges;
a graph entity or inferred same-as claim never establishes account ownership.

### Intentions

An intention names its **owner** (Nora, or the identity whose request she holds)
and who it is **owed to**, and has one of two **forms**:

- **achieve** — closes when its **done-when** condition is met by the recorded
  witness its form requires (the conditions table below);
- **maintain** — holds while its **holds-while** condition stays true, is
  reviewed at its **review time**, and advances through child achieve
  intentions that serve it.

Either form carries:

- **drop-when** — the condition under which it is abandoned;
- **rests-on** — the claims, lessons and aggregates it depends on; when one of
  them is revised, the intention is due for reconsideration;
- **serves** — the parent intention a child advances;
- **watches** — for a maintain intention, the outside sources code polls on its
  behalf for as long as it holds. A watch targets an entity that carries a
  **source reference** (its page address, account or saved place) of a kind an
  intake adapter can poll; admission refuses a watch on any other entity;
- **delivery** — *choose a moment*, re-judged at delivery, or *when it comes
  up*, raised only in a natural opening. A chosen moment has a **trigger**: a
  time, or a watched condition holding, such as an event starting. Moving a
  trigger is a revision that cites the evidence that moved it;
- **closed-by** — the item or aggregate that ended it, and its outcome.

**Outcomes.** An achieve intention ends with a verdict: **achieved** (its done-when was
met), **dropped** (its drop-when was met), **leaked** (its time passed with
neither, while every source that could have closed it was healthy) or
**unknown** (its time passed and a source it depended on was not healthy).
An outage is never a leak. The deadline ends an achieve intention’s window.

A maintain intention ends **ceased** (its holds-while was observed false) or
**dropped** (its drop-when was met); its watches stop when it ends. While its
condition cannot be evaluated because a source is unhealthy, it stays open and
its review records unknown. It has no verdict of its own: its track record is
its children's verdicts.

**Conditions** are one of a closed set of forms, each evaluated by code to
true, false or unknown. The form decides the witness and who writes the ending:

| what it waits for | form | closing witness | written by |
|---|---|---|---|
| Nora's own work done | event match on her act | the act's outcome item reporting done | observe's compute |
| an identity's response, such as accepting a suggestion | event match on their item | the identity's explicit item: the acceptance, the pick, the confirmation | observe's compute |
| a state reached | predicate over an aggregate or current head, such as a poll having a winner | the aggregate revision that meets it | observe's compute |
| time passing | deadline | the clock instant, with the health of the sources it depended on | study's verify; no closed-by edge |
| nothing code can evaluate | untyped | none | study's verify, at its deadline |

**A deadline never achieves.** Time passing is not the promised thing done: a
deadline may be a drop-when or the end of an intention's window, never its
done-when. When a **drop-when** deadline passes, the intention is dropped. When
the **window** of a typed achieve intention ends with neither its done-when nor
its drop-when met, it is leaked — or unknown, if a source it depended on was
unhealthy. Untyped and maintain intentions keep their own endings below.

A condition in none of these forms is **untyped** and cannot close
mechanically. An untyped achieve intention must carry a deadline; when it
passes, the intention ends unknown, never leaked. An untyped maintain intention
must carry a deadline in its drop-when; each review keeps or drops it
explicitly, and at the deadline it ends dropped. Untyped conditions are counted
as loose.

An intention has at most one scheduled delivery at a time, and every delivery
passes the unit-wide arbitration in [the loops](loop.md). An intention grants
no delivery authority: whatever it leads to still passes the fire path, the
disclosure rules and the cap on unsolicited proposals.

## Presentations

A presentation is a read over rows, assembled for an occasion. Nothing writes a
presentation.

- **profile** — the fact, trait and guess [claims](claims.md) about one entity.
  A room's or unit's profile is its culture; Nora's is how she has
  come across.
- **Standing** — Nora's current position in a room: what she holds open, owes
  and has on offer, what was recently settled and how, and where the
  conversation stands. It reads intentions and situation claims, showing
  each situation claim's age and how many items arrived since it was
  asserted, so a stale claim reads as stale. Nothing writes it.
- **lessons** — lesson claims about Nora's own practice in a unit.
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
| commitment | achieve intention owed to an identity |
| decision | intention whose deliberation recorded the options; "reopen if" is a rests-on condition |
| reminder | the scheduled delivery of an intention |
| event, a gathering | an item once it happens; before it, a claim about the plan, and an intention for Nora's own part |
| topic | entity, with the claims about it |
| live thread | its state is a claim with a valid-to time; its question is an achieve intention |
| Character, culture | profile presentation |
| lesson | claim about Nora's practice |
| Standing, Register | presentations over intentions and claims |
| assess report, tension | aggregate or claim with operator exposure |
