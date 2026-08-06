# 0002 — Honcho session binding: deferred, with a deadline

- **Status:** accepted (as a *deferral*) — the binding itself is **not yet
  chosen**, and the choice is the operator's
- **Date:** 2026-08-06
- **Index:** DEC-14 (one person, one peer), DEC-16 (lazy adoption backfill),
  DEC-27 / R-21 (room scoping), DEC-36 (one peer's stream, one speaker),
  ADR 0001 (its "Rejected" section names this decision and defers to it)

## Context

ADR 0001 settled *what Honcho is for*. It did not settle *what a Honcho session
is bound to*, and that question has a deadline attached, so it is recorded here
rather than left in a transcript.

**What is deployed today.** `nora/infra/honcho/client.py::ensure_session` builds
one session per PERSON:

    session_id = f"nora-{peer_id}"

and `nora/ingest/stages/memory_feed.py` feeds it the message body with the
sender's `honcho_peer_id` and **nothing else** — no conversation id, no room,
no group. `representation()` deliberately omits `session_id` for exactly this
reason, and says so in its own docstring: "sessions here are per-peer
(`ensure_session` builds `nora-<peer_id>`), so scoping to one would return a
subset of what the unscoped call already returns for that same peer."

**The operator's correction, 2026-08-06, and it is load-bearing.** `nora-<peer_id>`
was believed to be working *around* a Honcho constraint. It is not. Honcho
sessions are created at the **workspace** level (`POST /v3/workspaces/{ws}/sessions`,
which is what DEC's `ensure_session` fix established) and a session holds
**multiple peers** — messages are posted to a session each carrying their own
`peer_id`. A session is Honcho's model of *a conversation between peers*. One
session per person is therefore not a workaround; it is a session with exactly
one participant, which is fighting the grain of the thing rather than
accommodating it.

**Why there is a deadline rather than an open question.** Nora runs one group
today. The messages already fed carry no conversation dimension anywhere — not
in the session id, not in the message payload, not in a metadata field. Once a
second group joins, every message fed before that point is permanently
unattributable to a room. There is nothing to backfill *from*: the SQLite audit
record does hold the conversation, so a re-feed is theoretically possible, but
it would mean re-deriving a corpus rather than migrating one, and DEC-36 is the
precedent for what "not repairable by backfill" costs. **The window is open
only while there is one room.**

**The operator's position, 2026-08-06:** "nothing serious now." Deferred, not
refused.

## Decision

**Deferred. No binding is chosen, and the current `nora-<peer_id>` scheme keeps
running until one is.**

Two options are on the table, and they are the same trade R-21 already made
once:

**Workspace-per-group.** Isolation by construction — a conclusion derived in
one room cannot be retrieved in another, because it is in a different workspace
and `HonchoClient` holds one `workspace_id`. Nothing to enforce at retrieval,
nothing to get wrong.

**Session-per-conversation.** One peer per person, workspace-wide, as today
(DEC-14, A2: one person = one Honcho peer). Sessions carry the conversation
dimension, and scoping a retrieval to a room becomes a **retrieval-time**
argument rather than a storage-time partition.

**The recommendation is session-per-conversation**, on the R-21 precedent: this
project has already decided once that scope is a property of *how an answer is
assembled*, not of how identity is fragmented. `RoomView.people` resolves within
scope instead of resolving-then-filtering, and it does that without giving a
person two identities. Workspace-per-group would give a person one Honcho
identity per room, which is the same shape as the `#n`-beside-`ts` problem
design/18 §3.1 refuses: two identities that can diverge, with nothing that
notices.

**The choice is the operator's and has not been made.** This ADR records the
options, the deadline and the recommendation. It does not record a resolution,
because there is not one.

## Consequences

**Better.** The deadline is now written down where someone will find it, with
the reason it is a deadline (no conversation dimension exists to recover). The
operator's correction is recorded, so the next person to read
`ensure_session`'s `nora-<peer_id>` does not re-derive the wrong explanation —
that it was forced by Honcho — and build on a constraint that does not exist.

**Worse, and accepted.**

- **Every day of the deferral spends the window.** The cost is not zero and it
  is not constant; it grows with corpus size. When the choice is finally made,
  whichever way it goes, the messages fed under `nora-<peer_id>` will be a
  legacy shape someone has to decide what to do with.
- **The current scheme is actively the worse of the two options**, not a
  neutral middle. It has neither the isolation of workspace-per-group nor the
  conversation dimension of session-per-conversation. It is the null choice,
  and it is running in production.
- **A second group joining is not a loud event.** Nothing today warns that the
  window has closed. Under R-5, Nora will simply keep working while the
  property this ADR is protecting quietly stops being recoverable. That is the
  same failure shape this repo keeps finding, and this ADR does not fix it —
  it only names it.
- If session-per-conversation is chosen, **cross-room leakage becomes a
  retrieval-correctness problem** rather than a structural impossibility, and
  it will need the R-21 treatment: enforced where the answer is assembled, with
  a test that "in another room" is indistinguishable from "does not exist".
  That is real work that workspace-per-group would not need.

**Related, and not resolved here.** DEC-36 (4) notes that with Nora's own reply
no longer fed, the tag path's write is a byte-identical duplicate of what
`ingest/stages/memory_feed` already fed — kept rather than deleted **precisely
because session-per-conversation is expected to want that stage back**. If
workspace-per-group is chosen instead, that stage is dead weight and should go.

## Rejected

**Nothing is rejected yet — that is the point of this record.** The two options
above are both live. What *is* rejected is the reasoning that produced the
current state:

**"`nora-<peer_id>` is what Honcho makes us do."** False. Sessions are
per-workspace and hold multiple peers; a one-participant session is a choice,
not a constraint. Any future argument that starts from the old premise should
start over.

**Deciding it now on the strength of the argument alone.** The recommendation
above is an argument, and the R-21 precedent is a good one, but this is an
operator decision about how Nora's memory is partitioned across social
contexts, and this repo's standing preference (DEC-39, and design/14 §4's
"read the counter first") is to prefer evidence over a well-formed argument
where evidence is obtainable. There is exactly one room today, so there is no
evidence to obtain and no cost to waiting — until there is a second room, at
which point there is also no choice left.
