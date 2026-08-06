# 0001 — Honcho is the interpretation; SQLite is the record

- **Status:** accepted
- **Date:** 2026-08-06
- **Index:** DEC-14 (one person, one peer), DEC-27 (room scoping), DEC-16 (adoption backfill)

## Context

Nora has two stores and they were routinely conflated — including in
`ARCHITECTURE.md`, which until this date described the tag path as reading the
"tagger's Honcho peer card" for an endpoint that returns null.

Measured on 2026-08-06 against the live instance:

- A `representation` for `miles_novak` returned 17 lines, of which **2** were
  durable traits; the rest were events ("asked what time it was") — the
  transcript restated in the third person, at ~151 tokens and ~190 ms.
- A throwaway peer fed 48 realistic first-person messages produced **77**
  conclusions, ~74 of them durable traits. **The noise is the corpus, not the
  deriver.**
- `peer_card`, believed dead since DEC-27, is **dreamer-gated, not broken**. It
  needs 50+ documents; `miles_novak` has 16, so no dream had ever run. Forced on the
  probe peer it returned a 17-line typed block — `IDENTITY: / ATTRIBUTE: /
  RELATIONSHIP: / INSTRUCTION:` — in **9 ms**, versus 198 ms for
  `representation`.

## Decision

**SQLite is the RECORD. Honcho is the INTERPRETATION.**

SQLite holds what was literally said. It is authoritative, is what R-2 and A9
guarantee, and serves two layers of the same store: the 40-row window injected
every turn, and everything older via `read_buffer` — *a wider window, not a
second system*.

Honcho holds what a deriver concluded *about people*. It is best-effort and may
be absent, which is why `nora.infra`'s two clients have opposite error
contracts.

The operative test for any Honcho call: **could she have read this in the
buffer?** If yes, it is negative value — it costs tokens and wall clock, and it
invites her to repeat the room back to itself.

## Consequences

**Better.** The layers are nameable, so "nothing found" and "could not ask" stay
distinct — `read_buffer` empty means *nothing was said*; `recall` empty means
*nothing was concluded*, a much weaker claim. That distinction is why
`Recollection` carries a reachability bit, and collapsing it is what hid the
`peer_card` misdiagnosis for the life of the deployment.

**Worse, and accepted.** Honcho's value is now explicitly *conditional on
corpus quality*, which Nora does not control — it depends on people talking
normally rather than testing her. Until real traffic accumulates, the honest
position is that Honcho provides near-zero value on the tag path, and we ship
knowing that.

**Follow-on work this forces**, each with a cost:

- ~~`feedback.py` feeds a **two-speaker** transcript under one `peer_id`, so
  Nora's own words land inside a person's message stream. Corrupting, and not
  repairable by backfill.~~ **Done 2026-08-06, DEC-36.** Her reply is now fed
  nowhere; the stage feeds the tagger's body only. Fixing it surfaced a
  second, smaller thing this ADR did not know: `ingest/stages/memory_feed`
  runs for every message *before* the engage gate, so the tagging message's
  body was already fed under the same peer into the same session — with the
  `[Nora]:` half gone the tag-path write is a byte-identical **duplicate**. Not
  corrupting, so it is recorded and pinned by a test rather than removed here;
  deleting it removes a pipeline stage that session-per-conversation is
  expected to want back. See DEC-36 (4).
- The tag path should move to `peer_card` (9 ms, typed, ~206 tokens) and prefill
  it, rather than spending a tool call on `representation`.
- ~~`_conclusions()` mangles the Deductive/Inductive sections it has never been
  tested against — its fixture contains only `## Explicit Observations`, the
  exact "fake that cannot produce the real shape" this repo keeps finding.~~
  **Done 2026-08-06, DEC-37.** Measured before the rewrite: the live body for
  `probe_zzz` yielded **151 "conclusions" from 25 documents**; it now yields
  exactly 25. `POST /conclusions/query` was evaluated as the parser-free
  alternative and rejected — it is semantic search alone, so it cannot express
  the derived and recent channels DEC-38 restores. Two corrections to this
  ADR's own text fall out: the stamp is `message_created_at` copied onto every
  conclusion in a derivation batch (`probe_zzz`'s 102 documents carry six
  distinct stamps, one shared by 77), and no `## Contradictions` section exists
  anywhere in the live database, so that fixture is built from the emitter's
  source rather than the wire.
- `recall` is misnamed: it fetches a standing profile, not memories, and the
  docstring's "what they told you before" invites her to repeat the transcript.

## Rejected

**Workspace-per-group for isolation.** Deferred rather than refused (see the
session-binding decision): it would guarantee no cross-group leakage, at the
cost of her forgetting a person entirely when she moves rooms — which is most of
what derived memory is for. The R-21 precedent favours enforcing scope at
*retrieval* rather than by fragmenting identity.
