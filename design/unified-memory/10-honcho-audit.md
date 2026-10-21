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
| `conclude` | `nora/subject/knowledge.py:812` | eval seeding only |

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

> 2026-10-21 (18 / ADR 0026): [18](18-message-readings.md) and [§6](#6-honchos-architecture-mapped) amend the replacements below: source statements live in Record message readings, Character entries are the bounded card, and Views use `memory_search` + pre-fetch, with Nora’s own turn doing the reasoning Honcho’s dialectic agent does. The old `feed` and explicit-conclusion rows are historical: in the revised design every batch gets a reading; Q3 leaves source-rechecked seeding open and recommends Honcho as measurement reference only for the first measure. No unchecked conclusion becomes evidence.

| Honcho output | replaced by | open |
|---|---|---|
| `search_memory`, `search_memory_workspace` (Cognition's main use) | the derived index, scoped per room or unit, lexical + vector ([00](00-intent.md) part 1) | the bake-off ([20](20-embedding-bakeoff.md)); parity with Honcho's search measured on the Cognition session's own recorded queries |
| `peer_card` block | the person's Persona: its Identity part beside the (already live) Character part ([35](35-persona.md)) | Character · person is **live** (DEC-290; 35 person rows on norty, the latest written 2026-10-14). Persona's Identity part is not built |
| tagger `representation` block (`peer_history`) | the index, queried with the message, filtered to the tagger | **present**: [context-plan/09](../context-plan/09-what-a-turn-carries.md) measured 200 live tag turns on 2026-10-12: `peer_history` in 197 (median 363 tokens), `peer_card` in 164 (262). Whether either **changes replies** is not measured. The measure is the one replay in [45](45-persona-adaptor.md) §6 (amended 2026-10-15, one threshold everywhere): ≥ 80 recorded tag turns, the two blocks' arm against the `<persona>` arm (one variable per arm, as-recorded arm included), blind pairwise judgement; the switch passes only if Persona is preferred in ≥ 55% of the pairs |
| `about` | the person's Persona, plus the index | — |
| `ask_memory` (Honcho `chat`) | `ask_memory` over the index, answered by Nora's own model call | the operator's single verb; [00](00-intent.md) |
| `suggest_venue` preference question | the index: told preferences and induced Character lines | 1 call site |
| `remember` → `conclude` | a Record item plus a stimulus; the Persona adaptor writes the Identity line citing the message ([30](30-identity-and-character.md) §7, [35](35-persona.md)) | ruled 2026-10-15 (35) |
| `feed` (5 sites) | nothing: the Record already holds every message, and the index reads the Record | the media reading feed: the index indexes each attachment `(message, sha256)` with the reading copied in from `media_objects.{title,description,content}` ([25](25-memory-graph.md) §7). There is no `media_descriptions` table; `nora/infra/sql/media_descriptions.py` is a cache over `media_objects` and `sticker_index` |
| 21,722 + 1,425 `explicit` conclusions | **not carried.** They have no citation to a Record row, so under rule 3 of cognition-loop/80 §6 ("memory is a hint, never a citation") they cannot ground a tier entry | **dropped** (operator, 2026-10-15, [35](35-persona.md) §4) |
| `deductive` / `inductive` conclusions | dropped; cognition-loop/80 §6 step 2 already plans their exit from the prompt once Character ships | step 4 there ("measure before cutting") is this table's last column |

**The order this forces.** Search can go first: it is the bulk of the use and
has a clean replacement. The two prefilled blocks go last, after a person's
Persona has its Identity part beside the (already live) Character part, and after
the blocks' live contribution is read. `feed` and
`remember` go with them, because a write with no reader is not worth keeping.

## 5. The rest of the client: every remaining use, mapped (2026-10-15)

§4 maps what the model reads and writes. These are the uses §1 lists that §4
did not, so that "Honcho out" (00 step 4) leaves nothing calling it:

| use | site | after Honcho |
|---|---|---|
| `feed` of a newly mapped person's earlier messages (DEC-16) | `nora/app/adoption.py`, `nora/app/memory_backfill.py` | **deleted.** The Record already holds those messages, and the index projects them at ingestion ([25](25-memory-graph.md) §7). |
| `feed` of Nora's own replies (no reader since DEC-292) | `nora/engagement/stages/feedback.py` | **deleted.** Her replies are Record rows and become index nodes like any message. |
| the portal's memory page (`representation` + `peer_card`) | `nora/web/memory.py` | **re-pointed** to the caller's own Persona with the evidence behind each line (30 §7 «Seeing it»). The page stays user-scoped. |
| `/status` Honcho health | `nora/web/status.py` | **replaced** by the index's own health: last projection, the embedding worker's backlog, unembedded node count, the projection-failure queue ([25](25-memory-graph.md) §7). |
| `/health` Honcho reachability, which turns `healthy` false | `nora/obs/health.py:155` | **replaced** by the index's reachability (the database it lives in); a Honcho outage stops being a Nora health failure. |
| startup: `ensure_workspace`, and the `foreign_workspaces` `StartupError` | `nora/app/composition.py:1992`, `:1998` | **deleted** with the client. There is no external workspace to create or to mistake for another application's. |
| `honcho_feed_success` / `honcho_feed_failure` gauges | `nora/obs/metric_specs.py:2267-2268` | **retired** with `feed`. The metric-name contract is append-only, so they stay declared and read zero; the index's own projection counters are new names. |
| the Persona adaptor's own `search_memory` and `about` (`CHARACTER_PERSON_TOOLS`) | `nora/language/character.py:119-121` | **re-pointed** to the index: `ask_memory` over it, and `about` reading Persona ([45](45-persona-adaptor.md) §4 «Tools»). |
| workspace routing (DEC-214) | `nora/app/memory_routing.py`, `WorkspaceRouter` | **deleted.** Scope is the unit boundary over Nora's own unit and room columns ([25](25-memory-graph.md) §4 step 1, [35](35-persona.md) §3; `[lineage]` stays off); there is no external workspace to route to. `identity.toml`'s `[workspaces.*] honcho_workspace` binding goes with it. |
| `carry-conclusions` | `nora/app/memory_carry.py` | **deleted.** It moved uncited conclusions between scopes, and those are dropped ([35](35-persona.md) §4). |
| `detach-unit` (a unit leaving a workspace) | `nora/app/memory_detach.py` | **replaced, not dropped** (amended 2026-10-15; it said "now lineage", but `[lineage]` is off and stays off). Its purpose, that a departed unit's speech stops shaping another unit's picture of a person, is the unit boundary: a Persona line and an index node carry their unit, and a read in another unit never sees them ([35](35-persona.md) §3). A unit leaving is a policy change, and the next index rebuild re-scopes its rows. |
| the per-call budget (`reserve`, `release`, `spent`, `at_ceiling`) | the memory client's guard | **deleted** with the client. Search becomes a local query; `ask_memory`'s model call is budgeted like any other tool. |
| per-draw eval workspaces | `nora/subject/memory_workspace.py` | **deleted.** The eval sandbox is per-database already, and the index lives in that database. The 1,218 leftover `nora-eval*` workspaces on the Honcho server are deleted once. |
| the eval workspace binding, and the known / unknown twin pairs keyed on Honcho conclusions | `nora/subject/obtain.py:537`, `:542` | **re-pointed.** The binding goes with routing; a twin pair's `-known` half is seeded with a Persona line instead of a conclusion, so the pair still tests that the knowledge cut works. |
| `conclude` in eval seeding | `nora/subject/knowledge.py:812` | **re-pointed** to seeding a Persona line. |

With §4 and §5 together, every Honcho call site in `nora/` has a fate.

## 6. Honcho's architecture, mapped

> 2026-10-21 (operator audit): Honcho v3.1.2 on norty (`~/workspace/honcho/src`) has three stages. This supplements the earlier call-site snapshot; [18](18-message-readings.md) records the resulting design revision.

1. **Deriver** (`src/deriver/prompts.py`): every message batch, a cheap model
   extracts all explicit atomic facts, with no cap.
2. **Dreamer** (`src/dreamer/specialists.py`): tool-using deduction and
   induction specialists, 10–12 iterations, plus peer-card refresh. The
   deployed configuration requires 50 new observations and 60 minutes idle,
   with at least 8 hours between runs.
3. **Dialectic**: a query-time agent over observations and messages.

Honcho has **no abduction and no code Rollup**. Its stages map into our
existing layers; Memory no longer sits beside the flow:

| Honcho | ours | layer |
|---|---|---|
| deriver (explicit facts) | message readings | Record |
| (none) | Rollup | Rollup |
| dreamer deduction / induction (+ our abduction) | Character entries: traits, hypotheses | Notebook |
| peer card | Character entry profile | Notebook |
| dialectic | `memory_search` + pre-fetch over readings, Rollup and entries; Nora’s own turn does the reasoning Honcho’s dialectic agent does | Views |
