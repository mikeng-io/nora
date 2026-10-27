# 24 — Honcho integration v2: session-per-conversation, Nora as a peer

Date: 2026-08-07. **Status: IMPLEMENTED 2026-08-08** (commits `3933bef` bedrock,
`52a6adf` review remediation, `4c9f107` session-id fix). Original proposal below
§S is unchanged as the rationale; the OPEN DECISIONS of §7 are resolved in §S.

---

## S. Settled (2026-08-08): the clean topology + what shipped

### S.1 The clean boundary / authority / scope — "group" was overloaded

The word **group** did double duty and caused a session's worth of confusion.
Named properly:

| layer | what it IS | authority / scope |
|---|---|---|
| **workspace** | ~~a **group of PEOPLE**~~ — ~~**REVISED 2026-09-19: one whole NORA.**~~ **SUPERSEDED 2026-09-28 (DEC-214):** a Nora WORKSPACE is a group of UNITS (`policy.toml` `[units.<unit>] workspace`), and each is bound to exactly one **Honcho workspace** (`identity.toml` `[workspaces.<name>] honcho_workspace`; unbound = its own name; no `[units]` = one implicit workspace bound to `honcho.workspace_id`). The two are separate names for separate things. | the PERSON-MODEL boundary: Honcho peers are per Honcho workspace, so units of one workspace share one model of a person and different workspaces never do |
| **unit** | a **group of PEOPLE** — a social unit (my-friends, my-family, a team). DEC-105's key, from policy.toml. At most one room per platform (enforced since DEC-214). | the NOTE tier's scope, the bound on `send_to`, and — since DEC-214 — a Honcho NAMED SCOPE: every room's session joins its unit's scope and every memory read for the unit passes `scope`. |
| **session** | a **channel** that same unit uses — their Signal group, Discord group, WhatsApp group | one conversation stream (same people, different app); `conversation_id = platform group id` |
| **peer** | a **person** (all their accounts → one person_id via identity.toml) | one identity |
| **room / roster** | who is actually IN a given channel | the DISCLOSURE authority: what Nora may SAY here |

- "group of people" = social unit = **unit**. "platform group-chat" = channel = **session**. Never mix them — and note that the word for the first of those was `workspace` until 2026-09-19, which is what the row above records.
- ~~You never put two social units in one workspace, so cross-session blend WITHIN a workspace is safe **by construction**.~~ **NO LONGER TRUE (2026-09-19).** One Nora is one Honcho workspace and it holds every unit, so Honcho blend crosses units and the "same humans" argument does not cover it. What still holds is the second bullet below: knowledge is workspace-wide, disclosure is channel-local, and the ROSTER is what gates whom Nora may speak about. That is now the only guard, not a construction. **Amended 2026-09-28 (DEC-214):** recall is now UNIT-wide — each unit is a Honcho named scope and every read passes it — so blend crosses the rooms of one unit and no further; the roster still gates disclosure.
- **Two orthogonal scopes:** ~~workspace~~ unit (DEC-214) = what Nora may *know together*; roster = whom she may *speak about*. Knowledge is ~~workspace-wide~~ unit-wide; disclosure is channel-local (R-21/R-31).

### S.2 Recall is Nora's own model — observer/observed (resolves §7 A1)

Honcho keys memory by **(observer, observed)** peer pairs. Nora is a peer, and
joins every session with `observe_others=True` (humans get `observe_me=True`),
so the deriver builds `(nora → each person)` — NORA's model of someone from
messages she witnessed, distinct from their self-model. All recall reads
`(observer=ai_peer, observed=person)`, workspace-wide (no session scope);
the roster gates the *subject*. **Amended 2026-09-28 (DEC-214):** unit-wide,
through the unit's named scope — which swaps the observer to the scope's own
peer, so a read is `(scope.<unit>, person)`, and `conclude` writes that row too. Proven live: a probe peer's job+hobby came back
from one observed message; empty without the observe-config.

### S.3 The four recall verbs (2 stores)

- `load_transcript` — Postgres, verbatim this-channel (renamed from `chat_earlier` 2026-08-08).
- `about(person)` — Honcho `representation`, retrieve Nora's model. Fast.
- `ask_memory(person, depth)` — Honcho `chat`/dialectic, REASON; `depth` model-chosen (minimal→max), slow (~14-19s), budget 1.
- `search_memory(query)` — Honcho `search` over raw messages. BUILT 2026-08-08: workspace-scoped, roster-speaker-filtered, R-18-screened, refuses in an unverified room.

### S.4 Resolved OPEN DECISIONS (§7)

- **Granularity: SUPERSEDED 2026-09-28 (operator; DEC-214).** The hierarchy is workspace > unit > room: a Nora workspace holds units, a unit holds rooms (one per platform), each Nora workspace is bound to one Honcho workspace, and each unit is a named scope inside it. The 2026-09-19 REASON below still holds and is respected — units never get a Honcho workspace of their own; only a Nora workspace does. The three follow-ups listed under the former plan are done: every `MemoryStore` verb is routed by room or unit (the port's `workspace_id` parameter is gone), `ensure_workspace` provisions every bound workspace with Nora's peer and every unit's scope, and peers are ensured per (workspace, peer). `WorkspaceRouter`'s docstring and `nora.app.memory_routing` carry the rest.
- ~~**Granularity: REVERSED 2026-09-19 (operator).**~~ (superseded, kept as the record) One Nora is ONE Honcho workspace, permanently: every unit feeds the same `workspace_id`, and a second workspace means a second Nora, not a second unit. Honcho is multi-tenant (read back live 2026-09-19: five workspaces on the deployed instance) and peers are workspace-scoped, so a workspace per unit would split one person into N unrelated person-models and N self-cards, with no way to split the derived history that already exists. The `WorkspaceRouter` overrides map below is therefore **dead by decision**, not pending. What follows is kept as the record of what populating it would have cost.

  ~~**Former plan:** each distinct social unit gets its own `workspace_id`, declared per-group in identity.toml, graduating to a dedicated `workspaces.toml`.~~ The code hook is `WorkspaceRouter` (a74972a) — the FEED path is fully routed today. **But populating the overrides map is NOT sufficient on its own** (seam review 2026-08-08); three follow-ups are required or overridden conversations break SILENTLY (best-effort Honcho swallows the errors):
  1. **Recall must route too.** `about`/`ask_memory` call `representation`/`chat` without `workspace_id`, so recall always reads the DEFAULT workspace — while feed WRITES to the override workspace. Add `workspace_id` to the `MemoryStore` port (it exists only on the concrete client today) and wire the tools to resolve it from their `conversation_id` (already in `build_tool_specs` scope) and pass it.
  2. **Override workspaces + peers must be provisioned.** `ensure_workspace`/`ensure_peer`/`_create_peer` only touch `self._ws`; a feed to an override workspace assumes Honcho get-or-creates the workspace/peer, else every feed there returns False silently. Route these by conversation too.
  3. Confirm Honcho's session-create/add-peers auto-create semantics for a not-yet-existing workspace before relying on (2).
  This is the REAL "phase 1".
- **N4 (subagent review):** non-identifier content blends across a unit's channels — accepted, because a workspace IS one social unit, so there is no cross-context to leak. **The premise died 2026-09-19** (see Granularity): a workspace holds every unit, so the blend DOES cross contexts and the roster is what bounds it. **Restored in substance 2026-09-28 (DEC-214):** reads pass the unit's named scope, so blend is across one unit's channels again — by the scope, not by the workspace.

### S.5 The base64 session-id trap (live-caught, `4c9f107`)

Honcho constrains session ids to `^[a-zA-Z0-9_-]+$`. A Signal group id is
standard base64 (`+` `/` `=`) → 422 on session-create → the WHOLE feed/recall
path was silently dead on every real group (offline tests used clean ids and
never saw it — the case for verifying live). `_safe_conversation_key` maps
base64→url-safe with a hash fallback. `session_id = nora-conv-<safe-key>`.

---

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

## DECISIONS LOCKED (operator, 2026-08-07)

- **A — workspace granularity: PHASED.** Phase 0 (build now) = **one workspace**
  (A1), sessions per conversation. Phase 1 (future) = **workspace bound to
  (circle_id + group_id), 1-to-many** (A2-shaped, the group↔circle pairing theme
  extended to workspaces). Phase 0 must therefore route the workspace through a
  SEAM — `workspace_for(conversation_id)` — that today returns the single
  configured workspace and later maps conversation → (circle+group) → workspace.
  The phase-0→1 split is SAFE despite §7's irreversibility warning: it is a
  workspace SPLIT re-derived by BACKFILL from the record store (which retains the
  text), not an in-place re-key — new per-binding workspaces start fresh and
  backfill seeds them. So phase 0 does not paint into a corner.
- **B — Nora card scope: ONE GLOBAL Nora card** (B-global). One `nora` aiPeer
  across all conversations → a single derived self-model.

The two sections below are the original options, kept for the reasoning behind
the locked choices.

## 5. OPTION A — workspace granularity (LOCKED: phased A1→A2, see above)

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

- `config.toml [honcho]`: add `ai_peer` (the Nora peer id) and, if A2, the
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
