---
type: design
title: "Fact Dreamer — Per-Adaptor Schedule, Full-Surface Review, and Bounded Context"
description: "The fact extractor runs on a shared global schedule that fires on 30 minutes of quiet, so a single message is consumed as its own batch and a claim's exchange is never accumulated. The Dreamer has no read surface for the tier's own stores. This node gives every adaptor its own schedule, gives the fact tier a comprehensive read surface (facts, notes, memory), and bounds the context the way the orchestrator bounds its own."
tags: [nora, dreamer, fact-tier, schedule, per-adaptor, tools, context-budget, provenance]
timestamp: '2026-09-01'
edges:
  - {dst: 22-config, rel: extends, provenance: asserted}
  - {dst: 23-retrieval, rel: extends, provenance: asserted}
  - {dst: 20-knowledge-tier, rel: extends, provenance: asserted}
  - {dst: 07-loop, rel: corrects, provenance: asserted}
  - {dst: 08-state, rel: extends, provenance: asserted}
  - {dst: 16-open-items, rel: closes, provenance: asserted}
---

# Fact Dreamer — Per-Adaptor Schedule, Full-Surface Review, and Bounded Context

## 1. The problem, in the operator's words

Two complaints, both confirmed against the live system on 2026-09-01:

1. **The extractor fires too often and accumulates too little.** The shared
   fire condition (`nora/app/runtime.py:486`) includes
   `(lag > 0 and quiet >= quiet_seconds)` with `quiet_seconds = 1800` — thirty
   minutes of silence consumes a batch. A single message sent, followed by a
   reply ten minutes later, is split across two batches; the extractor never
   sees the exchange. Measured in Langfuse: batches of 1–7 messages, most
   returning `{"candidates": []}`.

2. **The candidate row carries no attribution.** `CandidateClaim` is
   `(subject, predicate, object, message_ids)` — a bare integer id. The row
   has `observed_in` (ids), `first_seen_at`/`last_seen_at` (when the claim was
   SEEN, not when it was SAID), and no sender. The `messages` table has
   `sender_name`, `person_id`, `ts`, `conversation_id` — the data exists and
   nothing joins it.

The operator's direction, verbatim:

- **Daily cadence for facts.** "每一日淨係行一次" — one run per day, quality
  over latency. "我唔 care 佢行得好耐，甚至可能要行半個鐘至一個鐘冇問題."
- **Per-adaptor config, not a global dreamer.** "之後每一個 dreamer 都有佢
  自己嘅 config，而唔係話 set 死曬個 global dreamer." Fact daily, language
  twice a day, note dreamer hourly — one universal config cannot serve all.
- **A comprehensive review surface.** "search memory 就有 get fact、search
  fact，又有 check notes、get note、search note 嗰啲，你都要加返落去. 我
  expect 呢個 Fact Dreamer 係一個好全面嘅審視."
- **Bounded context.** "唔係所有嘢都要塞曬落個 context 度，如果唔係你個
  token 好快爆." The Dreamer must manage its context the way the orchestrator
  manages its own.
- **Batch cap 1000.** "你食返一千" — `batch_limit = 1000`, not 5000.
- **Big budgets.** "可能甚至要 set 20 萬甚至 50 萬" total tokens; "十幾二十
  turn" for a full review that searches memory, reads about, checks history.

## 2. What is already true (verified against the code)

| Claim | Where | Status |
|---|---|---|
| Budgets are already per-adaptor | `[dreamer.budgets.<name>]` → `AdaptorBudget` (`nora/agent/dreamer.py:96`) | ✅ no change needed |
| `is_nora` rows are excluded from the batch | `RecordStore.since` (`nora/infra/sql/audit.py:154`) | ✅ no change needed |
| The Dreamer has no write tools | `WORKSPACE_SAFE_TOOLS` + `read_only_tools()` ceiling | ✅ structural |
| The fact tier has no read tools | `get_fact`/`search_fact`/`check_note`/`get_note`/`search_note` do not exist in `tools.py` | ❌ to build |
| Schedule is global | `dream_loop` takes one `quiet_seconds`/`max_age_seconds`/`batch_limit` from `[language]` (`nora/__main__.py:197`) | ❌ to split |
| No tool-result truncation in the Dreamer | runtime passes tool results through untruncated | ❌ to build |
| No batch-render cap | `FactCandidateAdaptor.inputs` renders every row (`fact_adaptor.py:292`) | ❌ to build |
| No attribution on candidates | `CandidateClaim` has no sender/ts; `fact_candidate` has no `asserted_by`/`asserted_at` | ❌ to build |

## 3. Design

### 3.1 Per-adaptor schedule

`DreamAdaptor` gains one optional field:

```python
@dataclass(frozen=True)
class AdaptorSchedule:
    min_interval_seconds: float   # floor between runs; None/absent = global
    batch_limit: int             # this adaptor's batch cap
    # quiet_seconds / max_age_seconds are NOT carried here: min_interval
    # subsumes both. A watermarked adaptor with a schedule fires when
    # (lag > 0) AND (now - last_run >= min_interval), and the age-only
    # heartbeat is replaced by the interval itself.
```

`DreamAdaptor.schedule: AdaptorSchedule | None = None` — `None` means "use the
shared `[language]` values", so every existing adaptor is untouched.

`_dream_adaptor_once` (`nora/app/runtime.py:444`) reads the override:

```python
if adaptor.schedule is not None:
    interval = adaptor.schedule.min_interval_seconds
    batch_limit = adaptor.schedule.batch_limit
    # fire when there is unread backlog AND the interval has elapsed
    fires = lag > 0 and age >= interval
else:
    fires = (lag > safety_lag
             or (lag > 0 and quiet >= quiet_seconds)
             or age >= max_age_seconds)
```

The watermark's `updated_at` already records the last run, so `age` is the
natural interval clock — no new state. The age-only latch reset
(`runtime.py:523`) still applies: a run with an empty batch refreshes the
watermark row so the interval clock does not fire every tick.

**The global 5-minute tick stays.** It is the wake interval, not the fire
condition. An adaptor with `min_interval_seconds = 86400` wakes every 5
minutes, checks, and skips until the interval has elapsed — the same shape as
`[heartbeat]`'s "best case for lateness, not a bound" docstring.

### 3.2 Fact tier schedule and budget

```toml
[dreamer.budgets.fact-candidates]
turns = 20              # 2 -> 20: a full review searches memory, reads about,
                        # checks notes, fetches the web — ten to twenty turns
total_tokens = 200000   # 20000 -> 200000: operator's "20 萬甚至 50 萬"
output_tokens = 4000    # unchanged: the answer is still a JSON envelope
timeout_seconds = 3600  # 60 -> 3600: "行半個鐘至一個鐘冇問題"
```

`FactCandidateAdaptor.schedule = AdaptorSchedule(min_interval_seconds=86400,
batch_limit=1000)`.

### 3.3 Bounded context — the orchestrator's own pattern

The Dreamer gets the same three mechanisms the orchestrator uses:

1. **Attributed transcript rendering.** `FactCandidateAdaptor.inputs`
   currently renders `f"{row.id}: {row.body or ''}"` (`fact_adaptor.py:292`)
   — a bare id and a body, with no sender, no time, no room, no media, no
   reply. `TranscriptRow` already carries `sender_name`, `person_id`, `ts`,
   `conversation_id`, `quoted_ts` and `media_json`
   (`nora/domain/models.py:141`); the renderer just does not use them. The
   batch renders as:

   ```
   [2026-08-30 14:03:09] 隨風而去 (friends): 我沖涼先
   [2026-08-30 14:03:14] 隨風而去 (friends): 有人玩call我
   [2026-08-30 14:00:32] Clara Yates (friends): [sticker]
   [2026-08-30 14:01:36] 熱咖啡☕少奶多糖 (friends): @Clara Yates u see see will play this type of game https://www.youtube.com/watch?v=dQw4w9WgXcQ
   ```

   Every line carries: sender name, timestamp, room display name, and the
   body. Media rows render their type from `media_json` (`[image]`,
   `[video]`, `[sticker]`, `[attachment: filename]`) instead of an empty
   body. Reply rows render the quoted target: `(reply to 1950)`. The model
   can then attribute a claim to a person, a moment, a room, and a medium —
   which is the operator's original complaint ("冇寫住邊一個講、幾時講、
   喺邊度講") and the media complaint ("reply、reference、image、video、
   sticker 呢啲"). The objective's `message_ids` contract is unchanged — ids
   are still the provenance key; the labels are context for the model's
   judgement.

2. **Batch-render cap.** `FactCandidateAdaptor.inputs` renders at most
   `MAX_RENDERED_MESSAGES` (500) rows; beyond that it appends a summary line:
   `"... and N more messages (ids X..Y) not shown"`. The model still sees the
   full id range (so it cannot invent an id outside it) but not the full
   text. The objective already says "do not use an id that is not in the
   batch" — the cap keeps the batch's id set intact while bounding the prompt.

3. **Tool-result truncation.** The runtime wraps every tool result before it
   re-enters the loop: `web_fetch`/`web_search` results capped at
   `MAX_TOOL_RESULT_CHARS` (8000), `search_memory`/`about`/`ask_memory`
   results capped at `MAX_MEMORY_RESULT_CHARS` (4000), with a
   `"... truncated"` marker. The model is told the cap exists so a truncated
   result reads as truncated, not as the whole answer.

4. **Per-tool budget already exists.** `NATIVE_TOOL_BUDGETS` caps how many
   times each tool may be called per turn — the Dreamer inherits this. A
   `search_memory` budget of 4 means at most 4 memory searches per run, which
   is the "search 六個人嘅 memory" case bounded.

### 3.4 The comprehensive read surface

New tools, all read-only, all workspace-scoped (the Fact Dreamer is
workspace-keyed; room-scoped tools answer EMPTY for it — `verify_tool_subsets`
already refuses them):

| Tool | Reads | Returns |
|---|---|---|
| `get_fact(subject)` | `fact` where `superseded_at IS NULL` | the live facts about a subject |
| `search_fact(query)` | `fact` + `fact_entity` | facts matching the query across the workspace |
| `fact_premises(fact_id)` | `fact_premise` | the dependency edges — which facts this one rests on |
| `fact_asserters(fact_id)` | `fact_asserter` | who said it, when, in which message |
| `fact_entity(name)` | `fact_entity` | the canonical entity + aliases a name resolves to |
| `note_read(title)` | `note` (live heads) | **EXISTS** (`tools.py:5793`) — expose to the Dreamer |
| `note_search(query)` | `note` | **EXISTS** (`tools.py:5935`) — expose to the Dreamer |
| `search_memory_workspace(query)` | Honcho, all peers in the workspace | memory excerpts across the workspace's people |

**The graph is the point, not a flat list.** The fact tier's schema is
already a graph — `fact_entity` (nodes), `fact_premise` (dependency edges),
`fact_asserter` (attribution edges), `fact.object_entity_id` (object→entity
FK), `fact.superseded_by` (supersession chain). The operator's requirement is
that the Dreamer understands these relations AT BREAKDOWN TIME, not after:
`fact_premises`/`fact_asserters`/`fact_entity` expose the edges so a claim
like «GTA 6 requires 顯卡» is read together with its premises, its asserters,
and the entity it resolves to. `fact_premise` currently holds 0 rows (no
writer exists for `warrant_kind='premises'`) — the read surface ships ahead
of the writer, the same way the candidate table shipped ahead of its
adjudicator.

**Reuse, not rewrite.** `note_read`/`note_search` already exist for the tag
path (`NativeTool` rows at `tools.py:775/786`, implementations at
`tools.py:5793/5935`). The Dreamer gets them by adding the names to its
`tools` set and to `WORKSPACE_SAFE_TOOLS` — the same function objects, no
second copy. `SqlFactStore` already has `asserters()` (`facts.py:532`) and
`standing_facts()` (`facts.py:783`) — the new tools wrap those store methods,
not duplicate them. `search_memory_workspace` is a workspace-scoped variant
of the room-scoped `search_memory` — the existing implementation is reused
with a different scope argument, not copied.

All eight are added to `WORKSPACE_SAFE_TOOLS` and declared in `NativeTool`
with `read_only=True`, `source_tier="remembered"`, bounded budgets (2–4 per
turn).

**Attribution.** `SqlFactCandidateStore.propose` joins `messages` on
`observed_in` and writes `asserted_by` (the `person_id` of the first message)
and `asserted_at` (its `ts`) into `fact_candidate`. Migration adds the two
columns. The adjudicated `fact` row keeps its own warrant; the candidate's
attribution is the "who said it, when" the operator asked for.

## 4. What this does NOT change

- **Other adaptors.** Language, exchange, portrait, note curator/revisitor
  keep the shared `[language]` schedule and their existing budgets. Only
  `FactCandidateAdaptor` declares a schedule.
- **The global tick.** 5-minute wake stays.
- **The model.** Still `ollamacloud/deepseek-v4-flash:0731` — the
  "DIFFERENTLY SITUATED" claim is tested by same-model, not swapped-model.
- **The write path.** Still no write tools; the model proposes, code disposes.
- **The screen.** R-18/R-28 screening still runs before `accept`.

## 5. Closed decisions (operator, 2026-09-01)

1. **Memory search is workspace-scoped.** The Fact Dreamer is workspace-keyed;
   "search 六個人嘅 memory" means searching the whole workspace's memory. A
   workspace-scoped memory search tool is added alongside the five fact/note
   tools — it searches all peers' Honcho memory in the workspace, not one
   room's history. The existing room-scoped `search_memory` stays for the tag
   path; the Dreamer gets the workspace variant.
2. **Attribution carries to the fact row.** `asserted_by`/`asserted_at` are
   written on the candidate at `propose` time (joined from `messages`), and
   copied onto the adjudicated `fact` row when the candidate is settled — so
   the rendered `<facts>` block can say who said it and when, not only the web
   warrant.
3. **Tool names are the operator's.** `get_fact`, `search_fact`,
   `fact_premises`, `fact_asserters`, `fact_entity`, `note_read`,
   `note_search`, `search_memory_workspace` — the two note tools already
   exist under those names; the fact tools wrap `SqlFactStore`'s existing
   read methods; the workspace memory variant reuses `search_memory`'s
   implementation. A rename is a three-place contract change and is not made
   by taste.

## 6. Reversal conditions

- **Schedule:** if a daily run misses a claim that a same-day run would have
  caught, and the miss is traced to the interval (not to the batch cap), the
  interval is too coarse — reopen with the measured miss.
- **Context caps:** if a truncated tool result causes a wrong verdict that a
  full result would have avoided, and the truncation is the cause, the cap is
  too tight — reopen with the measured case.
- **Read surface:** if the five new tools are never called in real runs
  (Langfuse shows zero invocations), the surface is decorative — reopen with
  the objective's tool guidance.
