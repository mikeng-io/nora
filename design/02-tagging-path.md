---
kind: design
title: "Nora — Tagging Path"
description: "When a configured trigger text appears in a group message, Nora collects context (SQLite transcript + Honcho peer card + peer mapping), pre-fills a fresh Strands agent, invokes it, and delivers the reply back to Signal — then writes audit + feeds Honcho."
tags: [nora, design, tagging, strands, injection, reasoning, stateless]
timestamp: '2026-08-03'
edges:
  - {dst: ../00-overview.md, rel: decided_by, provenance: asserted}
  - {dst: ../01-ambient-ingest.md, rel: triggered_by, provenance: asserted}
  - {dst: ../03-strands-reuse.md, rel: uses, provenance: asserted}
  - {dst: ../04-config-identity.md, rel: uses, provenance: asserted}
---

# Nora — Tagging Path

## Role

The tagging path is Nora's brain. It runs **only** when a configured trigger text
(e.g. `nora.000`) appears in a group message. It answers three questions:

1. How is context injected?
2. How does reasoning happen?
3. How is the reply returned?

Every tag is a **fresh, stateless invocation** of a Strands agent (Decision D4 in
`00-overview.md`). Continuity comes from pre-filling context, not from session state.

## Flow

```
TRIGGER (from ambient path: tag.is_tagged(body, trigger) == true)
   │
   ▼
① COLLECT CONTEXT (no LLM — reads from stores)
   tag_id ← generate UUID                    ← correlation ID (01 §Observability)
   ├─ recent N messages for this conversation  ← SQLite audit (db.recent)
   ├─ tagger's Honcho peer card              ← Honcho (durable recall)
   ├─ peer mapping config                    ← identity.toml (sender → identity)
   └─ the tag message itself                 (conversation_id, sender, name, body)
   │
   ▼
② INJECT (Strands primitive)
   Agent(messages=[pre-fill context])        ← conversation-history pre-fill
   │
   ▼
③ REASON (Strands agent loop)
   result = agent.invoke_async(tag_message)
       loop: model → tool call → execute → model ...
       tools: read_buffer / read_peer_card / send_reply / feed_honcho
   │
   ▼
④ RETURN (record-first, send-after)
   reply ← result
   db.append(... is_nora=1, delivery_status='pending')  ← SQLite audit FIRST
   send_reply(conversation_id, conversation_type, reply) ← signal-cli RPC (only side effect)
       → delivery_status = 'sent' | 'failed'
   honcho.feed(tagger, exchange)             ← Honcho Deriver (fire-and-forget)
   │
   ▼
done (agent discarded — no session, no carry-over)
```

## ① Injection

Injection is **conversation-history pre-fill** — a Strands built-in:

```python
agent = Agent(
    model=model,                    # OmniRoute via OpenAIModel(custom client)
    system_prompt=SYSTEM_PROMPT,    # persona (04-config-identity.md / persona.py)
    tools=[read_buffer, read_peer_card, send_reply, feed_honcho],
    messages=[...pre-filled context messages...],
)
result = await agent.invoke_async(tag_message)
```

The pre-filled messages carry:
- the recent group transcript (formatted `[name]: body` lines from SQLite),
- the tagger's Honcho peer card (durable facts about who tagged her),
- the peer mapping identity (who the sender is, per config).

The tag message itself is the invocation input. The agent's tools let it pull more
context mid-loop if it decides it needs it (`read_buffer`, `read_peer_card`) — the
pre-fill covers the common case, the tools cover the deep case.

**Tool-use policy (prompt, audit A11):** the pre-fill header states what context is
already present — e.g. `[Context: last 40 messages of this group are shown above;
use read_buffer only if you need older messages.]` — so the agent does not burn an
LLM roundtrip redundantly calling tools for data it already has. Same for the peer
card. This is the primary guard against tool-loop overhead (audit T4).

## ② Reasoning

Reasoning is the **Strands agent loop** — SDK-owned:

```
invoke → model reasons → wants a tool? → execute tool → feed result back → model again
      → until final response
```

- Loop, tool selection, tool execution, context accumulation: SDK.
- Observability (OTel, hooks): SDK.
- Execution limits (max tokens, loop bounds): SDK.

We write none of this. We write only the tool implementations and the persona.

## 4. Return (record-first, send-after — audit A9 fix)

The agent's final response is the reply. Delivery order — **audit-recorded before
any send**, so a failed send never loses the reply from context:

1. `db.append(... is_nora=1, delivery_status='pending')` — Nora's reply enters the
   SQLite audit first, so it is part of group history even if delivery fails, and the
   next tag sees it.
2. `send_reply(conversation_id, conversation_type, text)` — signal-cli JSON-RPC
   `send`. **The only side-effect tool.** Guardrailed: called at most once per tag;
   silent on failure. Sets `delivery_status='sent'` on success, `'failed'` on error.
3. `honcho.feed(tagger, "[name]: tag\n[Nora]: reply")` — fire-and-forget to the
   Deriver.

**Why this order:** the original design (send → db → honcho) had a hole — if
`send_reply` failed, the reply was never appended, contradicting the "reply already
in SQLite" error-handling note. Record-first makes the audit the source of truth;
delivery is a downstream effect of a recorded reply.

**No auto-retry in v2** (resolves the retry contradiction, audit F2): a failed send
leaves the reply in the audit with `delivery_status='failed'`; the operator sees it
and can resend manually if needed. Auto-retry is a v2.1 concern with a retry queue.

## Stateless / Sessionless Semantics

- A **fresh `Agent` per tag** — nothing is carried in memory between tags.
- **No** Strands Session Management, **no** snapshots, **no** persisted `agent.messages`.
- `ConversationManager` = Null (or per-invocation only) — no cross-invocation state.
- `invocation_state` may carry per-run metadata (conversation_id, sender_id,
  tag_id) but is scoped to the single invocation and never enters model context.
- Continuity is entirely external: SQLite (transcript) + Honcho (memory), both
  re-injected on every tag via pre-fill.

## Debounce

Rapid re-tags within `tag.min_interval_seconds` (config, default 5s) for the same
group are ignored — one reply per burst. Deterministic, in the ambient layer before
any agent invocation.

## Model Tiering & Reasoning Depth (audit T3, 2026-08-03)

Default: **flash-tier, shallow thinking** (`deepseek-v4-flash`, `max_tokens: 400`,
`temperature: 0.7`) — right for casual group-chat replies (short, warm, language-
matching). The architecture expresses tiering **config-driven, per invocation**:

```yaml
llm:
  default:
    model: "deepseek-v4-flash"
    max_tokens: 400
    temperature: 0.7
  deep:                       # escalation for complex tags
    model: "deepseek-v4-pro"  # or any deeper reasoning model via OmniRoute
    max_tokens: 1200
    temperature: 0.5
```

- Escalation trigger (v2.1): tag length/complexity heuristic, or the agent itself
  requesting a deeper pass — configurable policy, not hardcoded.
- `max_tokens` in the default tier is the **total** reply budget (thinking + answer
  for reasoning models, or plain answer for non-thinking); keep it small enough that
  a runaway loop cannot burn unbounded tokens.
- Latency SLOs: p95 ≤ 5s, p99 ≤ 15s end-to-end (tag → reply sent). Measured via the
  metrics histogram (see `01-ambient-ingest.md §Observability`).

## True Ambient Hooks (audit A3, future milestone)

The deterministic `tag.is_tagged()` gate is the **only** ambient→tagging hand-off
today. For a future true-ambient milestone, this gate becomes a **relevance
classifier** — a single cheap LLM completion on the ambient path deciding
yes/no/threshold before the tagging path runs. The seam is already there: the
hand-off point is unchanged; only the decision function changes. The classifier
output (yes/no/reason) can be pre-filled into the tagging agent's context so it
knows why it was invoked. D1 (amended) permits this; the full Agent SDK still runs
only on the tagging path.

## Error Handling

| Failure | Behavior |
|---|---|
| Context read fails | proceed with what's available (empty card, fewer rows) |
| Agent raises (LLM down) | log; **stay silent** in the group |
| Empty reply | log warning; stay silent |
| send_reply fails | log; reply already in SQLite (context not lost); no auto-retry |
| Honcho feed fails | log; reply already sent |

**Principle: Nora is invisible when things break.** No error messages go to the
group. The operator reads `data/nora.log`.

## Verification (acceptance)

The roundtrip is the deliverable: a working tag → reply cycle in a real Signal group,
with the Strands agent as the brain. See `03-strands-reuse.md §Verification` for the
full checklist.
