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
| person | their messages; Rollup rows about them; Register rows naming them; memory as a hint, never cited | `<character>` (exists), the accounts of the people in the turn, capped (below) |
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

> **What renders, 2026-10-07 (character-who).** `<character>` carries the accounts
> of the people IN the turn, not one per listed person. Tag: the tagger, anyone
> the tag message @-mentions, the replied-to message's author and mentions, and
> every listed person who speaks in the transcript window. Fire: the window's
> speakers and the occasion's own person. Listed persons only, roster order,
> from resolved person ids (`nora/engagement/character_who.py`). Measured the
> same day: friends' seven accounts were 9,065 characters of prose, 11,183 rendered
> (~3,000 tokens), re-sent on every model call. Each account is capped at 2,500
> characters and the rendered block at 12,000 (`PERSON_ACCOUNT_MAX_CHARS`,
> `CHARACTER_BLOCK_MAX_CHARS`): the first account the budget cannot hold whole
> is cut, later ones are left out whole and counted in one closing marker, each
> cut saying how many it dropped. `character_read` (C4) would serve anyone
> else's account, but it is Dreamer-loop only until a `tool_selection` run, so a
> tag or fire turn cannot reach an absent person's account. DEC-278 and DEC-290
> are narrowed in the decision index.

> **As built, branch `mikeng-io/character-sessions` (C2a, C2b), 2026-10-07.**
> The list above is what ran before it. The room session now writes through
> `character_revise` (`nora/language/character_session.py`), citing batch
> row ids, and writes the room alone. `<people_views>` and the `memory.chat`
> pre-fetch are deleted; each (unit, person) has its own `character-person`
> session (`nora/language/character.py`), keyed `{unit}#character:{person}`,
> woken by `[memory.profile.person] update_every_messages` (100) or `stale_seconds`. The per-room
> person heads are NOT carried to the unit key (security review, 2026-10-07: a
> one-room account would render in every room of the unit); each person's first
> session writes fresh, on the first tick after deploy. The room fold no longer
> holds `where_been`/`online_now`, so no Character account rests on presence.
> C3a: `character-unit` (key `{unit}#unit`), woken by `[culture]
> memory.profile.unit.update_every_room_profile_changes` (5) new room-culture revisions or `stale_seconds`, cites
> through the Record ids its rooms' cultures cite (Register rows are not read
> yet), rendered as `<unit_culture>` after `<culture>` on tag and fire turns.
> C3b: `character-nora` (key `{unit}#nora`), woken by `[culture]
> memory.profile.nora.update_every_replies` (100) closed episodes of the current Rollup build or
> `stale_seconds`, handed each send with the replies that quoted it, its
> counts and outcome; the verb refuses a revision whose citations are only her
> sends. No block (the self block is [20](20-self-block.md)).

## 3. The target workflow

```
code, continuously (no model)
  ingest ──▶ Record (messages, reactions, her sends)
  Rollup ──▶ episodes (stays, co-location, tie, response)
  evidence counter: per (unit, entity), citable items since that key's watermark

wake (code, the dream scheduler's tick, one count per key)
  wake (unit, entity) when:
      new evidence ≥ wake_batch for its kind
   or account older than stale_seconds AND any new evidence
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
4. **Ordering.** A person session is handed the current cultures of the
   unit's rooms as input (code reads the store) rather than writing beside
   them in one envelope. `character_read` (C4) is the pulled read for turns
   and Cognition; C2b does not wait for it.
5. **Unit-keyed persons.** Today a person's account is keyed per ROOM
   (`character_key(scope, person_id)` in `nora/domain/culture.py`, `scope`
   the room). The target is one account per (unit, person); C2 moves them.

### 3.1 Code at the edges, an agent loop in the middle (operator, 2026-10-07)

Code does what must be the same every time: count evidence, wake the key,
hand over the previous account and pointers to what is new, validate every
write. The model does the judgement: what to look up and what the account
now says.

An agent loop is not research by itself. Today's culture pass is an agent
loop, and the median run calls no tool ([50](50-readback-and-sizing.md)
§3.4): everything it could look up was handed in. So the session is handed
less (no pre-fetched `<people_views>`, pointers rather than full text where
the batch is large), every changed line must cite a Record id or Rollup key
the write verb can resolve, and tool calls per session and the share of
lines with a citation are read back after C2. A median of zero tool calls
after C2 means it is still a pipeline.

Research matters by entity: a room needs little beyond its own messages; a
person needs their messages across rooms, presence patterns and ties; Nora
needs her sends joined to their responses. One adaptor and one session shape
serve all four; the evidence query and the objective section differ.

### 3.2 Where the wake lives

On the **dream scheduler** (`AdaptorSchedule` in `nora/agent/dreamer.py`,
evaluated in `_dream_adaptor_once` in `nora/app/runtime.py`), not on
Cognition's wake evaluator (`nora/app/wake.py`). The dream scheduler already
runs culture with per-key watermarks; today it fires on `lag > 0 and age >=
min_interval_seconds`, which with a 7-day interval is already the stale
wake; C1 adds `or lag >= wake_batch`. **C1 is rooms only.** The scheduler's
one count is `language.lag(scope_id=key)`, human messages in one
conversation, and it lists keys of kind `scope` or `unit` only. Person keys
(C2b), Nora's episode count (C3b) and Lessons each need their own key kind
and their own count; C1 must not hard-code `lag` as the only evidence. Cognition's
evaluator carries stimuli kinds, brakes and floors built for fast group
thinking, which none of the slow adaptors need. Lessons uses the same rule
([40](40-lessons.md) §2), once the scheduler takes a count per key kind.
Per-kind batches (100 persons, 5 units, 100 Nora) and moving the wake off
Cognition's evaluator amend `design/cognition-loop/20-observe.md` §3, which
sets one `character_batch` of 200 for every entity; DEC-288 records the room
part with C1, and each later kind's PR records its own. This holds unless the dream scheduler cannot run one
session per key concurrently or cannot be woken mid-cycle for a correction.

### 3.3 Memory is a hint, never a dependency

Honcho is a read tool the session may call (`ask_memory`, `search_memory`),
never a pre-fetch the account depends on. Today DEC-278 makes every person
account depend on a `memory.chat` answer fetched before the pass. C2 removes
that, so a later merge or sunset of Honcho (#736) changes no Character code.

## 4. Character · Nora

- **Key:** `(unit, nora)`. Per unit, not per room: a room pass cannot write it.
- **Evidence:** closed `response` episodes from `nora/infra/sql/rollups.py`.
  `silent` counts only where an answer was expected and the platform was
  proved up; otherwise it is `unknown`, not evidence.
- **Echo rule** (`design/cognition-loop/10-tiers.md` §2 rule 4): a revision
  citing only her own sends is refused by the write verb.
- **Descriptive only.** What she has learned to *do* is a lesson
  ([40](40-lessons.md)), not a line of her Character.
- **Doctrine check.** Every revision runs it once the instrument exists
  (Lessons L3, on the law subset of [30](30-doctrine-sort.md)); an instrument error writes no tension row and is counted. Until
  then the revision commits without it, which is safe because Character is
  descriptive and renders below the doctrine.

## 5. Choice: how Character · Nora is built

| option | cost | what it gives |
|---|---|---|
| DEC-278 style: ask memory "what is Nora like here?", combine | small | close to `self_card` again; not grounded in responses |
| **design style: her sends paired with responses, cited** | medium | the only version where she grows from how people respond |

Recommendation: design style. The response episodes already exist.

## 6. Work, in PRs (Character finished before Lessons, operator 2026-10-07)

| PR | content | check |
|---|---|---|
| C1 | rooms only: count-or-stale wake on `AdaptorSchedule`; `[culture]` gets `wake_batch` (200) and `stale_seconds` (7 days); the daily `fold_interval_seconds` goes | a test wakes a key at the threshold and not below, a stale key with one new item, never a key with none; read back fires per room for a week (expected: Friends about twice, quiet rooms once) |
| (C1 side effect) | person accounts are still written in the room pass (DEC-278) until C2b, so they slow with their room, from daily to about weekly | named in DEC-288 |
| L0 (parallel) | record the brief's `ask` intent on each send ([40](40-lessons.md) §6) | `silent` appears in the Rollup; it needs weeks of data before C3 and Lessons read it |
| C2a | `character_revise` write verb (citations resolve to Record ids, schema, provenance); the room case moves from envelope to verb | room render parity; refusal of an unresolvable citation in tests |
| C2b | person sessions per (unit, person): unit-wide evidence count (their messages across the unit's rooms), own watermark, keys moved from room to unit, `<people_views>` pre-fetch removed, memory a tool | each poster's account written per key; tool calls per session read back |
| C3a | Character · unit: evidence = new room revisions (5) (Register rows deferred, DEC-290); its block | block declared in `_BLOCKS`, renders in the unit's rooms only |
| C3b | Character · Nora: evidence = closed response episodes that are evidence (100; `Episode.is_evidence`), echo rule in the verb, at least three cited episodes (DEC-290 amended 2026-10-07) | a revision citing only her sends is refused; labelled accounts judged against their citations (`nora-eval`) |
| C4 | `character_read` | `tool_selection` before and after (`nora-tool`) |

Step 2 (the self block, [20](20-self-block.md)) follows C3b. New blocks
follow `_BLOCKS` and context assembly; `design/context-plan/` (on master) is
an experiment-only selector: it classes `culture` and `character` as
selectable, and its experiment filter keeps Culture visible. New blocks
declare themselves in `_BLOCKS` the same way, so the selector can see them;
nothing here conflicts with it.

## 7. Open

- ~~Section schema for accounts~~ SETTLED by DEC-290 (2026-10-07): prose, no
  schema; DEC-184 stands. The cognition design's fixed Character sections are
  not built. **Reversed for Persona by the operator, 2026-10-15**
  ([unified-memory/45](../unified-memory/45-persona-adaptor.md) §0): typed
  lines around prose claims. Prose stays the live format until that is built.
- Whether a stale wake needs a floor of a few items
  ([50](50-readback-and-sizing.md) §4).
