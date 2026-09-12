# The Culture Tier — overview

## 0. What this bundle became

The first version of this bundle proposed a new tier with its own store, its
own derivation, a three-section body under a CHECK constraint, and two
synthesizers. Almost all of that is now deleted. What survives is much smaller:

> **A Dreamer adaptor that asks the memory system a question it can already
> answer, on a slow clock, and keeps every answer.**

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
| `nora_relevance_gate_total{verdict="no_go"}` | **148** (discord 67, signal 81) |
| `nora_relevance_gate_total{verdict="go"}` | **1** |
| `nora_relevance_gate_errors_total{kind="call_failed"}` | 11 |
| `nora_participation_spoke_total` | **1** (signal) |
| `nora_participation_silence_total` | **no series** |

**One admission in 149 verdicts — 0.67%.** And on that one admission she
spoke, so she has never once been admitted and then chosen silence:
`silence.md`'s "healthy default" has never been exercised on this path.

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

That is precisely the failure `CMS.md` records and that this bundle quotes:
bind to the real property, never a proxy. The proxy was "is there a series
right now". The property was "has she ever spoken". **Any claim about a
counter in this repo needs a range query and reset-aware arithmetic**, and the
numbers above are stated with their method for that reason.

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
(the same admission measurement, taken 2026-09-10), #194 (the gate instruction
has no non-degeneracy floor), #216 (when the turn *does* run it false-speaks on
4 of 5 silence twins — the strongest reason to fix measurement before doctrine).

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

## 4. The memory system can already produce an account

Measured 2026-09-12, live, against the deployed instance. `chat` — the
dialectic endpoint, an LLM inside Honcho — answers a culture question with
prose carrying causation, self-naming with its origin, register, how decisions
get made, and Nora's own standing in the room. Session-scoped, three real
rooms each returned a *different* account, each leading with a different thing
(register / identity / practice).

**So this tier does not need to derive anything.** It needs to ask.

The reason it cannot simply be called inline:

| | shape | latency |
|---|---|---|
| `representation` | chronicle | ~0.2s |
| `chat` (dialectic) | **account** | **17–30s** |
| gate timeout | | 20s |

Per-window is impossible. Per-day is nothing. **That gap is the entire reason
a tier exists**, and it makes the tier a cache rather than a model.

## 5. What that settles

**No second model of the room.** The earlier draft built one, and the operator
named the danger: a room/workspace-scoped re-invention of `representation` and
the peer card. Two systems accumulating an evolving model of the same entity
from the same messages is CMS's *interpret twice and two copies drift* — and
here both would be prose, both injected, both about the same room, with no way
for the reader to know which to believe. Storing Honcho's own answer removes
the second interpreter entirely.

**Nothing retires.** Culture is not `standing` and not a `note`. It has no
`closing_condition`, because it is never settled; no `retired_at`, because it
does not stop being true; no TTL, because a quiet room's culture is not stale,
it is *accurate*. A revision's parent link means "the same thing, earlier" —
not "superseded". The old name does not become false; it becomes history, and
the history is part of what the new name means.

**The chain is the evolution.** A single `chat` call reasons from scratch and
has no memory of its last answer, so one call is a snapshot. Honcho gives
snapshots; keeping them in order is what gives continuity. That is the tier's
one genuine contribution and it is why a store exists at all.

## 6. Scope, honestly

Three things the first draft claimed as new are the memory system's already:

| claimed | actually |
|---|---|
| workspace culture | workspace-wide recall already is this |
| her own standing in a room | her self-model, already deriving (`observe_me=true`) |
| per-room knowledge of a person | `session_id` on `representation` — one parameter, works today |

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
