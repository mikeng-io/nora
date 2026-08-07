---
kind: spec
title: "Reminders + heartbeat — a deferred request, fired on its own path"
description: "Nora's first unprompted-speech path. A tick selects due rows from a one-shot reminders table and runs them through a dedicated fire path — not the tag pipeline, whose ingest gating, trigger gate, audit write, memory feed, admission and reactions are all wrong for a synthetic turn. v3 after two review rounds: v1's pipeline-reuse premise was false, v2's tool_choice=auto answer was refuted by the measurement it cited."
tags: [nora, spec, reminders, heartbeat, proactive, doctrine]
timestamp: '2026-08-08'
status: settled — READY FOR PLAN after six review rounds
derives_from:
  - design/01-ambient-ingest.md
  - design/11-recall-and-threading.md
  - design/12-prompt-tool-skill-boundaries.md
  - design/17-prompt-substrate.md
  - design/proposed/19-presence-collector.md
  - design/proposed/20-postgres-migration.md
  - docs/superpowers/specs/2026-08-08-turn-budget-finding.md
supersedes: "v1 and v2 of this file — see §0.1"
---

# Reminders + heartbeat

## 0. What this document is

Nora's **first unprompted-speech path**. Until now she has only spoken in
response to a tag. That makes **R-1 the rule this design changes** (§14), which
is the single most important sentence here.

**Scope note.** `heartbeat` is a scheduling loop, unrelated to
`nora.obs.logging.heartbeat` (a 60s counters log line, `nora/obs/logging.py:130`).
The log line has seniority; this design renames if review finds the collision
confusing. Stated once.

**Platform note.** Nora v2 handles **groups only**. `nora/platform/signal/parser.py:3`
— *"Group messages only in v2; DMs and syncMessages are dropped"* — and
`send.py:82` / `react.py:66` refuse any `conversation_type != "group"`. So the
operator's original 「fire to either the user or the group」 is **half-unavailable
at the platform layer**, not by choice here. Group-only until a DM path exists.

### 0.1 What two review rounds changed

**Round 1** (fact-check / adversarial / quality) killed v1 on three findings:

1. *"The injector reuses the pipeline unchanged"* was **false both ways.** Through
   `Dispatcher.on_message` the trigger gate returns `engage=False, reason="no_trigger"`
   (`nora/ingest/policy.py:36`), `audit_stage` records the synthetic text under the
   requester's `person_id` (`nora/ingest/stages/audit.py:34`), and `memory_feed`
   feeds it to Honcho under the speaker's peer (`nora/ingest/stages/memory_feed.py:47-50`)
   — poisoning the store the staleness check reads. Through `EngagementPipeline.run`
   directly, `policy.toml` gating never runs at all: it lives in **ingest**
   (`nora/ingest/pipeline.py:106-109` room, `:140-152` sender).
2. v1's `stand_down` **regressed every tag turn**: `_terminal_budget_spent` is an
   AND over all terminal tools (`nora/agent/guards.py:412-414`), and
   `tests/test_guards_terminal.py:184` pins
   `terminal_tools() == frozenset({"send_reply"})`.
3. v1 had no per-audience tool registry to hang `stand_down` on, and none exists.

**Round 2** killed v2's replacement answer:

4. v2 said the fire turn opens on `tool_choice="auto"`, citing row A of
   `nora/agent/tool_choice.py:14`. It **ignored row D**, which is the fire turn's
   actual configuration — *auto throughout*, nothing relaxes it because
   `ReplyGuard` only relaxes after she has spoken — and row D measured
   **0 messages sent, 6/6.** The design would have shipped a reminder that never
   reminds. v3's answer is §6.1: **no silence mechanism in v1 at all.**
5. v2 said the fire path could take the conversation queue lock directly. It
   cannot, and doing it strands queued tags (§5.3).
6. v2's `tag_id` boot-recovery mechanism has no read path (§8.2).

Corrections of fact carried into v3: `[llm] timeout_seconds` is **60**, not 30
(`config.toml:71`), while `config.example.toml:61` still says 30 — see §14 for
what that actually means, which is not what v3's first draft claimed; DEC-24's
holding is the broader *"a tool never asks the model for a value the tool does
not read"*; metric **names** are append-only but **labels are not**
(`nora/obs/metric_specs.py:40-44` records `platform` being added to four existing
series).

**A repo defect found on the way, reported not resolved.** `design/09` carries
**two rows numbered R-29** — the queue/flood rule (`:100`) and a skills rule
(`:102`) — and no R-30 row at all. `RULE_TESTS` disagrees with the index and
assigns `R-29 → test_a_message_can_name_a_skill_but_never_load_one` (`:61`) and
`R-30 → test_three_tags_for_one_conversation_are_answered_in_fifo_order` (`:67`),
matching the inline comment at `admission.py:90`. **This spec uses R-30 for
flood-shed**, following the test registry, and the duplicate belongs on someone's
backlog. An earlier draft "corrected" R-30 to R-29 by fiat; that was wrong.

---

## 1. What is NOT being built

1. **`note()`** — dissolved. The test applied: *name one thing Nora should write
   to herself that is neither a fact about a person nor a timed reminder.*
   Nothing survived. `about` / `ask_memory` / `search_memory` cover the fuzzy side.
2. **Recurring reminders** — the runaway shape (§9). Note §7.1: `remind` must also
   refuse *on a fire turn*, or Nora re-arms her own reminders and recurrence
   arrives through the back door.
3. **The staleness check** — phase 2 (§11), gated on a measurement.
4. **Any silence mechanism** — §6.1. It exists to serve staleness; staleness is
   phase 2; so it is phase 2.
5. **A second terminal verb** — ever (§0.1(2)).
6. **Cross-conversation targeting** — §7.2.

---

## 2. The core idea

> **A reminder is a deferred request.** The row stores the requester's own
> sentence; at fire time it is put to Nora as a request and she answers it.

Rejected alternative: a structured payload naming an action. It fails on
「幫我睇下 Tyler 喺邊」, whose payload is not text but an instruction requiring a
tool call at fire time — a payload schema would need action types, a dispatch
table, and an entry per capability.

Storing the sentence gives: **every capability Nora gains later is automatically
remindable.**

*"Request", not "message"* — deliberately. "Message" implies replay through the
message pipeline, which is exactly §0.1(1)'s mistake. v1 used both words and
contradicted itself.

---

## 3. Architecture

| part | knows | owns |
|---|---|---|
| **heartbeat** | that time has passed | tick cadence, liveness, crash recovery |
| **reminder row** | why it matters | text, conversation, requester, `due_at`, state |
| **fire path** | how to run one turn | policy re-check, prompt, invoke, deliver, outcome |

**Nora never touches the tick.** No tool starts, stops, skips or schedules it.

**Loop precedent: `nora/app/runtime.py`'s `prune_loop` / `debounce_sweep_loop`
(`:102`, `:120`)** — same sleep-and-repeat shape, in-process, spawned via
`BackgroundTasks.spawn` (`:54`). *Not* the presence collector: that is a separate
process (`nora/presence/__main__.py:3`) with no adapter, no agent and no access
to the engagement machinery. v2 cited it and thereby made §5.3's lock problem
look easier than it is.

---

## 4. Data — one table

```
reminders
  id                   uuid pk
  requester_person_id  text        not null   -- identity.toml person_id (DEC-14)
  platform             text        not null   -- see below
  conversation_id      text        not null   -- set here, fires here (§7.2)
  conversation_type    text        not null   -- 'group' (§0 platform note)
  text                 text        not null   -- the requester's own sentence
  created_at           timestamptz not null
  due_at               timestamptz not null   -- UTC
  state                text        not null   -- pending|firing|done|cancelled|failed
  fired_at             timestamptz null
  outcome              text        null       -- sent|failed_policy|failed_timeout|failed_error
```

**`platform` and `conversation_type` are not optional.** Every conversation key
in this codebase is a *pair*: `ConversationTarget(platform, conversation_id,
conversation_type)` (`nora/domain/models.py:12`), the queue/in-flight key is
`(message.platform, message.conversation_id)` (`nora/engagement/pipeline.py:156`),
and `Policy.is_allowed` takes `platform` (`nora/policy.py:56`). v1 and v2 stored
`conversation_id` alone and could construct none of the three.

`state` is CHECK-constrained. Migration is **hand-written**; the gate is
`tests/infra/test_schema_parity.py`, not `alembic check`, which is blind to CHECK
constraints (CLAUDE.md:52).

Index `idx_reminders_due` on `(state, due_at)`, named explicitly.

---

## 5. The fire path

```
every [heartbeat] tick_seconds:
    rows = SELECT … WHERE state='pending' AND due_at <= now() ORDER BY due_at
    group rows by (platform, conversation_id)          # §5.2
    for each group:
        if not Policy.is_allowed(...): mark failed_policy; continue   # §5.1
        acquire the conversation slot, or skip to next tick           # §5.3
        mark firing → run ONE turn → record outcome → done|failed
```

It **reuses** the stage functions that fit and **explicitly does not use** the
ones that do not. Being explicit about the second list is the design:

| stage | fire path | why |
|---|---|---|
| ingest room + sender gating | **re-checked**, one call | §5.1 |
| trigger-text gate | **not used** | the text carries no trigger; forging one corrupts the stored sentence |
| `audit_stage` | **not used** | would fabricate a message the requester never sent |
| `memory_feed_stage` | **not used** | would feed Honcho a sentence under their peer, at the wrong time |
| `admission.run` (R-9 / R-12 / R-30) | **not used** | §5.3 |
| conversation serialisation | **used**, via a hoisted collaborator | §5.3 |
| context assembly | **own `TagContext`**, not `ContextAssembler.assemble` | §6.4 |
| `MentionRenderer.rehydrate` | **used** — it rewrites U+FFFC mention placeholders in the message *and every transcript row* (`pipeline.py:259-263`); skipping it renders raw placeholders wherever anyone was @-mentioned |
| `feedback.run` | **used** — Nora's own reply fed to Honcho under the aiPeer. A fire turn is still something she said, and omitting it makes her memory of her own speech incomplete |
| doctrine + prefill | `build_prefill` with a header parameter | §6.3 |
| `invoke.run` | **not used** — it increments `nora_silent_failures_total` | §6.2 |
| `runner.invoke` + own `wait_for` | **used**, own timeout and error accounting | §5.4, §6.2 |
| `deliver.build` / `Deliverer` | **used** — the only caller of `adapter.send` stays so | |
| `deliver.check_outcome` | **not used** | §6.2 |
| `indicate` (👀/✅/⚠️) | **not used** | §5.5 |
| `LeakGuard` / R-18 egress | **used**, unchanged | |

### 5.1 Policy is re-checked at fire time — and it is one call

`Policy.is_allowed(*, platform, conversation_id, person_id)` (`nora/policy.py:56-77`)
already enforces **both** the room and the sender gate, so the fire-time re-check
is **one public call**, not the duplication risk v2 called it. What genuinely
cannot be reused is the trigger gate, which §5 excludes on purpose.

**Do not reach for `scope_stage` instead.** It is *not* a wrapper around
`is_allowed` — it checks the room half only
(`policy.conversation(platform, conversation_id) is not None`,
`nora/ingest/stages/scope.py`). The two predicates diverge exactly where it
matters: in a room with an explicit `persons = [...]` list,
`is_allowed(…, person_id=None)` is `False` where `scope_stage` is `True`. Only
the ingest *sender* check (`nora/ingest/pipeline.py:140-144`) wraps `is_allowed`.

Why re-check at all: `policy.toml` is loaded once at startup and a row may be 30
days old. Trust withdrawn between `remind()` and `due_at` must bite.

### 5.2 One turn per conversation, never across

A turn has one conversation scope — one `Room`, one policy decision, one
`Deliverer` binding (R-3). Rows for two conversations cannot share a turn without
leaking one room's reminder into the other. Group by `(platform, conversation_id)`.

### 5.3 Serialisation requires hoisting the queue — a prerequisite refactor

The fire turn must not run concurrently with a tag in the same conversation. It
must **not** go through `admission.run`, which would drop it three ways:

- **R-9 debounce** (`admission.py:84`, `min_interval_seconds = 5`) rejects a fire
  turn within 5s of a tag — and a fire turn passing through would *consume* the
  window (`nora/tag.py:70` writes `_last[key]` on the allow path), so a human
  tagging just after an unprompted message gets nothing.
- **R-30 flood-shed** (`admission.py:90`, `max_depth = 16`) sheds the **newest**
  arrival — always the fire turn, which has waited hours.
- **R-12 self-tag** rejects if the synthetic sender resolves to Nora.

But it also cannot simply grab the lock. `_in_flight` and `_queues` are **private
to `EngagementPipeline`** (`pipeline.py:128-131`), and the R-10 ownership split
documented at `pipeline.py:9-21` means an outside acquirer that discards `key`
without calling the private `_dispatch_next` **strands queued tags**: a tag that
arrived during the fire turn sits until the *next* tag arrives, which then jumps
ahead of it. That is a regression of the FIFO guarantee pinned by
`test_three_tags_for_one_conversation_are_answered_in_fifo_order`.

**Therefore: hoist `_in_flight` + `_queues` + `_dispatch_next` into an injected
collaborator that both paths share.** This is a live-path refactor with its own
tests, and it is a **prerequisite**, not a footnote — §16 makes it step 0.

**One trap to route around.** `_dispatch_next` spawns `self._run_queued`, a
pipeline method, so a naive hoist needs a callback *back into* the pipeline —
which CLAUDE.md forbids ("collaborators through `__init__` only, no `*_fn`
hooks"). The escape: **enqueue a coroutine factory**, so the collaborator needs
only `spawn` and never a reference to the pipeline.

Production readers are exactly two — `pipeline.py` (`:128-131`, `:159-160`,
`:170`, `:204-217`, `:370`, `:379`) and `admission.run`'s `in_flight` parameter
(`admission.py:71`, `:89`, `:107`). The cascade is four constructors
(`composition.py:474`, `offline.py:85`, and two test files), the `queue_max`
wiring from `config.toml:123`, and ~20 assertions in
`tests/unit/test_engagement_pipeline.py`.

Contention policy, stated once (v2 gave two): a tick that cannot acquire the slot
**leaves the rows `pending` and retries next tick.** It does not wait and it is
never shed. A reminder is not second-critical and the row persists.

### 5.4 Its own timeout

`[heartbeat] timeout_seconds`, separate from `[llm] timeout_seconds = 60`.
`invoke.run` already takes `timeout_seconds` per call, so this is free.

Correction to v2: v2 argued from 30s and called wall-clock the binding
constraint. The real value is **60** and there is no per-tier timeout
(`composition.py:293` is `tier = cfg.llm`; `LLMConfig.timeout_seconds` is a
scalar). Wall clock is therefore *not* the reason staleness is deferred — §11
gives the reasons that survive.

Caveat: `RetryPolicy` is derived once from `llm.timeout_seconds` and held on
`LiveAgentRunner._retry` (`nora/app/agent_runner.py:58`), so a longer heartbeat
timeout inherits a backoff budget sized for 60s. Safe direction; note that
`build_retry_policy`'s stated invariant stops describing the fire path.

### 5.5 No reactions

`indicate.begin` reacts to the inbound `(author, timestamp)`
(`indicate.py:255-262`) and returns `None` when either is falsy. Fabricating them
means failed `sendReaction` calls every tick, or — on collision — 👀 then ⚠️ on an
unrelated old message. A fire turn shows no ⚠️, which is correct: ⚠️ means "done,
nothing is coming" to someone waiting, and nobody is waiting.

### 5.6 Timing contract

> Fires **at or after** `due_at`, **never before**, within one tick (plus any
> deferral from §5.3).

"Never early" is the invariant.

---

## 6. The turn

She receives the sentence, when it was set and when it was due, and answers it.

### 6.1 `tool_choice="required"`, and no silence mechanism in v1

The fire turn opens exactly as a tag does: `FIRST_CALL = "required"`
(`nora/agent/tool_choice.py:45`). She always answers.

v1 proposed a terminal `stand_down`; v2 proposed `tool_choice="auto"`. Both are
refuted (§0.1). The `auto` refutation is worth keeping visible, because it is the
kind of mistake that recurs:

```
A. auto, fresh question          plain text 5/6, called a tool 1/6
D. auto throughout, invited      0 messages sent 6/6     <- the fire turn
```

v2 cited row A and ignored row D. A fire turn is *auto throughout*. Row D is the
measurement, and it says the reminder never fires.

**v1 needs no silence mechanism**, because silence exists to serve the staleness
check and staleness is phase 2 (§11). A v1 reminder fires unconditionally — which
is what every shipped agent does (§15.1). Silence gets designed with the feature
that needs it, against a measurement, rather than invented ahead of it.

One consequence to accept honestly: until phase 2, a reminder whose premise has
evaporated still fires. Cancelling is the remedy (§7.3).

**The model is per-turn**, so nothing here leaks across turns: `LiveAgentRunner.invoke`
calls `self._models.create()` per invocation (`nora/app/agent_runner.py:93`), which
is why `ReplyGuard`'s relax is safe (`tool_choice.py:52`). v3 does not exploit
this, but it is why v2's approach was *architecturally* possible even though it
was behaviourally wrong.

### 6.2 Outcome accounting is the fire path's own

`nora_silent_failures_total` is targeted at <1% (A8) and is the alarm this
feature depends on.

**Two earlier drafts tried to isolate the fire path from it by not calling
things, and both undercounted.** Draft one excluded `deliver.check_outcome`.
Draft two also excluded `invoke.run` (`:109-112`, `:116-118`). But `Deliverer`
— which the fire path **does** use, and must, because it is the only caller of
`adapter.send` — increments the same series on **five more** branches:
`empty_reply`, `doctrine_leak`, `egress_block`, `audit_write_failed`,
`send_failed` (`nora/agent/guards.py:146, 155, 169, 193, 220`).
`check_outcome`'s own docstring says so (`deliver.py:135-137`).

**So isolation-by-omission is abandoned. The series is shared, and the label
carries the distinction.**

Add an `audience` label (`tag` / `heartbeat`) to `nora_silent_failures_total`.
This is precedented, not novel: `metric_specs.py:41-44` records `platform` being
**added** to four pre-existing series. Metric *names* are append-only; labels are
not (§13).

Consequences to carry into implementation:

- **All nine increment sites must pass it.** Five in `Deliverer`
  (`guards.py:146, 155, 169, 193, 220`), two in `invoke.run`
  (`invoke.py:109-112, 116-118`), one in `check_outcome`, **and one in
  `EngagementPipeline`'s outer `except BaseException`**
  (`pipeline.py:355-360`, `reason="handler_error"`). Missing one leaves
  `audience=""` (`Metrics._full_labels`, `nora/obs/metrics.py:41-47`) — an
  unlabelled series, worse than no label.

  *That ninth site was missed by this section's first draft, which is the
  fourth consecutive enumeration of these call sites to come up short. The count
  is nine; the list above is the list.*
- **The A8 alarm must filter to `audience="tag"`**, or fire-path failures silently
  consume a <1% budget sized for conversation. The `handler_error` site matters
  most here: it is the last-resort R-5 signal, and an unlabelled one would be
  excluded by that filter.
- **`docs/runbook.md:10` carries the only in-repo copy of the A8 expression**
  (`nora_silent_failures_total / nora_tags_total`, with a `curl | grep` recipe at
  `:17`). After the label, that ratio mixes heartbeat failures into a tag-only
  denominator. The runbook is part of step 4's scope (§16).
- A fire-path alarm on `audience="heartbeat"` is then a separate expression, which
  is what §13's liveness argument wants anyway.

**Scope of the label, stated deliberately.** Only `nora_silent_failures_total`
gets `audience` in v1, because it is the alarm. `Deliverer` also moves
`nora_replies_total{status,platform}` (`guards.py:216, 218`),
`nora_guard_vetoes_total`, `nora_leak_blocks_total` and `nora_egress_blocks_total`,
and those stay unlabelled — so **a fire-path send is indistinguishable from a tag
reply in `nora_replies_total`**, the series an operator reaches for straight after
the A8 ratio. That is a real limit (§15), accepted rather than overlooked.

The fire path still calls `runner.invoke` inside its own `asyncio.wait_for` with
`[heartbeat] timeout_seconds` rather than `invoke.run` — not for metric isolation
now, but because it needs its own timeout and its own `outcome` mapping (§8.1) —
and it reimplements the `llm_state.last_success_at` touch `check_outcome`
performs.

### 6.3 The header is not doctrine — parameterise, don't duplicate

`_context_header` (`nora/agent/prefill.py:100-102`) hardcodes *"You do not speak
unless somebody calls you, and somebody just did — that is why this turn is
happening."* Every clause is false on a fire turn, and this is **not** a doctrine
file, so §10's `applies_to` mechanism does not reach it.

**`build_prefill` gains a header parameter.** It must not be duplicated: R-13/R-13b
escaping is pinned to `build_prefill` by
`test_prefill_escapes_angle_brackets` and `test_forged_closing_tag_is_escaped`
(R-13 / R-13b, `tests/test_acceptance_rules.py:27-28`), and a hand-rolled fire prefill rendering
the same untrusted transcript would sit outside both named tests. Everything else
it needs (`_now_line`, `_peer_card_block`, `_transcript_block`) is private to that
module.

### 6.4 The fire path builds `TagContext` itself — and must not let the requester fall out

`TagContext` requires a non-optional `message: NormalizedMessage`
(`nora/domain/models.py:212-220`). The fire path constructs one **in memory only**
— nothing writes it (§5's table) — with `platform` / `conversation_id` /
`conversation_type` from the row, and `sender_id` / `timestamp` **blank**, which
is what makes §5.5's "no reactions" hold by construction (`indicate.begin`
returns `None` when either is falsy).

**But it must not reach `ContextAssembler` with that message.**
`nora/engagement/context.py:62` resolves the tagger as
`self._persons.resolve(msg.platform, msg.sender_id)` — a blank `sender_id`
resolves to `None`, and then:

- `ctx.tagger is None` and `peer_card = ()`
- `Room(tagger=None).scope()` returns **unavailable** (`nora/agent/room.py:165-167`)
- `search_memory` refuses (`tools.py:1133`), and every room-scoped tool goes dark
- §12's `tagger is None → refuse` gate fires on **every** fire turn, for the wrong
  reason

So the fire path **assembles `TagContext` directly** rather than calling
`ContextAssembler.assemble`:

| `TagContext` field | source |
|---|---|
| `message` | the synthetic `NormalizedMessage` below |
| `tagger` | the requester's `PersonRef`, via `Persons.by_person[requester_person_id]` (`nora/persons.py:59`) |
| `transcript` | `RecordStore.recent(conversation_id, limit)`, **then `MentionRenderer.rehydrate`** — see §5's table |
| `peer_card` | `MemoryStore.peer_card(peer_id=ref.honcho_peer_id)` |
| `tag_id`, `started_at` | minted by the fire path |

And the synthetic `NormalizedMessage` — every field, because they are all
required with no defaults (`nora/domain/models.py:62-65`):

| field | value |
|---|---|
| `platform`, `conversation_id`, `conversation_type` | from the row |
| **`body`** | **the reminder sentence.** This is what `build_tag_message` escapes into `<tag_message>` (`prefill.py:322-342`) and what the tag path passes as `question=` to `ToolRegistry.build`. It is how she receives the request at all. |
| `sender_name` | the requester's display name — **not** `Persons.display_name(msg)`, which reads `msg.sender_id` (`persons.py:69-73`) and returns `""` here. Take it from the `PersonRef` above. |
| `sender_id`, `timestamp` | **blank**, which is what makes §5.5's "no reactions" hold (`indicate.begin` returns `None` when either is falsy) |
| `raw` | `{}` |

**The set-at and due-at facts go in the header**, not the body — §6.3 already
parameterises `_context_header`, and the body must stay the requester's own
sentence or §2's whole premise breaks. Nothing else has a slot for them:
`build_prefill` and `build_tag_message` render fixed shapes.

This is the context step §5's table omitted. It is listed there now.

*(The `sender_name` line is the same trap as the blocker this section was written
to fix: blanking `sender_id` is correct for reactions and wrong for everything
that reads it. Two fields now depend on that, so both are named.)*

*(Earlier drafts said only "sender_id blank" and left the rest implied. Under
that reading an implementer gets a fire turn with no requester at all, and §15's
claim that the room degrades "to the requester" is false. Naming the fields is
the fix.)*

---

## 7. The model-facing surface

### 7.1 `remind(when, text)`

`when` is an **ISO-8601 timestamp with offset**, produced by the model. The turn
already knows the time (`_now_line`, `prefill.py:30`), and the repo pins
`_HKT = timezone(timedelta(hours=8), "HKT")` (`nora/agent/tools.py:68`, no tzdata
dependency). `due_at` stores UTC. Ambiguity (「七點」 — tonight or tomorrow?) is
resolved by the model against the current time and **stated back** (§10.2); it is
not a parse rule.

Refuses, with a reason, when: the requester is unmapped (§12), a cap is exceeded
(§9), `when` is unparseable or in the past, **or the turn is a fire turn**.

That last one is not optional: without it Nora can re-arm her own reminder each
time one fires, which is recurrence (§1.2) with none of §9's caps able to stop it
— `max_pending_per_requester` counts pending rows, and each fire frees one before
the new one is written.

**Nothing carries turn provenance today.** Neither `build_tool_specs`
(`tools.py:702-725`) nor `ToolRegistry.specs()` / `build()` knows what kind of
turn it is serving, so a `fire_turn: bool = False` keyword must be threaded
through all three. That is legitimate: the registry docstring's bar is about
registry-owned *collaborators*, not per-turn values.

*(An earlier draft justified this with "`build()` hands the same list to every
caller." That is loose — `build()` re-derives closures per call. The conclusion
holds; the reason given for it did not.)*

### 7.2 No `target` parameter

DEC-24: *"A tool never asks the model for a value the tool does not read"* — the
conversation-parameter ban is its application. Its anecdote: on the first real
tag the model invented the wrong group id three times, R-3 cancelled each, and the
turn died `no_send_call`. Also, the scope counter keys on the literal name
`conversation_id` (`guards.py:316`), so a `target` parameter would be invisible to
`nora_tool_scope_violations_total`.

**A reminder fires into the conversation it was set in**, resolved from the
closure exactly as `send_reply` does. This removes an unprompted-speech lever into
a room the requester may not belong to.

### 7.3 `list_reminders()` / `cancel_reminder(id)` — tools, conversation-scoped

Not a prefill block. `build_prefill`'s docstring (`prefill.py:260-275`) records a
roster block removed after it listed five people who had never been members, and
notes **R-18 saw nothing to block, because the block carried only names.** A
reminder block carries free text, so R-18 would see nothing to block there either.

*(v2 justified this with a DM-to-group leak scenario. That scenario is impossible
— there are no DMs (§0). The conclusion stands on the roster precedent alone.)*

`build_tool_specs` already closes over `conversation_id`
(`nora/agent/registry.py:248-278`), so scoping is free. `cancel_reminder` re-checks
scope inside the tool: a uuid carries none of its own.

**Store injection:** the reminders store is a per-process collaborator, so it goes
on `ToolRegistry.__init__` alongside `transit` / `route` / `search` / `presence`
— the registry docstring (`registry.py:44-66`) makes exactly this argument, and
`specs()`'s docstring forbids threading a new parameter through every caller.

### 7.4 The CLI is read-only

`nora/app/cli.py:1` — *"The read-only operator control plane"*, opened through
`create_readonly_sql_engine` with `default_transaction_read_only=on` and a
`SELECT`-only grant. **`nora reminders list` only.** The kill switch is
`[heartbeat] enabled = false` plus a restart, which stops all firing rather than
one row.

---

## 8. State machine

### 8.1 The fire path returns a typed outcome

`EngagementPipeline.run` returns `None` regardless of how a turn ended, including
when admission merely **queued** it — so a tick that reused it would mark rows
`done` before the turn had run. The fire path is ours and returns
`sent | failed_policy | failed_timeout | failed_error`.

### 8.2 Boot recovery is at-most-once, by decision

Rows left `firing` by a crash resolve to **`failed`**. They are never re-fired.

v2 claimed the sweep could consult `tag_id` because `Deliverer` writes
`raw={"tag_id": …}` and a `delivery_status` (`guards.py:183-188`). The write is
real; **the read is not**: `messages` has no `tag_id` column (it lands inside
unindexed `raw_json`), `RecordStore` exposes no query by tag or by raw
(`nora/domain/ports.py:343-367`), adding one would need a `FakeRecordStore` update
and a `tests/contract/` entry, and `prune_all` could delete the row first.

So the honest choice is **at-most-once**: a crash mid-fire loses that reminder,
visibly (`outcome=failed_error`, a metric, a log line). Losing one reminder to a
crash beats double-sending, and "either re-queued or reported" was never a
decision.

### 8.3 Cancel does not stop a turn in flight

A cancel against a `firing` row is a no-op. The tool and the CLI say so.

---

## 9. Anti-runaway — structural

| control | value |
|---|---|
| no recurring rows | v1 |
| `remind` refuses on a fire turn | §7.1 — without it, recurrence returns |
| one-shot state machine | a row fires once, ever |
| `max_pending_per_requester` | 20 |
| `min_lead_seconds` | 120 (> `tick_seconds`) |
| `max_horizon_days` | 30 |
| the off switch | `[heartbeat] enabled = false` |

Every one holds whether or not the model behaves. **The three numbers are
unmeasured starting points** — said plainly rather than dressed as derived.

---

## 10. Doctrine

A new **audience**, `heartbeat`. The mechanism exists —
`DoctrineStore.get(applies_to=…)` filters and caches per audience, honouring
`"always"` (`nora/doctrine/loader.py:533-554`, filter at `:540`) — but it is more
than frontmatter:

- **Three** hardcoded `applies_to="tag"` call sites: `nora/app/composition.py:228`,
  `nora/engagement/pipeline.py:264` (the per-turn production path), and
  `evals/run.py:232`. The last is correct as-is — evals want the tag audience —
  but `evals/` is inside the ruff and importlinter gates, so it counts.
- `_scan` rejects duplicate `order`s (`:485`); `CAPABILITY_ORDER = 35` is reserved
  (`:334`); 10/20/30/40 are taken, so **21–29 is the free range**, and sorting is
  `(order, path.name)` (`:492`).
- `_REQUIRED` is **ten** frontmatter keys (`:52-55`), and `check_authority_chain`
  is **symmetric** (`:402-415`) — a new file forces `authority_below` edits in
  every file it names.
- `kind: conduct` rejects **uppercase** RFC-2119 absolutes (`:111`, scoped to
  uppercase on purpose at `:95`). `check_kind_boundaries` (`:289`) applies only to
  `identity`/`conduct` — `constraint` is exempt, so "DEC-29 forbids naming tools"
  is narrower than v1 stated.

### 10.1 All four files must gain the audience — not just `conduct.md`

`identity.md` (10), `voice.md` (20), `conduct.md` (30) and `constraints.md` (40)
are **all** `applies_to: [tag]`. `get(applies_to="heartbeat")` today returns only
the generated capability section. v2 discussed splitting `conduct.md` and nothing
else, which would have run the fire turn with **no persona and no constraints** —
and would have shrunk `LeakGuard`'s verbatim-leak corpus, since `Deliverer` builds
`LeakGuard(doctrine, canary)` (`guards.py:83`).

So: all four become `[tag, heartbeat]`, minus the extraction below.

**The extraction — and it is smaller than the section.** `# Silence is the
default` is not homogeneous. Only its *first three sentences* contradict a fire
turn (`conduct.md:20-22`: *"You're only reading this because someone called
you… That's the only reason you ever speak… you never follow up on your own
reply"*).

Its **last two sentences are a multi-message rule, not a silence rule**
(`conduct.md:22-24`): *"Within a single call you usually say one thing; you may
send a separate message when that one call genuinely addresses more than one
person — but you never split one answer across several."* That is DEC-40, it is
true on a fire turn, and moving the whole section to a `[tag]`-only file would
**delete it from the heartbeat audience**.

So: extract the first three sentences into `silence.md` (`[tag]`, `order` 21–29,
sorting before `conduct.md`); **leave the multi-message sentences in
`conduct.md`** under a heading of their own. Splitting on the section boundary
would have been the obvious move and the wrong one.

A second candidate, flagged by review and **left in for v1**: `conduct.md:32`,
*"One reply, then stop. If they want more, they'll call you again."* It reads
oddly on a fire turn but is not false, and v1's fire turn does answer once and
stop. Revisit with phase 2.

**v2's justification is withdrawn.** v2 claimed the split assembles
byte-identical text for the tag audience. It cannot: `_assemble` emits one
`<section …>` wrapper per file (`:261-268`), so two files add a boundary. The
true, weaker, assertable guarantee: **the concatenated section *bodies* for the
`tag` audience are unchanged, and the only structural diff is one added
boundary.**

A second test must assert the **heartbeat** audience is non-degenerate — that it
contains identity, voice, conduct and constraints. Nothing else would catch
§10.1's failure mode.

### 10.2 `voice.md` additions

**Precision lives in the database; vagueness lives in the voice.**

- 「等陣提我洗衫」 → 「好, 等陣提你」 — *not* 「好, 19:47 提你」
- 「七點提我出門」 → 「好, 七點」

She picks a sensible time and asks 「幾點?」 only when there is no reasonable
default. *Rationale, recorded because it was the operator's correction: a friend
who answers 「等陣提我洗衫」 with 「幾點? 15 分鐘定 30 分鐘?」 is not a friend, it is
a judge.* She always confirms *something*, so a wrong guess dies now rather than
at fire time.

**Constraint:** `voice.md` is `kind: identity`, which forbids **both** absolute
and advisory RFC-2119 keywords (`loader.py:112`). Written as description, not rule.

---

## 11. Phasing

**Phase 1 — fires unconditionally.** Queue hoist (§5.3), table, migration, store,
tick, fire path, `remind` / `list_reminders` / `cancel_reminder`, doctrine
audience, CLI listing, metrics.

**Phase 2 — the staleness check.** Two mechanisms block it, and neither is in this
design's control:

1. **`search_memory` fails closed on unmapped room members** —
   `if view.degraded or view.unnamed > 0: refused` (`nora/agent/tools.py:1133`).
   §12's gate covers the requester, not the others standing in the room. In a
   realistic group the *evidence* verb simply refuses, leaving only `ask_memory`
   — derived, lossy, and budget **1** (`tools.py:295`; *"a second would blow the
   loop budget on its own"*, `:292`).
2. **`memory_feed` is spawned, never awaited** (`memory_feed.py:34-37`), so
   「唔洗洗衫喇」 is not guaranteed to have been *submitted* to Honcho, let alone
   embedded. The ingest→searchable lag is unmeasured.

*Wall clock is no longer a reason* — that argument rested on 30s and the value is
60 (§5.4).

Phase 2 opens with a measurement, not code: feed a cancellation, time
submission→retrievable, and check whether `search_memory` is reachable at all in
the target room.

**Later:** recurring reminders; event triggers. The latter validates the
abstraction — 「Tyler 返到屋企話我知」 is not time-based and needs only a different
`WHERE` clause in the tick.

---

## 12. Mapped identity required

Only a mapped `person_id` may create a reminder. With §7.2 removing `target`, the
destination is a conversation the requester was already speaking in, so the
requester check is the whole gate.

**`Room` needs a public accessor first.** It stores `self._tagger`
(`nora/agent/room.py:141`) with no property, and `scope()` surfaces the tagger
only in its degraded branch. The tool needs more than a `None` check anyway — it
needs `tagger.person_id` to populate `reminders.requester_person_id` (§4). So a
one-line `@property tagger` on `Room` is part of this work, not something already
available.

Enforced at `remind()` **and re-checked at fire time** (§5.1) — `identity.toml`
loads once at startup and trust can be withdrawn inside a 30-day horizon. A guard,
not a doctrine line.

### 12.1 No heartbeat-specific `turns`; budgets unchanged

`turns = 20`; a fire turn is 3–4 round trips. `turns` is not the binding cap
(`config.toml:93-94` says `total_tokens` binds first), and R-24 requires `turns`
to be reachable (`total_tokens >= turns × ~6124`), so a second `turns` knob would
need a paired `total_tokens` or become dead config.

Current budgets, correcting v1: `search_memory` **4** (`tools.py:299`), `route`
**4** (`:196`), `send_reply` **3** (`:356`), `ask_memory` **1** (`:295`).

The knob that needs a per-audience value is `timeout_seconds` (§5.4).

### 12.2 Budget reality

Measured ceiling ~9 cycles at ~14,400 tokens/call — see
`2026-08-08-turn-budget-finding.md`. **Qualifier:** that trace is a *pathology*,
~86k of its 130,809 spent in a budget-veto retry loop. Not a baseline. A 3–4 cycle
fire turn has room.

---

## 13. Metrics — new series, not new labels

Metric **names** are append-only (`nora/obs/metric_specs.py:40`). *Labels are
not* — `:41-44` records `platform` being added to four existing series. v2 said
both; only the first is true.

Even so, fire turns must not silently move tag-path series
(`nora_tags_total`, `nora_tag_latency_seconds`, `nora_messages_per_tag`,
`nora_silent_failures_total`), so the fire path gets its own:

| metric | why |
|---|---|
| `nora_reminders_created_total{status}` | writes and refusals, by reason |
| `nora_reminders_fired_total{outcome}` | `sent` / `failed_*` (§8.1) |
| `nora_reminders_pending` (gauge) | runaway visible without a query |
| `nora_heartbeat_last_tick_age_seconds` (gauge) | **liveness** — a gauge that climbs when up-but-not-ticking, which a frozen fired-count cannot show (R-5). Same intent as `nora_presence_last_collection_age_seconds` (`metric_specs.py:489`). |

**The liveness gauge only climbs if it is published from outside the tick body.**
The presence precedent publishes from *inside* its loop
(`collector.py:375-390`), so a loop that dies leaves the gauge frozen — the exact
opposite of what a liveness signal is for. Either store `last_tick_at` and let the
metrics endpoint compute the age on scrape, or follow `prune_loop`'s never-die
discipline (`runtime.py:109-117`) so the loop cannot stop publishing. Copying the
collector verbatim would ship a liveness metric that goes quiet precisely when it
matters.

These append to `_COUNTERS`/`_GAUGES` and flow into `DECLARED_NAMES`.

**No test will catch it if they are forgotten.** `tests/test_metrics_export.py`
asserts only that a frozen historical set is still present
(`PRESERVED_METRIC_NAMES <= DECLARED_NAMES`, `:31`); adding new series requires no
edit and the suite stays green either way. So these four are held by review, not
by a gate — worth knowing, since §14 exists to list what *does* catch mistakes.

---

## 14. Gates

| gate | why |
|---|---|
| **`design/09-decision-index.md` — R-1** | *"Only the tagging path may cause a send"*. A fire turn is a send not caused by a tag. **This is the rule the design changes.** Its named test (`test_ambient_never_constructs_agent`, `tests/integration/test_ambient.py:13-19`) asserts both clauses — but only over *ambient input*, so a reminders fire path never enters its harness and it stays green untouched. Amending the row alone therefore deletes the only written brake on a third send path without anything going red. **A replacement named test is required**: the tag path and the fire path are the only two senders. |
| hand-written alembic migration + `tests/infra/test_schema_parity.py` | new table, CHECK constraint, named index |
| `tests/contract/test_reminder_store_contract.py` | **required, not conditional.** The `ReminderStore` port gets a SQL adapter *and* a test fake, and CLAUDE.md forbids a second implementation of a port — including a fake — without a shared contract suite both pass (§16 step 1) |
| `tests/test_acceptance_rules.py::RULE_TESTS` | mapped-identity guard, never-early, fires-once |
| `tests/test_guards_terminal.py` | must stay green — §6.1 adds no terminal verb |
| doctrine tests | tag-audience body-identity **and** heartbeat-audience non-degeneracy (§10.1) |
| queue-hoist tests | FIFO order must still hold after §5.3's refactor |
| `tests/test_metrics_export.py` | **a gate for the label, not for the names.** New metric *names* pass untouched (§13). But `:76` and `:152` assert **exact rendered label sets** — e.g. `nora_silent_failures_total{platform="signal",reason="llm_error"} 1.0` — and `generate_latest` emits labels **sorted**, so adding `audience` renders `{audience="",platform=…}` and both fail. `:152` is `test_platform_label_present_on_the_four_named_series`, i.e. the test the cited precedent itself produced — direct evidence that a label change owes a test edit |
| `ARCHITECTURE.md` | `test_architecture_doc.py:8` fails on any new top-level package **or module** under `nora/` |
| `uv run pytest -q` / `ruff` / `mypy` (scoped) / `lint-imports` | CLAUDE.md's four |

**Placement: `nora.app`.** The importlinter layers contract puts
`nora.app | nora.presence` above engagement/agent/infra/domain, so a component
touching all of them belongs there and passes cleanly. Cost: `nora.app.*` is
inside the mypy strict override, so the fire path must be fully typed; and
`nora.app` must not import `strands` directly (`tests/test_boundaries.py:54`) —
fine via `LiveAgentRunner`/`factory`. A new top-level `nora/reminders/` would be
**absent from every importlinter contract** and is rejected for that reason.

**A trap in `config.example.toml`, stated because an earlier draft got it exactly
backwards.** The example is *not* diffed against `config.toml` — the only
cross-file assertions are `tests/test_turn_budget.py:99-100`, covering
`[llm.limits]` and `max_tokens`. But it is not ungoverned either:
`tests/test_config.py` loads **the example** as its fixture and asserts specific
values, including `cfg.llm.timeout_seconds == 30` (`:135`).

So `timeout_seconds` is 60 in `config.toml` and 30 in the example, and the
example's stale value is **pinned by an explicit assertion**: "fixing" it turns
`test_config.py` red. Anyone touching it must change the assertion in the same
commit. New `[heartbeat]` / `[reminders]` keys go into both files by hand, and any
value asserted in `test_config.py` must match what is added.

---

## 15. Honest limits

- v1 reminders **fire unconditionally**. A changed plan is handled by cancelling.
- **At-most-once**: a crash mid-fire loses that reminder (§8.2).
- `Room.scope()` degrades to the tagger alone when the roster is unreadable
  (`nora/agent/room.py:151-167`); on a fire turn that is the requester. Tools other
  than `search_memory` answer with `partial_view` rather than failing closed.
  Bounded here because §7.2 keeps the room the requester's own.
- Cap values are unmeasured (§9); phase 2's feasibility is unmeasured (§11).
- **Only `nora_silent_failures_total` gains the `audience` label.** A fire-path
  send is therefore indistinguishable from a tag reply in `nora_replies_total`,
  and likewise in `nora_guard_vetoes_total` / `nora_leak_blocks_total` /
  `nora_egress_blocks_total` — all of which `Deliverer` moves on both paths. The
  alarm is separable; the traffic counters are not. Accepted for v1 because the
  alarm is what has an SLO.
- §5.3's hoist touches the live tag path. It is the riskiest change in the design
  and it is a prerequisite, so it lands first, alone, behind its own tests.

### 15.1 Prior art

Almost no shipped agent does implicit cancellation — ChatGPT Tasks, Gemini, Siri,
Alexa and Todoist all fire unless explicitly cancelled. The dominant pattern is
*scheduled prompt, fresh context*, which is independently §2's deferred request.
So phase 1 matches the baseline and phase 2 is the part with no precedent — which
is why it is gated on a measurement. *(External knowledge, uncited, not verifiable
from this repo.)*

---

## 16. Build order

0. **Hoist conversation serialisation** out of `EngagementPipeline` into an
   injected collaborator (§5.3), via the coroutine-factory escape. Live-path
   refactor, no new behaviour, FIFO tests must stay green. **Blocks step 3** (the
   tick, which needs §5.3's slot acquisition) — not steps 1 or 2, which touch
   nothing the queue owns. It lands first and alone regardless, because it is the
   only live-path change in the design and should not share a review with new
   behaviour.
1. **Table + migration + `ReminderStore` + boot sweep + `nora reminders list`.**
   No model, fake-clock testable end to end. The §8.2 sweep (`firing` → `failed`)
   belongs here — earlier drafts specified it and named it in no step.

   **The store needs a port decision before a line is written.** Under the layers
   contract a store is a **port in `nora.domain`** with an **adapter in
   `nora.infra`** — and the moment a test fake exists, CLAUDE.md requires a shared
   contract suite both pass. §8.2 already invokes that rule for `RecordStore` and
   then never applied it here. So step 1 owns: the port, the SQL adapter, the
   fake, and `tests/contract/test_reminder_store_contract.py`. §14's gate table
   lists the contract suite for exactly this reason.

2. **Doctrine audience** — the `heartbeat` audience, the §16.1 extraction, and
   the `applies_to` call sites. **Moved ahead of the fire turn deliberately:**
   today `get(applies_to="heartbeat")` returns only the generated capability
   section, so a fire turn built before this ships would deliver messages with no
   identity, no voice, no conduct and no constraints — and would hand that
   near-empty doctrine to `LeakGuard` (`guards.py:83`), gutting R-6's
   verbatim-leak corpus on the very step that first sends. An earlier draft had
   this as step 4, after delivery. That ordering was wrong.
3. **Tick + policy re-check + outcome recording**, with a stub turn returning the
   §8.1 outcome enum. Still no model.
4. **The fire turn** — own `TagContext` and synthetic message (§6.4), header
   parameter (§6.3), own `wait_for` and error accounting (§6.2), `required`,
   deliver. Plus the `audience` label, whose full scope is: **all nine increment
   sites** (§6.2), the **three** exact-label assertions in
   `tests/test_metrics_export.py:76`, `:152` and `tests/test_metrics_stub.py:48`,
   and the A8 expression in `docs/runbook.md:10` / `:17`.

   *`test_metrics_stub.py` was missed by v3.3's list of two. `snapshot()` keys on
   `tuple(sorted(sample.labels.items()))` (`metrics.py:135-138`), so the added
   label changes the key and the exact-2-tuple subscript raises `KeyError`. Every
   other assertion in the suite reads through `Metrics.get`, which subset-matches
   (`metrics.py:51-53`) and survives untouched.*
5. **Tools** — `remind` / `list_reminders` / `cancel_reminder`, plus the
   `Room.tagger` accessor (§12) and the `fire_turn` keyword threaded through
   `build_tool_specs` / `specs()` / `build()` (§7.1).

### 16.1 The doctrine edits, exactly

`check_authority_chain` runs inside `_scan` over **all** scanned sections
(`loader.py:490`), before any `applies_to` filtering — so a `[tag]`-only
`silence.md` named by `[tag, heartbeat]` files is fine for both audiences.

- **New `nora/doctrine/silence.md`** — all ten `_REQUIRED` keys; `name: silence`;
  `order` in 21–29; `kind: conduct`; `applies_to: [tag]`;
  `authority_above: [constraints, identity, voice]`;
  `authority_below: [capability, situation, message-content, tool-output]`.
- **Add `silence` to `authority_below`** in `identity.md`, `voice.md`,
  `constraints.md` (symmetry, `loader.py:402-415`).
- **`conduct.md`** — body loses only the first three sentences of
  `# Silence is the default`; **the multi-message rule (DEC-40) stays**, under its
  own heading. `version: 5` → 6. No chain edit needed if `silence` names it in
  neither direction.
- **All four** — `applies_to: [tag]` → `[tag, heartbeat]`.
- **No edit to the capability section** — its `authority_above` is derived
  (`loader.py:369`) from whoever lists `capability` in `authority_below`, so it
  picks up `silence` automatically.
- `check_kind_boundaries` and `check_normative_register` pass on the extracted
  text as it stands (no snake_case token, no uppercase RFC-2119 keyword).
