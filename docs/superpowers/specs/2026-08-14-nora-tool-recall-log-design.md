---
kind: spec
title: "Nora tool-recall log: reusing a lookup from earlier in the conversation"
description: "Every native tool's successful call is logged (denylist, not allowlist), linked to the tag it happened under. A new on-demand recall_lookups tool lets the model pull recent results back within a 7-day window, instead of re-querying place_info/route/web_search for something already looked up 10-20 messages ago. Extends R-45's context nudge to also fire off a recall_lookups call."
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
Honcho (which holds only her reply text and the tagger's own words, per
DEC-36 — never a tool's raw structured result).

Scope, settled during brainstorming: this is broader than `make_artifact`.
Any follow-up — "what was that place's rating again?", not just building a
report — hits the same wall.

## 1. Why not a vector-similarity cache

The operator's own first sketch: embed every tool call, look up by cosine
similarity before running a tool again, treat a high-similarity hit as a
cache hit. That is a legitimate, more powerful design — it would also
handle a VAGUE reference ("that Japanese place near Harbour City") and gaps
longer than the retrieval window below. It was set aside for v1 for three
concrete reasons, not because it's wrong:

1. **Three new pieces of infrastructure, not one.** `db` runs plain
   `postgres:18-alpine` — no pgvector extension, so this needs a new base
   image plus `CREATE EXTENSION vector`. Nora's only wired LLM
   (`ollamacloud/deepseek-v4-flash`, `config.toml` `[llm]`) is text
   generation only; there is no embedding model configured anywhere in this
   stack, so this also needs a new model selected, wired, and paid for.
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

## 2. Data model: a log, not a cache — denylist, not allowlist

A new table, `tool_call_log`, one row per qualifying tool call:

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `conversation_id` | text | scopes recall to ONE conversation, never cross-conversation — the same scoping `messages`/transcript already hold |
| `conversation_type` | text | mirrors `messages`/`compute_jobs`'s own column, for the same reason (a `group` and a `direct` tag are never conflated) |
| `tag_id` | text | the transcript anchor this call happened under — this is the "link to the transcript" the operator asked for: given a row, you can find which turn produced it |
| `tool_name` | text | |
| `tool_input` | JSONB | the tool's own arguments, as called |
| `tool_result` | JSONB | the tool's own structured return value, verbatim — never re-derived from prose, the same discipline `context` already holds (DEC-100) |
| `created_at` | timestamptz | |

Index: `(conversation_id, created_at DESC)` — the only query shape v1 needs
(§4).

**A row is written for every tool call that returned `status: "ok"`,
except:**
- `send_reply` — already in the transcript verbatim; logging it here is a
  second, redundant copy of the same text.
- `recall_lookups` itself and `make_artifact` — the reader and the
  consumer of this log are not themselves entries in it. Logging
  `recall_lookups`'s own return value would let a later call read an
  earlier call's read, an unbounded chain for zero benefit; `make_artifact`
  returns a URL, not a fact worth recalling.

This is a denylist because the operator's own instruction was **"all,
decide by Nora"** — the system does not curate which tools are "lookups"
at write time; every successful call is a candidate, and the MODEL decides
at read time (via `recall_lookups`, §4) whether any of it is relevant right
now. A hand-maintained allowlist would need editing every time a new native
tool ships; a two-entry denylist does not.

A call the guard vetoed, or that returned `refused`/`unavailable`/`failed`,
writes nothing — "you can probably exclude the null things because they
are kind of redundant" (operator). There is nothing to recall about a call
that told the model it could not check.

## 3. Write path: a new hook provider, not bolted onto `ReplyGuard`

`ReplyGuard` (`nora.agent.guards`) is veto/budget logic — a DIFFERENT
responsibility from persistence. This gets its own small collaborator,
`ToolCallLog`, constructed fresh per tag exactly like `ReplyGuard` is, and
registered as its own `HookProvider` on `AfterToolCallEvent` — the same
composable-hook-providers pattern `ReplyGuard`/`LeakGuard` already use, so
adding this one changes no other class's shape.

Ordering: `AfterToolCallEvent` fires for every completed call, whether or
not `ReplyGuard` vetoed it (`ReplyGuard._after_tool`'s own comment: "Strands
fires this hook for a call `_before_tool` vetoed too... `cancel_message` is
the ONLY field that separates the two"). `ToolCallLog`'s own hook checks
`cancel_message is None` the same way, and additionally checks the tool
result's own `status` field before writing — a vetoed call and a
called-but-refused call both write nothing, for the same reason (§2).

Write failures are logged and swallowed, never surfaced to the model or the
turn — this is decoration on a turn that has already completed correctly
without it, the same category `DeliveredObserver.settle()`'s docstring
names ("decoration failing is never allowed to become the turn's problem").
A missed log row costs one future recall opportunity; it must never cost
the reply that already went out.

## 4. Read path: `recall_lookups`, an on-demand tool

Not auto-injected into every prompt. Auto-injection was considered and
rejected during brainstorming: it taxes every single turn's token budget
with a standing block of possibly-irrelevant old lookups, where this
codebase's existing recall verbs (`load_transcript`, `about`, `ask_memory`)
are ALL model-invoked, on demand, never silently included. `recall_lookups`
follows the same shape:

```python
async def recall_lookups(limit: int = 10) -> dict:
    """Pull back facts you (or an earlier turn) already looked up in THIS
    conversation, within the last 7 days — a place's rating, a route's
    duration, a web search's results. Call this before re-running
    place_info/route/web_search for something that might already be
    known: reusing what you already have costs nothing; re-searching
    costs a live call and may not even find the same result twice.
    """
```

- **Scope:** this `conversation_id` only (never cross-conversation — the
  same boundary `load_transcript` already holds for the transcript itself).
- **Window:** `created_at > now() - interval '7 days'`. Bounded, per the
  operator's own choice during brainstorming — recency-only for v1 (§1),
  and an unbounded query would let `recall_lookups` surface a rating/price
  that's since drifted without any staleness signal attached.
- **Ordering:** newest first — "the past 10 or 20 messages" is the stated
  scenario, so recency is the whole retrieval logic for v1.
- **`limit`:** model-chosen, clamped server-side — mirrors
  `LOAD_TRANSCRIPT_MAX`/`LOAD_TRANSCRIPT_DEFAULT`'s own reasoning (the model
  invents the number; the clamp is what stands between that number and the
  turn's token budget). Proposed: default 10, max 30 — a tool result is
  denser than one transcript message, so a smaller ceiling than
  `load_transcript`'s 60 is enough.
- **Budget:** 2, matching `load_transcript`/`about`'s own budget — a recall
  verb that can run once, notice it needs a different limit, and run once
  more.
- **Return shape:** the raw logged rows (`tool_name`, `tool_input`,
  `tool_result`, and how long ago each happened, so the model can say "I
  checked this 3 messages ago" rather than presenting a stale fact as
  fresh) — never re-summarised or filtered server-side. The model decides
  what's relevant; the tool's job is only to surface candidates.
- **R-27:** an empty result (nothing logged, or nothing within the window)
  is `nothing_found`, distinct from a DB read failure (`unavailable`) — the
  same three-state discipline every other tool in this codebase already
  holds. Never silently returns `[]` for both.

## 5. Touches R-45 (DEC-101)

R-45's nudge currently fires only when `make_artifact` is called with
`context` empty after `place_info`/`route` ran THIS turn. This is orthogonal to §2's write-path
denylist: `ReplyGuard.counts` already tracks every call for budget
purposes regardless of what `ToolCallLog` persists, so counting
`recall_lookups` here costs nothing new. Extend the same trigger to also
count a successful `recall_lookups` call this turn: if the
model pulled prior facts back via recall and then still hands
`make_artifact` empty `context`, that is the identical defect R-45 already
names — the model has structured data in hand and typed prose instead. One
line added to `ReplyGuard.evaluate`'s existing check
(`self.counts.get("recall_lookups", 0)` joins `place_info`/`route` in the
`gathered` sum); no new rule, no new veto message.

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
- **No cross-conversation recall.** A fact gathered in one group's
  conversation is never visible to another's `recall_lookups` call — the
  same boundary every other conversation-scoped read in this codebase
  already holds.
- **No pruning job in v1.** Old rows outside the 7-day window are simply
  never read (the query itself filters on `created_at`); nothing deletes
  them. Acceptable for v1 on the same growth profile `messages` already
  has (an append-only table with no active pruning); revisit if storage
  becomes a real concern.

## 7. Files

- **Create:** `nora/domain/tool_call_log.py` (or fold into an existing
  domain module if a smaller shape fits — the implementation plan decides
  this, following whatever precedent `nora.domain.compute` set for a new
  small domain type plus its store protocol).
- **Create:** a `ToolCallLogStore` port (`nora.domain.ports`) and a SQL
  implementation (`nora.infra.sql`), following the exact shape
  `ComputeJobStore`/`SqlComputeJobStore` already established (DEC-99) — a
  port never gains an implementation without a shared contract suite
  (`tests/contract/`), per this repo's own convention.
- **Create:** the migration adding `tool_call_log` (hand-written, per this
  repo's own alembic convention for anything touching a new index — the
  `(conversation_id, created_at DESC)` index needs the same care
  `idx_messages_conv_ts` already got).
- **Modify:** `nora/agent/guards.py` — the new `ToolCallLog` hook provider,
  and R-45's one-line extension (§5).
- **Modify:** `nora/agent/tools.py` — the new `recall_lookups` native tool.
  `nora/agent/registry.py` — its budget entry.
- **Modify:** `design/09-decision-index.md` (a new DEC row — this feature
  is a new behavioural capability, per CLAUDE.md's own rule) and
  `tests/test_acceptance_rules.py::RULE_TESTS` if a new rule number is
  warranted for the "vetoed/refused calls write nothing" guarantee (§2) —
  the implementation plan assigns the actual DEC/R numbers at write time,
  following the same practice DEC-100/R-44 and DEC-101/R-45 both did.
- **Release fragment:** `release-fragments/unreleased/` — user-observable
  (a new tool the model can call, and better report/reply quality when it
  does).
