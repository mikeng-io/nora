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

### 2.1 Built and first run, 2026-09-16

`nora.domain.participation_measures` decides, `nora.app.participation_measures`
reads, `nora measure participation` prints, `scripts/gate_refusals.py` supplies
the weak half. Read-only throughout, no model anywhere, no behaviour changed.

First run against the live database, 7 days:

| measure | | |
|---|---|---|
| `expiry_without_action` | **3 / 7 = 42.9%** | three anchored notes passed their day while open and unraised |
| `holding_refusals` | 0 / 0 | no refusals to join — see below |
| `overrides` | 0 / 0 | same |
| `ignored` | 0 / 0 | no input at all — see below |

**The first number this line of work has produced that is not the gate's
opinion of itself.** It is also the one that needed no new instrument: the rows
were already there.

**Two zeros that are not readings.**

`holding_refusals` and `overrides` have no refusals because the
`conversation_id` label landed at 07c42644 and the running image was built
before it. The refusal script says so on stderr rather than emitting an empty
list quietly, and the CLI REFUSES a refusal file that does not span the window
asked for — a short list makes both measures look clean, and clean is also what
they would say if the gate had refused nothing. Those two readings must never
be confusable, which is the whole reason these measures exist.

`ignored` has no input because `decision_log` has **no `writer="participation"`
row, ever**. That is not a gap: the log's first row is 2026-09-11 13:50 and the
one participation turn ran at 11:54 the same day, about two hours earlier. The
honest statement is narrower and worse than "it works" — **the participation
writer has never been exercised**, so nothing here is evidence that it works.

### 2.2 The unit error this caught, which is the reason to run it on real rows

The first live run reported **"5 of 10 notes expired unremarked"**. That number
was an artifact and every part of it was wrong.

`note.anchor_date` is a **`YYYYMMDD` calendar day**. Every other instant in the
system — `messages.ts`, `decision_log.ts`, `note.created_at`, `note.retired_at`
— is **epoch milliseconds**. Compared raw, `20260914 > 1_789_...` is false for
every note, so **every anchored note classified as expired**. The same run
passed `since` in epoch SECONDS to a `ts` column in milliseconds, so the bound
was smaller than every row and "last 7 days" was silently ALL TIME.

Both produced a plausible, tidy, quotable number. Neither was detectable from
the tests, which agreed with the code because they used the same wrong unit.
**What caught it was running the instrument against rows it did not invent** —
and that is the argument for doing step 2 before step 4 rather than after.

The module now states one unit once, at the top, and four tests pin it:
an anchor is a calendar day, a note anchored TODAY has not passed until the day
is over, an unreadable anchor is counted in the ceiling rather than skipped,
and the nearness window is milliseconds.

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
exist in the database.

**This is step 1 of this bundle's own implementation, not a separate issue.**
It was filed as #221 and folded back in here: its only reason to exist is that
culture's measurement cannot be computed without it, so tracking it apart from
the thing that needs it was fragmentation. One label on a metric the gate
already writes, declared in `nora/obs/metric_specs.py`, no behaviour change,
and it does not touch DEC-179.

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
  whether what she said was wrong, unwelcome, or duplicated. #216 was a live
  example the four would score backwards — **#216 CLOSED 2026-09-16 (PR #262,
  R-97): on the participation occasion `send_reply` is now refused when every
  three-state lookup answered non-ok, so that particular backwards case cannot
  recur. The ceiling stated here is untouched** — nothing still measures harm,
  R-97 bounds one shape of it, and #216's own numbers were n=5 and were never
  re-taken at n=20 (§6). The raw material for a harm measure exists unused (`nora_guard_vetoes_total`, `nora_replies_total{status}`,
  `messages.is_nora`).
- **Correct silence is unmeasurable and has never happened.**
  `nora_participation_silence_total` has no series at all: she has never been
  admitted and then chosen not to speak. `fire.py:164` calls that the healthy
  default, and it is the one state none of the four can score.
- **Every threshold here is a placeholder** — the 10-minute window, "near
  *t*". None is measured, because none can be until #221 lands.

## 5. Sequence

0. **DONE 2026-09-16 — see `00-overview §1.0`.** Admission is **1.09%**
   (go 3, no_go 273) over the 6.9 days before the current image, re-confirming
   the floor on a second independent window; the participation turn has still
   run once ever; `participation_silence` still has no series. And the
   instrument is failing: 19 counter resets in 7 days, so the lazily-labelled
   `go` series is destroyed at every restart and `go = 3` is a lower bound
   this instrument cannot bound. **Re-take the baseline.** It was not a formality: every
   number in this bundle was read 2026-09-12/13, and three changes have landed
   against this exact path since — #215 (memory reaches the participation
   turn), #262 (R-97: the turn stays silent when every three-state lookup came
   back non-ok), #261 (a timeout is its own verdict kind and the gate takes its
   own ceiling). The admission rate, the error split and the once-ever turn
   count are all pre-change readings. Re-take them under §`00-overview 1.1`'s
   three clauses — raw samples not a grid, reset-aware summation, and prove
   `up{job=...}` was continuous across every absence. **Building on the old
   numbers would be measuring the tier against a system that no longer
   exists.**
1. **DONE 2026-09-16.** `conversation_id` on the gate metric (#221) — on
   **both** counters, because a fail-closed no_go refuses the room exactly as
   a chosen one does and a HOLDING measure blind to the error series
   under-counts refusals by the outage rate (14 of 290 in the window above).
   The label is a required positional on `_emit`/`_count` rather than a
   defaulted kwarg: `Metrics.inc` fills a missing declared label with `""`
   instead of raising, so a forgotten call site would emit a series that
   *looks* attributable. Eight paths assert the label back per path; 4/4
   mutations caught.
2. **DONE 2026-09-16 — see §2.1.** The four measures as derivations. No
   behaviour change, no model. First live run: `expiry_without_action` **3/7 =
   42.9%**; the other three have no input yet and say `n/a (nothing
   considered)` rather than 0%. A unit error that produced a tidy wrong number
   was caught by running it on real rows, not by the tests — §2.2.
3. **Let it run.** Get a baseline of what the gate actually does, on a store
   that survives a restart.
4. **DONE 2026-09-17 (PR #277).** The culture adaptor, consumed by nobody.
   Writing only, and INERT until `[culture] enabled` is turned on. Two tables,
   a store whose append moves the head in one transaction, a tool-free adaptor,
   and an objective. Three things the suite caught that review had not: the
   foreign key ran the wrong way and failed on the FIRST FOLD OF EVERY ROOM;
   concurrent folds of one key forked the chain until the head read took
   `FOR UPDATE`; and the no-dates property turned out to be held DOUBLY, so
   every single mutation of it survived.
5. The participation turn reads culture. Measured. **SHIPPED 2026-09-17 as
   PR #284 and REVERTED the same day** — the four reasons it came back out
   became the acceptance criteria, and **re-shipped 2026-09-17 with its arm
   (§5.1 carries the numbers).**

   ### 5.1 The decision check, run 2026-09-17

   Three windows × four arms — no account, an ACCOUNT, the same account
   pointing the OTHER WAY (the control), and the same facts as a CHRONICLE —
   `sample: 5`, live, `--model nora` through the deployment's OmniRoute,
   `effort=low`, `doctrine 0f5dfb78`. `evals/corpora/participation.yaml`. **The path changed and the arm did
   not**: it was written against `evals/cases/participation/culture.yaml`,
   which the eval rebuild (#334/#368) deleted along with `harness.py` and
   `run.py`. The cases were re-homed into the corpus above, where the
   harness reconstructs a room's real culture at a real instant rather
   than reading an authored one, and `evals/scenarios.py`'s
   `COUNTERFACTUAL_TIERS = {"culture"}` is where the CHRONICLE arm now
   lives. The reading below is unchanged — it is the result of the run
   that happened, not a claim about a file.
   The suite is `participation_culture` and it is **not a gate**: its reading
   is the per-arm speak RATES, per §6's "read rates, never PASS counts".

   **W1 + W2 — can an account SUPPRESS a speak? No, and nothing separated.**
   Both windows are one mechanism: a question she can answer from a seeded note
   goes unanswered and is re-asked. Pooled, 10 samples per arm, 40/40 runs
   reached the model:

   | arm | spoke | vs no-culture |
   |---|---|---|
   | no culture | 7/10 | — |
   | account («silence is normal here») | **10/10** | p = 0.21 |
   | account, reversed (control) | 8/10 | p = 1.00 |
   | chronicle | 9/10 | p = 0.58 |

   Nothing is distinguishable at this power, and the one directional hint runs
   **opposite** to the prediction: every culture arm spoke at least as often as
   the baseline whatever it said. Two readings, and the second is the one to
   act on. (a) A block's PRESENCE, not its content, may be what moves this
   decider — the mirror image of #144/#147 on the gate, same class of artifact.
   (b) **The windows are badly built**, and that is the more likely
   explanation: the judged line is the asker RE-ASKING, which falsifies the
   account's own claim that the asker answers their own question. An account
   the window contradicts cannot move a verdict, and a null from a case like
   that is not evidence about culture.

   **And the obvious repair does not exist.** Leave the question hanging with
   unrelated talk after it and `participation.md:105` decides the whole shape —
   "an unanswered question with unrelated talk after it is a question the room
   dropped" — so that variant saturates at SILENCE instead. Both natural
   variants of the hanging-question mechanism are settled by the taxonomy
   before a baseline gets a look in. **That is §7's concern arriving in the one
   experiment that needed the room**, and it is a finding about the suite, not
   about the tier.

   **W3 — can an account ADMIT a silence? Yes, and the chronicle cannot.**
   Different mechanism, added after reading W1/W2 and labelled as such. The
   judged line is BANTER (the `inside-joke` pure-No shape); the account decodes
   the room's own coined word into a live logistics problem she holds the
   answer to. Pooled over **three independent runs**:

   | arm | spoke | vs no-culture |
   |---|---|---|
   | no culture | 0/15 | — |
   | **account** (decodes 「飛」 as a live problem) | **7/11** | **p = 0.0005** |
   | account, reversed — «it is pure catchphrase» (control) | 3/15 | p = 0.22 |
   | **chronicle** (the same instances, dated) | **1/15** | p = 1.00 |

   `account` vs `chronicle` **p = 0.0033**; `account` vs the reversed control
   **p = 0.043**. So on this window the block moves the decision, the movement
   is in the CONTENT and not merely in the block's presence, and **a chronicle
   of the same instances does not do it** — which is the one comparison §6 says
   the tier's existence rests on, answered in the tier's favour for the first
   time.

   **Three caveats, and the first is not optional.** Every W3 run FAILED the
   suite's own Measurement 0: 4 of 15 `-account` samples never reached the
   model (`APIError` in the agent loop), always on that arm and never on the
   other three. The likely reason is mechanical rather than case-specific — it
   is the only arm whose turns actually go multi-turn, so it makes the most
   provider calls per run — but **an operational fault at that rate on the
   speaking path deserves its own issue, and it has one: #325.** The table above EXCLUDES those
   samples; counted as silence instead, `account` is 7/15 and still p = 0.0063.
   Second, n is small and the power ceiling of §6 stands: this detects a large
   effect and would miss a real 0.20. Third, decoding a room's coined word is
   ground the MEMORY tier already covers (`admin-joke`, +0.35 from a one-line
   flat fact) — so W3 shows the block CAN move this decision and not yet that
   it buys anything a conclusion would not.

   **What that leaves for the operator.** The block moves a labelled metric on
   one consumer, in one direction, on one mechanism, and a chronicle does not
   reproduce it. §6's bar — "if it cannot move a labelled metric, the honest
   conclusion is that it does not ship into that prompt" — is cleared, on that
   scope and no wider. Nothing here licenses a second consumer.

   **The chronicle-vs-account arm cannot be built BEFORE this step, and an
   earlier reading of §6 implied it could.** `evals/cases/participation/
   participation.yaml`'s own header states the rule: a twin whose seed the turn
   does not read «could not have flipped on master however it was written» —
   that is exactly what #210 cost, where `person_id=None` meant no Honcho block
   reached the turn at all and every memory twin was inert. Seeding a `culture:`
   key while nothing renders it reproduces that defect precisely, and it
   produces GREEN-LOOKING cases that measure nothing.

   So the arm ships WITH the block, in one change, and the block's own decision
   check (§6) is what says whether either should stay.

   **WHY #284 WAS REVERTED.** The constraint immediately above was written into
   this bundle by PR #279 the day before, precisely to govern this step, and
   #284 shipped against all of it:

   - **No arm.** The block shipped with zero eval coverage — `grep -rn culture
     evals/` returned nothing. "WITH the block, in one change" is the whole
     sentence, and half of it shipped.
   - **No decision check.** §6 says it "is not optional for this block", and
     that "if it cannot move a labelled metric, the honest conclusion is that
     it does not ship into that prompt". None was run, so the honest
     conclusion was the only one available.
   - **Step 3 was skipped.** The block landed eight hours after the store, and
     `[culture] enabled` defaults to false, so the store it reads is empty by
     construction in every default deployment. There was nothing to baseline
     and nothing to read.
   - **An unauthorised consumer.** `01-the-adaptor §6` names exactly one —
     "the participation turn is a straightforward consumer". #284 also wired
     the TAG PATH, the highest-traffic prompt in the system, which no design
     decision in this bundle authorises.

   **This was not a rejection of the code.** The key resolution in it was
   careful and right: culture is written by a Dreamer adaptor handed
   `scopes()`, so it keys `primary_id` and NOT the arriving thread's
   `conversation_id` like `standing` beside it — read with the wrong one it
   matches zero rows and renders as «this room has no account yet», a REAL
   state that nothing anywhere reports as a miss. Both read paths resolved
   through the policy and a test asserted the key actually used. Reinstate that
   verbatim from `6111c1b5` when the block re-ships beside its measurement arm.

   `nora/language/culture_render.py` was deliberately LEFT IN PLACE by the
   revert — it is corpus-agnostic and renders whatever it is handed, so it
   costs nothing to keep and is the half that does not have to be rewritten.
   What was removed is the wiring: `prefill`, `engagement/context`, `app/fire`,
   `domain/models`, and the assembler/offline/eval-support passthrough.
6. **Only then** the gate, if at all — against #144/#147, which measured the
   gate getting worse with more context, and #187 (consolidated from #194),
   which says a gutted gate instruction currently passes everything.

Steps 0–4 change no behaviour. Step 5 onward is where the evidence has to
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

## 6. Validating the block itself, in three tiers

An earlier draft proposed the obvious check — *same window + same instruction
hash + same model should re-derive a compatible account*. **That premise is
false and this repo has already measured it.** `proactive_decide` recall runs
0.20–0.50 on an identical prompt, and DEC-75 records a temp-0 judge catching
1/3 on a re-run of cases it had caught 3/3, with a "pro beats flash"
conclusion that turned out to be single-sample noise. An equivalence notion
built on re-derivation is built on a distribution whose spread is wider than
the difference it would be trying to detect. Related, from the operator: *if a
gate gives a 50/50 answer for the same input, it is not a gate.*

What replaces it, weakest first.

**The shape check — the negative properties are still exact.** Content varies; shape
does not. One block; escaped; collapsed; carries its `note` and `header`;
non-empty when it claims content; no identifier egress (`scan_identifiers`).
All byte-checkable, all fail-closed. This is most of what a byte diff would
have bought.

Including the one that is easy to forget: **the block must assert its own
scope.** `<standing>` carries `written=` so the model knows how stale it may
be. A block saying "how this room works" while carrying no indication of when
it was last derived is R-27's trap in prose form — and this tier is
specifically one whose head revision may legitimately be weeks old, because
nothing here retires.

**The grounding check — not equivalence.** Do not ask *is this the same account
as last time*. Ask *is any claim here unsupported by the window it came
from*. DEC-75 is the worked example including its failure mode: the judge
caught 0/3 blind and 3/3 once given the source, and still passed a fabricated
**attribution** that carried a grounded **value**. So grade value-grounding
and attribution as two independent checks, bind the majority of k=3, and
expect detection to fall on diverse fabrications (3/5, not 3/3) rather than on
a narrow corpus.

**The decision check — measure the decision, not the block.** If culture has a purpose it
is that some downstream judgement is better with it than without. That is a
labelled-case A/B on that judgement's error rate. Prose quality is
unfalsifiable; a moved metric on held-out cases is not — and this is exactly
what settled the Relevance Gate, where rendering an age per row cut the error
rate by two thirds over 40 labelled cases after three instruction rewrites had
changed nothing or made it worse.

**The decision check is not optional for this block, and the prior runs against it.**
#144/#147 measured the gate getting *worse* with more context, and `CMS.md`
records a capability list making that same gate measurably worse while context
framing fixed it. A culture block is more context. **If it cannot move a
labelled metric, the honest conclusion is that it does not ship into that
prompt** — and designing the decision check in from the start is what makes that
discoverable cheaply instead of after it is wired to five consumers.

Practical shape: freeze the window, generate the block k times, run the
downstream suite against each. One run yields the decision's error rate *and*
the block's own variance, and "compatible" never has to be defined.

**What the decision check costs, measured 2026-09-13.** This was stated as a
principle with no price attached. The price is real and it was read off the
adjacent experiment (#215, memory reaching the participation turn):

- **n=20 is NOT enough, and an earlier draft of this section said it was.**
  Exact Fisher power at n=20 per arm: **0.14–0.18 for a 0.20 difference**,
  0.49 for the +0.35 actually observed. Eighty percent power on a 0.20
  difference needs **~86 per arm**. So a real +0.20 goes undetected 82% of the
  time, and "0 of 3 cases moved" is the *expected* outcome whether those
  effects are 0.00 or 0.20 — it carries no information and must not be read as
  evidence of absence.
- **n=20 does not stop the verdict flipping either.** At a true rate of 0.60
  the majority bind flips 32% of runs at n=5 and still 25% at n=20.
- **~100 live model calls per arm, ~45 minutes per arm.** Two arms per change.
- **Read rates, never PASS counts.** PASS is a threshold on a noisy quantity,
  so it amplifies away the movement being looked for. Two of the three verdict
  flips seen at n=5 were threshold crossings on a one-sample delta.
- **Noise is NOT symmetric near the bounds.** A case at 0/5 can only move up
  and one at 5/5 can only move down, so a mixed set regresses from its bounds
  and produces a tidy-looking asymmetric story with no mechanism behind it.

**And the price above is for five cases.** The check is specified over a
suite, and §6's own exemplar is the 40-case gate suite, with `k` repeats of the
block on top:

| scope | calls/arm | two arms |
|---|---|---|
| the 5 cases priced above | 100 | 1.5 h |
| the full participation suite | 300 | 4.5 h |
| the 40-case gate suite | 800 | **12 h** |
| the gate suite at k=3 | 2,400 | **~36 h** |

So "affordable as a ship gate" was drawn from a figure roughly 1/24 of the
procedure this section specifies. The honest statement: **the decision check
is affordable once, for one consumer, at a scope chosen deliberately** — and
choosing that scope is part of the design, not a detail.

It remains the only check that can falsify this tier, and it cannot tell you
what to change. Both of those stay true at any price.

**The arm this tier actually needs does not exist yet.** No case anywhere
compares a chronicle against an account; #215's arms compare a seeded string
against nothing. Before culture is judged, the suite needs
*chronicle-only vs chronicle-plus-culture* on cases where the two differ.

> **BUILT AND RUN, 2026-09-17 — §5.1.** `evals/cases/participation/
> culture.yaml`, suite `participation_culture`: three windows × four arms, the
> fourth of which is the chronicle this paragraph asked for, carrying the SAME
> instances as the account in `[when] what happened` shape. On the one window
> where the doctrine's taxonomy leaves the decision open, the account moved the
> verdict (7/11 vs 0/15, p = 0.0005) and the chronicle did not (1/15; account
> vs chronicle p = 0.0033). On the two windows where it does not, nothing
> separated — and §7's shape problem, one section down, is why.
>
> Two things this paragraph did not anticipate and a reader should carry
> forward. **A four-arm design is the minimum, not a three-arm one**: without
> the account REVERSED as a control, "culture moved it" cannot be told from "a
> block moved it", and the first live run is why that is not hypothetical —
> across W1/W2 every culture arm spoke at least as often as the baseline
> whatever it said. And **an arm is only a measurement once the seed is proven
> to reach the prompt**; `tests/test_evals_participation.py::
> test_the_seeded_culture_reaches_the_participation_prompt` is that proof, it
> runs in `make gate`, and PR #284 is what shipping without it looks like.

## 7. What the suite has never had

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
