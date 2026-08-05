---
kind: plan
title: "Nora Phase 9 — grounding: time, threading, membership, and memory lookup"
description: "Everything found by using her: what must be fixed, and what must be built. Perplexity/Maps deferred to Phase 10."
tags: [nora, plan, phase9, grounding, memory, roster]
timestamp: '2026-08-06'
---

# Phase 9 — Grounding

Everything below came from the operator actually using her, not from review.
Two lists, as asked: **what is broken** and **what is missing**. Perplexity and
Google Maps grounding are explicitly **deferred to Phase 10**.

## Decisions taken without asking

The operator asked for autonomy rather than questions. These were open; they
are now settled, and each is reversible:

1. **Unmapped group members render by DISPLAY NAME**, not a count — his answer.
2. **Nora lists herself** as a member. She is one; the honest answer includes her.
3. **Roster refresh every 15 min; treated as unavailable past 60 min.**
4. **`nora-miles` (the orphaned peer) is kept.** Honcho cannot merge peers, so
   deleting it destroys 11 messages and buys nothing.
5. **`nora persons rename` is a separate WRITE-mode command**, visibly distinct
   from the read-only control plane (DEC-20), not smuggled into it.
6. **Cross-room aliases:** the roster carries the aliases of people *in that
   room*. Knowing that 阿豹 is Tyler in a room Tyler is not in comes from the
   MEMORY LOOKUP path instead, which returns what she remembers about a person
   — never the address book. This satisfies "Tyler is 阿豹" without re-opening
   the enumeration leak.
7. **`persons = "*"`** is unaffected: the roster is platform-sourced, not
   policy-sourced.

---

## A. WHAT WE NEED TO FIX

### A1 — She has no clock, and the transcript is timeless *(highest value, smallest change)*
No time anywhere in the prompt: not `_context_header` (`nora/agent/prefill.py:31`),
not the persona, and `_transcript_block` (`:43`) renders `[who]: body` while
dropping the `ts` column that `messages` already stores. So 今晚 / 琴日 / 而家
are ungrounded and she cannot tell a five-minute-old message from a five-day-old
one. **Not a tool** — always relevant, ~15 tokens, and a clock cannot fail.

### A2 — Reply threading dies at the tag boundary
`_quoted` (`nora/platform/signal/parser.py:43`) captures the quote and
`build_tag_message` renders `<replying_to>`, but only for the tagging message.
No quote column on `messages`; `TranscriptRow` has no quote field;
`_transcript_block` renders a flat list. Grant asks, Tyler answers, she sees
three unrelated lines. The data is in `raw_json`, just never surfaced.

### A3 — `ensure_session` fails silently on a tombstoned session
A soft-deleted Honcho session 404s until its row drains. `client.py:68` folds
that into a generic warning and returns `None`, so feeding stops and silently
retries on every message. Needs its own branch and
`nora_memory_session_unavailable_total{reason}`.

### A4 — The prompt has no scope gate
Proven by the roster leak: R-18 (`nora/agent/egress.py`) guards the exit, but
nothing guards what prompt assembly is *allowed to contain*. Needs **R-19**:
the prefill may name a person only if they belong to the tagged conversation,
enforced at assembly and counted.

### A5 — Registered but unpopulated
`Membership` (`nora/domain/identity.py:146`) exists with ADMIN|MEMBER and
nothing constructs, stores or reads one. Populated by A6's roster.

### Already fixed this session (kept for the record)
`peer_card` read the wrong key and returned `[]` for the life of the deployment;
R-18 shipped unregistered; the alias-never-resolves property was claimed but
untested; the Honcho session was recovered and all rows re-adopted.

---

## B. WHAT WE NEED TO IMPLEMENT

### B1 — Conversation roster, platform-sourced
`Roster`/`ConversationMember` in `nora/domain/roster.py`;
`PlatformAdapter.rosters()`; `RosterStore` refreshed by a background loop
beside `prune_loop`. **Zero network on the ingest or tag path.** Renders three
states, and the `status="unavailable"` state is the point: she says she cannot
see the member list instead of inventing one.

### B2 — Name-to-peer memory lookup
She queries exactly one Honcho card per tag — the tagger's
(`nora/engagement/context.py:64`). Asked about Tyler, she has nothing, because
**Honcho never does the mapping**: it only knows peer ids Nora sends, and that
阿豹 means `tyler_cortez` lives only in `identity.toml`. Honcho already offers
`representation` (with `target`, `search_query`) and `chat`; none is used.
Design in flight.

### B3 — Transcript threading persisted
Whatever A2's design chooses — column, raw_json parse, or derived index.

### B4 — `nora doctor`
A third read-only control-plane command in DEC-20's shape. Per conversation:
roster known/age/source, platform members vs. `identity.toml` names vs.
`policy.toml` grants, Honcho peer/session existence and last feed, unattributed
row count. Would have made every defect in this phase visible in one command.

### B5 — `nora persons rename`
One `UPDATE messages SET person_id = NULL WHERE person_id = :old`, so DEC-16
re-adopts under the new peer. Never touches `identity.toml` (D6) or Honcho.

---

## Order of work

A1 first — smallest change, unblocks every time-relative question, and needs no
design. Then A4+B1 together (the scope gate is worthless without a roster, and
the roster is dangerous without the gate). Then A2/B3, then B2, then A3, then
B4/B5.

## Deferred to Phase 10
Perplexity Agent API and Google Maps Grounding. Note `soul.md` currently states
she has no web access; that line must change in the same commit that gives her
any, or she will refuse to use a tool she has.
