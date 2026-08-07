# 24 — Honcho integration v2: session-per-conversation, Nora as a peer

Date: 2026-08-07. Status: PROPOSED — design only, no code. Operator directed
the direction (heavy/aggressive Honcho integration, not Honcho-as-optional-tool;
and Nora's own messages feeding a NORA peer card). This document locks the
architecture and, deliberately, leaves the load-bearing granularity decisions as
**OPEN DECISIONS** for the operator, because they are hard to migrate later
(§7) — the corpus keeps *conclusions, not text*, so a wrong granularity cannot
be fully undone.

## 0. Why now, and the one thing that blocks everything

The operator wants two things:
1. **Nora's own replies feed Honcho, building a NORA peer card** — her derived
   self-model (how she talks, her patterns), not just the humans' cards.
2. **Honcho woven through Nora, not a side tool** — group dynamics, the
   relationship between speakers, deep recall — not only flat per-person cards.

Both converge on ONE keystone: **session-per-conversation.** Today
(`HonchoClient.ensure_session`) a session is `nora-<peer_id>` — one session per
PERSON, holding only that person's messages, one peer. That model structurally
cannot hold a conversation (two speakers), so:
- Nora's replies have nowhere honest to go (under a human's peer they become the
  DEC-36 bug — her words derived as the human's; under a Nora peer they'd land in
  a session that *means* one human's private stream).
- Honcho never sees a relationship or a group — only monologues.

So this document is really about that one change and everything it unlocks.

## 1. The keystone — sessions keyed by conversation, not person

Honcho sessions hold MULTIPLE peers (verified against the live instance, and
`feed` already tags each message with its `peer_id`). The change:

- `ensure_session(conversation_id)` → `nora-conv-<conversation_id>` (a session
  per Signal group / DM), replacing `nora-<peer_id>`.
- Every message is fed **under its own speaker's `peer_id`, into that
  conversation's session.** A human's message → their peer; Nora's reply → the
  Nora peer; both in the same conversation session.
- **DEC-36 is preserved and made cleaner, not broken:** the invariant was "one
  peer's stream holds one speaker's words." A multi-peer session honours that at
  the MESSAGE level (each message carries its speaker), which is exactly Honcho's
  own model — DEC-36's bug was rendering two speakers into ONE peer's text, which
  this design makes structurally impossible.

## 2. The peer model — humans + a Nora aiPeer

- Each mapped person stays a peer (their `honcho_peer_id`, from identity.toml).
- **Nora becomes a first-class peer** (`aiPeer`, a config'd id, e.g. `nora`).
  Her replies feed under it. Honcho derives a NORA card from her own corpus —
  operator's point 1, and genuinely additive over the record store (the record
  gives her TEXT; the card gives her a derived self-model — ADR 0001's "she can
  read it in the buffer" argument covers retrieval, not derivation).
- Unmapped speakers still feed NOTHING (the existing rule): a person Nora cannot
  name has no peer, so their words enter no session — the presence/identity
  asymmetry already established.

## 3. What feeds what (the write path)

| speaker | peer | session | when |
|---|---|---|---|
| a mapped human | their peer | the conversation's session | every message (ambient), as today but conversation-keyed |
| Nora | the `nora` aiPeer | the conversation's session | after she replies (the feedback stage, re-enabled with a handle on the reply) |
| an unmapped human | — | — | never (unchanged) |

The feedback stage (`nora.engagement.stages.feedback`) currently feeds nothing
and is structurally unable to (the handle was removed, DEC-36). v2 restores the
handle BUT feeds the reply under the **Nora** peer into the **conversation**
session — the option DEC-36 explicitly deferred to "session-per-conversation
work," which this is. The `memory_feed` ambient stage moves from per-person to
per-conversation sessions in the same change (and the known duplicate feed of the
tagging message, noted in feedback.py, is resolved here — one write, one session).

## 4. Recall (the read path) — lightweight auto, deep on-demand

The terseness tension is real: Nora is a terse companion, and querying Honcho
heavily on every turn adds latency against the p95 ≤ 5s SLO. Resolve it by
SPLITTING recall by cost, not by making everything heavy:

- **Automatic, cheap, every tag:** the tagger's peer card injected into context
  (as today, `context.py`). Unchanged — already "heavy enough" for the common case.
- **On-demand, deeper:** `about(person)` stays (peer card for a named person).
  ADD a dialectic-`chat()` capability — Honcho's reasoning endpoint over a
  conversation/peer — as a tool the model reaches for when a question genuinely
  needs interpretation ("點解佢今日咁", "佢係咪嬲緊") rather than a fact. This is
  where "aggressive integration" lands: not more automatic calls, but a deeper
  RECALL VERB the model can choose, gated by the same "check only when there's
  something to check" doctrine.
- Group dynamics become derivable because the session now holds the whole
  conversation — a future "read the room" recall can query the conversation
  session, not stitch monologues.

## 5. OPEN DECISION A — workspace granularity (operator to lock)

The operator flagged possibly wanting a workspace per group. Options:

- **A1 — one workspace, sessions per conversation (recommended default).** Peers
  are shared across sessions, so a person's card aggregates everything Nora knows
  about them across every group — usually what you want ("what do I know about
  Miles"). Simpler; one workspace.
- **A2 — a workspace per group.** Hard isolation between groups (a person's card
  in group X can't see their group Y behaviour). Stronger privacy boundary
  between social contexts, at the cost of a fragmented per-person view and more
  Honcho objects to manage.

**Recommendation: A1**, unless cross-group isolation is a stated privacy
requirement — in which case A2, decided BEFORE first write (migration between
them is not clean, §7).

## 6. OPEN DECISION B — the Nora card's scope

Does the `nora` peer aggregate across ALL conversations (one global Nora
self-model) or per-conversation (how Nora is *in this group*)? Under A1 (one
workspace) the peer is global by default — one Nora card. If you want
"Nora-in-this-group" you need per-conversation Nora peers (`nora-<conv>`), which
multiplies peers. **Recommendation: one global Nora card** (B-global) — a self-
model is about her, not the room; per-group Nora identity is a different feature.

## 7. Migration & irreversibility — why this is design-first

Honcho stores DERIVED CONCLUSIONS, not the source text (feedback.py's own
warning). Consequences:
- **The existing per-person sessions (`nora-<peer>`) become legacy.** Their
  conclusions already live on the peer cards; those cards CARRY FORWARD (peers
  are unchanged). The old sessions are simply no longer written to. No data loss;
  the cards are continuous.
- **Granularity (A/B) cannot be cleanly re-decided after writing** — re-keying
  sessions or splitting workspaces re-derives from text Honcho no longer has, so
  it adds conclusions beside the old ones rather than replacing them (the DEC-36
  lesson). Lock A and B first.
- **Backfill is possible and additive** (the record store HAS the text): replay
  historical messages into the new conversation sessions to seed derivation. Opt-
  in, off the hot path, same shape as any backfill.

## 8. Config & code surface (once decisions are locked)

- `config.toml [memory]`: add `ai_peer` (the Nora peer id) and, if A2, the
  workspace-per-group strategy. `workspace_id` stays for A1.
- `HonchoClient.ensure_session(conversation_id)` — re-key; keep the idempotent,
  deterministic-id, best-effort contract.
- `memory_feed` (ambient) + `feedback` (Nora's reply) — both take
  `conversation_id`, feed under the speaker's peer.
- `ensure_peer` for the Nora aiPeer at startup (like `ensure_workspace`).
- The dialectic-chat recall tool (§4) — a new native tool, R-7 budgeted,
  identifier-screened on its output like `about` (design/14 §2.1).
- Feed metadata (operator's earlier ask): pass `{ts, conversation_id, reply_to}`
  in the message so Honcho's deriver has structure, not bare text.

## 9. What must be true before it ships

1. A and B locked (this doc's open decisions) — the un-undoable part.
2. Sessions are per-conversation and multi-peer; each message feeds under its own
   speaker's peer (a two-speaker session, probed against the live instance).
3. The Nora aiPeer exists and its card derives from her replies only.
4. Recall splits cheap-auto (peer card) from deep-on-demand (dialectic chat); the
   deep verb is identifier-screened and R-7 budgeted.
5. DEC-36's invariant holds at the message level (no speaker's words under
   another's peer) — a named test.
6. Backfill is opt-in and off the hot path.
7. A decision-index entry and the named tests, per convention.
