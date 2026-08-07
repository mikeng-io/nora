---
kind: spec
title: "Reminders + heartbeat — a deferred message, re-decided at fire time"
description: "Design for Nora's first unprompted-speech path: a cron-shaped heartbeat loop, a one-shot reminders table, and an injector that replays a held sentence into the ordinary agent pipeline. Establishes that a reminder is a deferred MESSAGE (not stored text to echo), that the fire turn RE-DECIDES rather than plays back, and that silence must be a tool call rather than an absence."
tags: [nora, spec, reminders, heartbeat, proactive, honcho, doctrine]
timestamp: '2026-08-08'
status: draft — awaiting review
derives_from:
  - design/01-ambient-ingest.md
  - design/09-decision-index.md
  - design/11-recall-and-threading.md
  - design/12-prompt-tool-skill-boundaries.md
  - design/proposed/19-presence-collector.md
supersedes: "the `heartbeat ↔ note ↔ reminder` bullet in .claude/.../memory/future-ideas-backlog.md"
---

# Reminders + heartbeat

## 0. What this document is

The design settled in the 2026-08-08 brainstorm, written down before implementation.
It covers Nora's **first unprompted-speech path**: until now she has only ever
spoken in response to a tag.

It resolves three things the backlog left open:

1. **`note()` is dissolved, not deferred.** See §1.
2. **A reminder is a deferred *message*, not stored text to echo.** See §2.
3. **The fire turn re-decides; it does not play back.** See §6.

**Scope note.** `heartbeat` here is a scheduling loop. It is *unrelated* to the
existing `nora.obs.logging.heartbeat`, which is a 60-second counters log line.
Same word, different job. If the collision proves confusing in review, this one
renames — the log line has seniority.

---

## 1. What is NOT being built, and why

### 1.1 `note()` — dissolved

The original backlog entry paired `reminder` with a `note()` scratchpad: Nora
writing things down for herself, unprompted.

It is not being built, and the reason is not "later" — it is that **its consumer
does not exist and its content belongs elsewhere.**

- What separates a scratchpad from Honcho is **lifecycle**: Honcho remembers what
  *was true*; a scratchpad holds what is *unfinished*. Honcho has no
  representation of "still open."
- But everything Nora would actually write to herself is either *a fact about a
  person* (Honcho's `about` / `ask_memory`) or *a thing due at a time* (the
  reminders table below). Nothing was left over.
- The operator's own recall topology — `about()` (representation),
  `search_memory()`, `ask_memory()` — already covers the fuzzy side, and
  `search_memory` shipped in `21f9712` (DEC-68).

**The test applied:** name one thing Nora should write to herself that is
neither a fact about a person nor a timed reminder. Nothing survived it.

Delete the `note` half of the backlog entry rather than leaving it as phantom
scope.

### 1.2 Recurring reminders — not in v1

A recurring row *is* the runaway shape. One-shot only; see §8.

### 1.3 A `list_reminders` tool — replaced by prefill

See §7.3. She needs the pending set on every turn, not on request.

---

## 2. The core idea

> **A reminder is a deferred message.** The row stores *the requester's sentence*,
> and at fire time it is delivered to Nora as if they had just said it.

This is the whole design, and everything else follows from it.

The alternative — a structured payload describing an action to perform — was
rejected against a single test case: 「幫我睇下 Tyler 喺邊」. That reminder's
payload is not text to send; it is an *instruction requiring a tool call at fire
time*. A payload schema would have needed action types, a dispatch table, and a
new entry per capability.

Storing the sentence needs none of that, and gives a property worth stating
explicitly:

**Every capability Nora gains later is automatically remindable.** `route`,
`transit`, `search_memory`, whatever comes next — nothing to wire.

Consequence for §6: because the row is a *request*, not a *message*, the fire
turn is an ordinary agent turn. There is no second way to speak.

---

## 3. Architecture — three parts, deliberately separate

| part | knows | does not know | owns |
|---|---|---|---|
| **heartbeat** | that time has passed | why it matters | tick cadence, liveness, crash recovery, "already fired?" |
| **reminder row** | why it matters | that time has passed | text, target, requester, `due_at`, state |
| **injector** | how to make a turn | either of the above | building the synthetic inbound event |

Neither of the first two is useful alone: a row with no loop is never read; a
loop with no rows ticks forever doing nothing.

**Nora never touches the tick.** There is no tool to start, stop, skip or
schedule it, and there must not be. The tick is infrastructure in exactly the
sense the Signal listener's receive loop is infrastructure — she does not decide
to receive messages either. Her only two decisions are *whether to write a row*
(request time) and *what to do when rows are handed to her* (fire time).

The precedent for the loop's shape is `nora/presence/collector.py`: sleep-and-
repeat, its own liveness, dedup by state rather than by memory.

---

## 4. Data — one table

```
reminders
  id                       uuid pk
  requester_person_id      text not null      -- identity.toml person_id (DEC-14)
  target_conversation_id   text not null      -- where the reply goes
  text                     text not null      -- the requester's own sentence
  created_at               timestamptz not null
  due_at                   timestamptz not null
  state                    text not null      -- pending | firing | done | cancelled
  fired_at                 timestamptz null
```

Index on `(state, due_at)` — the tick's only query.

**One-shot.** A row fires once, ever. Double-firing is impossible by state
machine, not by care.

**`state` is a CHECK-constrained enum.** Note for whoever writes the migration:
the schema gate is `tests/infra/test_schema_parity.py`, **not** `alembic check`,
which is blind to CHECK constraints (CLAUDE.md).

---

## 5. The loop

```
every [heartbeat] tick_seconds:
    rows = SELECT * FROM reminders
           WHERE state = 'pending' AND due_at <= now()
    if not rows:
        continue                      # the overwhelmingly common case
    mark rows 'firing'
    inject ONE batched turn (§6)
    mark rows 'done' after successful egress
```

**Placement: in-process**, via `BackgroundTasks.spawn` (`nora/app/runtime.py:41`)
— *not* a separate daemon. The presence collector is a separate process precisely
because it must never touch the agent (DEC-55). This loop must. The reasoning
that separated one argues for keeping the other joined.

**Boot recovery:** rows left in `firing` by a crash are swept on the first tick
after start — either re-queued or reported. The state column is what makes this
possible; an in-memory timer would simply lose them.

### 5.1 The timing contract

> A reminder fires **at or after** `due_at`, **never before**, within **one tick**.

"Never early" is the invariant. Early is *wrong*; 40 seconds late is just a
reminder.

Resolution is a free config knob — the query is one indexed lookup on a Postgres
already running, so `tick_seconds` can be tuned from 60 down to 15 with no code
change and no meaningful cost. `reminder` is an abstraction over the tick, and
**the tick is the resolution floor**; publishing that floor is what keeps the
abstraction honest.

Sleep-until-next-due (sub-second accuracy, near-zero wakeups) was considered and
rejected for v1: it needs an `asyncio.Event` to interrupt a long sleep when a new
row is written, plus a max-sleep cap so a missed signal or clock jump cannot
strand a row. That is a state machine where a `while True` suffices. If precision
is ever wanted, lowering `tick_seconds` buys it with zero new code.

### 5.2 Batching

All rows due in one tick become **one** turn, not one turn each.

At realistic volume the count is 0 or 1 almost always, so batching degenerates to
the simple case; on the rare tick with two, the result is one coherent message
rather than two robots talking over each other.

**The heartbeat owns the state, not Nora.** She is never given a "mark done"
tool. If she is handed three and mentions two, that is a voice failure — visible
in the transcript — not a state corruption that re-fires tomorrow.

---

## 6. The fire turn

The injector builds a synthetic inbound event: **the input channel is virtual,
the output is real.** She receives on an internal channel; she replies into the
actual conversation named in the row.

Because the event is shaped like an ordinary inbound message, every existing
stage applies unchanged — `policy.toml` gating, the per-conversation engagement
queue (which serialises the fire turn against live traffic), conversation-bound
tool scope (DEC-21), `LeakGuard` / R-18 on egress. **No new egress path is
built.** The reminder text is model-adjacent content and is not trusted.

The turn receives:

- the sentence
- when it was set, and when it was due
- the ordinary tool set, plus `stand_down`

and it **re-decides**: here is what you were asked to do, here is the time — act,
or let it pass. That distinction is what makes this a friend rather than an alarm
clock, and it is the reason option "verbatim echo" was rejected.

### 6.1 Silence must be a tool call

`nora/agent/tool_choice.py` records the measurement that forces this:

```
FIRST_CALL = "required"
A. auto, fresh question    plain text 5/6, called a tool 1/6
```

Under `auto` on a turn's **first** model call the model answers in prose — not a
tool call, therefore not a message. **Nora silent in the group while the turn
looks successful.** The module's own comment names that as R-5's exact failure
shape.

So "she decides not to speak" **cannot** be implemented by relaxing
`tool_choice`. It is implemented as a terminal verb:

`stand_down(reason)` — satisfies `required`, marks the rows handled, emits a
metric, sends nothing.

This is strictly better than a real silence: *she chose not to speak* becomes
countable and **distinguishable from a crash**, which is precisely what R-5
exists to guarantee.

### 6.2 Staleness — checked at fire time, not at message time

The motivating case: the requester says 「唔洗洗衫喇」 **without tagging Nora**.
The reminder should not fire.

She already has that message — Nora is ambient (design/01); every message is
ingested whether or not she is tagged. She simply does not *speak* unprompted.

**The check happens at recall time, not at hear time.** The alternative —
evaluating every ambient message against every pending reminder — would mean
running the model on untagged messages, which she has never done, at a cost of
one model call per group message to find nothing ~99.9% of the time. It would
change what she is.

The check uses **both** recall verbs, **concurrently** (the orchestration
doctrine already permits batched concurrent tool calls; the shape is the same as
`saved_places` + `about` in the place resolver):

| verb | reads | catches |
|---|---|---|
| `search_memory(the reminder's own words)` | **raw messages**, semantically | 「唔洗洗衫喇」 verbatim — immune to derivation loss |
| `ask_memory("does X still intend to …?")` | **derived** reasoning | 「聽日先啦」 — which moves the reminder rather than cancelling it, and matches no keyword |

Cost is `max(latency)`, not the sum — and **at fire time nobody is waiting.**
This is why the measurement that ruled dialectic out of the per-turn prefill
(6.9–8.3 s against a ~5 s budget, design/11) **does not bind here**: that is a
*conversational* budget, and this turn has no human staring at a typing
indicator.

**Precedence rule, for when the two disagree: `search_memory` is evidence,
`ask_memory` is opinion — evidence wins.** A raw message that contradicts the
reminder outranks a derived conclusion that does not. Same exact-beats-fuzzy
ordering as the Honcho/Nora boundary rule.

**No transcript is injected. Ever.** An earlier draft fed "messages since
`created_at`", which is unbounded by construction — a reminder set seven days out
would have carried seven days of chat. `search_memory` makes the cost constant
and independent of horizon: one targeted retrieval finds the relevant message
whether it was said ten minutes or five days ago.

### 6.3 Overdue

No grace-window constant and no config flag. She is handed the due time and the
recall verbs, and judges: still useful → 「唔好意思, 呢個係七點提你嘅…」; moot →
stand down. A `voice.md` line, not a mechanism.

Note that 「睇下 Tyler 喺邊」 fired two hours late is **still correct** — she
re-runs `where_is` fresh. Only the timing is stale, never the answer.

---

## 7. The model-facing surface

Two new tools, one prefill block, one CLI.

### 7.1 `remind(when, text, target)`

Writes one row. Refuses — and says why — when the caps in §8 are hit, **or when
the requester or the target is not a mapped `person_id`** (§12.1). The mapped
check is a guard, not a doctrine line: it holds whatever the model decides.

### 7.2 `cancel_reminder(id)`

The explicit path — 「唔使提我洗衫喇」. Works with the ids already in front of
her (§7.3).

### 7.3 Pending rows in the **prefill**, not a `list_reminders` tool

The set is capped (§8) and usually empty, so it costs nothing on almost every
turn and a few lines when it does not. Three things this buys that a tool does
not:

1. She has it without deciding to look.
2. `cancel_reminder(id)` has its ids.
3. **Runaway becomes visible to her**, not buried in a table.

### 7.4 `stand_down(reason)`

§6.1. Registered for the heartbeat audience only.

### 7.5 `nora reminders list|cancel <id>` (CLI)

Outside the model entirely. `nora/app/cli.py` already exists.

**This is the actual safety valve** — the thing an operator reaches for at 2am.
Handing the model a cancel button does not protect against a misbehaving model.

---

## 8. Anti-runaway — structural, not model-dependent

| control | value |
|---|---|
| no recurring rows | v1 |
| one-shot state machine | a row fires once, ever |
| max pending per requester | `[reminders] max_pending_per_person` |
| minimum lead time | `[reminders] min_lead_seconds` |
| maximum horizon | `[reminders] max_horizon_days` |
| operator CLI | §7.5 |

Every one of these holds whether or not the model behaves.

---

## 9. Configuration

Ships **inert**, the pattern `[media]` / `[search]` / `[presence]` already set.

```toml
[heartbeat]           # the loop
enabled = false
tick_seconds = 60

[reminders]           # the policy
max_pending_per_person = 20
min_lead_seconds = 60
max_horizon_days = 30
```

### 9.1 No limits configuration — deliberately

An earlier draft proposed a heartbeat-specific `turns`. **Dropped**, and the
reasoning is worth keeping so it is not re-proposed:

- Conversational `turns` is **20** (8 → 20, operator, 2026-08-07), sized for
  "locate a person → read a place → describe an image → reply". A fire turn is
  `[search_memory ∥ ask_memory] → route → send_reply` ≈ 4–5. Nowhere near.
- `turns` is not the binding cap anyway; `config.toml`'s own comment records that
  `total_tokens` binds first on a heavy turn.
- **R-24 requires `turns` to be reachable** — `total_tokens >= turns × ~6124`.
  That is why 64k → 128k moved when 8 → 20. A second `turns` knob would need its
  own paired `total_tokens` or become dead config: the exact trap that tuning
  climbed out of.

Tool budgets likewise need **no change**: `search_memory` 4, `ask_memory`,
`route`, `send_reply` 3, `stand_down` 1 — a fire turn fits inside today's numbers
(doubled 2026-08-08).

*(Correction recorded to prevent a wrong inference downstream: `ToolRegistry` is
built **once per process** (DEC-10), not per turn. The per-turn seam is
`ReplyGuard`, which consumes `budgets()`.)*

### 9.2 `total_tokens` has NO headroom — measured, 2026-08-08

An earlier draft of this section said `total_tokens = 128000` "has large
headroom." **That is false**, and the correction matters enough to state at
length. See `2026-08-08-turn-budget-finding.md` for the full investigation.

Langfuse trace `0459c8b9cb9f61c0bf9566418ae26981` (2026-08-07), one
`invoke_agent nora`, nine generations:

```
input reported     129,543   (76.4% cache reads)
output               1,266
TOTAL              130,809   -> stopped at limit_total_tokens
finish             {"message": "", "finish_reason": "limit_total_tokens"}
```

Three consequences for this design:

1. **A real turn already hits the ceiling at ~9 cycles**, not 20. Per-call is
   ~14,400 measured, against the ~6,124 R-24 sized reachability with — off by
   2.4×. `turns = 20` is unreachable today; the token cap binds first.
2. **The cap counts cached tokens at full weight.** Only ~32k of that 130,809
   was *new* work (30,614 fresh input + 1,266 output); the rest was prompt-cache
   reads billed at a fifth the rate. The budget therefore penalises the exact
   mechanism that makes the turn cheap — the turn cost **$0.0074**.
3. **The failure mode is silence.** `message: ""`. The group got nothing.

A fire turn is `[search_memory ∥ ask_memory] → route → send_reply`, and
`search_memory` returns raw messages, so its input is *fatter* than a
conversational turn's. At ~15k/cycle it has roughly **8 cycles**, not 20.

**And it is worse here than on a tag.** A tagged human notices silence. A
reminder that dies this way is *invisible* — nobody was waiting for it. So
`stand_down`'s metric and an alarm on `nora_llm_stop_reason_total{reason=~"limit_.*"}`
are **load-bearing for this feature**, not observability nice-to-haves.

This design does not attempt to fix the budget — that is the finding doc's
scope. It records the constraint it is being built under.

---

## 10. Doctrine

**No new doctrine *kind* is needed — a new *audience* is, and the mechanism
already exists.**

Every doctrine file carries `applies_to` (`voice.md:5` is `[tag]`), and
`DoctrineStore.get(applies_to=…)` already filters and caches per audience,
honouring `"always"` (`nora/doctrine/loader.py:533–553`).
`nora/app/composition.py:228` merely hardcodes `applies_to="tag"`.

So the `heartbeat` audience is **frontmatter plus one call site.**

| file | change |
|---|---|
| `constraints.md`, `identity.md`, `voice.md` | `applies_to: [tag, heartbeat]` — she is still herself, still bound |
| `conduct.md` | `applies_to: [tag, heartbeat]` — after extracting `# Silence is the default` into its own `[tag]`-only file (§12.2) |
| **new** `reminders.md` | `kind: conduct`, `applies_to: [heartbeat]` |

**Named `reminders.md`, not `heartbeat.md`.** The doctrine is about handling a
deferred request. The loop is infrastructure she never sees; naming a doctrine
file after it would teach her a mechanism instead of a behaviour.

Two constraints on writing it, both machine-enforced:

- **DEC-29 — doctrine names no tools.** It says *"you may decide the request no
  longer applies, and let it pass"*, never *"call `stand_down`"*. The verb
  reaches her through the generated `capability` block.
- **`kind: conduct` is SHOULD-register.** The loader rejects absolute MUSTs in a
  conduct file (`_RFC2119_ABSOLUTE`).

### 10.1 `voice.md` additions (both audiences)

**Precision lives in the database; vagueness lives in the voice.**

The row must store an exact `due_at` — the tick has no other way to work. That is
an implementation detail and must never become the requester's problem.

- 「等陣提我洗衫」 → 「好, 等陣提你」 — **not** 「好, 19:47 提你」
- 「七點提我出門」 → 「好, 七點」 — precise ask, precise confirm

She picks a sensible time herself (a model is good at that judgment) and only
asks 「幾點?」 when there is genuinely no reasonable default.

**Rationale, recorded because it was the operator's correction:** a friend who
answers 「等陣提我洗衫」 with 「幾點? 15 分鐘定 30 分鐘?」 is not a friend, it is a
judge. Interrogating the requester to populate a column is the design leaking
through the persona.

She should still confirm *something* back, so a wrong guess dies immediately
rather than at fire time.

---

## 11. Phasing

**Phase 1 — everything.** Table, loop, injector, `remind`, `cancel_reminder`,
`stand_down`, prefill block, CLI, doctrine audience, staleness check.

The staleness check was originally phase 2, gated on `search_memory`. That
shipped in `21f9712` (DEC-68) during the brainstorm, so the gate is open.

**Later, not now:** recurring reminders; event triggers. The latter is worth
noting because it validates the abstraction — 「Tyler 返到屋企話我知」 is not
time-based at all, and it needs **only a different `WHERE` clause in the tick.**
Same table, same injector, same fire turn. That the pipeline absorbs a
non-temporal trigger without a rebuild is the main evidence it sits at the right
altitude.

---

## 12. Decisions taken (were open questions)

### 12.1 Mapped identities only — BOTH ends (operator, 2026-08-08)

**A reminder requires a mapped `person_id` at both ends: requester AND target.**
No unmapped sender may create one, even a group-targeted one; no unmapped person
may be a target.

This is stronger than the constraint that falls out of DEC-14 (a DM target needs
an `identity.toml` address, so *targets* were already mapped-only). The operator
extended it to the requester as well: **producer or consumer, mapped or nothing.**

Rationale: an unprompted-speech lever is exactly the wrong thing to hand to
someone the deployment cannot name. Deny-by-default, same posture as
`policy.toml` and the R-31/R-35 disclosure gate.

Enforcement is at `remind()`: if the requester resolves to no `person_id`, refuse
and say so. It is a guard, not a doctrine line — it must hold whatever the model
decides. Needs an R-rule and a named test (§13).

### 12.2 `conduct.md` — SPLIT, not rewritten (operator, 2026-08-08)

Read of the file settles it: **only the first section contradicts a fire turn.**

> `# Silence is the default` — "You're only reading this because someone called
> you. That's the only reason you ever speak. … once your turn is done you never
> follow up on your own reply."

Every clause is false when a clock fires: nobody called her, and a reminder *is*
a follow-up on her own earlier 「好, 七點提你」.

Everything from `# Replying` down applies to both audiences unchanged — and three
parts are load-bearing here, which shrinks `reminders.md` considerably:

- **"You have judgment — a friend, not a command line"** is already the doctrinal
  basis for standing down. *"You are not a thing that runs whatever it's handed"*
  generalises to a reminder whose premise has evaporated.
- **"Check before you answer"** already says *"or on something said further back
  than you were shown — find out first, then answer"* — nearly the staleness check.
- **"When one answer needs more than one look"** already says *"when you need two
  things that don't depend on each other, get them in the same breath"* — the
  concurrent `search_memory ∥ ask_memory` instruction, already written.

**Migration:** extract `# Silence is the default` into its own file at
`applies_to: [tag]`; `conduct.md` becomes `[tag, heartbeat]`.

**Why split rather than rewrite the section to be true for both audiences:** a
split assembles **byte-identical text** for the tag audience, so live behaviour
provably does not change and a prefill test can assert it. Rewriting edits a
live prompt at `version: 5` and would need the eval suite re-run to prove no tag
regression. Same outcome; one is free to verify.

**Carry into the plan:** the new file's `order` must sit adjacent to
`conduct.md`'s (`order: 30`), or the assembled string shifts and the
byte-identity test fails for a reason that is not a real regression.

### 12.3 Cap values — as configured, tunable later (operator, 2026-08-08)

`max_pending_per_person = 20`, `min_lead_seconds = 60`,
`max_horizon_days = 30` stand. They are config, so they are cheap to change
once there is usage to size them against.

## 12a. Still open

### 12a.1 Name collision

`heartbeat` already names the observability log line
(`nora/obs/logging.py:130`). If review finds this confusing, **this** design
renames — the log line has seniority.

---

## 13. Gates this touches

| gate | why |
|---|---|
| alembic migration + `tests/infra/test_schema_parity.py` | new table with a CHECK constraint — `alembic check` is blind to it |
| `design/09-decision-index.md` | new behavioural rules (never-early; a row fires once) need DEC entries |
| `tests/test_acceptance_rules.py::RULE_TESTS` | each new rule needs a named test — **including the mapped-both-ends guard (§12.1)**, which is a security rule, not a behaviour preference |
| `tests/test_prefill.py` (or equivalent) | the `conduct.md` split must assert the **tag** audience assembles byte-identically (§12.2) |
| `tests/contract/` | only if the clock or store gains a second implementation — including a test fake |
| `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence` | new config classes are in scope |
| `uv run lint-imports` | the loop lives in `nora/app`; check the boundary before placing it |

The clock is taken through `__init__` (test-seam policy), so the loop's timing
behaviour is a **fake-clock unit test with zero model calls**.

---

## 14. Honest limits — state these, do not paper over them

- **"Consider whether anything has changed" is a doctrine line, not a guard** — a
  request to a model, per design/12's mechanical-vs-asserted distinction. It will
  mostly work and will sometimes not.
- **Implicit cancellation is best-effort.** Explicit cancel (`cancel_reminder`,
  or the CLI) always works.
- `search_memory` returns other people's raw words, so the roster-speaker filter
  and per-message R-18 apply at fire time exactly as they do on a tag — same
  guard, no new one.
- **Ingest → searchable lag in Honcho is unmeasured**, and the 40-minute laundry
  case lives or dies on it. Embedding is far cheaper than derivation, so the
  prior is good, but it is a number to obtain rather than assume.

### 14.1 Prior art

Worth knowing before paying for the novel part: **almost no shipped agent does
implicit cancellation.** ChatGPT Tasks, Gemini/Assistant reminders, Siri, Alexa,
Todoist — a reminder fires unless explicitly cancelled. The dominant pattern is
"scheduled prompt, fresh context", which is independently the same shape as §2's
deferred message. Where agents do consult history, they *retrieve* rather than
stuff a window — which is §6.2.

So §6.2 is the part of this design with no industry precedent to lean on, and the
part most worth measuring rather than assuming.
