---
type: design
title: "Character: one adaptor, one store, one agent-loop session per (unit, entity), woken by evidence"
description: "Culture is the Character of a room or unit; people and Nora are the other entities. Today one daily room pass writes the room and its posters' accounts from a pre-fetched memory answer. The target: an evidence counter per (unit, entity), a wake when it crosses a threshold, and one Dreamer agent-loop session per woken key that investigates with read tools and writes through a validated character_revise verb. Character · Nora reads her sends paired with their response episodes."
tags: [nora, character, culture, dreamer, event-driven, self]
timestamp: '2026-10-07'
edges:
  - {dst: 20-self-block, rel: sequences, provenance: asserted}
  - {dst: ../cognition-loop/50-character-and-standing.md, rel: realizes, provenance: asserted}
---

# 10 · Character

## 1. Character and culture are one thing

Culture is the Character of a room or of the unit
(`design/cognition-loop/50-character-and-standing.md` §1). There is one
Character adaptor (today's culture adaptor, generalised), one store and one
revision chain, keyed by `(unit, entity)`. DEC-278 already stores person
accounts in the culture store under `character_key(scope, person_id)`.

| entity | evidence it counts and reads | rendered as |
|---|---|---|
| room | the room's messages | `<culture>` (exists) |
| unit | new room-culture revisions; Register rows (notes, memos, events) | a unit culture block (new) |
| person | their messages; Rollup rows about them; Register rows naming them; memory as a hint, never cited | `<character>` (exists) |
| Nora | closed `response` episodes: her send paired with what followed; never her sends alone | `self` ([20](20-self-block.md)) |

The adaptor and the store are shared. Each entity kind has its own evidence
query and its own section of the objective. Every block renders through
context assembly (`docs/adr/0021-every-prompt-is-assembled-and-the-enforcer-finds-it.md`):
a row in `_BLOCKS` in `nora/agent/prefill.py` and a renderer in
`nora/language/culture_render.py`.

## 2. What runs today

Read from `nora/language/culture.py` and `[culture]` in `nora/config.py`.

- **Clock with a watermark.** `fold_interval_seconds = 86400`. A room fires
  at most once a day and only if it has new messages since its watermark.
- **Only posters are updated** (DEC-280). A person who said nothing gets no
  memory call and no new account; the old one stands.
- **The room half is an agent loop; the person half is not.** The session
  runs on the Dreamer runtime with nine read verbs (`CULTURE_TOOLS`:
  `ask_memory`, `search_memory`, `about`, `lookup_person`, `group_members`,
  `where_been`, `online_now`, ...). The person accounts come from code
  pre-fetching one `memory.chat("What is the character of X?")` per poster,
  handed in as `<people_views>`. One envelope carries the room account and
  every person's account.

## 3. The target workflow

```
code, continuously (no model)
  ingest ──▶ Record (messages, reactions, her sends)
  Rollup ──▶ episodes (stays, co-location, tie, response)
  evidence counter: per (unit, entity), citable items since that key's watermark

wake evaluator (code, the existing ~30 s tick, one query)
  wake (unit, entity) when:
      new evidence ≥ character_batch[kind]
   or account older than character_stale_seconds AND any new evidence
  never for a key with nothing new

Character session (Dreamer agent loop), one per woken key
  given:  the previous account, the new evidence, the room description
  reads:  ask_memory / search_memory   memory's view (a hint)
          record_query / rollup_read   their words, their patterns
          where_been / online_now      presence joins
          character_read               the room's culture, other accounts
  writes: character_revise(entity, body, citations)
          code checks: citations resolve to Record ids or Rollup keys,
                       the echo rule (Nora: cites response episodes),
                       the schema, provenance stamped
          or closes "no change"
  the watermark advances only on success
```

Differences from today:

1. **Event-driven, not daily.** A person who talks a lot is revised often; a
   quiet one rarely. The 7-day staleness wake still needs new evidence.
2. **An agent loop per person.** The session decides what to look up. The
   pre-fetched `<people_views>` goes away.
3. **A write verb, not an envelope.** The same move Cognition made at the
   step 4 cut-over: code validates each write as it happens.
4. **Ordering.** A person session reads the room's culture through
   `character_read` rather than writing beside it in one envelope.

## 4. Character · Nora

- **Key:** `(unit, nora)`. Per unit, not per room: a room pass cannot write it.
- **Evidence:** closed `response` episodes from `nora/infra/sql/rollups.py`.
  `silent` counts only where an answer was expected and the platform was
  proved up; otherwise it is `unknown`, not evidence.
- **Echo rule** (`design/cognition-loop/10-tiers.md` §2 rule 4): a revision
  citing only her own sends is refused by the write verb.
- **Descriptive only.** What she has learned to *do* is a lesson
  ([40](40-lessons.md)), not a line of her Character.
- **Doctrine check.** Every revision runs it once [30](30-doctrine-sort.md)
  exists; an instrument error writes no tension row and is counted. Until
  then the revision commits without it, which is safe because Character is
  descriptive and renders below the doctrine.

## 5. Choice: how Character · Nora is built

| option | cost | what it gives |
|---|---|---|
| DEC-278 style: ask memory "what is Nora like here?", combine | small | close to `self_card` again; not grounded in responses |
| **design style: her sends paired with responses, cited** | medium | the only version where she grows from how people respond |

Recommendation: design style. The response episodes already exist.

## 6. Work, in PRs

| PR | content | check |
|---|---|---|
| C1 | evidence counter and per-key wake for the Character adaptor; daily clock removed | a test wakes a key at the threshold and not below; a quiet key never wakes |
| C2 | `character_revise` write verb with citation and echo checks; the room case moved onto it; person accounts become their own sessions | room render parity; person accounts written per key |
| C3 | Character · unit and Character · Nora; objective sections; blocks | a labelled set of accounts judged against the Record they cite (`nora-eval`) |
| C4 | `character_read` | `tool_selection` rows before and after (`nora-tool`) |

## 7. Open

- Thresholds per entity kind. The cognition design starts at 200 items and 7
  days; 200 is too high for one person in a small group. Sized by
  [50](50-readback-and-sizing.md).
- Whether `<people_views>` survives as one input among the tools, or goes.
- Section schema for accounts (DEC-184 says prose, no schema; the cognition
  design gives Character fixed sections). Decide at C2.
