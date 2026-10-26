# 0026 · The Character tier's entry parts are profile, traits and hypotheses

- **Status:** superseded by [0029](0029-a-prompt-block-names-its-subject-and-author-by-id.md)
- **Date:** 2026-10-21
- **Reverses, in part:** [design/unified-memory/45](../../design/unified-memory/45-persona-adaptor.md)'s "Persona" naming.
- **Index:** a DEC number is taken at merge.

## Context

[05 · Data model](../../design/cognition-loop/05-data-model.md) §1 calls
Notebook rows "Nora's judgement"; §2 defines Layer › Tier › Kind › Entry.
The operator settled the Character tier's names on 2026-10-21. The collisions
are vocabulary problems within that hierarchy, not a new topology.

| level | name | replaces or avoids | source |
|---|---|---|---|
| tier | **Character** (kept) | "Account tier" collides with platform account and does not fit a room or unit | 05 §2; [GLOSSARY](../../GLOSSARY.md), «Person, and the things that are not a person» |
| kinds | **person, room, unit, Nora** (kept) | design 45's "Persona" for the person kind | 05 §2; design 45 |
| one row | **entry** | "account" collides with platform account; "Persona" duplicates the row's name | 05 §2; GLOSSARY, «Character» |
| part: who they are | **profile** | `identity` would be a fourth meaning of "identity" | design 45 §1; GLOSSARY, «Identity — three different things» |
| part: what they are like | **traits** | `character` repeats the tier's name | design 45 §1; 05 §2 |
| part: why | **hypotheses** | `hypothesis`, kept in the plural | design 45 §1 |
| modes | **told / deduced / induced / abduced** (kept) | no change | design 45 §3 |

[GLOSSARY, «Doctrine»](../../GLOSSARY.md) records DEC-29 (2026-08-06):
"persona" names a put-on role, not a person. Design 45 reintroduced that term.
This ADR restores DEC-29's naming decision.

## Decision

Keep **Character** as the tier, **person, room, unit, Nora** as its kinds, and
call one Notebook row an **entry**. Its parts are **profile** (who they are),
**traits** (what they are like), and **hypotheses** (why). Keep the four modes
**told / deduced / induced / abduced**. Retire **Persona** as a term; "account"
is now only for platform accounts.

For the Dreamer, 05 §1 rule 3 requires **one writer per tier**. Today two
adaptor families write Character: `culture` (the room adaptor) and
`character-*` write live prose through `character_revise`; `persona-*` writes
shadow typed lines through `persona_write`, with `persona-verdict` judging
hypotheses. The target is
**one `character-*` family** (person, room, unit, nora, verdict) writing typed
profile/traits/hypotheses lines. The prose adaptors retire at the switch.
Building that target is outside this ADR's implementation.

**The rename is deferred, not done.** Docs use the names above; existing code
names stay until **one later migration PR**, before
[design/unified-memory/00, «Sequence», step 8](../../design/unified-memory/00-intent.md).
The deferred scope includes `persona_line`, `persona_key`, `persona_dispute`,
migrations 0117–0125, `nora/{domain,language,infra/sql,subject}/persona*.py`,
`persona-*.md` objectives, `<persona_lines>` prompt blocks and `[persona]`
config. This docs-only change renames no code, table, migration, objective,
config key, test or tool.

## Consequences

**Better**

- Tier, kind, entry and part each have a distinct name within the existing
  hierarchy; Notebook entries remain judgements, not evidence.
- The target writer follows the existing one-writer-per-tier rule and keeps
  typed lines without giving the person kind a separate tier name.
- The retired term agrees with DEC-29 again.

**Worse**

- Two vocabularies coexist until the rename PR: documentation names and
  unchanged implementation names.
- Approximately 975 code/data occurrences of "persona" remain to rename in
  that later migration PR; this is the operator brief's estimate, not a new
  count measured by this change.
- Eval and corpus names in `evals/` still say persona.

## Rejected

- **Account tier:** collides with platform account and does not fit a room
  or unit. Keep Character.
- **Keeping Persona:** repeats the put-on-role naming problem DEC-29 already
  rejected and obscures the existing person kind and entry level.
- **Keeping `identity` / `character` as part names:** adds a fourth meaning
  of identity and uses Character for both tier and part. Use profile / traits.
