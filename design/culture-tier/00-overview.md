# The Culture Tier — overview

> 2026-10-28 (operator, ruled; [unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md#9-profiles-are-reshaped-in-the-loop-from-the-graph)): the `culture` adaptor retires. A room's or unit's culture is reshaped by step 2 of the one Cognition loop whenever a member's or the room's relations change; its readers keep reading the same store.

## 0. What this bundle became

The first version of this bundle proposed a new tier with its own store, its
own derivation, a three-section body under a CHECK constraint, and two
synthesizers. Almost all of that is now deleted. What survives is much smaller:

> **A Dreamer adaptor that synthesises a short account of one room across the
> stores Nora already holds, on a slow clock, keeping every answer.**

That sentence has been wrong twice and the second time is worth more than the
first. It read *"asks the memory system a question it can already answer"*
until §4 falsified it: Honcho does not scope at the layer this tier needs, so
the tier writes the account ITSELF rather than storing someone else's
(DEC-182). It then read *"folds its own room's messages"*, which is narrower
than what was decided — **DEC-182 killed the cache shape, not the use of
memory** (`03-decisions`, DEC-182 as narrowed 2026-09-17). The room's batch is
one input among several; `01-the-adaptor §1` and `§1.2` name the rest. What is
unchanged through both corrections is the clock, the chain, and the fact that
nothing retires.

The path to that is worth recording, because two of the three corrections came
from the operator catching a category error, and the third came from a probe
that falsified this document's own first draft.

## 1. What was measured, 2026-09-12/13

Read live off norty. **Reset-aware over the full ~4 days Prometheus retains**,
60s step, summing positive deltas and treating a counter drop as all-new — the
first draft of this document used an *instant* query and was wrong because of
it (§1.1).

| series | total |
|---|---|
| `nora_relevance_gate_total{verdict="no_go"}` | ~148 (discord 67, signal 81) |
| `nora_relevance_gate_total{verdict="go"}` | **>= 2** |
| `nora_relevance_gate_errors_total{kind="call_failed"}` | 13 |
| `nora_participation_spoke_total` | 1 (signal) |

**Admission is under 2%.** The exact numerator is not settled and the table
should not be read as though it were — see §1.1, which is now about two
errors rather than one.

### 1.0 Re-taken 2026-09-16 — step 0

Retention is **7 days**, not the ~4 the table above assumed. Raw samples
(`query?query=metric[7d]`), reset-aware, and `up{job="nora"}` checked: 39 failed
scrapes in 20,160, no gap wider than the scrape interval, so clause 3 holds.

The window is split at the image build (`nora:latest`, 2026-09-16 07:24 UTC),
because #215, #261 and #262 all reached production there. A sample straddling
the boundary is charged to PRE, so POST is a lower bound.

| | pre-deploy (6.9d) | post-deploy (3.1h) |
|---|---|---|
| `go` | 3 (all signal) | 0 |
| `no_go` | 273 (discord 153, signal 120) | 12 |
| **admission** | **1.09%** | n=12, says nothing |
| `errors{kind="call_failed"}` | 14 | 0 |
| `participation_spoke` | 1 | 0 |
| `participation_silence` | no series | no series |

**The floor is confirmed and is not an artifact of the first window.** 3 in 276
over seven days against "1 in 149" over four: the same rate, measured twice,
across a doctrine unchanged between them.

**The participation turn has still run exactly once, ever** — 2026-09-11 11:54
UTC, the route question. Five days of nothing since.

**And the instrument itself is failing, which is new.** The `no_go` counters
reset **19 times** in 7 days (discord 12, signal 7) — the process restarts
2-3x a day — and `process_start_time_seconds{job="nora"}` does not exist, so
restarts are visible only as those resets. Prometheus labels lazily, so the
`go` series is **destroyed at every restart and only re-created by the next
`go`**: its last sample is 2026-09-11 11:54 and it has been absent for five
days. So `go = 3` is a lower bound whose error this instrument cannot bound,
and §1.1's three clauses do not rescue it — they make the loss visible, not
recoverable.

That is a second, independent argument for the per-verdict durable row §`02-
measurement 3` says `override` needs and DEC-179 excluded: **at this restart
rate a counter cannot hold a rate at all**, let alone a timing.

**The gate was in `shadow = true` for the first ~10 hours of this window**
(`config.toml` flipped 2026-09-09 17:10 UTC). While shadow is on, a `go`
short-circuits and **no turn runs at all**. So the admissions before that
point could not produce a turn, and one of them is recorded in Prometheus as
a *failed send* (`stage="relevance_gate_shadow"`).

An earlier draft concluded from the absent `nora_participation_silence_total`
series that "she has never been admitted and then chosen silence, so
`silence.md`'s healthy default has never been exercised." **That conclusion is
withdrawn.** The absence is explained by configuration, and the number of
admissions on which silence was even reachable is 1 — a doctrinal claim drawn
from n=1, which is the thing `CMS.md` exists to forbid.

The one `go`, in full:

```
08:42:43  a member, untagged:  <asks how to get somewhere by 10:00 tomorrow>
08:43:17  GATE:                go
08:43:57  Nora:                <a correct route with times and a departure time>
```

2026-09-11. The ambient loop worked end to end, once.

### 1.1 The correction this document exists to not repeat

The first draft of §1 claimed *"the `go` counter has never been incremented"*
and *"the participation turn has never run."* Both false. Both series were
incremented on 2026-09-11 morning and vanished at a process restart, because
Prometheus labels counters lazily and the new process never re-created a label
it had not yet used. An instant query 19 hours later read absence-of-series as
never-happened.

**And the replacement was wrong too, by the same class of error.** The second
draft said `go` = 1. It is at least 2: a sample lived under 30 seconds inside a
46-minute hole, so a step-60 grid point falls either before it or after its
staleness marker and positive-delta summation adds nothing. The same aliasing
hid a 2→1→2 reset on `call_failed`, which is 13 rather than 11.

So the rule this section exists to state needs all three clauses, not one:

1. **Raw samples, not a grid** — `query?query=metric[5d]`, because
   `query_range` at any step can step over a sample shorter than the step.
2. **Reset-aware summation**, treating a drop as all-new.
3. **Prove the target was scraped during every absence** — check `up{job=...}`.
   Absence of a series means zero increments only if the scrape was alive.

Clause 3 does hold here (`up` is continuous over the window bar 11 failed
scrapes) but the earlier drafts did not check it, and without it "absence =
zero" is the exact inference this section condemns.

Related but distinct: `nora_relevance_gate_errors_total` is counted
*separately* and fail-closed refusals are **not** in the 149
(`relevance_gate.py:707-717`), so 149 is verdicts, not evaluations.

## 2. Why 0.67% is not a tuning problem

`participation.md` v5 is a taxonomy — five `go` shapes and a `no_go` list —
read by a model configured `reasoning_effort = "none"`, `max_tokens = 512`,
against up to 40 rows of real chat.

A reasoning-less model handed a taxonomy pattern-matches; a pattern-matcher
handed ambiguity takes the safe label. The operator's framing:

> 其實你係冇可能 define Nora 幾時去介入。

So stop defining it. A window judged against nothing is judged in a vacuum —
"nobody picked this up" means nothing until you know whether this room normally
picks things up. **A baseline is what turns a window into a deviation**, and a
deviation is readable by a model with no reasoning budget, because the
reasoning already happened offline.

That is the reconciliation between `reasoning_effort = "none"` and the
judgement we want: the reasoning moves out of the model.

Two existing issues carry the rest of this and are not restated here: #163
(the same admission measurement, taken 2026-09-10), #187 (the gate instruction
has no non-degeneracy floor — consolidated from #194), #216 (**n=5, never re-taken; CLOSED 2026-09-16 by
PR #262** — when the turn *did* run it false-spoke on 4 of 5 silence twins, and
R-97 now refuses the reply when every three-state lookup came back non-ok. The
reason it is still cited is the reason it was cited: a fault this size went
unseen because the measurement did not exist, which is the argument for fixing
measurement before doctrine — not the count itself, which was n=5).

## 3. Chronicle is not culture

The memory system already models entities. It was not obvious why that is not
enough, and the operator's analogy is what made it precise:

> 你唔會同我講，中國漢朝原來「漢人」呢一個 term 係會 retire 同 sunset 嘅，唔會嘛?

Honcho's `representation` returns exactly one shape, verified live:

```
## Explicit Observations
[2026-09-12 16:30:59] <peer> responded with the message 'GFS'.
[2026-08-30 20:11:21] <peer> wrote the message 'try try try again'
```

`[when] what happened`. Every row pinned to an instant, every row independent
of every other. That is a **chronicle** — and a chronicle of dynasties and
their dates is not a culture. The format has no slot for continuity or
causation, so 「漢人」 — a term that outlived its dynasty, meant something
different each century, and is *why* people still say it — cannot be expressed
in it. `times_derived` ranks by frequency, and frequency is not continuity.

Culture is an **account**: what this is, and why it became so.

### 1.2 What is borrowed rather than this bundle's own work

Two numbers quoted here and in `04-phase-two-note.md` — the +0.35 on a room
assigning her a role, and the n=20 noise band behind `02-measurement §6`'s
price — were read off **#215**, a separate branch that gives the participation
turn memory. That work is not this bundle's, and resolving and measuring it was
a detour from this line.

**And it is evidence of something other than what this bundle needs.** The
"with memory" arm is a `FakeMemoryStore` replaying seeded literal strings
(`harness.py:508,531`); neither `representation` nor `chat` was called. The
`admin-joke` seed is a flat pre-stated fact — *"the room jokingly calls nora
the channel's 管理員"* — and the transcript already contains 管理員 three
times. So it is neither a chronicle nor an account, and no arm compared the
two.

The honest reading, which the earlier draft did not state: **the number sets
this tier's bar rather than supporting it.** A one-line flat fact delivered in
~0.2s already reaches 0.60 on the flagship capability. Culture proposes a
daily model fold over a room's batch, a store, a revision chain and an adaptor
against that baseline. The arm that would settle it — chronicle-only versus
chronicle-plus-culture — does not exist in any suite, and `02-measurement`
should propose it.

It also falsifies one sentence in `04-phase-two-note.md`: the 秘書 case, as
seeded, **is** a lookup, and a same-window one.

## 4. The memory system cannot scope an account — and does not need to

**An earlier version of this section claimed `chat` with `session_id` gives a
room-scoped account, and built the tier on it. That is false.** The measurement
behind it landed on the wrong question: asked *"how many distinct people have
spoken in this room"*, `chat` answers 7 / 1 / 2 correctly per room — because
that is answerable from the session's MESSAGES. A culture account is a question
about CONCLUSIONS, and conclusions are workspace-wide.

Falsified directly, 2026-09-13: `chat` scoped to a probe session holding two
messages answers freely about people appearing nowhere in it. `search` is worse
— scoped to that same session it returns six hits from other rooms, byte-identical
to the unscoped query. **Neither endpoint honours session scoping for the layer
this tier needs**, and there is one Honcho workspace behind all of Nora's rooms
(`_workspace_for` returns the single default; no router is wired). *(True when
written. Since 2026-09-28 (DEC-214) each Nora workspace has its own Honcho
workspace and each unit is a Honcho NAMED scope that every read passes — a
unit boundary Honcho does honour; a ROOM boundary still does not exist there,
which is what this tier's argument rests on.)*

So there is no room boundary in Honcho, and no workspace boundary either.

### 4.2 Re-measured 2026-09-16 — the workspace boundary DOES hold

**Narrow the claim above.** "Honcho does not scope" is too strong and this
section's own falsification only ever tested the SESSION boundary. Honcho
3.1.2 (the deployed instance) exposes a workspace-level dialectic path,
`POST /v3/workspaces/{workspace_id}/chat`, and it was probed with a positive
control:

| arm | result |
|---|---|
| `nora` (holds every room) | **2,586 chars** — an account: room self-naming, its origin year, register, how decisions get made |
| `ridgeline` (holds nothing) | **`NOTHING-KNOWN`**, 13 chars, as instructed |

The positive control passes and the empty workspace declines, so the boundary
is **enforced, not hoped for**. The correct statement is therefore:

> **Honcho does not scope BELOW the workspace.** Session scoping leaks
> (§4, unchanged); workspace scoping holds.

Latency re-measured on the same path: **16.6 / 17.9 / 17.4 s**. Still far over
the gate's 20s ceiling once a real prompt and a cold path are added, so
§4.1's per-window-is-impossible conclusion is untouched and this stays a
stored tier either way.

**What it opens, and what it costs.** One Honcho workspace per room would make
`/chat` hard-scoped by construction, which would restore the route DEC-182
withdrew — and that route was BETTER on §5's axis, because storing Honcho's
answer means there is no second interpreter, while DEC-182's fold creates
exactly one. Against that: `_workspace_for` returns the single default and no
router is wired; `design/24 §4` says workspace-wide recall is deliberate
(*"recall is workspace-wide ON PURPOSE"*), so splitting per room fragments the
person model across rooms — a real loss, not a migration detail; and the
existing conclusions would have to be moved.

**And one thing is free today, with no routing change at all.** At the current
single-workspace binding this endpoint already yields a WORKSPACE culture
account. `01-the-adaptor §7` deleted the workspace half on the grounds that
re-deriving workspace-wide recall is duplication — this path means it need not
be re-derived at all.

**OPERATOR DECISION — TAKEN 2026-09-17.** Not "route a Honcho workspace per
room", and not "keep the fold and ask nothing". **Synthesise across the stores,
including memory.**

The tier is a Dreamer adaptor, and what a Dreamer adaptor is for is deriving
across the stores Nora already holds rather than restating the one corpus it
was handed (`design/dreamer-agent-loop/03-no-agent-loop §7`). So the room's
batch is one input among several — with the account written last time, the
room's `standing`, what the room has open, and what memory holds about the
people in it (`01-the-adaptor §1.2`). DEC-182 is narrowed accordingly: it
killed the CACHE shape — ask Honcho once, store the answer, do nothing else —
and not the use of memory at all (`03-decisions`, DEC-182 as narrowed).

Per-room Honcho workspaces stay unrouted, and for the reason this section
already gave: `design/24 §4` makes workspace-wide recall deliberate, splitting
it per room fragments the person model, and the existing conclusions would have
to be moved. None of that is required by the decision above, because the fold
is the author either way — what changes is only what the fold is allowed to
read.

### What that changes: the tier stops asking Honcho

Nora already holds every room's messages in Postgres keyed by
`conversation_id` — **hard-scoped by construction** — and a Dreamer adaptor is
handed exactly that batch via `record.since(conversation_id=key,
after_id=watermark)`. The account is written by the Dreamer's own model from
that batch.

The chronicle-versus-account distinction in §3 still stands and is still the
point. What was wrong was attributing it to Honcho: **producing an account is a
property of asking a model to write prose, not a property of the dialectic
endpoint.** The Dreamer has a model and a loop already, and it reads a corpus
that is actually scoped.

Three consequences, all simplifications:

- **No new tool.** `ask_room_memory` / `ask_workspace_memory` were built and
  then closed unmerged (#237) once the path was falsified.
- **Workspace culture comes back.** Fold the messages of one workspace's rooms
  and the boundary is DEC-105's own, enforced in our schema rather than hoped
  for in someone else's API.
- **The watermark is a cursor again, not a gate** (`01-the-adaptor §4`): the
  adaptor now reads its batch, so `batch_limit` must be sized for it rather
  than pinned at 1.

## 4.1 What the dialectic probe still supports, and what it no longer does

Measured 2026-09-12, live, against the deployed instance. `chat` — the
dialectic endpoint, an LLM inside Honcho — answers a culture question with
prose carrying causation, self-naming with its origin, register, how decisions
get made, and Nora's own standing in the room. Three real rooms each returned a
*different* account, each leading with a different thing (register / identity /
practice).

**What that measurement supports is an existence claim about the SHAPE**, and
that is all: an account — causation, continuity, a room naming itself — is a
thing a model will write when asked for prose. It does NOT support routing this
tier through that endpoint, because §4 falsified the scoping the routing rested
on, and the "different per room" reading above is the same unsound inference as
the speaker-count one: different sessions, different answers, with nothing
proving the difference came from a room boundary rather than from the prompt.

The latency table, which the earlier draft used to argue the tier is a cache:

| | shape | latency |
|---|---|---|
| `representation` | chronicle | ~0.2s |
| `chat` (dialectic) | **account** | **17–30s** |
| gate timeout | | 20s |

**The conclusion drawn from it — "the tier is a cache rather than a model" — is
withdrawn.** The tier writes its own prose now, so it IS a model, and the
argument for a slow clock is not a cache miss but a production one: one fold a
day over a room's batch instead of a model call per window (`01-the-adaptor §5`).
The per-window/per-day gap still holds for any inline derivation of an account,
which is why this remains a stored tier rather than something the gate computes.

## 5. What that settles

> **SWEPT 2026-09-17 — two of the three paragraphs below were authored in
> `db71b8a6` (2026-09-13 02:13), the Honcho-cache draft, and were not touched
> by `f856cccf` 19 hours later or by `6691daa1`'s sweep. They are the seventh
> and eighth sites of `03-decisions` correction 13.** They are corrected in
> place below rather than deleted, because the arguments are still the right
> arguments — it is their conclusions that belonged to the withdrawn route.


**No second model of the room.** The earlier draft built one, and the operator
named the danger: a room/workspace-scoped re-invention of `representation` and
the peer card. Two systems accumulating an evolving model of the same entity
from the same messages is CMS's *interpret twice and two copies drift* — and
here both would be prose, both injected, both about the same room, with no way
for the reader to know which to believe.

**The last sentence of this paragraph said "storing Honcho's own answer removes
the second interpreter entirely". That is dead text and its conclusion is
withdrawn**: the tier writes its own prose now, so DEC-182's fold *creates*
exactly one second interpreter, which §4.2 states and this paragraph was never
updated to admit. The danger the operator named is therefore real and unpaid
rather than removed, and what actually answers it is not an absence — it is
that the two accounts are not the same shape and not written from the same
question: `representation` is a chronicle of instants (§3), the culture body is
an account with a `written=` date on its block (`01-the-adaptor §5`), and a
reader who can see which is which is not being asked to choose between two
copies of one claim. That argument has never been measured, and the arm that
would settle it is `02-measurement §6`.

**Nothing retires.** Culture is not `standing` and not a `note`. It has no
`closing_condition`, because it is never settled; no `retired_at`, because it
does not stop being true; no TTL, because a quiet room's culture is not stale,
it is *accurate*. A revision's parent link means "the same thing, earlier" —
not "superseded". The old name does not become false; it becomes history, and
the history is part of what the new name means.

**The chain is the evolution.** A single model call reasons from scratch and
has no memory of its last answer, so one call is a snapshot. Keeping the
snapshots in order is what gives continuity, and that is the tier's one genuine
contribution — it is why a store exists at all.

(This paragraph read *"Honcho gives snapshots"* until 2026-09-17. The property
is a property of any single call, not of that endpoint; the fold is the caller
now, and the adaptor's own `inputs` renders the previous account FIRST for
exactly this reason — `nora/language/culture.py:193`.)

## 6. Scope, honestly

> **SWEPT 2026-09-17 — the ninth site of `03-decisions` correction 13.** This
> section was authored in `db71b8a6` (2026-09-13 02:13) and reads the whole
> question as "what would this tier RE-INVENT". After DEC-182 that is the wrong
> axis for two of its three rows: the question is no longer whether another
> store holds the thing, it is whether this tier READS the store that holds it.
> A thing another store holds and this adaptor never reads is not
> re-invention — it is an input the design forgot to name
> (`01-the-adaptor §1.2`).

Three things the first draft claimed as new are the memory system's already:

| claimed | actually | as of 2026-09-17 |
|---|---|---|
| workspace culture | workspace-wide recall already is this | **withdrawn.** DEC-182 restores the workspace half — *"fold one workspace's rooms together and the boundary is DEC-105's own"* — and §4.2 finds a workspace account free at today's single-workspace binding. Not built; not duplicative either |
| her own standing in a room | her self-model, already deriving (`observe_me=true`) | **true and beside the point.** The per-room `standing` tier holds it, `relevance_gate.py:266-272` calls it the only authority on it, and this adaptor reads neither — while `01-the-adaptor §2` asks the model for it in prose. An input, not a re-invention |
| per-room knowledge of a person | `session_id` on `representation` — one parameter, works today | stands, narrowed by correction 12: `session_id` scopes MESSAGES, not conclusions |

The client omits `session_id` deliberately (*"recall is workspace-wide ON
PURPOSE"*, design/24 §4). That is a Nora decision, **not a Honcho limitation** —
this bundle's first draft and one of its reviewers both asserted the limitation
and both were wrong.

What is genuinely absent is only this: **an account of the room, kept over
time.** Everything else in the first draft was re-invention.

## 7. Two phases

| | | measurable |
|---|---|---|
| **Phase 1** | she supplies something she holds | yes |
| **Phase 2** | she is a member of the circle and can take a role the room gives her | uptake only |

The boundary is a measurement boundary, which is why the order is not
preference — Phase 1's instruments are the only thing that could tell you
whether Phase 2 works. "Proactive" is a third thing on a different axis
(actively pushing the room forward) and Phase 2 should not borrow its name.

See `04-phase-two-note.md`, deliberately not executable.
