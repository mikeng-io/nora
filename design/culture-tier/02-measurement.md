# Measurement

## 1. Today the verdict is the measurement, which is the failure

| path | signal | binds to | durable | ground truth |
|---|---|---|---|---|
| participation | model says `go`/`no_go` | a model's opinion of a taxonomy | no — counters only | 40 constructed cases |
| `proactive_decide` | model says `remind`/`decline` | same | yes, `decision_log` | none |

A model agreeing with an instruction someone wrote is not evidence about the
room. And a `no_go` is unfalsifiable by construction: she stays silent,
nothing happens, and silence is also what success looks like. That is why
0.67% admission over four days went unremarked until someone went to
Prometheus — **a refusal is indistinguishable from health in every log the
system keeps.**

The operator's word for this was 虛偽: there is a thing called measurement,
and nothing it actually decides.

## 2. Four measures the rooms produce for free

None of them defines when she should intervene. None needs a model.

| measure | rule | labelled by |
|---|---|---|
| **expiry without action** | a note passes `anchor_date` while open and she never raised it | her own store |
| **HOLDING refusal** | gate refused room R at *t* while she demonstrably held something for R | her own store |
| **override** | a human tags her in R within ~10 min of a `no_go` | the room |
| **ignored** | she speaks unprompted; the next human line does not engage | the room |

The founding anecdote is itself an instance: a `no_go`, then 34 seconds later
the member gives up and tags, then a correct answer. A human overruled the
gate and the transcript recorded it.

Two pairs, failing differently on purpose — one from her own state, one from
human behaviour — so neither is self-attesting.

### `HOLDING`'s second arm does not compute, and this is not a detail

The intended definition included *"or the room's `standing` records an unmet
commitment"*. It cannot: `standing` is `body TEXT` plus a length CHECK, with
no field, flag or structure for a commitment. Computing it needs a regex over
free Cantonese or a model — and a model re-imports the exact proxy failure §1
rejects.

Worse, `relevance_gate.py:266-272` makes `standing` *"the ONLY place that can
establish what she owes this room"*, so the standing arm **is** the interesting
half of HOLDING, and it is the half that does not work. Filed as #222; not
fixed here. Until it is, HOLDING means the note arm only, and should say so
wherever it is reported.

## 3. What blocks all four today

`nora_relevance_gate_total` carries `platform` only, **not
`conversation_id`** — so a refusal cannot be joined to a room and neither
HOLDING refusal nor override is computable, though both sides of the join
exist. That is #221 and it ships first.

One thing the label does not fix: override needs the *time of each individual
refusal*, and a Prometheus counter gives scrape resolution, loses everything
on restart (§`00-overview 1.1`), and expires at retention. A per-verdict row
is what override actually needs, and DEC-179 excluded the gate from
`decision_log` deliberately. **This bundle does not reopen that**; it records
that override stays approximate until someone does.

## 4. Ceilings, stated up front

- **Override only fires when someone cares enough to tag.** Most refusals are
  never labelled, so it is a lower bound and can never be the true rate. It is
  blind to the failure that matters most: the room silently giving up.
- **HOLDING cannot see category 1.** Lookupable world facts — weather, opening
  hours, a route — are not held by her. The one measured `go` in four days was
  exactly this kind, so the blind spot is not hypothetical.
- **Nothing measures harm.** All four score refusal and uptake; none asks
  whether what she said was wrong, unwelcome, or duplicated. #216 is a live
  example the four would score backwards, and the raw material for a harm
  measure exists unused (`nora_guard_vetoes_total`, `nora_replies_total{status}`,
  `messages.is_nora`).
- **Correct silence is unmeasurable and has never happened.**
  `nora_participation_silence_total` has no series at all: she has never been
  admitted and then chosen not to speak. `fire.py:164` calls that the healthy
  default, and it is the one state none of the four can score.
- **Every threshold here is a placeholder** — the 10-minute window, "near
  *t*". None is measured, because none can be until #221 lands.

## 5. Sequence

1. `conversation_id` on the gate metric (#221). Unblocks the rest.
2. The four measures as derivations. No behaviour change.
3. **Let it run.** Get a baseline of what the gate actually does, on a store
   that survives a restart.
4. The culture adaptor, consumed by nobody. Writing only.
5. The participation turn reads culture. Measured.
6. **Only then** the gate, if at all — against #144/#147, which measured the
   gate getting worse with more context, and #194, which says a gutted gate
   instruction currently passes everything.

Steps 1–4 change no behaviour. Step 5 onward is where the evidence has to
carry the argument.

**A note on the eval labels.** If plan-recap is ever to be admitted, the
labels change — `real/plans-tonight` and the other eight `members_only` cases,
all `no_go`. That is re-specification, not making a test pass, and it must be
its own visible commit *before* any prompt edit, or the suite silently becomes
a mirror of the new prompt. Widening also costs discriminating power: a gate
saying `go` to everything except the six safety cases already scores 16
errors out of 40, so loosening moves the instrument toward that baseline
unless new `no_go` cases land beside it. The six `forge/*` safety cases are
untouched by anything here — none is `members_only`.

## 6. What the suite has never had

The 40 labelled cases are 1–4 clean lines with the askable line last.
Production windows are up to 40 rows with the askable line buried and chatter
after it — which trips `participation.md:105-107` ("an unanswered question
with unrelated talk after it is a question the room dropped"). That bullet
fires on almost every real window and almost no eval case, and it is the
leading hypothesis for the floor.

Live-shaped cases are needed before any doctrine change is judged: 30-row
windows, askable line mid-window, real chatter following. Until they exist a
green suite says nothing about production, which the 18/40-versus-1/149 gap
already demonstrates.
