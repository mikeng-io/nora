# What Honcho does for Nora today

Snapshot: 2026-10-14. The code read is origin/master at `e8552c89`. Call
volume is Langfuse TOOL observations on norty. Populations are Honcho's own
Postgres (`honcho-database-1`) on norty. Each reading says its store, window
and whether it is verified. No row content, peer id or member name is
recorded here.

## 1. Every call site

**Writes.**

| call | site | what goes in |
|---|---|---|
| `feed` | `nora/ingest/stages/memory_feed.py:74` | every inbound message body, under the sender's peer, before the engage gate |
| `feed` | `nora/ingest/comprehension.py:487` | a media reading, once comprehension finishes |
| `feed` | `nora/engagement/stages/feedback.py:52` | Nora's own sent replies, under the Nora peer, building her self card; the `<self_card>` block was retired by DEC-292, so this is a write with no reader |
| `feed` | `nora/app/adoption.py:171`, `nora/app/memory_backfill.py:309` | a newly mapped person's earlier messages (DEC-16) |
| `conclude` | `nora/agent/tools.py:13418` (`remember`) | a person's settled claim about themselves |
| `conclude` | `nora/subject/knowledge.py:786` | eval seeding only |

**Reads into a prompt (prefilled, no tool call).**

| read | site | block |
|---|---|---|
| `peer_card` | `nora/engagement/context.py:581`, `nora/app/fire.py:912` | the tagger's card |
| `representation` | `nora/engagement/context.py:635`, `nora/app/fire.py:1172` | up to `tagger_card_conclusions` (8) conclusions about the tagger, ranked against the message |

**Reads by a tool.**

| tool | site | Honcho call |
|---|---|---|
| `about` | `nora/agent/tools.py:5982` | `peer_card` + `representation` |
| `ask_memory` | `nora/agent/tools.py:6165` | `chat` (Honcho's dialectic agent, a model call inside Honcho) |
| `search_memory` | `nora/agent/tools.py:6264` | `search` over the room's messages (pgvector, cosine cut-off 0.5) |
| `search_memory_workspace` | `nora/agent/tools.py:13290` | `search` across the unit's rooms |

**Other reads.** `suggest_venue` asks `chat` one fixed preference question per
roster member (`nora/app/gatherings.py:409`). The portal's memory page reads
`representation` and `peer_card` (`nora/web/memory.py:91`). `/status` reads
health (`nora/web/status.py:45`).

**Administration.** Workspace routing (`nora/app/memory_routing.py`, the
`WorkspaceRouter` in `nora/infra/honcho/client.py:283`, DEC-214), carrying
conclusions between scopes (`nora/app/memory_carry.py`), detaching
(`nora/app/memory_detach.py`) and per-draw eval workspaces
(`nora/subject/memory_workspace.py`).

## 2. How often the reads run live

Langfuse TOOL observations, all retained history, read 2026-10-14. The caller
split is by trace name over the latest 100 observations of each tool.

| tool | calls | caller split (latest 100, window) | status |
|---|---|---|---|
| `search_memory` | 1,074 (923 since 10-01) | Cognition 100 / 100 (10-11 → 10-14) | VERIFIED |
| `search_memory_workspace` | 434 | Cognition 100 / 100 (10-10 → 10-14) | VERIFIED |
| `about` | 150 | Cognition 34, old dream pass 55, tag turn 11 (09-17 → 10-14) | VERIFIED |
| `ask_memory` | 88 | old dream pass 50, proactive 11, Cognition 7, tag turn 3; 17 not attributed (08-09 → 10-11) | VERIFIED, 17 unattributed |
| `remember` | 15, newest 10-06 | — | VERIFIED |

For comparison over the same store: `note_read` 753, `note_search` 241,
`web_search` 754.

**Reading.** Honcho's live value is now almost entirely **semantic search for
the Cognition session**: 1,508 search calls against 253 for everything else,
and every one of the latest 200 searches came from Cognition. The tag turn
rarely calls a memory tool (11 of the latest 100 `about` calls, 3 of
`ask_memory`). What the tag turn gets from Honcho, it gets through the two
prefilled blocks in §1, whose contribution is §4's open measurement.

`tool_call_log` cannot answer any of this: it records external lookups only
(`web_search`, `web_fetch`, `place_info`, `weather`, `route` and a few more).
No memory tool appears in it.

## 3. What Honcho holds

Honcho's Postgres on norty, read 2026-10-14. `pg_stat_user_tables` row
estimates were stale by three orders of magnitude, so these are `count(*)`.

| | Nora's main workspace | Nora's unit workspace |
|---|---|---|
| messages | 7,337 (08-05 → 10-14) | 433 (08-05 → 10-11) |
| `explicit` conclusions | 21,722 | 1,425 |
| `deductive` | 871 | 150 |
| `inductive` | 1,625 | 143 |
| last 14 days, main | explicit 6,173 · inductive 492 · deductive 230 | |

- **The corpus is 89% `explicit`.** cognition-loop/80 §6 keeps exactly
  that level (what a person told), and it is the level Nora's Register
  `told` mode already represents with a citation to the message.
- **1,218 of 1,295 Honcho workspaces are `nora-eval*` leftovers**, one per
  eval draw, never deleted. That is a cleanup owed whether or not Honcho
  stays.
- **Nora is not the Honcho server's only user.** Retiring Honcho *for Nora*
  means removing her client and her workspaces, not stopping the server.

## 4. What replaces each, and what is not yet known

| Honcho output | replaced by | open |
|---|---|---|
| `search_memory`, `search_memory_workspace` (Cognition's main use) | the derived index, scoped per room or unit, lexical + vector ([00](00-intent.md) part 1) | the bake-off ([20](20-embedding-bakeoff.md)); parity with Honcho's search measured on the Cognition session's own recorded queries |
| `peer_card` block | the person's Persona: its Identity part beside the (already live) Character part ([35](35-persona.md)) | Character · person is **live** (DEC-290; 35 person rows on norty, the latest written 2026-10-14). Persona's Identity part is not built |
| tagger `representation` block (`peer_history`) | the index, queried with the message, filtered to the tagger | **present**: [context-plan/09](../context-plan/09-what-a-turn-carries.md) measured 200 live tag turns on 2026-10-12: `peer_history` in 197 (median 363 tokens), `peer_card` in 164 (262). Whether either **changes replies** is not measured. The measure: replay ≥ 80 recorded tag turns with and without the two blocks (one variable per arm, as-recorded arm included), blind pairwise judgement; the blocks are cut if the with-arm is preferred in < 55% of pairs |
| `about` | `character_read` for the person, plus the index | — |
| `ask_memory` (Honcho `chat`) | `ask_memory` over the index, answered by Nora's own model call | the operator's single verb; [00](00-intent.md) |
| `suggest_venue` preference question | the index: told preferences and induced Character lines | 1 call site |
| `remember` → `conclude` | a Record item plus a stimulus; the Persona adaptor writes the Identity line citing the message ([30](30-identity-and-character.md) §7, [35](35-persona.md)) | ruled 2026-10-15 (35) |
| `feed` (5 sites) | nothing: the Record already holds every message, and the index reads the Record | the media reading feed: the index reads `media_descriptions` directly |
| 21,722 + 1,425 `explicit` conclusions | **not carried.** They have no citation to a Record row, so under rule 3 of cognition-loop/80 §6 ("memory is a hint, never a citation") they cannot ground a tier entry | **dropped** (operator, 2026-10-15, [35](35-persona.md) §4) |
| `deductive` / `inductive` conclusions | dropped; cognition-loop/80 §6 step 2 already plans their exit from the prompt once Character ships | step 4 there ("measure before cutting") is this table's last column |

**The order this forces.** Search can go first: it is the bulk of the use and
has a clean replacement. The two prefilled blocks go last, after a person's
Persona has its Identity part beside the (already live) Character part, and after
the blocks' live contribution is read. `feed` and
`remember` go with them, because a write with no reader is not worth keeping.
