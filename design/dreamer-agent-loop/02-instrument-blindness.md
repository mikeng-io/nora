---
type: analysis
title: "Instrument Blindness — The Layers That Cannot Measure Themselves"
description: "Independently, at six layers — one of them in another tier entirely — an instrument cannot observe the thing it exists to observe. This is one missing discipline, not six bugs, and it is why these failures stayed invisible for weeks."
tags: [nora, dreamer, observability, metrics, evidence, r-27]
timestamp: '2026-08-22'
edges:
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


# Instrument Blindness — The Layers That Cannot Measure Themselves

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


CLAUDE.md carries this rule:

> **Never read an instrument's output as evidence without first checking it can
> measure the thing** — state what a positive result would look like before
> concluding from a null one.

**It now carries eight more, and this node is what they were derived from.**
CLAUDE.md's *Instruments and spans* section states them one line each and
`docs/maintainers/conventions-rationale.md`'s *Instruments and spans — the war-stories* carries the
measurement behind each; neither repeats the catalogue below, and this node
stays the place the instances live. The rules, by shape: one root span per entry
point; spawned work does not inherit the caller's span; a span with only a name;
measure the cost, not the answer; R-27's three states on the instrument; a
declared metric with no call site; derived label vocabularies; and a positive
control beside every zero-assertion.

It is violated at six independent layers. They were found by six separate
investigations that were not looking for the same thing — and Layer 6 is not in
the Dreamer at all, which is what settles this as a repo-wide discipline gap
rather than one subsystem's bug.

The common cause is structural: **a background loop has nobody waiting on it.**
On the tag path a broken instrument is discovered within one turn, because a
human is holding the phone. Here, a null reading and a broken gauge are the
same observation, and nothing forces the question.

One layer below is in the note tier, not the Dreamer. That is what settles this
as a repo-wide discipline gap rather than one subsystem's bug.

## Layer 1 — The stop-reason counter is blind to both caps that bind

`nora_dream_stop_reason_total` exists to answer *"did a budget cap bind?"*. It
is incremented **only on the success path**, after the try block returns.

- The timeout branch constructs its outcome with `stop_reason="timeout"` and
  hands it to the finaliser — which does not touch that series. So
  `reason="timeout"`, a value the metric's own help text advertises, **can
  never appear**.
- `MaxTokensReachedException` leaves through the generic exception branch with
  `stop_reason=None`.

Both of the caps that actually bounded this subsystem in production are
invisible to the one counter designed to see them. All thirteen failures appear
only as `nora_dream_run_total{status="unavailable"}` — indistinguishable from a
provider 429, a prose reply, or a network fault.

## Layer 2 — The unmigrated tiers emit no trace and log nothing on success

The legacy model client has no OpenTelemetry instrumentation, so the language
and exchange tiers produce **no Langfuse trace at all**. The consolidator path
logs nothing on success. There is no per-cycle counter.

> For the entire language/exchange tier, **"ran and found nothing" is
> indistinguishable from "never ran."**

This is exactly the `unavailable`-reported-as-`nothing_found` substitution R-27
exists to forbid, promoted from a tool result to a whole subsystem. It is also
why §6 of [01-live-state.md](01-live-state.md) cannot answer whether two rows
from 963 messages is correct conservatism or a starved tier.

The migrated fact adaptors *do* trace. The asymmetry means the tier we can see
is the one that has produced nothing, and the tier that produced the wrong rows
is the one we cannot see.

## Layer 3 — Backoff and skip decisions leave no evidence

Neither the exponential backoff nor the in-flight skip emits a log line.
`grep -ic backoff` over the whole 88,678-line log returns **zero**. Their only
signal is a Prometheus counter.

Those counters are **in-process**, and the container was recreated. The numbers
that would have quantified the incident are gone and are not recoverable. The
tick-by-tick reconstruction in [01-live-state.md](01-live-state.md) had to be
rebuilt by joining log timestamps against message arrival times in the
database, because the instrument that was supposed to record it had already
been reset.

## Layer 4 — The wrong-gloss guard cannot falsify a wrong gloss

The language renderer refuses to render a lexeme with no quotable example, and
its stated reason is that *"the verbatim quote is the only thing that makes a
wrong gloss visible."*

Both live rows satisfy it. Both quotes contain 「big walk」. Neither quote
falsifies either gloss, because the term appearing in a message is not evidence
about what the term means. The guard measures *presence of a quote*, and the
property it is asserted to protect is *refutability*. Those are different
properties, and the gap is invisible from the code.

## Layer 5 — The egress screen cannot see the content class it is asked to guard

`dream_screen` walks every string in the model's envelope and tests four
identifier patterns plus a coordinate pair. That is its entire alphabet.

It is cited — including in an earlier draft of this bundle — as the reason a
background loop may safely read a room. It cannot see names, nicknames, quoted
messages, memory conclusions, roster composition, membership counts, or
relationship claims. It also never sees tool *arguments* or tool *results*,
only the model's final restatement.

Full argument in [11-disclosure.md](11-disclosure.md). It is listed here
because the failure mode is identical to the other five: a real instrument,
correctly implemented, measuring a narrower property than the one it is
credited with.

## Layer 6 — A tier declares ten metrics and ships none of them

> **RESOLVED 2026-08-27, and not by building ten.** The tier ships **three**;
> four were already counted by `nora_tool_calls_total` and a second series would
> double any dashboard summing tool volume; three more measure an adaptor that
> does not exist, and declaring those would have manufactured a fresh instance of
> **the declared-name-with-no-call-site failure** — `nora_fact_entity_unresolved_
> total`'s class ([21](21-substrate-contract.md) §6.4), named here by shape
> rather than by layer number because the numbering moved under this line once
> already. See
> [27](27-note-dreamer.md) §6.1. The layer's finding stands; the fix was to
> decide which ten were real, not to build ten.

Not the Dreamer. The **note tier**, found by an independent investigation
looking for something else.

Its design declares ten counters by name — candidates by trigger, writes by
kind, reads and searches by result, index size, curator and revisit outcomes,
refusals by reason. **Not one of them is declared in the metrics module**, so
none can ever be incremented. Note tool calls fall through to the generic
per-tool counter.

What that costs is written in the tier's own spec, which describes the
diagnostic it believes it has:

> *"a climbing `candidates_total` with a flat `writes_total` → she recognises
> and does not act. **That is the inert-skill failure, made visible.**"*

It is not visible. The single most important failure mode the tier names for
itself is the one it cannot observe — and the tier is presently sitting at zero
notes with one miss-cache row, which is exactly the state that diagnostic exists
to disambiguate.

It also blocks something concrete: the note tier's re-derivation sweep is
deferred **behind an evidence gate naming two of these ten metrics**. The gate
cannot be satisfied, because the instruments it requires do not exist.


## Layer 7 — The trace is the bundle's provenance claim, and Nora emits no spans

**Found 2026-08-27, by grep, and it is the widest layer here.**

This bundle's spine says the Langfuse trace is *"the provenance every claim about
a turn re-derives from."* Four nodes rest on it. The reality:

```
start_span / start_as_current_span sites in nora/ :  0
```

**Nora creates no spans anywhere.** `nora/obs/tracing.py` builds a real
`TracerProvider` and an OTLP exporter to Langfuse, and then nothing in the
application ever opens a span. Every span that reaches Langfuse is emitted by
**Strands**, from inside the agent loop, because `factory.py` hands it
`trace_attributes`.

**So the trace covers the model call and nothing around it.** Dark today:

| dark | consequence |
|---|---|
| ingest → normalize → policy → admission | a message refused before the agent exists leaves **no trace at all** |
| prefill assembly | which blocks were built, from which reads, at what size — the thing every disclosure and budget argument reasons about |
| store reads on the turn's critical path | the unbounded reads [26](26-standing.md) §5 names as the class defect are invisible |
| delivery | the strict-silence boundary — the one authored surface — is untraced |
| the debrief stage | a stage whose whole risk is holding a slot |
| the Dreamer's loop around its adaptors | the loop this bundle exists to fix |

**The asymmetry is the finding.** A turn that *reaches the model* is
well-traced. A turn that fails *before* it — refused at admission, starved at
prefill, blocked at a store read — produces **nothing**, which is
indistinguishable from a turn that never happened. That is the exact shape of
Layers 1–6, one level up: the instrument covers the healthy path and goes silent
on the failing one.

**And it undercuts a load-bearing claim rather than a nice-to-have.** "No
self-attestation — re-derivable, so no actor is trusted" is the third of the
three rules the spine says make it hold. Re-derivation currently stops at the
model call.

**What this does NOT need.** Not a span per function. The gap is that the
turn has no *skeleton*: one span for the turn, children for the named stages the
architecture already has — admit, comprehend, prefill, invoke, deliver, debrief —
so a turn that ends early ends *visibly*, at a named stage. The stage boundaries
already exist in code; nothing is traced against them.

**Why it was invisible.** `[obs.tracing]` is configured, the exporter is real,
and traces *do* arrive — so every check short of grepping for span sites reports
a working tracing setup. **A partially-instrumented system reads exactly like an
instrumented one from the outside**, which is Layer 5's argument applied to the
tracer itself.

## The silent-match family — six instances, and the sixth is ours

Every layer above is an instrument that cannot see the thing it is credited
with. **This section is one layer down**: the instruments we use to FIND those
layers have their own failure mode, and it produced five instances between two
sessions on 2026-08-27.

**The shape, stated once:**

> **A matcher that fails to match, and returns something that reads as success.**

Not an error. Not an empty result that looks empty. A *positive-looking* answer
— a passing test, a clean merge, a zero, a `0` — produced by machinery that
never engaged with the thing it was pointed at. Every one of these was found by
accident while looking for something else, which is the property that makes them
worth naming as a family rather than fixing one at a time.

**A sixth landed on 2026-08-28, and it is the one where the instrument was
OURS rather than the code's** — see below the table for why it is not a
duplicate of #3.

| # | the matcher | what it returned | why it read as success |
|---|---|---|---|
| 1 | `LIKE '%note_%'` over `tag_trace` | 7 rows | `_` is a LIKE wildcard, so it matched `note` + any character. Seven *hits* is far more convincing than zero. The precise regex returns 0 |
| 2 | `metrics.get(name) == 0` in a new test | PASS | `get` sums matching series and answers `0.0` when there are none, so it cannot distinguish «published zero» from «never published». The test passed against reverted code |
| 3 | `str.replace(old, new)` anchored on `## 7.` | no exception | the file's next section was `## 6.`. The replace silently no-opped, the section was never written, and a RESOLVED banner was published pointing at it |
| 4 | `version: 13` beside a changed body | green gate, clean merge | `sha256` was computed over the body and moved on its own, so the machine-checked half stayed correct while the human-readable half rotted. (It is over the RENDERED section since #175 — the body plus the served `<section …>` tag — which changes what a silent edit is, not the shape of this failure) |
| 5 | a per-test `trace.set_tracer_provider` | passing fixture | it is **one-shot per process**: every later test silently reads the FIRST exporter, and the un-torn-down global then failed two timing tests elsewhere that passed in isolation |
| 6 | an edit script whose `assert s.count(old) == 1` failed | `All checks passed!` · `47 passed` | the script raised and wrote NOTHING, but it ran beside `ruff` and `pytest` whose output was healthy **for the file it had not changed**. A metric label existed in code and never reached its spec; the endpoint emitted four values while the documentation described three, for two review rounds |

**#6 is the one instance where the tool did NOT lie, and that is what makes it
worth its own paragraph.** #1–#3 returned a positive-looking answer to the
question they were asked. #6's script asserted, raised, and reported the
failure correctly — the silence was one layer out: **the adjacent instruments
were green, and they were green about a file the script had never touched.**
The rule this breaks is the bundle's own — *never read an instrument's output
as evidence without first checking it can measure the thing* — inverted. The
instrument was working; it was measuring something else, and «something else
passed» was read as «this passed».

That makes the family's remedy broader than «make it fail on purpose». For a
multi-step edit, the check has to be **the edit's own effect** — grep for the
text you just wrote — because a test suite passing after a no-op edit is
exactly what a test suite does.

**Three of these are matchers, two are globals, and one is a reading error,
and that is still not three families.** #1–#3 fail to match; #4 and #5 fail to
*reset*; #6 fails to be *read*. Both hand back a value
indistinguishable from the value a correct run produces, and in both the
verification step is the same one: **make it fail on purpose.** A test that has
never been seen red is a claim, not a measurement — which is the bundle's own
no-self-attestation rule applied to the instrument rather than to the work.

**#4 deserves its own sentence, because it is the tightest instance of the
unguarded-copy class this bundle has recorded** ([21](21-substrate-contract.md)
§6). Not two files drifting apart over weeks — one field drifting from the body
three lines below it, in a document whose `change_policy` is `agent-may-edit`,
so «nobody is watching the version» is the *common* case rather than the
exception. And the reason it hid is the sharp part: **an invariant that
self-maintains can mask a neighbouring one that does not.** `sha256` needs no
discipline, so it stayed right; `version` needs discipline, so it went wrong;
and the two sit in the same file describing the same document. It had a real
victim — one live turn, 2026-08-27T14:23:22Z, served the changed body and logged
`conduct: 13`, the same integer as every turn before the change existed.

**What each one cost, and what closed it:**

| # | closed by |
|---|---|
| 1 | `autoescape=True` — which `SqlNoteStore.search` already passes, one layer down, for exactly this reason. The lesson was in the codebase before it was learned again |
| 2 | assert membership in `snapshot()` BEFORE asserting the value. Presence and magnitude are two questions |
| 3 | assert `s.count(old) == 1` before every substitution. A `replace` whose count you never check is a write-side matcher with no result to inspect |
| 4 | `tests/test_doctrine_version_parity.py` — a pin, not a rule. No test can know which version is *correct*; a test can refuse to let the body move while the version stands still |
| 5 | one process-wide provider set once, with the exporter swapped per test — the thing that is genuinely global is configured globally |

**The generalisation, and it is the point of writing this down:** every fix in
the right column is *cheaper than the search that found the bug*, and none of
them would have been prompted by the failure, because there was no failure.
These are found by deliberately breaking the thing you just built and checking
it notices. **That is the same discipline as [09-measurement.md](09-measurement.md)
§3 one level up — an instrument you have never seen report a negative is not an
instrument yet.**

## The bug-pinning test — when the gate is recruited to defend the defect

The family above is about instruments that **fail to see**. This one is its
opposite and is worse, because the machinery works perfectly:

> **A missing test leaves a visible hole. A test that pins the bug DEFENDS it —
> it goes green, it reads as intent, and the next person changing that code has
> to argue with it first.**

Two instances on 2026-08-27, from two different sessions, neither found by
running anything:

| the test | what it asserted | the defect it protected |
|---|---|---|
| `test_the_dream_loop_is_not_spawned_when_the_flag_is_off` | *"`language.enabled = false` must not spawn `dream_loop`"* | the Dreamer's clock being gated on a TIER flag. The runtime shipped as "on", never ticked for five days, and the suite was green throughout |
| `test_two_proposals_against_one_note_in_one_tick_refuse_the_second` | *"the FIRST one landed and is not rolled back"* | `SqlNoteStore.propose` running each proposal in its own transaction, while `propose`'s own docstring required the batch to be atomic |

**In both cases the gate was working exactly as designed.** It was asked whether
the code does what the test says, and the answer was yes. Nothing in the gate's
job description covers whether the test says the right thing — that is the
no-self-attestation rule pointed at the specification instead of at the work, and
it is the one place a suite structurally cannot help.

### The recognition signal

This is the part worth carrying, because both instances have the same findable
shape:

> **A test and a docstring, in different files, requiring opposite things —
> where neither half fails on its own.**

The docstring says atomic; the test says partial; the code satisfies the test; the
suite is green. The docstring says the clock belongs to the runtime; the test says
it belongs to the language tier; same result. **Neither half is falsifiable
without reading the other**, which is why both survived every gate run and why
both were found by a cold reader rather than by their author.

So the check is not "is this tested?" but **"does anything else in the repo state
the opposite?"** — and the search that answers it is a grep for the claim, not a
test run.

### Why this argues for review as a gate rather than a courtesy

Six failure families were found on 2026-08-27. **Exactly one of them has a cheap,
total, automatable gate available** — a declared metric with no call site, which
an AST walk over `DECLARED_NAMES` versus `metrics.inc(` closes permanently
(issue #56). The other five needed a human reading with fresh eyes.

That is worth stating plainly, because the instinct after a day like this is
that more gates would have caught more. **The evidence says the opposite for five
of the six.** A gate can prove code matches its test. It cannot notice that the
test and the design disagree, that a double's docstring describes behaviour the
double no longer has, or that a field is written by two implementations and
asserted for one.

## The never-re-mutated test — when the gate never measured anything

The other half of the family above. A bug-pinning test **defends** a defect; this
one never **measured** one. Both are green, both are cited as evidence, and
neither is.

**Four instances in one session, all in one author's own work, all found by
someone else or by re-mutating.**

| test | what it asserted | what it missed |
|---|---|---|
| `test_the_first_fold_of_a_conversation_is_not_a_rederivation` | `rederivations == 0` and `prior="absent" == 1` | both are true of a **crash** — remove the guard and the stage raises, writes nothing, and the test passes |
| the first latch test | one rebuild after an ungrounded verdict | the helper defaulted `grounded=true`, so the fold wrote a **fresh** verdict and masked the latch it was testing |
| `test_the_accessor_hands_out_a_copy_not_the_live_set` | that `notes_read` is a `property` | passed while the accessor returned the live set |
| two slot-hold config tests | that a bad budget refuses to start | replaced the literal `150.0` after it had been changed to `160.0` — a **silent no-op**, so they tested an unmodified config and reported DID NOT RAISE |

**The shape is always the same: a green test written after a change the author
made, never re-mutated.** Writing the test is not the check. Breaking the code
again is.

Three of the four were caught only because an independent reviewer mutated a
clause the author had not, or because the author re-mutated on a later pass. The
author had truthfully reported falsifying three properties of the same feature —
and had, and the fourth clause was the one that shipped the defect. **Reporting
"falsified by mutation" is not a property of a change; it is a property of each
individual clause**, and the clauses nobody thinks to break are exactly the ones
whose tests are decorative.

**What actually works**, and it is cheap: after a test goes green, break the
production line it names and watch it go red. The two instances the author caught
alone were both caught that way, within a minute of writing the test. One of them
— the store-call count — then caught a *later* miscount by the same author, which
is the difference between fixing an instance and closing a family.

## The screened-identifier family — three adaptors, one screen, and an instrument nobody read

**2026-08-28.** `DreamerRuntime` runs `nora.agent.dream_screen.screen` over the
model's raw envelope items **before** `adaptor.accept`, and the screen drops the
WHOLE item on any identifier reachable in it. `egress._UUID` matches any
hyphenated UUID. So an adaptor whose objective asks the model to echo a row's id
back has every such item destroyed, silently, forever.

**Three adaptors hit it within hours of each other**, independently, and none of
their test suites could see it:

| adaptor | the field | how it failed |
|---|---|---|
| `note-revisit` | `note_id` | **loudly** — every item screened, tier 100% inert |
| `note-curation` | `note_id` | loudly — *"the curator asked about UUIDs, so the screen rejected every answer"* (`e5c5bea`) |
| `fact-adjudication` | `supersedes` | **selectively — and still open** ([#63](https://github.com/mikeng-io/nora/issues/63)) |

**The third is the dangerous one, and the difference is the finding.** Only a
`contradicted` verdict carries `supersedes`, so `supported` and `not_covered`
flow normally: facts are written, counters move, the tier looks healthy. It has
simply never been able to record a contradiction. Four consequences, each in the
code:

1. the correcting fact is never written;
2. `_supersede_fact` never runs, so **the standing WRONG fact is never retired**
   and keeps rendering into every tag;
3. `_retire_candidates` iterates `proposals`, and a screened verdict never
   becomes one, so its candidate stays pending;
4. it is therefore **re-adjudicated every tick, forever**, at a `web_search`
   plus a completion each time.

Leg 4 was predicted verbatim by the code it happens in: `_retire_candidates`'
own docstring warns that without retirement *"the same unprovable claim is
re-extracted and re-adjudicated every tick forever, and it fails quietly because
nobody is waiting on the Dreamer."* It was written about `not_covered` and
describes the screened `contradicted` path exactly.

### Why every grep missed it

A sweep of every adaptor and objective:

```
fact-adjudication   supersedes         UUID   <- broken
fact-candidates     message_id         int    safe — _UUID cannot match an int
room-language       message_id         int    safe
room-exchange       message_ids        int    safe
note-revisit        cited_message_id   int    safe (the note itself is an ordinal)
observer / exchange_observer                  not on the DreamAdaptor path at
                                              all — no envelope_key, never screened
```

**`supersedes` is the only one not named `*_id`.** A grep for id-shaped keys —
the obvious sweep, and the one that would have been run — does not find it. The
field that carries an identifier without saying so is the field that survives
every search for identifiers.

### The test written to catch it could not see it

**Worth its own subsection because it happened while fixing the third
instance.** The test added to `test_fact_adaptor.py` drives a `contradicted`
verdict through the real screen — the right test, the one whose absence let this
ship. Under the decisive mutation (render the fact's UUID again, as the broken
code did) **it passed.**

`nora.testing.fact_store.InMemoryFactStore` minted `f"fact-{n}"`.
`SqlFactStore.propose` mints `str(uuid4())`. The screen matches a UUID. So the
double could not produce the shape the defect turns on, and a test written
specifically to prove the fix was decorative against the exact revert it
existed to catch.

That is the double-diverging-from-the-real-store family again — and this
instance is the sharpest, because the divergence was in a field nobody thought
of as behaviour. An id is an id; its *format* looked like a readability choice.
It was load-bearing. Both doubles now mint `uuid4()`, and the comment says why,
because the next person will find a readable id more pleasant in a failure
message and be right about everything except the one property a test turns on.

**The general form:** a double may differ from the real thing in what is
convenient, never in what something asserts on — and the shape of an
identifier is not obviously either until a screen reads it.

### What the family costs, and the two rules it earns

**CORRECTION, 2026-08-28.** An earlier revision of this section said the screen
has no per-adaptor counter. **That was false**, and it is corrected here rather
than quietly edited because the claim shipped to master and someone would have
built on it. `nora.agent.dream_screen.screen` increments
`nora_dream_screened_total` with `adaptor=` AND `reason=`, and
`metric_specs.py` declares both labels. The instrument exists, is correctly
labelled, and would have named the broken adaptor exactly.

**Which makes this the sharper finding, not the weaker one.** Not a missing
instrument — an instrument that was RIGHT and that nobody read. Three adaptors
failed the same way in one day while a correctly-labelled series sat on the
endpoint waiting to be queried, because the series lives in the RUNTIME's
namespace and every one of those failures was investigated through the TIER's
own counters, which showed exactly what a model that had stopped answering looks
like.

A metric nobody queries is not evidence, and «we have a counter for that» is
only true if something reads it. The remedy is not another counter. It is that a
tier's own instruments should be able to say «the runtime dropped my output» —
or that investigating a silent tier starts by asking the runtime what it threw
away.

> **An adaptor's tests must drive at least one item through the REAL screen, in
> the order the runtime does.** A test that calls `accept` directly is testing
> half a pipeline, and all three suites did.

> **Nothing an adaptor asks a model to echo back may be a stored identifier.**
> Render an ordinal handle and map it back; the model never sees the id. This is
> now the convention in two adaptors and it should be the convention in all.

**Widening the screen was rejected** in all three cases and should stay
rejected: it is a shared security rule, and widening it for one caller is how
such a rule dies.

## The gate measured the TREE — when a green run certifies content nobody can get

**2026-08-28.** A branch was reported green and ready to merge. `make gate` had
run to completion — 4955 passed, ruff, mypy and `lint-imports` all clean — and
every one of those numbers was true. The coordinator ran `git status` in the
worktree before merging rather than after, and found an uncommitted file: a
rebase resolution moving a rule row in `09-decision-index.md`.

> **The gate passing is not evidence the TIP is green — it is evidence the TREE
> is.**

Nobody in this repo had drawn that line, and five branches had been merged that
night on gate reports.

**It is not a silent match.** The gate did not fail to match, did not return a
misleading value, and did not no-op. It ran correctly, measured accurately, and
reported truthfully — **about a subject nobody else can reach.** A working tree
is private to one worktree; a merge takes the tip. So the artefact that was
certified and the artefact that would ship were different, and every instrument
in the gate agreed about the wrong one.

**The consequence lands on the file least able to absorb it.** What was
uncommitted here was the decision index — the document CLAUDE.md puts FOURTH in
the reading order and treats as current state. A row landing in the wrong place,
or not at all, is exactly the failure that produced a wrong answer elsewhere the
same night, from a stale row in that same file.

**The remedy is one line and it belongs before the report, not after the
merge:** a green claim about a branch must be made against a CLEAN tree, and
`git status --porcelain` empty is the cheapest possible check. The stronger form
— re-run the gate on the committed tip rather than reasoning that the content is
identical — is the same discipline one level up, and it is the one this bundle
already applies everywhere else: **no self-attestation.** «The commit contains
exactly what I just tested» is a claim by the doer about the doer's own work,
and it costs one command to replace with a measurement.

**Why it generalises past a slip.** Every worktree in this repo is a place where
a gate can be run and a tip can lag it, and the rule that keeps sessions out of
each other's way — one agent per checkout, each with its own worktree — is
exactly what makes the gap invisible: the person merging is not the person who
ran the gate, and neither of them sees the other's `git status` unless somebody
asks for it.

*The reading that would retire this entry:* a gate that refuses to report green
on a dirty tree, at which point the discipline is mechanical rather than
remembered.

## The consistent pair — two things that agree with each other and not with the requirement

The two families above are about one thing being wrong. This one is about **two
things being wrong together, consistently**, which is why none of them looked
wrong:

> **A consistent pair that is jointly wrong.** The reason none of them looked
> wrong is that the usual check is *"do these two agree"* — and they did. The
> check that finds it is different: **does either agree with the REQUIREMENT** —
> which needs a third thing in the room.

A test and its code are two things. A docstring and its function are two things.
A store method and its renderer are two things. **Neither pair can check
itself**, and a suite comparing one against the other reports agreement, which
is what a passing gate looks like.

Three instances on 2026-08-27/28, in three different tiers, none found by
running anything:

| the pair | how they agreed | the third thing that would have caught it |
|---|---|---|
| **`merge_groups` and `_render`** — the store dropped note bodies, the renderer dropped them again | Consistently. The curator was shown titles and closing conditions, and the prompt looked complete because nothing downstream expected a body | **The requirement**, [27](27-note-dreamer.md) §9.2's read table, which lists the body as what merge execution reads. `anchored_before` beside it already returned `(Note, body)` — the divergence was between the two halves of ONE store |
| **A test and the code it pins** — `test_two_proposals_…refuse_the_second` asserted the first proposal is not rolled back | Perfectly. The code did exactly what the test said | **The docstring three files away**, which required the batch to be atomic. Neither half is falsifiable without reading the other |
| **The evidence ordering in the language tier** (nora-a7's) | The producer and the consumer used the same order, so every round-trip matched | The spec's own statement of which end is authoritative |

**The self-directed version, which is the sharpest one and is why this is its
own section.** Fixing a finding is itself a pair — the fix and the finding — and
they agree by construction. On 2026-08-28 finding 2 corrected what
`nora_note_curator_bodies_read` COUNTS; finding 4's fix, applied an hour later,
changed what the store READS, so `merge_groups` began fetching every standing
note's body while the gauge still reported the handful shown to the model. **The
budget instrument was made to lie by the fix to the thing it measures**, and
neither finding mentioned the other:

> **Those are two halves of one sentence, and only one half was in the review.**

So the rule generalises to the person applying it: **verify a fix against the
PROPERTY, not against the finding.** The finding is one half of a pair with the
fix, and the pair agrees.

### What to go looking for

Not "is this tested" and not "do these agree". The questions that find this
shape:

- **Does either half agree with the spec?** — not with each other. Read the
  requirement first and both halves second.
- **Where is the third thing?** — if a claim is checked only by something that
  was written alongside it, there is no third thing and the pair is unchecked.
  A cold reader IS the third thing, which is why every instance here was found
  by one.
- **What else moved?** — after a fix, ask what property it touched rather than
  what finding it closed. The instrument that measures the fixed thing is the
  first place to look.

## What follows from this

**Every fix in this bundle ships its own measurement, or it is not done**
(D7 in [00-overview.md](00-overview.md)).

Concretely, that means a change is incomplete until someone can state what a
positive reading looks like *and* produce it:

- A cap that binds must be distinguishable from a provider failure.
- A loop that ran and found nothing must be distinguishable from one that never
  fired — for every adaptor, including the migrated ones.
- A backoff engagement and an in-flight skip must survive a restart, which
  means a log line, not only a counter.
- A guard must be tested against the property it is *claimed* to protect, not
  the property it is *easy* to assert. Layer 4 is the cautionary case: a test
  that a quote is present would have passed on both wrong rows.

This is the negative half of the repo's own `measure` verb, applied to the
subsystems that most need it and currently have it least. Layer 6 is the reason
to state it as a repo-wide discipline rather than a Dreamer fix: a tier written
by a different session, at a different time, reproduced it exactly.

## Status / Checkpoint

2026-08-28 — **the discipline left this node and became a rule set.** Eight
rules, one line each in CLAUDE.md's *Instruments and spans* section with the
measurement in `docs/maintainers/conventions-rationale.md`. Every one was verified against the code
before it was written — which turned up one correction worth recording here,
because it is the family's own shape: **`asyncio.create_task` DOES copy the
current context**, so the rule is not «context is lost at a spawn». It copies
the context of the task that CALLS it, and the queued-tag path calls it from the
*previous* turn's `finally` (`nora/engagement/slots.py`
`release_and_dispatch`) — so a QUEUED tag inherits a real context that is the
wrong one, which is strictly harder to see than inheriting none. Measured with a
three-case probe rather than reasoned from the signature, which is [25](25-knowledge-pipeline.md)
§2.1's correction applied to this node's own writing.

2026-08-28 — **the consistent pair added**, the third family and the one that
explains the other two's discovery pattern: every instance in this node was
found by a cold reader and none by running anything, because a pair that agrees
with itself is what a green gate looks like. Includes the self-directed case —
a fix and its finding are a pair too, so a fix must be verified against the
property rather than against the finding.


2026-08-27 — **the bug-pinning test added as its own family**, distinct from the
silent-match one: a silent match FAILS to see something, a bug-pinning test
actively protects it and recruits the gate to do so. Two instances, one from each
of two sessions, both green for the whole life of the defect they defended. The
recognition signal — a test and a docstring in different files requiring opposite
things, neither failing alone — is the reusable half.


2026-08-27 — **the title lost its count.** It read "Six Layers" while the file
held seven, because Layer 7 was appended and the title was not — a stale ordinal
in the one line nobody edits. Fixed by removing the number rather than
correcting it to seven: a count in a title is a copy of something the document
already states, and this node has now recorded that class twice in its own
checkpoint while carrying an instance of it in its own heading.

2026-08-28 — **the screened-identifier family**, three adaptors in one day.
`dream_screen` drops a whole envelope item on any reachable UUID, so an adaptor
asking the model to echo a row id back loses every item. Two failed loudly and
were fixed; `fact-adjudication` fails SELECTIVELY — only `contradicted` carries
`supersedes` — so the fact tier looks healthy and has never recorded a
contradiction, never retired a wrong standing fact, and re-adjudicates the same
candidate every tick forever. It escaped every sweep because `supersedes` is the
only such field not named `*_id`. **Latent rather than live**: `[facts]` is off
in this deployment, so it has never fired in production.

2026-08-28 — **the gate measured the TREE.** A branch reported green on a
complete, accurate `make gate` run that had been performed against a working
tree carrying an uncommitted file the branch tip did not have. Not a silent
match — the instruments were right about a subject nobody else can reach. Found
by running `git status` before a merge rather than after; the uncommitted file
was the decision index. A green claim needs a clean tree, and the stronger form
is to re-run on the committed tip rather than reason that the content is
identical.

2026-08-28 — **a sixth silent-match instance**, and the first where the
instrument was one of ours rather than one of the code's: an edit script raised,
wrote nothing, and the `ruff`/`pytest` output beside it was green about the file
it had not changed. A metric label lived in code and never reached its spec for
two review rounds. It shifts the family's remedy: for a multi-step edit, verify
the edit's OWN effect, because a suite passing after a no-op is what a suite
does.

2026-08-27 — **the silent-match family added**, five instances in one day across
two sessions. It sits one level below the layers: the layers are instruments
that cannot see their subject, this is the machinery we use to find them
returning a positive-looking answer without engaging. Recorded because all five
were found by accident, and because each fix is cheaper than the search that
found it.


2026-08-27 — **Layer 7 added: Nora emits ZERO spans.** All tracing comes from
Strands' agent-loop instrumentation, so the trace covers the model call and
nothing around it — and a turn that fails before the model produces nothing at
all. The bundle's provenance claim currently holds only for the half of the turn
that already worked.

2026-08-27 — **a TENTH instance, and a named class rather than an anecdote:
a copy of an operator-owned value, with no parity guard, is a fork with a
delayed fuse.**

Four of these were found in one afternoon, and the shape is identical every
time. A value the operator owns — a trigger phrase, a token budget, a timeout —
gets *restated* somewhere: a test fixture, a corpus, a design paragraph. The
restatement stays plausible after the original moves, so **nothing fails at the
moment of divergence.** When it finally fails it fails somewhere else and blames
something else.

| the copy | how it failed |
|---|---|
| the near-miss eval corpus hardcoded `nora.000` | the operator re-enabled the plain `nora` trigger; two cases stopped being near-misses and became real TAGS. The corpus swore the model would not be invoked, it was, and the gate blamed **the model** |
| four `tests/test_config.py` assertions on that same string | three were `str.replace` calls that had **silently become no-ops** — the tests loaded a perfectly valid config while asserting it fails to load. Invert the assertion and they pass while measuring nothing |
| `config.example.toml` behind the deployment on three keys | caught, because this one **has** a parity guard |
| [26](26-standing.md) §5 reasoning about `[llm] timeout_seconds = 60` | the value does not exist; the field is required and set to 120. **A design document is the same bug in prose** — and the only one here with no possible automated guard |

**The two with guards were found; the two without rotted for weeks.** That is
the whole finding, and it is the instrument-blindness thesis applied to
configuration rather than to metrics: an unguarded copy is not "slightly stale",
it is a second source of truth that nothing is watching.

**The rule.** A test, corpus or design node that needs an operator-owned value
**reads it** — from the config under test, from the rendered corpus, from the
loaded object. Where a value is deliberately pinned as a tripwire (this repo
pins the token budget on purpose, so a change forces review), the pin is only
safe **beside a parity guard** that fails when the two diverge. A pin without a
guard is the fork.

**And a stale copy can hide a second one.** Raising the synced budget exposed
`_OVER_BUDGET = 130_000`, a constant whose only job was to sit *past* the
budget and which now sat below it — so the scenario designed to blow the budget
would have quietly stopped blowing it, with two tests still green. Its own
comment named the budget from **two generations earlier**.

2026-08-27 — **a ninth instance, and it is this bundle's own authors.** The
Graphiti investigation reversed its own conclusion four times, and every reversal
had one cause: **a signature was read and a conclusion stated before the thing
was run.** Twice the error was identical — reading a plural orchestrator that
queries the graph and concluding the singular unit beside it was coupled too.
The corrective is in [25](25-knowledge-pipeline.md) §2.1: probes replaced the
driver, embedder and cross-encoder with stubs that **raise**, so "it never
touches a database" became a claim that fails loudly instead of an inference from
a type. **But running once was not enough either** — the predicate-collapse
result was a single sample of a stochastic LLM call reported as a table; re-run
five times it reproduced 3/5 and once false-merged the control. CLAUDE.md already
carried both rules; the node diagnosing instrument blindness broke them while
investigating a library.

2026-08-27 — **Layer 6 resolved**, and the shape of the fix is the finding. The note tier ships THREE counters, not ten ([27](27-note-dreamer.md) §6.1): four were already carried by `nora_tool_calls_total`, and four measure adaptors that do not exist — so shipping all ten would have closed Layer 6 by **manufacturing a declared name with no call site**, which is the very next failure in this family. Closing an instrument gap is a decision about which instruments are real, not a build order. `nora_fact_entity_unresolved_total` ([21](21-substrate-contract.md) §6.4) is that failure and stands open.

**This entry originally said "Layer 7" and was wrong within the hour** — it was written against a file where the next free number was 7, and a parallel branch took 7 for the spans gap. Two sessions appending to one numbered list is the DEC-number collision one file over, and git merged both cleanly because they touched different lines. **Cross-reference a layer by its shape, not its ordinal**; the ordinal is only stable inside the commit that reads it.

2026-08-24 — Six layers when written. **Now eight**: `nora_fact_entity_unresolved_total` has no call site ([21](21-substrate-contract.md) §6.4), and the note tier declares ten metrics and ships zero ([27](27-note-dreamer.md) §6). The pattern kept finding new instances as later nodes were written, which is the finding.
