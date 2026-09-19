---
type: design
title: "Room Access — Two Paths, Not One Change"
description: "Giving a scope-keyed adaptor room tools is a small already-sanctioned wiring change. Giving a unit-keyed adaptor the same thing is a design problem with no existing mechanism and an unclosed disclosure question."
tags: [nora, dreamer, room, roster, scope, unit]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 08-state, rel: depends_on, provenance: asserted}
---

# Room Access — Two Paths, Not One Change

## 1. The lock that is not the one people name

Three mechanisms sit between an adaptor and a room tool. Only the third
actually binds.

1. **The adaptor declares nothing.** Trivially reversible.
2. **`UNIT_SAFE_TOOLS`.** Applies only when `key_kind == "unit"`. It
   **does not touch a scope-keyed adaptor at all** — a fact widely assumed
   otherwise, including in this bundle's own first draft.
3. **The empty `Room`.** Every adaptor, regardless of key, receives a room
   constructed with no roster source and an empty platform. Every room-scoped
   read therefore answers *"I can't check who is in this group"* **even when
   declared**. The empty platform additionally breaks unit resolution, so
   the note verbs fail closed too.

The third is the real lock, it is documented as *"a DATA-level fail-closed
guard behind the name-level one, because the name-level one is a list and lists
are the thing that go stale"* — and it is correct reasoning. It is also the
thing that must be consciously reversed, per key kind, rather than worked
around.

## 2. Path A — scope-keyed adaptors

**This is wiring, and it is already the sanctioned direction.** A test named
for exactly this case asserts that a scope-keyed adaptor *may* hold a
room-scoped tool. The scope key already **is** a conversation id. The roster
sources the tag path uses are already built in the composition root, before the
Dreamer block runs.

What the tool source needs: the resolved platform, the roster for it, and the
self-identity set. It must keep taking the empty-room path when the key is a
unit, so the guard survives where it is still load-bearing.

Two pieces are easy to miss and both are load-bearing:

- **The self-identity set is not optional.** Without it Nora's own account
  resolves to nobody and is counted as a stranger in her own room. This has
  happened in production before and is recorded in the composition root.
- **The conversation type is currently hardcoded.** Fine while every scope is a
  group; it must not stay silently hardcoded if DMs ever become scopes.

Beneficiaries: room language, room exchange, and fact extraction — the three
adaptors that read messages. This is the path that makes
[03-no-agent-loop.md](03-no-agent-loop.md) §6 possible.

## 3. Path B — unit-keyed adaptors

**This is not a wiring problem, and it should not be attempted as one.**

A unit is not a conversation. Every room-scoped read is bound to one, and
the fact-tier spec's own "inputs" row assumes a unit-aware transcript read
that was never built. Concretely:

- **There is no unit→rooms index anywhere.** Units are enumerated as
  a set derived from the allowlist; the reverse map does not exist. One closure
  would supply it.
- **There is no multi-room read anywhere in the codebase.** The record store
  exposes single-conversation reads only. The one place that loops rooms does
  them one at a time and stops at the first readable one.
- **A unit transcript read needs decisions, not code**: which of a room's
  ids to read (a migrated room's history is split across several), a per-room
  label on every row, a total budget, and an R-27 answer for a partial read —
  one room readable and one not is `unavailable`, not a truncated `ok`.
- **Roster-shaped tools would need a union of rosters**, and that union is a
  disclosure decision that **conflicts with R-49** rather than merely being
  unmade. See [11-disclosure.md](11-disclosure.md).
- **Memory search cannot be unit-scoped** without reversing a decision
  made deliberately: private DMs are separate sessions inside one unit,
  and the session scoping is what keeps them apart.
- **Two tools have no unit meaning at all.** Tool-recall and media
  retrieval are per-conversation *by rule*, not by implementation.

**Decision (D4): Path B is blocked.** Not deferred for effort — blocked pending
an answer to the disclosure question. It is listed in
[16-open-items.md](16-open-items.md).

## 4. The cheaper answer for Path B, and it is already decided

The adjudicator's input is code-selected and can be widened without any tool:
adding a bounded per-room transcript window to what the adaptor renders is
countable, cappable, needs no new mechanism, and is what fact-tier §4.2 already
sanctions for exactly this shape of need.

That is not a substitute for the tier-read tools of
[08-state.md](08-state.md) — those answer questions Python cannot
pre-select, per [08-state.md](08-state.md). It is a substitute
for the *room* half, and it is the right first move while Path B is blocked.

## 5. What replaces the guard

Removing the empty room for one key kind removes a data-level backstop that
three rules quietly lean on. The name-level list is the half that rots, which
is the guard's own stated reason for existing.

So the replacement must be a pinned property, not an absence:

- which conversation a Dreamer-held room-scoped tool binds to, asserted;
- that a unit-keyed adaptor still reaches no roster, asserted separately
  from the tool-name list;
- that the self-identity set is present, because its absence fails *quietly* —
  the tool answers, it just answers wrongly.

The existing classification test is the model to follow: it fails **by name**
when something new is unclassified, rather than passing because a list happened
not to mention it.

## 6. Budgets are the tag path's, and nobody has costed them

The per-tool budgets a Dreamer-held tool would inherit are the numbers sized
for a human-waiting turn. Nothing has costed those against every scope, every
tick, all day, and there is **no per-run total tool-call ceiling** — only
per-tool. The loop's only global bounds are turns, tokens and the wall clock.

That sizing belongs with the rest of the budget work in
[06-trigger.md](06-trigger.md), and it should land in the same
change as the grant rather than after it.

## Status / Checkpoint

2026-08-24 — D6. Unchanged; Path B remains blocked by the disclosure condition [16](16-open-items.md) §2 now settles as a startup check.
