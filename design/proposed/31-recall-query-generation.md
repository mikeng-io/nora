# 31 — Recall query generation & personalization

**Status:** Draft for review (operator session 2026-09-03)
**Branch:** feat/recall-query-generation
**Triggering incident:** 2026-09-03 12:19 HKT — Nora recommended Raft as a co-op
game suggestion although the group had said (Discord, 2026-09-01 13:02) "Raft
應該今晚爆機" and Honcho held the conclusion ("miles_novak said the game Raft should
be completed on September 1, 2026"). Trace-level analysis showed the fact never
reached Nora's context: the recall query was the tagging message itself
("abiotic factor and enshrouded 以外仲有冇類似遊戲"), which semantically missed
the Raft fact, and the recent channel did not surface it either (47h old,
outside the 40-message transcript window).

## 1. Root cause — recall tools force-feed the tag message as query

Four call sites use the raw tagging message as the Honcho query:

| Site | Code | Query source |
|---|---|---|
| `about` tool | `nora/agent/tools.py:2792` | `(question or ref.name)[:500]` |
| `ask_memory` tool | `nora/agent/tools.py:2922` | `(question or ref.name)[:500]` |
| `_recall_tagger` (auto `<peer_history>`) | `nora/engagement/context.py:411` | `msg.body or ""` |
| `_recall_self` (auto `<self_card>`) | `nora/engagement/context.py:454` | `msg.body or ""` |

The justification is DEC-24 ("the question is Nora's to supply, not the
model's"). That rationale holds for values the tool does not read
(`conversation_id`) — **but the query IS read**. It is the semantic search
embedding against Honcho's conclusion store. A generic recommendation question
("有冇類似 game") embeds far from situation facts ("group finishing Raft"),
which is exactly the miss observed.

**Decision (operator, 2026-09-03):** the query should be a **dynamically
generated message** — Nora generates the question to ask memory based on the
situation, instead of force-feeding the raw tag message. This requires:

- **Guidelines** for how to generate the query — not "fetch everything about
  3/4/5 people and dump it back" (unreasonable), but **grouping/clustering**:
  the query should target the situation, the group state, or the person's
  relevant history in a focused way.
- The generated query is a **tool argument the tool actually reads**, so
  model-supplied is legitimate — this is the complement of DEC-24, not a
  contradiction of it.

### Open questions (to resolve during design)
- Which tools get the optional `query` parameter: `about`, `ask_memory`,
  `search_memory`? All three, or a subset?
- Fallback: when the model does not supply `query`, fall back to the tag
  message (current behaviour) — this keeps non-personalization turns unchanged.

### Auto-injection is OUT OF SCOPE — context-native, not query-affected (operator 2026-09-03)

`_recall_tagger` / `_recall_self` (the prefill `<peer_history>` / `<self_card>`
blocks) stay exactly as they are. They are **context-native**: they exist to
give the model the situation it is standing in, and their query behaviour is
not part of this fix. The model's own reasoning already recognises a
recommendation/personalization turn (verified in the Raft trace's
reasoningContent: "This is a game recommendation question. I have context:
group finished Abiotic Factor…") — so the awareness is there; what is missing
is the tool-query path when she reaches for more. The fix is therefore exactly
two things, and nothing else:

1. **Personalization skill** — teaches the behaviour (answer from what you
   know about the group, not a generic list) and the query strategy (generate
   a situation query, group/cluster, don't dump N people's profiles).
2. **Tool query optimisation** — `about` / `ask_memory` / `search_memory`
   accept a model-generated query instead of force-feeding the tag message.

The standing watermark read (`design/standing-watermark/`) remains a separate,
longer-term track for prefill-between-tags currency — referenced, not part of
this design.

## 2. Tool limit review — ask_memory across N people is structurally capped

Current recall-tool limits (`NATIVE_TOOLS`, `nora/agent/tools.py`):

| Tool | Budget | Cost shape |
|---|---|---|
| `about` | 4 | representation (~0.2-0.4s) + card read |
| `ask_memory` | 1 | dialectic chat (~14-19s at low) |
| `search_memory` | 4 | session-scoped message search |

The limits were sized for single-person recall ("one message can ask after two
spots"). A **personalization/recommendation turn across the group** ("suggest
for 6 people") needs N person-recall calls: `ask_memory` budget 1 means
exactly one person can be reasoned about per turn — "跨六個人已經收隊"
(operator). This is now unreasonable as the capability grows.

### Open questions
- Raise `ask_memory` (1 → 2-3)? Each call is 14-19s; must respect the ~60s
  loop wall clock. One `ask_memory` at `low` ≈ one person; three calls ≈ 45s+.
  Alternative: `about` (fast) for breadth, `ask_memory` only for the one
  person who needs depth.
- `about` per person: 4 calls can cover 4 people, but 6 needs 6. Raise to 6-8?
  Each is ~0.4s, cheap; the budget is really about model round trips.
- Grouping/clustering belongs in the generated query (item 1) — e.g. a query
  that asks Honcho for the group's collective state may return the same
  conclusions in one call instead of N person calls. This is the preferred
  lever before raising limits.

## 3. Personalization skill — one skill, one file

Confirmed (operator): **one skill, one file** (`nora/skills/personalization/SKILL.md`),
following the existing `navigation` skill shape (AgentSkills.io SKILL.md format:
`name` + `description` frontmatter, markdown body; progressive disclosure —
name + when-to-load line in `<available_skills>`, body loaded only on
activation). No new module; skills are an existing mechanism (DEC-48/50).

Scope naming (operator): "recommendation" is too objective/narrow;
**personalization** is the abstraction — recommendation is one manifestation
(game rec, restaurant rec, film rec are the same class: answers should be based
on what Nora knows about this group's state, history and preferences).

### Skill content shape (draft)
- **When to load:** recommendation / preference / taste questions — 有咩推薦,
  邊隻好, 有冇類似, 邊間好食, suggest for the group.
- **Behaviour:** a personalization answer is not a generic list. Base it on
  what is known about the group: what they are playing/finishing (current
  state), what they played/completed before, stated preferences, past
  recommendations and their reception.
- **Query strategy (ties to item 1):** when reaching for memory, generate the
  query that targets the situation — e.g. "the group's current and recent
  games, what they finished, what they said they want" — not the raw question.
  And **group/cluster**: don't fetch every person's full profile; ask the
  question that returns the situation's relevant slice, or use `about` for one
  or two people at most and prefer a group-level query.
- **Honesty boundary:** what Nora remembers is what she has seen and
  concluded; missing memory is not licence to invent preferences. Unverified
  conclusions stay hedged ("you said…", not "you like…").

## 4. Related but separate (NOT in scope here)

- **Standing watermark read** (`design/standing-watermark/`) — the root fix
  for group current-state reaching the prefill between tags. Referenced, not
  re-designed here.
- **RouteIntent** — operator flagged it was disputed with ChatGPT previously;
  do NOT expand it into a personalization router. This design deliberately
  avoids adding code-level routing for personalization.

## 5. Subagent review findings (2026-09-03, independent review)

All factual claims verified PASS against the code (md5-identical worktree). Two
MAJOR issues surfaced, both needing operator decision before implementation:

### Major 1 — RESOLVED (operator 2026-09-03): clustering = decomposition, not a group verb

"Clustering" was never about a tool accepting multiple inputs. It is **Nora's
planning behaviour**: when a personalization question arrives, she breaks the
requirement down into the queries she actually needs, and calls `about` /
`ask_memory` **several times** — one per person, deep for the key person
(solution (a) below). The mechanism is multiple calls, not a group-level verb.

The skill must teach this decomposition workflow, in order:

1. **Define the question** — what does this question actually want, and what
   is it trying to achieve? Before any query, Nora states the intent.
2. **Blast radius** — what is affected by the answer? Who/what does it touch?
3. **Overlap analysis** — what SHOULD overlap between the sub-queries (shared
   context, cross-person facts), and what should NOT (private/irrelevant
   slices)? This decides the grouping.
4. **Derive the query set** — from the above, decide what to query from each
   **storage layer**: Honcho memory (`about`/`ask_memory`), the facts tier
   (`search_knowledge`/`get_knowledge`), and notes (`note_search`). Different
   layers hold different kinds of truth; the decomposition names which layer
   answers which sub-question.
5. **Proposal outcome before synthesis** — after the queries return, form a
   PROPOSAL (the candidate answer), then decide what OTHER tools are still
   needed (web_search for freshness, place_info for a venue, etc.) before
   final synthesis.

This resolves the mechanism gap: the "preferred lever" is (a) — one
well-chosen deep `ask_memory` per key person, plus `about` breadth, plus the
fact/note layers — orchestrated by the skill's decomposition, not by a new
Honcho verb.

### Major 2 — RESOLVED (operator 2026-09-03): facts/notes are first-class storage layers in the decomposition

Commits d7852572/9644d0e0 shipped `<facts>` prefill (prefill.py:521) +
`search_knowledge` (budget 2, model-supplied query) + `get_knowledge` (budget
3). The operator's decomposition workflow (Major 1, step 4) explicitly names
the **storage layers** the query set draws from: Honcho memory (`about` /
`ask_memory`), the **facts tier** (`search_knowledge` / `get_knowledge`), and
**notes** (`note_search`). The fact tier is therefore a first-class surface in
this design, not a parallel build — the skill's decomposition decides which
layer answers which sub-question, and a "group finished Raft" style conclusion
routes to the fact tier first (it already has the model-supplied query shape
this design adds to Honcho).

### Minor findings (folded into the change surface)

- §1 heading count fixed (four, not three).
- "~60s loop wall clock" is STALE — whole-turn wall clock is **240s**
  (`[llm] timeout_seconds`, config.toml:150-163, invoke.py:145-151) and
  `turns = 20` (config.toml:201), not 8. The arithmetic (3 × 14-19s ≈ 45-57s
  affordable) survives and *strengthens* the case for ask_memory 1→2.
- `about` docstring "ask about the person, not about the topic"
  (tools.py:2689) must be rewritten when `query` is added (DEC-39: docstring
  wording is a routing surface).
- Two pinned tests must change in the same commit:
  `test_recall_asks_the_question_the_group_actually_asked`
  (test_room_tools.py:314-321) and
  `test_ask_memory_reasons_from_noras_model` (:334-343) pin the
  tag-message-as-query behaviour.
- `skills` budget is 1 with comment "Today exactly one skill exists, so a turn
  never needs two" (tools.py:1045-1051) — a second skill makes that false; a
  message spanning navigation + personalization can't load both in one turn.
  One-line raise when it matters.
- `test_skill_content.py` gates: description must say when to load AND when
  not to (:26-31), body must name no scenario (:34-40), body ≤30 non-blank
  lines (:44). The skill body must stay abstraction-level ("personalization",
  not "game night").

## Verification plan (draft)
- Replay the Raft incident end-to-end in eval: tag with the same question,
  assert the reply does not recommend Raft and ideally notes the group nearly
  finished it.
- Unit: `about`/`ask_memory` accept a generated query and it reaches Honcho.
- Eval journeys: recommendation questions return group-aware answers.
