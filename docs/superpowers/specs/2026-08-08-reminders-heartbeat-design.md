---
kind: spec
title: "Reminders + heartbeat — a deferred request, fired on its own path"
description: "Nora's first unprompted-speech path. A tick selects due rows from a one-shot reminders table and runs them through a DEDICATED fire path — not the tag pipeline — because the tag pipeline's ingest gating, trigger text, audit write, memory feed, debounce, flood-shed and reaction placement are all wrong for a synthetic event. v2 after three independent reviews found v1's pipeline-reuse premise false and two live-path regressions."
tags: [nora, spec, reminders, heartbeat, proactive, doctrine]
timestamp: '2026-08-08'
status: draft v2 — v1 failed review, rewritten against the findings
derives_from:
  - design/01-ambient-ingest.md
  - design/11-recall-and-threading.md
  - design/12-prompt-tool-skill-boundaries.md
  - design/17-prompt-substrate.md
  - design/proposed/19-presence-collector.md
  - design/proposed/20-postgres-migration.md
  - docs/superpowers/specs/2026-08-08-turn-budget-finding.md
supersedes: "v1 of this file (2026-08-08, same path) — see §0.1"
---

# Reminders + heartbeat

## 0. What this document is

Nora's **first unprompted-speech path**. Until now she has only spoken in
response to a tag.

**Scope note.** `heartbeat` here is a scheduling loop, unrelated to
`nora.obs.logging.heartbeat` (a 60s counters log line, `nora/obs/logging.py:130`).
The log line has seniority; if review finds the collision confusing, **this**
design renames. Recorded once, here, and not restated elsewhere.

### 0.1 What changed from v1, and why

v1 was reviewed by three independent agents (fact-check, adversarial, quality).
It failed. Three findings were fatal, and each is verified against source:

1. **v1's central claim — "the injector reuses the pipeline unchanged" — is
   false in both directions.** Through `Dispatcher.on_message` the trigger gate
   rejects a reminder (its text contains no trigger), `audit_stage` writes the
   synthetic message into `messages` as something the requester really said, and
   `memory_feed_stage` feeds it to Honcho **under their peer**
   (`nora/ingest/stages/memory_feed.py:47-52`) — the design would poison the
   store its own staleness check reads. Through `EngagementPipeline.run`
   directly, `policy.toml` gating is skipped entirely, because that gating lives
   in **ingest** (`nora/ingest/pipeline.py:106-152`), not engagement.
2. **v1's `stand_down` regressed the live tag path.**
   `ReplyGuard._terminal_budget_spent` is an **AND over every terminal tool**
   (`nora/agent/guards.py:412-414`): a second terminal verb means an ordinary tag
   turn never reaches its hard stop after `send_reply`×3. There is a pinned
   invariant — `tests/test_guards_terminal.py:185`,
   `terminal_tools() == frozenset({"send_reply"})`.
3. **v1 analysed the wrong budget.** `[llm] timeout_seconds = 30` wraps the whole
   loop in one `asyncio.wait_for` (`nora/engagement/stages/invoke.py:99`).
   `ask_memory` is measured at **14–19s** (`nora/agent/tools.py:294`). v1's fire
   turn blew wall-clock long before tokens.

v2's response is structural, not cosmetic: **a dedicated fire path** (§5), **no
new terminal verb** (§6.1), and **no staleness check in v1** (§11).

Three v1 conveniences are also withdrawn, each because a reviewer showed the
mechanism forbids it — §7.2 (no `target` parameter), §7.3 (no prefill block),
§7.4 (read-only CLI).

---

## 1. What is NOT being built

### 1.1 `note()` — dissolved, not deferred

The test applied: *name one thing Nora should write to herself that is neither a
fact about a person nor a timed reminder.* Nothing survived it. The fuzzy side is
covered by `about` / `ask_memory` / `search_memory` (DEC-68, shipped `21f9712`).

Delete the `note` half of the backlog entry rather than leaving phantom scope.

### 1.2 Recurring reminders — not in v1

A recurring row *is* the runaway shape (§9).

### 1.3 The staleness check — not in v1

See §11. Three independent mechanisms block it, and every shipped agent fires
reminders unconditionally (§15.1).

### 1.4 A second terminal verb — never

§6.1. `stand_down` was v1's answer to "she may decide not to speak" and it
regresses every tag turn.

---

## 2. The core idea

> **A reminder is a deferred request.** The row stores *the requester's own
> sentence*; at fire time it is put to Nora as a request, and she answers it.

The alternative — a structured payload naming an action — was rejected against
one case: 「幫我睇下 Tyler 喺邊」, whose payload is not text to send but an
instruction requiring a tool call at fire time. Storing the sentence needs no
action types and no dispatch table, and gives one property worth stating:

**Every capability Nora gains later is automatically remindable.**

v1 called this "a deferred *message*" and then, four paragraphs later, "a
request, not a message." **It is a request.** The word matters: a *message*
implies replay through the message pipeline, which is exactly the mistake §0.1
records.

---

## 3. Architecture

| part | knows | owns |
|---|---|---|
| **heartbeat** | that time has passed | tick cadence, liveness, crash recovery, "already fired?" |
| **reminder row** | why it matters | text, conversation, requester, `due_at`, state |
| **fire path** | how to run one turn | prompt assembly, invoke, deliver, outcome |

**Nora never touches the tick.** No tool starts, stops, skips or schedules it.
It is infrastructure in the sense the Signal listener's receive loop is
infrastructure. Her only decisions are *whether to write a row* and *what to do
when one is put to her*.

Loop shape follows `nora/presence/collector.py` — sleep-and-repeat, dedup by
stored state rather than memory, its own liveness gauge.

---

## 4. Data — one table

```
reminders
  id                     uuid pk
  requester_person_id    text        not null    -- identity.toml person_id (DEC-14)
  conversation_id        text        not null    -- where it was set AND where it fires (§7.2)
  text                   text        not null    -- the requester's own sentence
  created_at             timestamptz not null
  due_at                 timestamptz not null    -- UTC; see §7.1 on timezone
  state                  text        not null    -- pending|firing|done|cancelled|failed
  fired_at               timestamptz null
  tag_id                 text        null        -- set when the fire turn starts (§8.2)
  outcome                text        null        -- what happened: sent|silent|failed_*
```

`state` is CHECK-constrained. The migration is **hand-written**; the schema gate
is `tests/infra/test_schema_parity.py`, not `alembic check`, which is blind to
CHECK constraints (CLAUDE.md).

Index: `idx_reminders_due` on `(state, due_at)`, named explicitly — CLAUDE.md
carries a standing warning about unnamed/autogenerated indexes.

**One-shot.** A row fires once, ever.

`tag_id` and `outcome` exist because of §8.2: without a link from a row to the
turn it produced, the boot sweep cannot tell "crashed before egress" from
"crashed after egress", and v1's "either re-queued or reported" was an unresolved
fork presented as a design.

---

## 5. The fire path — dedicated, not the tag pipeline

This is v2's central change.

```
every [heartbeat] tick_seconds:
    rows = SELECT … WHERE state='pending' AND due_at <= now()
           ORDER BY due_at
    for each conversation_id group:              # §5.2
        re-check policy for that conversation    # §5.1
        mark rows 'firing', stamp tag_id
        run ONE fire turn
        record outcome, mark done|failed
```

The fire path **composes the same stage functions** as the tag pipeline where
they fit, and **explicitly does not use** the ones that do not. Being explicit
about the second list is the point:

| stage | fire path | why |
|---|---|---|
| ingest scope/sender allowlist | **re-implemented at fire time** | lives in ingest; must still hold, and must hold *now* — not at `remind()` time, up to 30 days earlier |
| trigger-text gate | **not used** | the reminder text contains no trigger; forging one would corrupt the stored sentence |
| `audit_stage` (write to `messages`) | **not used** | would fabricate a message the requester never sent |
| `memory_feed_stage` | **not used** | would feed Honcho a sentence under the requester's peer, at the wrong time |
| `admission` (debounce R-9, flood-shed R-30, self-tag R-12) | **not used** | §5.3 |
| per-conversation serialisation | **used, via the same queue key** | §5.3 |
| doctrine + prefill | **own assembly** | §10 |
| `invoke` | **used, own timeout** | §5.4 |
| `deliver` / `Deliverer` | **used** | the only caller of `adapter.send` stays the only caller |
| `indicate` (👀/✅/⚠️) | **not used** | §5.5 |
| `LeakGuard` / R-18 on egress | **used** | unchanged |

### 5.1 Policy is re-checked at fire time

`policy.toml` is deny-by-default and loaded once at startup. A reminder may be 30
days old; trust can be withdrawn in between. **The fire path re-runs the room and
sender checks immediately before firing.** A row whose conversation or requester
is no longer permitted resolves `failed`/`cancelled` and never fires.

v1 claimed policy applied "unchanged" via pipeline reuse. It did not apply at all.

### 5.2 Batching is per conversation, never across

v1 said "all rows due in one tick become one turn." A turn has exactly one
conversation scope — one `Room`, one policy decision, one `Deliverer` binding
(R-3). Rows for two conversations **cannot** share a turn without leaking one
room's reminder into the other.

**Group by `conversation_id`; one turn per group.** At realistic volume every
group has one row.

### 5.3 Serialisation without admission

The fire turn must not run concurrently with a tag in the same conversation, but
it must not go through `admission.run` either, because that path can **drop it**:

- **R-9 debounce** (`admission.py:84`, `min_interval_seconds = 5`) rejects a fire
  turn arriving within 5s of a tag — and, worse, a fire turn passing through
  would *consume* the debounce window, so a human tagging Nora just after an
  unprompted message gets nothing.
- **R-30 flood-shed** (`admission.py:90`, `max_depth = 16`) sheds the *newest*
  arrival — always the fire turn, which has waited hours.
- **R-12 self-tag** rejects if the synthetic sender resolves to Nora.

So the fire path takes the **conversation queue lock directly** (same key as the
tag path, so mutual exclusion holds) and **waits** rather than being shed. A tick
that cannot get the lock leaves the rows `pending` and retries next tick — which
is correct, because a reminder is not time-critical to the second and the row is
still there.

### 5.4 Its own timeout

`[heartbeat] timeout_seconds`, separate from `[llm] timeout_seconds = 30`.

The 30s value is a *conversational* budget sized for a human watching a typing
indicator. Nobody watches a fire turn. This is the knob v1 should have made
per-audience — not `turns` (§12.1).

### 5.5 No reactions

`indicate.begin` reacts to the inbound message's `(author, timestamp)`
(`nora/engagement/stages/indicate.py:255-262`). A synthetic event has neither.
Fabricating them means either failed `sendReaction` calls on every tick or — if a
fabricated timestamp collides — 👀 and ⚠️ landing on an unrelated old message.

Consequence: a fire turn that delivers nothing shows **no ⚠️ in the room**, which
is correct. ⚠️ means "done, nothing is coming" to someone who is waiting; nobody
is waiting.

### 5.6 The timing contract

> Fires **at or after** `due_at`, **never before**, within one tick (plus lock wait).

"Never early" is the invariant. Resolution is a config knob; `total_tokens`-style
reachability does not apply because the query is one indexed lookup.

---

## 6. The turn

She receives the sentence, when it was set, and when it was due, and answers it.

### 6.1 `tool_choice="auto"`, and no `stand_down`

v1 introduced a terminal `stand_down` verb so silence could be a tool call. Three
reviewers independently showed it breaks turn termination for **every** tag turn
(§0.1(2)), and that "registered for the heartbeat audience only" has no mechanism
— `NATIVE_TOOLS` is module-level, `ToolRegistry` is one per process (DEC-10), and
the generated capability section is `applies_to=("always",)`
(`nora/doctrine/loader.py:360-366`). It would have offered every tag turn a verb
for answering a human with silence.

**The fire path opens on `tool_choice="auto"` instead.**

The measurement that makes `auto` unsafe on a tag is exactly what makes it right
here (`nora/agent/tool_choice.py:14`):

```
A. auto, fresh question    plain text 5/6, called a tool 1/6
```

On a tag, prose-instead-of-a-tool-call means **Nora silent while the turn looks
successful** — R-5's failure shape. On a fire turn, prose-instead-of-a-tool-call
means **she read the request and chose not to speak**, which is the desired
stand-down. Same mechanism; the audience inverts whether it is a bug.

This costs nothing elsewhere: no new terminal verb, no change to
`terminal_tools()`, no per-audience tool registry, and the pinned invariant test
stays green. **The entire per-audience-registration problem dissolves** — because
`remind`, `cancel_reminder` and `list_reminders` *should* be on tag turns (that
is how you set one), and `stand_down` no longer exists.

Countability is preserved by the fire path recording its own outcome (§4
`outcome`, §13 metrics), not by a shared counter.

### 6.2 Not `nora_silent_failures_total`

A deliberate non-send must **not** increment
`nora_silent_failures_total{reason="no_send_call"}`. That series is targeted at
<1%, and every correct stand-down would erode the alarm this feature depends on.
The fire path owns its own outcome accounting and does not call
`deliver.check_outcome`'s silent-failure branch.

### 6.3 The turn's framing is not doctrine

`_context_header` (`nora/agent/prefill.py:100-102`) hardcodes:

> "You are in a group chat you have been reading all along. You do not speak
> unless somebody calls you, and somebody just did — that is why this turn is
> happening."

Every clause is false on a fire turn, and this is **not** a doctrine file, so
§10's `applies_to` mechanism does not reach it. The fire path builds its **own**
prefill with its own header. This is new code, not a frontmatter change.

---

## 7. The model-facing surface

### 7.1 `remind(when, text)`

`when` is an **ISO-8601 timestamp with offset**, which the model produces. The
turn already knows the current time — `_now_line` renders it, and the repo pins
`_HKT = timezone(timedelta(hours=8), "HKT")` (`nora/agent/tools.py:68`, no tzdata
dependency). `due_at` is stored UTC.

Refuses, with a reason, when: the requester is not a mapped `person_id` (§12),
the caps are exceeded (§9), `when` is unparseable, or `when` is in the past.

Ambiguity (「七點」 — this evening or tomorrow morning?) is resolved by the model
against the current time, and **stated back** (§10.1). It is not a parse rule.

### 7.2 No `target` parameter — the conversation comes from the closure

v1 had `remind(when, text, target)`. **DEC-24's whole holding is that no tool
takes a conversation argument**, because a parameter is a value the model must
invent every call — and the first real tag died `no_send_call` after the model
invented the wrong group id three times. `send_reply` and `load_transcript` both
resolve their conversation from the closure; `remind` does too.

Two things follow, and the second is a deliberate narrowing:

- The guard's scope counter keys on the literal parameter name `conversation_id`
  (`nora/agent/guards.py:316`), so a `target` parameter would be invisible to
  `nora_tool_scope_violations_total`.
- **A reminder fires into the conversation it was set in.** DM in, DM out; group
  in, group out. The operator's "fire to either the user or the group" is
  satisfied by *where you set it*, not by a parameter — and cross-conversation
  targeting is removed outright, which also removes an unprompted-speech lever
  into a room the requester may not belong to.

### 7.3 `list_reminders()` and `cancel_reminder(id)` — conversation-scoped, no prefill block

v1 put the pending set in the prefill. That re-creates a leak this repo already
paid for — `build_prefill`'s docstring (`nora/agent/prefill.py:260-275`) records
a roster block that was removed after it listed five people who had never been
members, and notes that **R-18 saw nothing to block, because the block carried
only names**. A reminder block carries free text: 「提我同 Anna 講分手嘅事」, set
in a DM, would sit in the group's prefill where R-18 again sees nothing to block.

So: a **tool**, scoped by closure to the current conversation, returning only
that conversation's pending rows. `cancel_reminder(id)` re-checks the same scope
inside the tool — a uuid carries no scope of its own.

(This reverses v1's reasoning, which rested on the retracted design/11 §1.6
argument. The retraction is real — `lookup_person` 6/6 on turn one — so a tool
does get called. The prefill was never the safe option.)

### 7.4 The CLI is read-only

`nora/app/cli.py` is *"the read-only operator control plane"*: it opens the store
through `create_readonly_sql_engine`, every connection sets
`default_transaction_read_only=on`, and deployment grants `SELECT` only.
`nora reminders cancel` would break both.

**`nora reminders list` only.** The operator's kill switch is
`[heartbeat] enabled = false` plus a restart — which is how this repo already
does inert features, and which stops *all* firing rather than one row.

---

## 8. State machine

### 8.1 The fire path returns an outcome

`EngagementPipeline.run` returns `None` across six distinguishable outcomes
(rejected, queue-full, queued-not-yet-run, invoke-failed, no-send, swallowed by
`except BaseException`). v1's "mark done after successful egress" was
unimplementable against it: a tick that got `QUEUED` would mark rows `done`
before the turn had run.

The fire path is ours, so **it returns a typed outcome** and the loop records it:
`sent` / `silent` / `failed_policy` / `failed_timeout` / `failed_error`.

### 8.2 Boot recovery cannot guess

Rows left `firing` by a crash are swept on the first tick after start. The sweep
**consults `tag_id`**: `Deliverer` writes a `messages` row carrying
`raw={"tag_id": …}` and a `delivery_status`, so the sweep can ask whether this
row's turn actually delivered.

- delivered → `done`. No double-send.
- not delivered → `failed`, and **not** silently re-queued.

v1's "either re-queued or reported" was a fork, not a decision: re-queue
double-sends everything that crashed after egress; report loses everything that
crashed before it. `tag_id` is the column that makes the question answerable.

### 8.3 `cancelled` does not stop a turn in flight

A cancel against a row already `firing` is a no-op — the turn is running. The
tool and the CLI say so rather than implying otherwise.

---

## 9. Anti-runaway — structural

| control | value |
|---|---|
| no recurring rows | v1 |
| one-shot state machine | a row fires once, ever |
| max pending per requester | `[reminders] max_pending_per_requester = 20` |
| minimum lead | `min_lead_seconds = 120` (> `tick_seconds`, §12) |
| maximum horizon | `max_horizon_days = 30` |
| the loop's off switch | `[heartbeat] enabled = false` |

Every one holds whether or not the model behaves. The values are unmeasured
starting points, and §14 says so plainly rather than dressing them as derived.

---

## 10. Doctrine

A new **audience**, `heartbeat`. The mechanism exists —
`DoctrineStore.get(applies_to=…)` filters and caches per audience, honouring
`"always"` (`nora/doctrine/loader.py:533-554`) — but v1 understated the work:

- There are **two** hardcoded call sites, not one: `nora/app/composition.py:228`
  and `nora/engagement/pipeline.py:264`. The second is the per-turn production
  path.
- `_scan` **rejects duplicate `order`s** (`loader.py:485`) and `35` is reserved
  for the generated capability section (`CAPABILITY_ORDER`, `loader.py:334`).
- `_REQUIRED` frontmatter is **ten keys**, and `check_authority_chain` is
  **symmetric** (`loader.py:402-415`) — a new file's `authority_above` forces
  matching `authority_below` edits in every file it names.
- `kind: conduct` rejects **uppercase** RFC-2119 absolutes (`loader.py:111`), and
  a `conduct`/`identity` file may not name a tool (`check_kind_boundaries`,
  `loader.py:289` — note this gate is narrower than "DEC-29 forbids naming
  tools": `constraint` is exempt).

### 10.1 The split, honestly justified

`# Silence is the default` is the only section that contradicts a fire turn
(*"the only reason you ever speak"*, *"you never follow up on your own reply"*).
Everything from `# Replying` down applies to both audiences — and three parts are
load-bearing here, which is why `reminders.md` stays small:

- *"You have judgment — a friend, not a command line"* is already the basis for
  declining to act.
- *"Check before you answer… or on something said further back than you were
  shown"* is nearly the staleness instruction, for when §11 lands.
- *"When you need two things that don't depend on each other, get them in the
  same breath"* (`conduct.md:112`) is the concurrency permission.

**Migration:** extract the silence section into its own file, `applies_to: [tag]`,
`order` in the free range **21–29** (10/20/30/40 taken, 35 reserved), sorted
`(order, path.name)` so it must sort *before* `conduct.md` to preserve position.

**v1's justification was false and is withdrawn.** v1 said the split assembles
byte-identical text. It cannot: `_assemble` emits one `<section …>` wrapper **per
file** (`loader.py:261-268`), so two files necessarily add a section boundary.

The honest test is therefore: **the concatenated section *bodies* for the `tag`
audience are unchanged, and the only structural diff is one added boundary.**
That is mechanically assertable and is what §14 requires — it is a weaker
guarantee than v1 claimed, and it is the true one.

### 10.2 `voice.md` additions

**Precision lives in the database; vagueness lives in the voice.**

- 「等陣提我洗衫」 → 「好, 等陣提你」 — *not* 「好, 19:47 提你」
- 「七點提我出門」 → 「好, 七點」

She picks a sensible time and only asks 「幾點?」 when there is no reasonable
default. *Rationale, recorded because it was the operator's correction: a friend
who answers 「等陣提我洗衫」 with 「幾點? 15 分鐘定 30 分鐘?」 is not a friend, it is
a judge. Interrogating someone to populate a column is the schema leaking through
the persona.*

She always confirms *something*, so a wrong guess dies now rather than at fire
time.

**Constraint:** `voice.md` is `kind: identity`, which forbids **all** RFC-2119
uppercase keywords. These lines must be written as description, not rule.

---

## 11. Phasing

**Phase 1 — fires unconditionally.** Table, migration, store, tick, fire path,
`remind` / `list_reminders` / `cancel_reminder`, doctrine audience, CLI listing,
metrics.

**Phase 2 — the staleness check.** Deferred, because three mechanisms block it
and none is in this design's control:

1. **`search_memory` fails closed on unmapped room members** — `if view.degraded
   or view.unnamed > 0: refused` (`nora/agent/tools.py:1133`). §12's mapped-both-
   ends gate covers the requester, not the other people standing in the room. In
   a realistic group the evidence verb simply refuses.
2. **Wall clock.** `ask_memory` is 14–19s against a 30s turn timeout, and its
   budget is **1**, not 4 (`tools.py:295` — *"a second would blow the loop budget
   on its own"*).
3. **`memory_feed` is spawned, never awaited** (`nora/ingest/stages/memory_feed.py:36`),
   so 「唔洗洗衫喇」 is not guaranteed to have been *submitted* to Honcho, let
   alone embedded. The ingest→searchable lag is unmeasured.

Phase 2 begins with a measurement, not code: feed a cancellation, time
submission→retrievable, and check whether `search_memory` is reachable at all in
the target room.

**Later:** recurring reminders; event triggers. The latter validates the
abstraction — 「Tyler 返到屋企話我知」 is not time-based and needs only a different
`WHERE` clause in the tick, same table and same fire path.

---

## 12. Decisions taken

**Mapped identity required (operator).** Only a mapped `person_id` may create a
reminder. With §7.2 removing the `target` parameter, the *target* is a
conversation the requester was already speaking in, so the requester check is the
whole gate. Enforced at `remind()` **and re-checked at fire time** (§5.1), because
`identity.toml` is loaded once at startup and trust can be withdrawn inside a
30-day horizon. A guard, not a doctrine line.

**`conduct.md` splits rather than being rewritten** — §10.1, with the corrected
justification.

**Caps as configured** (§9), explicitly unmeasured.

### 12.1 No heartbeat-specific `turns`

Conversational `turns = 20`; a fire turn is 3–4 round trips. `turns` is not the
binding cap — `config.toml`'s own comment says `total_tokens` binds first — and
**R-24 requires `turns` to be reachable** (`total_tokens >= turns × ~6124`), so a
second `turns` knob would need its own paired `total_tokens` or become dead
config.

Tool budgets need no change. Correct current values, which v1 got wrong:
`search_memory` **4**, `route` **4**, `send_reply` **3**, `ask_memory` **1**.

The knob that *does* need a per-audience value is `timeout_seconds` (§5.4).

### 12.2 Budget reality

The measured ceiling is ~9 cycles at ~14,400 tokens/call, not 20 — see
`2026-08-08-turn-budget-finding.md`. One qualifier v1 omitted: **that trace is a
pathology**, ~86k of its 130,809 spent in a budget-veto retry loop. It is not a
baseline. A 3–4 cycle fire turn has room; wall clock (§5.4) is the real
constraint.

---

## 13. Metrics — new series, never new labels on old ones

Metric names and labels are append-only in this repo
(`nora/obs/metric_specs.py:40`), and a fire turn must not silently move
tag-path series (`nora_tags_total`, `nora_tag_latency_seconds`,
`nora_messages_per_tag`, `nora_silent_failures_total`). So the fire path gets its
own:

| metric | why |
|---|---|
| `nora_reminders_created_total{status}` | writes, refusals and the reason |
| `nora_reminders_fired_total{outcome}` | `sent` / `silent` / `failed_*` (§8.1) |
| `nora_reminders_pending` (gauge) | runaway visible without a query |
| `nora_heartbeat_last_tick_age_seconds` (gauge) | **liveness** — the collector's precedent (DEC-55(4)): a gauge that climbs when up-but-not-ticking, which a frozen fired-count cannot show (R-5) |

---

## 14. Gates this touches

| gate | why |
|---|---|
| hand-written alembic migration + `tests/infra/test_schema_parity.py` | new table, CHECK constraint, named index |
| `design/09-decision-index.md` | **R-1 must be amended** — *"only the tagging path may cause a send"*, named test `test_ambient_never_constructs_agent`. A fire turn is a send not caused by a tag. This is the rule this design changes, and v1 never named it. |
| `tests/test_acceptance_rules.py::RULE_TESTS` | the mapped-identity guard, never-early, fires-once |
| `tests/test_guards_terminal.py` | must stay green — `terminal_tools() == {"send_reply"}` (§6.1 keeps it so) |
| doctrine body-identity test | §10.1 |
| `ARCHITECTURE.md` | `test_architecture_doc_covers_every_package` fails on any new top-level package under `nora/` |
| `config.example.toml` | kept in parity by `tests/test_config.py` |
| `uv run pytest -q` / `ruff` / `mypy` (scoped) / `lint-imports` | CLAUDE.md's four |

The clock is taken through `__init__` (test-seam policy), so the tick's timing is
a fake-clock unit test with zero model calls.

---

## 15. Honest limits

- Reminders in v1 **fire unconditionally**. A plan changed after setting one is
  handled by cancelling it, not by Nora inferring it.
- The fire path **duplicates** ingest's policy check rather than sharing it
  (§5.1). Duplication is a drift risk and must be tested on both paths.
- `Room.scope()` degrades to *the tagger alone* when the roster is unreadable
  (`nora/agent/room.py:151-167`). On a fire turn that is the requester. Tools
  other than `search_memory` do not fail closed on `degraded` — they answer with
  `partial_view`. Bounded here because §7.2 keeps the room the requester's own.
- Cap values are unmeasured (§9).
- Phase 2's feasibility is unmeasured (§11).

### 15.1 Prior art

Almost no shipped agent does implicit cancellation — ChatGPT Tasks, Gemini,
Siri, Alexa, Todoist all fire unless explicitly cancelled. The dominant pattern
is *scheduled prompt, fresh context*, which is independently §2's deferred
request. So phase 1 matches the industry baseline, and phase 2 is the part with
no precedent to lean on — which is why it is gated on a measurement.

---

## 16. Scope

Reviewers flagged v1 as too large for one unit. v2 is smaller (no staleness, no
prefill block, no `stand_down`, no per-audience registry, no cross-conversation
targeting) but still decomposes into four buildable steps, in dependency order:

1. **Table + migration + store + `nora reminders list`** — no model, fake-clock
   testable, ships alone.
2. **Tick + policy re-check + outcome recording** — still no model; the fire path
   can be tested with a stub turn.
3. **Fire turn** — own prefill/header, own timeout, `auto` tool choice, deliver.
4. **Tools + doctrine audience + the `conduct.md` split.**

The implementation plan takes these as its phases.
