# Decisions

**RESERVED, not claimed.** `DEC-181 .. DEC-185` — DEC-181 is withdrawn, see below. Per AGENTS.md, re-read
`design/09-decision-index.md` at MERGE time and take the numbers then.

The index tops out at **DEC-162**; `ambient-loop` reserved DEC-176..180, and
the #219/#220 session has reserved nothing. **DEC-163..175 is an unexplained
gap** — find its holder before banking on anything in this range.

---

## DEC-181 — the key follows from the writer, and is not a choice

**WITHDRAWN as an operator question.** It was escalated as "which key does
culture use" and it is not a preference. `GLOSSARY.md` now carries the rule
(PR #230), and it is this:

> Which key a store uses follows from **which scheduler hands it the key**. The
> ingest and tag paths get the message that arrived, so they key
> `(platform, conversation_id)` — `messages`, `standing`, `decision_log`. A
> Dreamer adaptor gets `scopes()`, so it keys `primary_id`.

Culture is Dreamer-written. **Therefore `primary_id`.** Nothing to decide.

What made this look like a choice is that `"room"` named four things and the
glossary defined two of them. `nora.policy.Conversation` carries
`ids: frozenset[str]` plus one `primary_id`, so `(platform, conversation_id)`
and `primary_id` are different concepts — which thread arrived, versus which
conversation it belongs to — not two spellings of one.

All six live conversations hold exactly one id, so the two are equal today.
That is an accident of data: give one a second id and a single conversation has
two `standing` rows and two `decision_log` streams against one `scope`, and
nothing reports it because nothing holds both keyings and so nothing compares
them. **Issue #229** carries the load-time guard and the open question of what
a multi-id conversation should mean for the per-room stores.

The original failure mode still stands as the reason to get it right: a store
keyed differently from its loop matches zero rows on the watermark query, so
the adaptor is scheduled, raises nothing, logs nothing and never runs.

---

## DEC-182 — the tier stores an answer; it does not derive one

The adaptor calls `ask_memory` (Honcho's dialectic `chat`) once per room per
Dreamer pass and stores the prose. It builds no model of its own.

Measured 2026-09-12: `chat` returns an **account** with causation, self-naming
and her own standing, where `representation` returns a **chronicle** of
`[when] what happened`. Latency 17–30s against a 20s gate timeout — so it
cannot be inline, and that gap is the whole reason a store exists.

This is what keeps the tier from becoming a second, drifting model of the same
rooms Honcho already models. One system reasons; the tier remembers.

---

## DEC-183 — nothing in this tier retires

No `retired_at`, no `closing_condition`, no TTL, no fail-closed staleness.
`parent_revision_id` means "the same thing, earlier", never "superseded".

Culture is not a claim that expires. A quiet room's culture is accurate, not
stale. The operator's test: 「漢人」 did not retire when the Han dynasty did —
the old name became history, and the history is part of what the new name
means.

The first draft imported `standing`'s lifecycle wholesale and was wrong in
every particular.

---

## DEC-184 — prose body, no section schema

`body` is free text. No JSONB, no required sections, no CHECK on shape beyond
a length cap.

A three-section schema was drafted and rejected: it is the same enumeration
error as `participation.md`'s five shapes one layer down, it could not have
held 「秘書」 — a role the room invented — and requiring a `shifted` section
makes the first revision unwritable or invites a `reasoning_effort = "none"`
model to confabulate a diff to satisfy a constraint.

`note_revision` is the precedent: free prose, CMS-clean, because provenance
sits *around* the text. Here the runtime supplies that provenance itself.

---

## DEC-185 — a time-driven pass does not require backlog

`AdaptorSchedule` on the Dreamer clock, `uses_watermark = False` in the sense
that a quiet room must still fire.

`runtime.py:635` already records the lesson and `note_curator.py:525` already
applies it. Requiring `lag > 0` would make culture go stale precisely in quiet
rooms — the defect this bundle criticises `standing` for.

Because every write is a new revision, trigger timing is genuinely low-stakes:
firing early or late costs one revision and corrupts nothing.

---

## Not decided here

- **The gate metric's `conversation_id` label** — no longer a separate issue
  (#221, closed into this bundle). It is step 1 of `02-measurement §5`, because
  nothing else here can be measured until it lands.
- **Whether the gate ever reads culture.** Owned by the gate, unhelped by
  #220, sequenced last, and with #144/#147 and #216 (n=5, unconfirmed) as evidence against
  assuming it. `02-measurement §5`.
- **The block's rendering.** #219/#220 owns the vocabulary. Its `key` axis
  landed 2026-09-13, so the declaration is now pinned rather than described:
  `placement="turn"`, `key="room"`, per `01-the-adaptor §5`. The rule that
  field encodes — anything keyed narrower than `global` belongs in `turn`
  whatever its cadence, because the shared static head fragments per room —
  belongs to that work, not this bundle.
- **`participation.md` from taxonomy to contrast, and `silence.md`.** Doctrine
  is `change_policy: operator-only`, and this repo's own worked example is
  three prompt rewrites judged by eye, each worse than the last.
- **`standing`'s repair** (#222), which HOLDING's second arm depends on.
- **Why `proactive_decide` declines while holding the notes it woke on.** A
  separate fault; likely #138's `memory before remind`.
- **Dropping `room_portrait` / `room_lexeme` / `room_exchange`.** Per the
  2026-09-07 rule the tables stay when the code goes.
- **Phase 2.** `04-phase-two-note.md`, not executable.

## Corrections this bundle carries

Recorded because they were load-bearing and wrong, and a reader should know
which claims were tested rather than argued.

1. **"The `go` counter has never been incremented."** False — an instant
   Prometheus query read a post-restart absence as never-happened.
   `00-overview §1.1`.
2. **"Honcho has no session scope, so per-room recall is impossible."** False;
   asserted by the first draft and by one reviewer. `session_id` works today
   and returns genuinely room-specific content.
3. **"A third block is forbidden."** A pin (`== 2`) mistaken for a
   prohibition. Every tier that landed added a block.
4. **"`<self_card>` already reaches the participation turn."** False — #210:
   `person_id=None`, so `peer_card` is `()` and `self_card` is assembled only
   on the tag path.
5. **"`strikes` counts wakes."** It counts consecutive revisits that found the
   head unchanged.
6. **A room-keyed block belongs in the cached head because it changes slowly.**
   Backwards: the head is shared across rooms, so a room key fragments it
   permanently.
7. **"Same window + same instruction hash + same model re-derives a compatible
   account" is a usable check.** It is not: `proactive_decide` recall runs
   0.20–0.50 on an identical prompt and DEC-75 records a temp-0 judge scoring
   1/3 on a re-run of a 3/3. The spread is wider than anything it could
   detect. Replaced by `02-measurement §6`, whose third tier — move a labelled
   metric or do not ship into that prompt — is the only one that can falsify
   this tier's value.
