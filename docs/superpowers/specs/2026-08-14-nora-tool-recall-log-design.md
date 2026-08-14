---
kind: spec
title: "Nora tool-recall log: reusing a lookup from earlier in the conversation"
description: "A NativeTool gains a declared cacheable field (mirroring budget/terminal), true today for place_info/route/web_search/weather/transit — world-fact lookups with no room/person-disclosure decision baked into the moment they ran. A new on-demand recall_lookups tool lets the model pull recent cacheable results back within a 7-day window, instead of re-querying for something already looked up 10-20 messages ago. Extends R-45's nudge, status-aware, to also cover a recall_lookups call."
tags: [nora, spec, tools, recall, memory, make_artifact]
timestamp: '2026-08-14'
status: accepted
derives_from:
  - design/09-decision-index.md
  - docs/superpowers/specs/2026-08-13-nora-compute-context-channel-design.md
---

# Nora tool-recall log

## 0. What this document is

The operator's own words, describing a real, repeated pain: *"for the past
10 or 20 messages, we have been conversing about a place, a restaurant, or
even games. If I suddenly ask Nora to put that in a report, the problem is
that Nora has already lost the context — like the place, the length, the
latitude, or any information about it. It probably has to search all over
again, which means that effort is wasted."*

This is a DIFFERENT gap from R-45/DEC-101 (2026-08-14, same day). R-45 fixes
the model reusing a result it already has THIS TURN. This document fixes the
model having no way to reuse a result from an EARLIER turn at all — the
information genuinely isn't anywhere the model can reach: not in the
transcript in structured form (only her own prose reply, if she replied),
not in `context` (per-turn only, discarded once the tag ends), not in
Honcho. Honcho carries only her own reply text (fed under the `nora` aiPeer,
DEC-65 — DEC-36 is the earlier, narrower decision that her reply is never
fed to the OBSERVED-side session at all; DEC-65 restored a feed at the
`nora` aiPeer specifically) and the tagger's own words — never a tool's raw
structured result. Confirmed directly: `nora/engagement/stages/feedback.py`
feeds only `deliverer.sent_texts`; `nora/ingest/stages/memory_feed.py` feeds
only `message.body`. Nothing else reaches Honcho.

Scope, settled during brainstorming: this is broader than `make_artifact`.
Any follow-up — "what was that place's rating again?", not just building a
report — hits the same wall.

**Revision note (2026-08-14):** the first draft of this document proposed a
denylist ("log everything except two tools") and several claims about
existing infrastructure that an independent review found factually wrong —
most importantly, that the denylist would re-serve `where_is`/
`search_memory`/`about` results past the ROOM-roster-dependent disclosure
check each of those tools runs at the moment it's actually asked (R-21/R-31/
DEC-68/DEC-79), because a cached answer from days ago carries no guarantee
the room hasn't changed since. §2 is rewritten to a declared-allowlist
design that never touches that class of tool at all. The review also
corrected a hook-provider claim (§3), a budget citation (§4), a table-
pruning claim (§6), and several file-placement details (§7); each is fixed
in place below with what was wrong and why.

## 1. Why not a vector-similarity cache

The operator's own first sketch: embed every tool call, look up by cosine
similarity before running a tool again, treat a high-similarity hit as a
cache hit. That is a legitimate, more powerful design — it would also
handle a VAGUE reference ("that Japanese place near Harbour City") and gaps
longer than the retrieval window below. It was set aside for v1 for three
concrete reasons, not because it's wrong:

1. **Three new pieces of infrastructure, not one.** `db` runs plain
   `postgres:18-alpine` — no pgvector extension, so this needs a new base
   image plus `CREATE EXTENSION vector`. This stack wires three models
   today, all generative (`[llm] model = "ollamacloud/deepseek-v4-flash"`,
   `[media] vision_model`, `[evals] judge_model`) — none is an embedder, so
   this also needs a new model selected, wired, and paid for.
2. **A real correctness risk, not just an engineering cost.** A similarity
   threshold that's too loose serves a STALE or WRONG cached result as if it
   were fresh — exactly the "ERROR read as PASS" failure shape R-27 exists
   to name and refuse elsewhere in this codebase. Getting the threshold
   right needs deliberate design (a hard TTL ceiling regardless of
   similarity score, at minimum), not a first pass.
3. **It doesn't match what the operator actually hit.** The reported
   scenario is recency-bound ("the past 10 or 20 messages"), not a vague
   reference to something from weeks ago. A much smaller mechanism closes
   the actual gap.

Vector similarity remains a legitimate follow-up if recency-bound recall
turns out insufficient — e.g. if gaps are routinely longer than the
retention window, or references too indirect for the model to know a
`recall_lookups` call would even help. Not built now (YAGNI).

## 2. Data model: `cacheable` is declared on the tool, not a separate list

**The security gap in the first draft, and why an allowlist.** `where_is`
and `search_memory` each decide what they're allowed to say AT THE MOMENT
they're asked, against the room's LIVE roster: `where_is` refuses to
disclose a circle member into a room it is not a verified subset of
(R-31), and `search_memory` refuses to query at all in a room holding
anyone it cannot place (DEC-68). A row logged today and replayed by
`recall_lookups` six days later carries none of that — the room's
membership can change in between, and the cached answer would still be
served as if the check had just run. `about`/`ask_memory` carry the same
roster-gated shape. A denylist naming only `send_reply`/`recall_lookups`/
`make_artifact` as exclusions — the first draft's design, following the
operator's own "log everything, let Nora decide at read time" instruction
— misses this entire class. Logging must default OFF and be turned on
per-tool, deliberately, not the reverse.

**The fix mirrors a pattern already in this codebase, not a new one.**
`NativeTool`'s `budget`/`terminal` fields already declare a per-tool
property AT the tool's own definition, and `NATIVE_TOOL_BUDGETS`/
`terminal_tools()` derive their sets from those declarations rather than
maintaining a second, parallel list (`nora/agent/tools.py:539-541`; DEC-10's
own argument for moving `budget` out of a hardcoded guard attribute:
"the number a tool costs lives with the tool, not with the guard"). `cacheable`
joins them as a third declared field, default `False`:

```python
class NativeTool(NamedTuple):
    budget: int
    description: str
    terminal: bool = False
    cacheable: bool = False   # NEW — may this call's result be logged and
                               # later replayed by recall_lookups? Default
                               # False: a future tool ships un-cacheable
                               # until someone makes the call explicitly,
                               # the same direction NATIVE_TOOL_BUDGETS
                               # already defaults safe (an unbudgeted tool
                               # is denied, not silently allowed).
```

Set `cacheable=True` on exactly five entries — chosen because none of the
five carries a room/person-disclosure decision baked into the moment it
ran, and each has a real cost to redo (an external call, not a free local
computation):

| tool | why cacheable |
|---|---|
| `place_info` | a business's rating/address/hours/price — public, and the request scenario itself |
| `route` | travel time/duration between two places |
| `web_search` | covers "games" and anything else researched, not just places |
| `weather` | conditions go stale fast, but carry no disclosure risk — `recall_lookups`'s own age field (§4) lets the reader judge freshness |
| `transit` | HK bus/feeder route lookups |

And explicitly NOT (so the boundary is a decision, not an oversight):

- `where_is`, `saved_places`, `about`, `ask_memory`, `search_memory` — the
  room-roster-dependent disclosure class above.
- `group_members`, `lookup_person` — a stale membership/identity snapshot
  would be actively misleading, not merely unhelpful; people join and
  leave.
- `media_fetch` — already has its own purpose-built cache
  (`nora/infra/vision/cache.py`'s `VisionCache`, a process-lifetime LRU
  keyed by content hash) that this document should not duplicate.
- `load_transcript` — it IS the transcript; caching it would be a second
  copy of `messages`.
- `remind`/`list_reminders`/`cancel_reminder`, `version`, `calculate` — not
  "facts learned" worth saving a re-fetch on: the reminders store and the
  version manifest are already the source of truth and cost nothing extra
  to re-read, and arithmetic costs nothing to redo.
- `now`, `send_reply`, `search_gifs`, `send_gif`, `skills`, `make_artifact`
  — not lookups (time is always-changing by definition; the rest are
  output/action tools, not research).

`ToolCallLog` (§3) derives its own write-eligible set from
`{name for name, spec in NATIVE_TOOLS.items() if spec.cacheable}` — the
same derivation shape `NATIVE_TOOL_BUDGETS` already uses — so a future
tool's author decides `cacheable` right where they already write its
`budget`/docstring, not in a file they would have to remember exists.

**Reconciling with the existing logging discipline.** `nora/agent/tools.py`'s
own convention (the block around line 593-620) is explicit: a log line may
carry a tool's name, one status, and counts — "never a conclusion, never a
transcript body, ... and never the model's own ARGUMENT." `tool_call_log`
stores full `tool_input`/`tool_result` JSON, which looks like it violates
that rule on its face. It doesn't, and the distinction matters: that
convention is about `data/nora.log`, a file exposed at "every exit" —
readable by anyone with host access, outside this conversation's own
boundary. `tool_call_log` is a Postgres table scoped to one
`conversation_id`, read back ONLY by `recall_lookups`, itself scoped to the
SAME conversation. Nothing here is disclosed to anyone who could not
already see it: the request and its answer both already happened in front
of this same room. The two are different exit surfaces with different
rules, and this document's five cacheable tools' arguments are, separately,
already narrow: `place_info`/`route`'s own arguments are already screened
by R-28 (`scan_identifiers`/`_looks_like_coordinate`/`_names_a_person`)
BEFORE either call is allowed to succeed, so a logged row inherits that
guarantee. `web_search`/`weather`/`transit` carry no equivalent argument
screen today (a search query, a place name, a bus route number) — worth
naming as a residual, not a blocking one: none of these three tools exists
BECAUSE it might be asked to relay a person's address the way `route`/
`place_info` do, so the risk they were built to guard against does not
apply to them the same way.

**Table**, one row per qualifying call:

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `conversation_id` | text | scopes recall to ONE conversation, never cross-conversation — the same scoping `messages`/transcript already hold |
| `conversation_type` | text | mirrors `messages`'s own column and its `CHECK (conversation_type IN ('group','dm'))` constraint (`nora/infra/sql/schema.py`) |
| `tag_id` | text | the transcript anchor this call happened under — given a row, you can find which turn produced it, bounded by however long that turn's own row survives `messages`'s own pruning (§6) |
| `tool_name` | text | |
| `tool_input` | JSONB | the tool's own arguments, as called |
| `tool_result` | JSONB | the tool's own return value, round-tripped through the same JSON encoding Strands already uses to carry it back (§3) |
| `created_at` | timestamptz | |

Index: `(conversation_id, created_at DESC)` — the only query shape v1 needs
(§4).

A call the guard vetoed, or whose tool answered anything other than `ok`
(`refused`/`unavailable`/`failed`/`nothing_found`), writes nothing —
"you can probably exclude the null things because they are kind of
redundant" (operator). There is nothing to recall about a call that told
the model it could not check.

## 3. Write path: a new hook provider — real wiring, not free

**Correction:** the first draft claimed this "changes no other class's
shape," citing `ReplyGuard`/`LeakGuard` as an existing pair of composable
hook providers. That's wrong on its own terms: `LeakGuard`
(`nora/agent/egress.py`) is a plain class with a synchronous `check()`
method, constructed inside `Deliverer.__init__` and called directly — it
registers no hooks and is never handed to Strands. `ReplyGuard` is the
ONLY `HookProvider` in this repo today (`grep HookProvider` — one class),
hardcoded as `hooks=[guard]` in `nora/agent/factory.py`. Adding a second
provider is real integration work, not a free extension:

- `nora/agent/factory.py` — `hooks=[guard]` becomes a list of providers.
- `nora/app/agent_runner.py` — `LiveAgentRunner.invoke`'s signature and
  pass-through.
- `nora/engagement/stages/deliver.py` — `DeliveryHandle`/`build` construct
  and thread the new collaborator (and its store).
- Both production callers of `deliver.build`:
  `nora/engagement/pipeline.py` and `nora/app/fire.py` — meaning the
  heartbeat/reminder/version-announcement path ALSO calls `deliver.build`
  (`nora/app/fire.py:520`), so whether a fire-path turn's tool calls get
  logged is a decision this document defers to the implementation plan,
  named here so it isn't silently decided by whichever wiring happens to
  be easiest.
- `nora/testing/scripted_agent.py` — currently registers exactly one
  provider (`guard.register_hooks(hooks)`); leaving a new provider
  unregistered here would let every scripted test scenario pass against a
  mechanism it never actually exercises — the precise kind of test/prod
  divergence DEC-33 and DEC-42(10) already removed twice elsewhere in this
  codebase.
- `nora/app/composition.py` and the offline/eval compositions — construct
  or explicitly omit the store (offline and eval have no DB).

Async hook callbacks ARE supported (`HookRegistry.invoke_callbacks_async`
awaits a coroutine callback), so an `await store.append(...)` inside the
hook is possible — but it is a real DB round-trip landing inside the
turn's own `asyncio.wait_for` budget, on every cacheable call (up to the
sum of those five tools' budgets), not a free synchronous no-op. Unlike
`DeliveredObserver.settle()` (a genuine no-op default, the analogy the
first draft used), this write has latency; the implementation plan should
measure it against the existing turn-budget headroom before assuming it's
free.

**Correction:** the first draft also claimed the hook checks "the tool
result's own `status` field." `AfterToolCallEvent.result` carries STRANDS'
own wrapper — `{"status": "success"/"error", "content": [{"text": <json
str>}]}` — not the tool's own `ok`/`refused`/`unavailable` status. Reaching
the tool's own status requires `json.loads(event.result["content"][0]["text"])`
first. Three of this document's five cacheable tools additionally need
checking against their actual return shape before this is written: confirm
`place_info`/`route`/`web_search`/`weather`/`transit` all return a dict with
a `status` key (the pattern most native tools follow) — the implementation
plan verifies this per tool rather than assuming it uniformly, since at
least three OTHER native tools in this file (`now`, `send_reply`,
`load_transcript`) do not follow it.

`ToolCallLog`'s hook checks `cancel_message is None` (mirroring
`ReplyGuard._after_tool`'s own discrimination between a vetoed and a
completed call) AND the unwrapped tool status, and only then — only for a
tool whose `NativeTool.cacheable` is `True` — writes a row.

Write failures are logged and swallowed, never surfaced to the model or the
turn — this is decoration on a turn that has already completed correctly
without it. A missed log row costs one future recall opportunity; it must
never cost the reply that already went out.

## 4. Read path: `recall_lookups`, an on-demand tool

Not auto-injected into every prompt. Auto-injection was considered and
rejected during brainstorming: it would tax every single turn's token
budget with a standing block of possibly-irrelevant old lookups. This
codebase's own recall verbs (`load_transcript`, `about`, `ask_memory`,
`search_memory`) are all model-invoked, on demand — `recall_lookups`
follows the same shape. (One EXISTING exception worth naming rather than
overlooking, since the first draft implied there were none: the per-tag
prefill already auto-injects up to 8 Honcho conclusions as a `<self_card>`
block, `nora/agent/prefill.py`, deliberately sized (~1,080 chars) for
exactly the token-cost reason argued here — a real sizing precedent for
`recall_lookups`'s own `limit`, not a contradiction of the on-demand
choice: a `<self_card>` is about the PERSON Nora is talking to, small and
near-constant in shape; a tool-call log entry is about whatever was
researched, variable in size and not always relevant, which is why IT
stays on-demand rather than following `<self_card>`'s auto-injected shape.)

```python
async def recall_lookups(limit: int = 10) -> dict:
    """Pull back facts you (or an earlier turn) already looked up in THIS
    conversation, within the last 7 days — a place's rating, a route's
    duration, a web search's results. Call this before re-running
    place_info/route/web_search/weather/transit for something that might
    already be known: reusing what you already have costs nothing;
    re-searching costs a live call and may not even find the same result
    twice.
    """
```

- **Scope:** this `conversation_id` only (never cross-conversation).
- **Window:** `created_at > now() - interval '7 days'`. Bounded, per the
  operator's own choice during brainstorming — recency-only for v1 (§1).
- **Ordering:** newest first — "the past 10 or 20 messages" is the stated
  scenario, so recency is the whole retrieval logic for v1.
- **`limit`:** model-chosen, clamped server-side — mirrors
  `LOAD_TRANSCRIPT_MAX`/`LOAD_TRANSCRIPT_DEFAULT`'s own reasoning
  (`nora/agent/tools.py:574-575`, verified: 60/25). Proposed: default 10,
  max 30 — a tool result is denser than one transcript message, so a
  smaller ceiling is enough; the implementation plan may revise these on
  its own measurement.
- **Budget:** the first draft cited `load_transcript`/`about`'s budget as
  "2, matching" — that's wrong; both are budget **4** as of 2026-08-08
  (`nora/agent/tools.py`'s own comment block records the doubling; a STALE
  comment elsewhere in the same file still says "budget: 2" and is what
  the first draft actually read). `recall_lookups`'s own budget should be
  picked on its own merits at plan time, not by copying a citation that
  turned out to be outdated — 2 is a reasonable starting proposal (run
  once, notice a different `limit` is needed, run once more) but is not
  inherited from precedent the way the first draft implied.
- **Return shape:** the raw logged rows (`tool_name`, `tool_input`,
  `tool_result`, and how long ago each happened, so the model can say "I
  checked this 3 messages ago" rather than presenting a stale fact as
  fresh) — never re-summarised or filtered server-side.
- **R-27:** an empty result (nothing logged, or nothing within the window)
  is `nothing_found`, distinct from a DB read failure (`unavailable`).

**Naming, unmeasured.** `recall_lookups` would be a fifth tool in a family
that already has `load_transcript`/`about`/`ask_memory`/`search_memory`.
DEC-39 measured a sibling collapse from exactly this kind of addition
(`person_profile` onto `lookup_person`, 0/16 self-routing, and it dragged
`load_transcript` from 14/24 down to 3/16); DEC-58 is the method this
codebase already uses for exactly this question — real capability block,
real schemas, only the identifier varies, n=16, decline on any sibling
leakage even if self-routing itself holds. The implementation plan should
run that measurement on `recall_lookups` before shipping the name, the
same way DEC-58/DEC-98 both did for their own tool names.

## 5. Touches R-45 (DEC-101) — status-aware, not a one-line add

R-45's nudge currently fires when `make_artifact` is called with `context`
empty after `self.counts["place_info"] + self.counts["route"] > 0`
(`nora/agent/guards.py`). **Correction:** the first draft called extending
this to `recall_lookups` "one line." It touches three lines (the condition,
the `gathered` sum, and the veto message text, which currently reads "You
already called place_info/route... and have those typed results" and would
become false the moment `recall_lookups` is the thing that ran) — and more
importantly, `ReplyGuard.counts` is incremented by `record()` for ANY
non-cancelled, non-raising call, with no visibility into what the tool
itself actually answered. A `recall_lookups` call that came back
`nothing_found` would still increment the count, and a naive extension
would fire R-45's nudge telling the model to pass context it demonstrably
does not have.

The fix: `ReplyGuard` needs to learn whether `recall_lookups` actually
found something, not just that it ran. The implementation plan's options
(neither trivial, both smaller than a new subsystem): (a) `ReplyGuard`
inspects the tool's own unwrapped result the same way `ToolCallLog` does
(§3), rather than relying on the status-blind `counts` mechanism, for this
one check; or (b) `recall_lookups` itself is excluded from the R-45
trigger and this document's own read path is judged sufficient on its own
— the nudge stays scoped to `place_info`/`route`, since a model that just
called `recall_lookups` and got real results back has, by definition,
already seen data it could pass as `context`, and a SECOND system nudging
it to notice its own tool call's own return value is arguably redundant
with the tool call itself. The plan decides between (a) and (b); this
document does not pre-commit to which.

DEC-101's own decision-index row would need amending either way, since it
currently quotes the pre-extension trigger condition verbatim.

## 6. What this does not do

- **No topic filtering.** `recall_lookups` cannot be asked for "the
  restaurant stuff" specifically if two topics were researched in the same
  window — it returns the last N, and the model reads through them. Named
  as the deliberate v1 scope limit (§1), not a silent gap.
- **No proactive staleness re-check.** A recalled `place_info` result is
  presented as what it was when logged, with its age, not re-verified
  against Google. The model (or the operator, reading a report built from
  it) judges whether a rating from 4 days ago is still trustworthy for the
  purpose at hand.
- **No cross-conversation recall.**
- **Retention is NOT "no pruning," and needs its own policy.**
  **Correction:** the first draft claimed `messages` has "no active
  pruning" as precedent for leaving `tool_call_log` unbounded. That's
  wrong: `SqlRecordStore.prune_all()` trims every conversation to
  `max_per_conversation` (500, `config.toml [store]`), run periodically
  from `nora/app/runtime.py`; `presence_fixes` separately has
  `retention_days = 180` with its own sweep. Every durable table in this
  repo gets pruned — `tool_call_log`, storing full `web_search` result
  payloads, is the outlier if it doesn't, not the precedent-following
  choice. v1 can still ship without an ACTIVE prune job (the 7-day window
  in §4's query means old rows are simply never READ, which is a real
  mitigation, not nothing) — but the implementation plan should state this
  as a deliberate choice with a named revisit trigger (e.g. "add a prune
  job once row count crosses N" or "once this ships"), not as inheriting a
  precedent that turns out not to exist.

## 7. Files

- **Create:** `nora/domain/tool_call_log.py` — the domain type AND its
  store protocol together, following `nora/domain/compute.py`'s own
  precedent (`ComputeJobStore` lives beside `ComputeJob`, not in
  `nora/domain/ports.py` — **correction**, the first draft named
  `ports.py` while citing `ComputeJobStore` as its template, which
  contradicts itself; `ReminderStore` in `nora/domain/reminders.py`
  confirms the same shape a second time).
- **Create:** the SQL implementation (`nora/infra/sql`) and a
  `FakeToolCallLogStore` (`nora/testing`) — a port never gains an
  implementation without a shared contract suite
  (`tests/contract/test_tool_call_log_store_contract.py`, parametrized
  `["fake", "sql"]` the same way
  `tests/contract/test_compute_job_store_contract.py` already is).
- **Create:** the migration adding `tool_call_log` (hand-written, per this
  repo's own rule for a DESC/expression index specifically — CLAUDE.md's
  `idx_messages_conv_ts` warning is about alembic's autogenerate being
  blind to a `sa.text("ts DESC")` declaration and emitting a phantom
  remove/add pair forever, not about "any new index" in general; the
  `(conversation_id, created_at DESC)` index here IS that same DESC shape,
  so the same hand-written rule applies, and it needs registering in
  `nora/infra/sql/migrations/env.py`'s `_DESC_EXPRESSION_INDEXES` and in
  `tests/infra/test_schema_parity.py`, the same two places
  `idx_messages_conv_ts` itself is).
- **Modify:** `nora/agent/tools.py` — `NativeTool` gains `cacheable: bool =
  False` (§2); the five `cacheable=True` entries; the new `recall_lookups`
  tool, budgeted in `NATIVE_TOOLS` directly (**correction**: budgets are
  DERIVED into `NATIVE_TOOL_BUDGETS` — `nora/agent/registry.py` reads that
  derived mapping, it does not hold a budget entry of its own; the first
  draft had this backwards).
- **Modify:** `nora/agent/guards.py` — the new `ToolCallLog` hook provider,
  and R-45's status-aware extension (§5).
- **Modify:** `nora/agent/factory.py`, `nora/app/agent_runner.py`,
  `nora/engagement/stages/deliver.py`, `nora/engagement/pipeline.py`,
  `nora/app/fire.py`, `nora/testing/scripted_agent.py`,
  `nora/app/composition.py` (+ offline/eval compositions) — the real
  wiring list from §3, not optional.
- **Modify:** `design/09-decision-index.md` (a new DEC row) and
  `tests/test_acceptance_rules.py::RULE_TESTS` for whichever guarantee
  this feature's plan decides is the named rule (candidates: "a
  non-cacheable tool's result is never logged," "a vetoed/refused call
  writes nothing") — the implementation plan assigns the actual DEC/R
  numbers at write time, following the same practice DEC-100/R-44 and
  DEC-101/R-45 both did.
- **Measure before shipping:** `recall_lookups` against DEC-58's method
  (§4) before the name is treated as final.
- **Release fragment:** `release-fragments/unreleased/` — user-observable
  (a new tool the model can call, and better report/reply quality when it
  does).
