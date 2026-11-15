# The loop

## Terms

- **Cognition Loop** — the one process over the graph: five stages, OODAS.
- **Stage** — a part of the loop that answers one question and writes one basis.
- **Action** — one step of a stage, with exactly one actor: **code** or the
  **model**. The model proposes; code admits, computes and delivers.
- **Pass** — one run of stages, started by one trigger.
- **Executor** — the runtime that runs a pass or an action.
- **CMS** — comprehend, measure, serialize: the shape of every action, every
  stage and the loop itself. An action comprehends its input once, measures its
  result fail-closed and serializes one admitted write with provenance.

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
| act | **compose** | model | drafts the act from the situation at that moment |
| | **guard** | code | runs disclosure and send guards against the current room |
| | **perform** | code | delivers what the model's `send_reply` call carries, or records silence |
| study | **verify** | code | writes each intention's verdict from closed-by and the clock |
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

## Passes

| pass | trigger | purpose | stages | executor | delivers |
|---|---|---|---|---|---|
| **called** | a tag, mention or reply to Nora | answer when asked | orient, decide, act | conscious turn | yes; a reply is owed |
| **ambient** | a room's conversation admitted as an offer, without a call | be present: join when welcome, otherwise listen | orient, decide, act | conscious turn | yes; never owed, silence by default |
| **proactive** | an intention due for delivery | follow through on what Nora committed to | orient, decide, act, re-judged at delivery | fire path | yes; may stay silent |
| **subconscious** | stimuli the wake evaluator claims | understand, keep concerns, commit intentions, learn | orient, decide, study's learn | Cognition session | never |
| **review** | a schedule | verify, grade and report | study's verify, grade, report | verifier, judge | never |

**Who starts it** separates the conversational passes: in a called pass
someone addressed Nora; in an ambient pass the room is talking and she was not
addressed; in a proactive pass Nora starts, from her own intention. Only the
conscious passes act. The subconscious pass reaches a room only by committing
an intention whose delivery a proactive pass carries out.

Admission is code: a call is admitted deterministically; an ambient offer is
admitted by room policy and may never suppress a call; a proactive pass is
admitted when its intention is due; a wake is admitted by the wake evaluator
with no model call. A failed admission produces no pass and no fallback.

## The open loop

The loop takes in any stimulus, keeps concerns that never finish, reaches
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
