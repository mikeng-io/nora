# The loop

## CMS at every grain

**Comprehend, measure, serialize** is the primitive. It is not a stage and not
a loop; it is the shape of every step at every grain:

- **a step** comprehends its input once, measures its result fail-closed and
  serializes one admitted write with provenance;
- **a stage** is the same three one level up: its input is comprehended, its
  check is the measure, its hand-off is the serialization;
- **the loop** is the same again: observe and orient comprehend, study
  measures, and every stage's writes are the serialized state the next pass
  comprehends.

## OODAS

The Cognition Loop has five stages.

| stage | answers | writes |
|---|---|---|
| **observe** | what happened? | items (observed); aggregates and closing edges (computed) |
| **orient** | what does it mean? | entities and claims (inferred) |
| **decide** | what will Nora do? | intentions (committed) |
| **act** | carry it out | nothing directly; the act re-enters through observe |
| **study** | how did what she committed to turn out, and what follows? | verdicts (computed); grades and lessons (inferred) |

**Study** compares the outcomes of what Nora committed to with what happened,
judges the gap and learns from it. It reads only outcomes of her own
commitments, never new input; making sense of new input is orient's.

## The stage template

Every stage is stated on one template:

| field | states |
|---|---|
| trigger | what starts it |
| input | what it comprehends, assembled by context assembly |
| steps | what it runs, named once; a **cycle** only where the steps repeat |
| writes | which kinds, in its own basis only |
| check | what code measures before admission, failing closed |
| hand-off | what the next stage or pass receives |
| executor | what runs it, and on what timescale |

## The steps

### Observe — code only

1. **receive** — a platform message, a watch result, a tool outcome, Nora's
   own act, a clock edge.
2. **resolve** — accounts to persons, rooms to units, links and references to
   entities code can resolve.
3. **record** — the item, observed, with its scope and provenance.
4. **compute** — aggregates over what changed, including baselines, and
   matching new items against open intentions' conditions: a match writes the
   closed-by edge, computed.
5. **stimulate** — make the change durable as a stimulus for the wake
   evaluator.

### Orient — a cycle

1. **prefetch** — code walks the graph from the agenda's anchors and pushes
   that neighbourhood, including the held claims and open intentions it
   touches.
2. **discover** — the session reads further: transcripts, items, aggregates,
   claims, media perception, permitted outside reads.
3. **link** — it proposes entities, claims and edges the evidence supports;
   code admits them and returns what was admitted with its neighbours.
4. **rediscover** — discovery continues from the new neighbours.

The cycle stops when discovery adds nothing new to connect, or a bound stops
it. Code makes saturation visible rather than imposing a quota.

### Decide

Its input is the open intentions, the ones due, the ones whose rests-on rows
were revised, and the track records of comparable intentions.

1. **deliberate** — the session weighs the situation against what it holds.
2. **commit** — it proposes intentions with their form, conditions, rests-on,
   watches and delivery; code admits them with a receipt. An untyped condition
   is admitted and counted loose.
3. **schedule** — code places the review times, due times and watches.

### Act

1. **compose** — the conscious turn drafts what would be said or done, at the
   moment of delivery.
2. **guard** — the send and disclosure guards run against the current room and
   roster; any unknown fails closed.
3. **perform** — only a `send_reply` call delivers; silence is a deliberate
   outcome.

Act writes nothing itself. What was performed, and what people did next,
arrive as items through observe.

### Study — a cycle

1. **verify** — code writes each intention's verdict from its closed-by edge
   and the clock: achieved, dropped, or leaked (its time passed with no
   closing item).
2. **grade** — a judge other than the writer grades claims and lessons against
   their later evidence.
3. **learn** — the session induces lessons from several verdicts, or from one
   person's correction ([learning](learning.md)).
4. **report** — the operator-exposure counts and tensions.

It repeats per session, nightly and across days. Verify is code and grade is a
separate judge: the writer never grades its own work.

## Passes and executors

One run of the loop is a **pass**: a **wake** (subconscious, a Cognition
session) or a **turn** (conscious: a tagged or offered turn, or a fire turn).

| executor | runs |
|---|---|
| ingest | observe for platform items |
| watch poller | observe's receive for outside sources an intention watches |
| wake evaluator | claims stimuli and admits a wake, with no model call |
| Cognition session | orient, decide and study's learn, in one model context per unit per wake |
| conscious turn | orient, decide and act for one room, under its own guards |
| fire path | act for a due intention, re-judged at delivery |
| verifier | study's verify |
| judge | study's grade, on a model independent of the writer |

One session holds orient and decide together: splitting them across agents
discards the context that makes them coherent. A conscious turn may commit an
intention too: "keep an eye on deals for us" is the same maintain intention
whether it is committed in a turn or a wake.

## The open loop

The loop is open: it takes in any stimulus, keeps concerns that do not finish,
reaches outward and takes its own results back in.

**Stimuli** — what can start a pass:

- a new item from a platform;
- a watched source changing;
- an aggregate crossing its baseline, such as a room's interest in something
  rising well above normal;
- the clock: an intention due, a review time, a valid-to time ending, a
  deadline passing;
- a verdict;
- a person's correction.

Code surfaces each stimulus; the model decides whether it means anything.
Nothing calls a model merely because a clock advanced.

**Watches** — a maintain intention can name outside sources to watch. The
model chooses what is watched as part of the commitment; code polls it; what
returns is an item through observe, citing the watch. A watch ends with its
intention.

**Bounds** — rounds, tokens and wall time bound a session; paid outside reads
keep their own spend controls; the cap on unsolicited proposals and the fire
path's re-judgement bound what reaches a room. If maintain intentions
accumulate faster than people respond to them, a per-unit limit on held
concerns is the next bound, set from the measured open count.
