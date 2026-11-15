# The loops

## Terms

- **OODAS** — the stage model: observe, orient, decide, act, study. Both loops
  run it.
- **Engagement loop** — Nora in a room, in the foreground: it reads
  understanding, composes, delivers or stays silent, and closes each pass with
  a debrief. The only loop that delivers.
- **Reflection loop** — Nora between conversations, in the background: it forms
  and revises understanding, learns and keeps concerns. It never delivers.
- **Stage** — a part of OODAS that answers one question and writes only its
  declared basis set (observe: observed and computed; act: none).
- **Action** — one step of a stage, with one owner per responsibility. An
  action marked *model → code* is a proposal and its admission: the model owns
  the proposal, code owns admission. Code alone computes and delivers.
- **Pass** — one run of stages, started by one trigger.
- **Executor** — the runtime that runs a pass or an action.
- **CMS** — comprehend, measure, serialize: the shape of every action, every
  stage and each loop itself. An action comprehends its input once, measures its
  result fail-closed and serializes one result with provenance: an admitted
  write, a read result or an outcome.

## OODAS

| stage | question | writes |
|---|---|---|
| **observe** | what happened? | item (observed); aggregate, closed-by edge (computed) |
| **orient** | what does it mean? | entity, claim, edge (inferred) |
| **decide** | what will Nora do? | intention (committed) |
| **act** | carry it out | nothing; its outcome enters through observe |
| **study** | how did her commitments turn out, and what follows? | verdict (computed); grade, lesson (inferred) |

Observe runs continuously, for every input, executed by ingest and the watch
poller. Every other stage runs inside a pass.

## Actions

| stage | action | actor | does |
|---|---|---|---|
| observe | **receive** | code | accepts an input: a platform event, a watch result, a tool outcome, Nora's own act, a clock edge |
| | **resolve** | code | binds accounts to persons, rooms to units, references to entities it can resolve |
| | **record** | code | writes the item |
| | **compute** | code | recomputes affected aggregates and baselines; matches the item against open intentions' conditions and writes closed-by |
| | **stimulate** | code | makes the change a durable stimulus |
| orient | **prefetch** | code | walks the graph from the pass's anchors and pushes that neighbourhood |
| | **discover** | model | reads further through its read tools |
| | **link** | model → code | proposes entities, claims and edges; code admits them |
| | **rediscover** | code → model | returns each admitted row's neighbours; discovery continues from them |
| decide | **deliberate** | model | weighs the situation against open intentions, due ones, ones whose rests-on changed, and track records |
| | **commit** | model → code | proposes intentions; code admits them with a receipt |
| | **schedule** | code | places due times, review times and watches |
| | **debrief** | model → code | engagement loop only: after act, a second, tools-less model reviews the pass's recorded execution and proposes the working state it left — intentions opened, revised, closed or dropped; code admits them |
| act | **compose** | model | drafts the act from the situation at that moment |
| | **guard** | code | runs disclosure and send guards against the current room |
| | **perform** | code | delivers what the model's `send_reply` call carries, or records silence |
| study | **verify** | code | writes each achieve intention's verdict, and each maintain intention's end, from closed-by, the clock and source health |
| | **grade** | judge | an independent model grades claims and lessons against later evidence |
| | **learn** | model → code | proposes lessons from verdicts or a correction; code admits them |
| | **report** | code | writes operator-exposure counts and tensions |

Orient repeats prefetch → discover → link → rediscover until nothing new
connects or a bound stops it. Study repeats on its timescales.

## Stage rules

| stage | must | fails as |
|---|---|---|
| observe | never interpret meaning; record everything admitted | an unrecorded input stays pending |
| orient | cite only observed or computed rows; search before creating | an unavailable read is unknown, never absence |
| decide | give every intention a condition (an untyped one is counted loose) and its rests-on | a refused write returns a typed reason to repair |
| act | deliver only through `send_reply` and the guards; treat silence as an outcome | any unknown in the guard means no delivery |
| study | verify by code; grade by a judge that is not the writer | missing evidence is an error, never a pass |

## Delivery arbitration

- When a delivery is scheduled, code resolves the act's subject and action and
  checks it atomically against the unit's pending, firing and delivered acts,
  whatever their destination or citations. A duplicate is refused; an
  ambiguous identity stays unresolved, never a guessed unique act. A
  delivery to several rooms is explicit and admitted all-or-none.
- At delivery, code rechecks the same resolved act against what was delivered
  or superseded and the destination's current eligibility. An incomplete check
  withholds.
- Scheduled is not sent and attempted is not completed. An outside send whose
  effect is uncertain is never blindly retried.

## Two loops over one graph

| | engagement loop | reflection loop |
|---|---|---|
| observe | shared: ingest records every item, the pass's own tool calls and sends included | shared |
| orient | reads only: prefetch and pull over understanding | forms and revises understanding: entities, claims |
| decide | commit for what the reply promises; debrief for everything the pass left | commits and revises intentions: reconsider, close stale, schedule, watch |
| act | the only loop that delivers | none |
| study | none | learn; the review pass verifies, grades and reports |
| writes | committed rows | inferred rows; committed rows through its own decide |
| executor | conscious turn, fire path | Dreamer runtime: reflection session, verifier, judge |

## Passes

| pass | loop | trigger | purpose | delivers |
|---|---|---|---|---|
| **called** | engagement | a tag, mention or reply to Nora | answer when asked | yes; a reply is owed |
| **ambient** | engagement | a room's conversation admitted as an offer, without a call | be present: join when welcome, otherwise listen | yes; never owed, silence by default |
| **proactive** | engagement | an intention due for delivery | follow through on what Nora committed to, re-judged at delivery | yes; may stay silent |
| **wake** | reflection | stimuli the wake evaluator claims | understand, keep concerns, commit intentions, learn | never |
| **review** | reflection | a schedule | verify, grade and report | never |

**Who starts it** separates the engagement passes: in a called pass someone
addressed Nora; in an ambient pass the room is talking and she was not
addressed; in a proactive pass Nora starts, from her own intention.

- The engagement loop never writes understanding; the reflection loop writes it
  from the engagement loop's recorded passes.
- The reflection loop reaches a room only by committing an intention whose
  delivery a proactive pass carries out.

## The debrief

Nora is sessionless: every engagement pass is a fresh agent, and nothing in
her own run follows the reply. The debrief is the one exception that carries a
pass's working state to the next: it serializes that state into the graph
before the room's slot is released.

- **Runs** on every finished engagement pass — reply, silence, veto or timeout —
  inside the slot, as a declared occasion of its own.
- **Reads** the pass's items (tool calls and results, sends, the recorded
  outcome), the room's messages since the last debrief, and the open
  intentions for the room and unit, including what the pass itself committed.
- **Writes** only intentions and their edges, as `commit` operations through
  `commit`'s admission: every citation is an item of this pass; a close needs a
  successful outcome item; a stale base is refused; deliveries pass the
  delivery arbitration.
- **Never writes** claims, entities, items or prose: understanding is the
  reflection loop's.
- **Fails** bounded and without raising: on any failure the prior rows stand
  and the miss is counted unavailable.

The pass commits only what its reply promises, so the reply never claims what
was not written; the debrief commits everything else the pass left and does
not repeat what the pass committed.

Admission is code: a call is admitted deterministically; an ambient offer is
admitted by room policy and may never suppress a call; a proactive pass is
admitted when its intention is due; a wake is admitted by the wake evaluator
with no model call. A failed admission produces no pass and no fallback.

## Open loops

Together the loops take in any stimulus, keeps concerns that never finish, reaches
outward and takes its results back in.

**Stimuli**: a platform item; a watched source changing; an aggregate crossing
its baseline; the clock (an intention due, a review time, a valid-to time
ending, a deadline passing); a verdict; a person's correction. Code surfaces a
stimulus; the model decides whether it means anything. Nothing calls a model
because a clock advanced.

**Watches**: a maintain intention names outside sources; code polls them while
it holds; results enter through observe.

**Bounds**: rounds, tokens and wall time per pass; spend controls on paid
reads; the cap on unsolicited proposals; delivery re-judged at the moment it
happens. A per-unit limit on held concerns follows only from a measured need.
