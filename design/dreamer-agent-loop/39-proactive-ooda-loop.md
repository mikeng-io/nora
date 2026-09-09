---
type: proposal
title: "The Proactive Adaptor Is Nora's Other Half — One OODA Loop, Two Entry Points"
description: "Decides what node 38 left open. The proactive adaptor is a Dreamer adaptor, workspace-keyed, with the whole tool registry to READ and exactly one act verb, remind; its turn decides WHEN and the fire path speaks THEN. Passive (tagged) and active (clocked) Nora run the same observe–orient–decide–act loop, and the loop closes only through a human. Every decision carries where it was checked and what would reopen it."
tags: [nora, proactive, initiative, dreamer, ooda, reminder, standing, measurement]
timestamp: '2026-09-09'
edges:
  - {dst: 38-proactive-adapter-handoff, rel: supersedes, provenance: asserted}
  - {dst: 33-note-dreamer-schedule, rel: revises, provenance: asserted}
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
  - {dst: 26-standing, rel: depends_on, provenance: asserted}
  - {dst: 27-note-dreamer, rel: depends_on, provenance: asserted}
  - {dst: 32-fact-dreamer-schedule, rel: extends, provenance: asserted}
  - {dst: 35-ambient-participation, rel: relates_to, provenance: asserted}
version: 1
---

# The Proactive Adaptor Is Nora's Other Half

## 0. What this node decides, and what it supersedes

Node 38 was a fact sheet and decided nothing. This node decides. Every
decision below was made by the operator on 2026-09-09, in conversation, and is
recorded with the evidence it rests on and the reading that would reopen it.

**It supersedes 38 whole.** 38 stays as the record of what was true before the
decisions; its seven open questions are answered here, and one of them
(«what R-31 returns for a turn with no asker») turned out to be the wrong
question — the guard never looks at the asker (§2, D2).

**It revises 33 §8 on one point.** 33 §8 said the proactive adaptor is
«model-proposes / code-disposes like every other adaptor». It is not: its
decision *is* a tool call, and the envelope is bookkeeping (D4). Everything
else in 33 §8 — separate adaptor, not a widened `NoteRevisitor`, not a
Heartbeat, daily, reads settled state — stands. 33 §8's list of settled
state names the knowledge and language tiers; both were deleted on 2026-09-07
(issue #115) and are historical there.

**The frame the operator gave, in his words:** the proactive layer is not an
adaptor bolted onto Nora, it is *the other half of her*. One half answers
when tagged. The other half acts when nobody asked. «佢一半嘅本體係點樣答問題
（passive），另外一半嘅本體就係呢一個 active proactive adapter.»

## 1. One loop, two entry points

Both halves run the same loop. It is an OODA loop — observe, orient, decide,
act — and it is closed *through a human*, never through Nora herself.

| stage | what it is in Nora | where |
|---|---|---|
| **Observe** | Human rows land in the record; Honcho derives her model of each person | `RecordStore`, `is_nora == 0` in `since`; Honcho `(nora → person)` |
| **Orient** | Conversation becomes tracked uncertainty; each room gets a one-paragraph position | note tier (`note-candidates`, curator); Standing (design 26) |
| **Decide** | Given the whole workspace, what is worth doing and *when* | the proactive adaptor's turn (this node) |
| **Act** | Say it, in a room, at the decided time | the fire path, via a `kind="proactive"` reminder row |

The passive half enters this loop at *Act*: somebody tags her, she reads the
same observe/orient state, answers in that room. The active half enters at
*Decide*: the clock fires, she reads the same state across the workspace, and
either schedules an act or does nothing. Same tools, same doctrine substrate,
same deliverer chain. What differs is the trigger, the scope, and the fact
that the active half is owed nothing and may stay silent.

**The feedback edge.** Act → Observe passes through a person. Nora's own
words are not observation: `since` excludes `is_nora` rows in the SQL
predicate (`nora/infra/sql/audit.py`), the curator excludes them again, and
design 27 §11.3 states the rule — «her saying *I'll book Yardbird* is an
intention she stated, not the group settling anything». What Nora keeps of
her own action is a *record* (D7), never *evidence*. A human replying to her
message is a human row; that is how her proposal reaches the note.

**Why the loop cannot self-run.** The operator's worry, verbatim: a reminder
sends a message, the message reaches memory, memory becomes a note, the note
is refined, a reminder is set again, «一路落去». Each edge was checked:

| edge | brake | where |
|---|---|---|
| Nora's message → note | excluded by predicate | `since`, `is_nora == 0`; curator filter |
| act turn → another reminder | `remind` refuses inside a fire turn | `nora/agent/tools.py` «a reminder that can set a reminder is a loop with no bound» |
| decide turn → unbounded rows | per-requester cap | `max_pending_per_requester = 20`, requester is Nora (D5) |
| decide turn → its own note | proactive holds no note content writer | D6 |
| act turn on a note that moved | screening cancels the row | D11 |
| same note, nobody replies | strikes | `record_wake`, `MAX_DATELESS_STRIKES = 4` (D6) |

Four of the six exist today; D6's content rule and D11 are the two this node
adds.

## 2. Decisions

Each decision: what, the evidence, the reading that would reopen it.

### D1. It is a Dreamer adaptor. Not a new runtime, not a fire-path occasion.

38 listed three shapes. The operator's question settled it: *why build a
separate thing when the runtime is built?* Design 03 §2's own finding is that
`DreamerRuntime` «is a correct implementation of a research agent, wired to
adaptors that give it nothing to research with». The proactive adaptor is
the first adaptor that gives it something.

Two properties of the runtime fit the active half exactly and the tag/fire
turn does not: the stopping condition is «the model declines to call
anything» under `tool_choice="auto"` — which *is* «may stay silent» — and
`AdaptorBudget` is per adaptor, so a once-a-day pass can afford more turns and
tokens than a tag turn's fixed budgets.

**Reopens if:** the decide turn needs a terminal verb after all (it would then
be a fire-path occasion, and D3's «no `send_reply`» is what keeps it from
needing one).

### D2. Workspace-keyed, with a home room per workspace.

`key_kind = "workspace"`, like `note-curation`. The operator's word is
*circle*; the codebase's word is *workspace*, defined in `policy.example.toml`
as «the SOCIAL UNIT a room belongs to — the same people reachable in more than
one place» (DEC-105, R-49). Notes are already workspace-scoped
(`Note` docstring: «one tracked uncertainty, scoped to a workspace»;
`established_in` is «a routing key, never a filter»).

**The home room.** A workspace has no conversation, and every room-scoped
tool — `where_is`, `about`, `ask_memory`, `load_transcript`, `group_members`
— resolves through a `Room` that needs `platform` and `conversation_id`.
`policy.toml` therefore gains one room per workspace marked as its home. The
adaptor's `Room` is built from that room with a real roster source, and
`remind` writes into it.

This answers 38's «R-31 with no asker» question by dissolving it. `Whereis.
locate` never reads the asker: it reads `room.scope()` and refuses when a
resolved member is outside the circle or an account is unnamed, waivable per
room by `location = true` (DEC-74/79). With a real home room the guard runs
exactly as it does on a tag. `WORKSPACE_SAFE_TOOLS`' refusal of room-scoped
tools for workspace-keyed adaptors was written because «binding one to an
arbitrary room of that workspace would be a disclosure decision (R-31) nobody
has made». The home room is that decision, made by the operator per
workspace, in the file where room trust already lives.

Live on 2026-09-09: three workspaces in `policy.toml` — `ridgeline` (2 rooms),
`friends` (3 rooms), `wedding` (1 room).

**Reopens if:** a workspace needs two home rooms (then the home room is per
platform, and `send_to`'s «one room per platform per workspace» rule already
constrains it that way).

### D3. Every read tool is accessible; the one act verb is `remind`.

The operator's position: «所有嘅 capability 都要 accessible». For reads this
is literal — the registry's whole read set, room-scoped included, through the
home room. For acts it is exactly one verb, and the reason is D4.

**What this changes in DEC-133.** DEC-133 (1) said a background process «that
can speak to the group unprompted — or set a reminder, or write to the events
store — with nobody waiting on it is a categorically different thing». Half of
that stands: the Dreamer still cannot *speak*. `deliverer=None` in
`RegistryToolSource.build` does not change, and R-1b (tag path and fire path
are the only two senders, pinned by an AST test on `deliver.build`'s callers)
is untouched. The half that is deprecated is «or set a reminder»: for this
adaptor alone, `remind` is admitted past the `read_only()` ceiling.

**Mechanically** (`nora/agent/dreamer.py`, `RegistryToolSource.build`): the
registry build is already the full one — every argument is already passed,
with `deliverer=None` and a synthetic `Room(platform="", roster_source=None)`.
The change is: the `& self.read_only()` filter gains a per-adaptor allowance
for `remind`; the synthetic `Room` becomes the home room's `Room`; and
`tool_subset_faults` exempts this adaptor from `WORKSPACE_SAFE_TOOLS`.
38's «three arguments» estimate holds, minus the deliverer.

**Reopens if:** measurement (§5) shows the decide turn needs to speak
*immediately* often enough that a 120-second reminder lead is the wrong
shape. Then `send_reply` enters the Dreamer and R-1b gets a third caller —
a separate decision with its own DEC.

### D4. Decide and Act are two turns. The decide turn writes rows; the fire path speaks.

The operator's test for whether something is proactive: «明明應該晏晝先同佢講，
但係你朝早八點鐘 run 一次嘅話，呢個唔係 proactive，呢個純粹叫 scheduling.» A daily
slot with `send_reply` would let her choose *whether*; it would not let her
choose *when*. Choosing when is the capability.

So the **decide turn** (Dreamer, D1) reads the workspace, uses tools, and its
only act is `remind(...)`: zero or several rows with `kind="proactive"` and a
`payload` carrying the note id and a one-line intent. It never composes the
message.

The **act turn** is the fire path. `event_registry` (composition root) maps a
due row's `kind` to an occasion factory and holds `reminder` and `gathering`
today; it gains `proactive` → a new factory in `nora/app/fire.py`. Its header
is not «X asked you to remind about Y» but «at 08:00 you decided to raise this
at 14:00 because …; here is the note now». The fire turn builds the full
registry, so `where_is` and `route` are called *at 14:00*, which is when
«where everyone is coming from» is a question worth asking.

**The envelope becomes bookkeeping.** 33 §8's «model proposes, code disposes»
does not describe this adaptor: the proposal *is* the `remind` call, already
landed when the envelope is parsed. The envelope carries the decision record —
which notes it considered, what it scheduled or declined, why — and the sink
writes `record_wake` (D6). 38's open question 7 («research in the Dreamer
turn or the fire turn») is answered: *both*, split by what each needs to know.

**Reopens if:** the act suite (§5) shows the fire turn routinely contradicts
the decide turn's intent. Then the payload carries more, or the decide turn
composes a draft.

### D5. Nora owns her own reminder rows.

`remind` refuses when `room.tagger is None` — «only a MAPPED person may own a
row» (§12) — because «inventing an owner would put a reminder into a room
under nobody's name». A proactive turn has no tagger. The owner is Nora
herself: her `PersonRef` from `identity.toml` is the home-room `Room`'s tagger
in the decide turn, and `requester_person_id` on every row she writes.

Not the note's anchor person: «Miles: dinner at 8» and «dinner at 8» are
different claims (R-49), and a row under Miles's name that Miles never asked for
is the first.

Consequences that come free: `max_pending_per_requester = 20` becomes the
global ceiling on unfired proactive rows; the fire-time policy re-check
(§5.1) runs against Nora's own membership of the home room; the fire header
can say «you set this yourself».

**Reopens if:** a workspace wants proactive rows attributed to a human
sponsor. That is a relay under R-49's terms, not a proactive row.

### D6. Note content is read-only to proactive. Bookkeeping is not.

The self-refine edge in §1 runs through `note_update`. The proactive
adaptor's declared tool set therefore holds `note_read` and `note_search` and
not `note_create` / `note_update`. Notes are written from human rows
(`note-candidates`) and from tag-path note tools where a human is present.

What proactive *does* write on a note is a wake: `record_wake` with
`strikes`, the curator's own mechanism. One wake per note per decision;
`MAX_DATELESS_STRIKES = 4` ends it; a human revising the note resets it
(existing semantics: «reset says the head had moved on»). This is how «I have
raised this three times and nobody replied» is a number on the note rather
than something the model has to notice in memory.

**Reopens if:** revisions gain a provenance field and the adaptor's inputs can
exclude its own revisions the way `since` excludes `is_nora`. Then content
writes could be admitted. Not cheaper than refusing.

### D7. Self-observation is a READ, not a store.

The operator: a proactive agent «應該某程度上要 observe 返自己». Checked: Nora
already observes herself in three places, none of which a Dreamer turn reads.

| what she keeps of herself | where | who reads it today |
|---|---|---|
| Distilled conclusions from her own replies | Honcho `(nora → nora)`, `observe_me=true` on her peer in every room session (verified live, §4) | tag path `<self_card>` (DEC-96) |
| Her own words verbatim | record rows, `is_nora=True` | `recent` (tag window); never `since` |
| What she decided, when, and how it went | reminder rows: `kind`, `payload`, `state`, `outcome`, `fired_at` | heartbeat, due rows only |

No third store. Two reads are added to the decide turn's `inputs()`: the
`<self_card>` block, rendered as DEC-96 frames it («a record she may
contradict, at transcript authority»); and a **decision log** — proactive rows
by `requester_person_id = nora` for this workspace over the last N days, with
outcomes. The reminder store has `pending_for`, `all_pending`, `count_pending`
and no history read; one is added.

**The discipline:** she may see herself; she may not cite herself as the
state of the world. The world's state comes from human rows, notes, and
Standing.

### D8. Per-room Standing is a primary input.

«Standing» here is design 26 — one paragraph per room, Nora's own position on
where that conversation stands, revised by a second model after each tag
turn, keyed `(platform, conversation_id)` — not `NoteStore.standing()`, which
is a query for not-retired notes. 38 and an earlier draft of this reasoning
conflated the two; 33 §8's «standing» means design 26.

Live on 2026-09-09: `[standing] enabled = true`, all six policy rooms have a
row (§4). The decide turn injects every room of the workspace's Standing body
*with its `updated_at`*, because Standing is written only after tag turns and
a quiet room's paragraph is days old.

### D9. Inputs: inject the index, call for depth.

The same shape as `<notes>` + `note_read`. Design 03 §3 names the failure
this avoids: «everything it knows arrives as a string rendered before the loop
started, and it can never ask for more, ask differently, or ask again».

| injected by `inputs()` | called by the model |
|---|---|
| open notes of the workspace (title, anchor, `revisit_at`, strikes) | `note_read` |
| Standing per room, with age (D8) | `load_transcript` for one room |
| decision log (D7) | `search_memory_workspace` |
| `<self_card>` (D7) | `about`, `ask_memory` for a person |
| `now` | `where_is`, `saved_places`, `route`, `web_search`, `web_fetch` |

### D10. Trigger: a daily slot on the Dreamer's existing clock.

`dream_loop` is the Dreamer's one trigger; `AdaptorSchedule` is its per-adaptor
setting, introduced 2026-09-03 (commit dad9927c, design 32 §3.1, 33 §5 N1a)
and used by both live adaptors. A watermark-free adaptor fires on its slot
alone — «which is what lets a time-driven cron fire in a silent room». The
proactive adaptor adds a third entry in `composition.py`:
`min_interval_seconds=86400` and a `phase_offset_seconds` that lands the slot
in the Hong Kong morning. The curator's entry is the template.

### D11. Screening cancels a row whose note has moved.

When the heartbeat claims a `kind="proactive"` row, it reads the note named in
the payload. Retired, or `head_revision_id` past the one the row was written
against → the row resolves `cancelled` and no model is called. A human settling
the thing at 11:00 means Nora does not raise it at 14:00. Code-level, before
the model, in R-5's shape.

### D12. Doctrine: the act turn may stay silent.

38 §2.3 found the gap: the fire audience (`applies_to: heartbeat`) has no
may-speak clause because a reminder turn is owed to whoever asked. A
`proactive` occasion is owed to nobody. The act turn must be able to end
without speaking, the way participation's does. Mechanism left to
implementation: participation's silent path through `FirePath.speak` is the
precedent, cited for its structure only (38 §0's warning stands).

The decide turn's instructions are an objective in the Dreamer corpus, not
doctrine; it says what earns a row and what does not, and it is the text §5
tunes.

### D13. The two deleted tiers are not inputs.

The knowledge (fact) tier and the room-language tier were deleted 2026-09-07
(issue #115; `nora/domain/facts.py` keeps two DDL ceilings). 33 §8, issue #103
and node 38 name them as settled state; those references are historical.

## 3. What the loop reads and writes, in one place

**Reads** (decide turn): open notes; Standing per room; decision log;
`<self_card>`; Honcho via `about` / `ask_memory` / `search_memory_workspace`;
presence via `where_is` and friends through the home room; the web.

**Writes** (decide turn): reminder rows, `kind="proactive"`, owner Nora;
`record_wake` on each note it decided about. Nothing else.

**Reads** (act turn): everything a fire turn reads today, plus the payload.

**Writes** (act turn): a message in the home room, through the deliverer
chain; the row's `outcome`. It cannot write a reminder.

## 4. Live state, verified 2026-09-09 on `norty`

Read back over `ssh norty` against the running deployment (HEAD `24c246f4`).

```
[dreamer]   enabled=true  model=nora-dreamer  reasoning_effort=none
[notes]     enabled=true  curator_enabled=false  candidates_enabled=true
[standing]  enabled=true  max_chars=1200  event_enabled=true
[memory]    workspace_id=nora  self_card_conclusions=8
[presence]  enabled=true
```

**Standing rows**, one per policy room (folds / body chars / last update HKT):
Friends signal 5 / 203 / 09-09 07:58; Zone 42 discord 2 / 133 / 09-07 21:59;
Ridgeline discord 1 / 228 / 09-06 23:01; Ridgeline signal 2 / 140 / 09-06 19:16;
WhatsApp Friends 2 / 65 / 09-05 04:18; Wedding signal 4 / 179 / 09-04 17:23.

**Honcho observe configuration**, identical in all six room sessions
(`GET /v3/workspaces/nora/sessions/{id}/peers/{peer}/config`):
`nora` → `observe_me=true, observe_others=true`; every human →
`observe_me=true, observe_others=false`. So Honcho derives Nora's model of
each person and Nora's model of herself, and no human's model of Nora — DEC-66
as designed. The operator's recollection that «observe me» was off refers to
the human side, which is off on purpose.

**The one live note** (from 38 §2.7): «AI Hackathon dinner plan», anchor
2026-09-12, `revisit_at` 2026-09-09 08:00 HKT — now past; the curator is off
and nothing else reads `revisit_at`, so nothing happened. It is the first
`remind` case of the decide suite.

## 5. Measurement

The operator's rule: what earns an unprompted message is a measurement
question. Two suites, both driving the real runtime over in-memory stores
with only the model live, both grading structurally on the state left
behind, both treating `unavailable` as ERROR (design 37 §7's shape).

**Decide suite.** Input: a workspace's open notes, Standing rows, decision
log, memory snippets, `now`. `expect`: `none`, or a set of rows each with a
note id and a `due_at` window. PASS = exactly those rows, in those windows,
owner Nora. This suite measures *when* — the thing that makes it proactive.

**Act suite.** Input: one proactive row plus the room's state at fire time.
`expect`: `speak` with `must_cite`, or `silent` (note moved, or already said).
Graded on `Trajectory.sent` — participation's shape (design 35), borrowed for
its structure.

**Numbers the corpus yields:** false-positive rate on `none`/`silent`
(primary — an ambient agent's costly failure is being annoying, not being
late); recall on `remind`/`speak`; cost per fire from `nora_dream_run_seconds`
and `nora_dream_stop_reason_total`. The corpus must be majority `none`/
`silent`: most days there is nothing to say, and a corpus of only
should-speak cases breeds a Nora who speaks daily.

The objective text (D12) is what gets tuned against these numbers. The
threshold is the operator's; it is a number.

## 6. Build map

| # | change | file | size |
|---|---|---|---|
| 1 | `remind` past the ceiling for this adaptor; home-room `Room`; `WORKSPACE_SAFE_TOOLS` exemption | `nora/agent/dreamer.py` | three arguments and one allowance |
| 2 | `home_room` per workspace | `policy.toml`, `nora/config.py`, `policy.example.toml` | one field |
| 3 | the adaptor: `inputs()` (D9), envelope → `record_wake` sink | new module under `nora/language/` | the main piece |
| 4 | schedule entry | `nora/app/composition.py` | one entry |
| 5 | reminder history read | `nora/domain/reminders.py`, both stores | one method |
| 6 | `proactive` occasion factory + `event_registry` entry + screening (D11) | `nora/app/fire.py`, `nora/app/heartbeat.py`, composition | small |
| 7 | act-turn may stay silent (D12) | doctrine / fire path | small, precedent exists |
| 8 | decide objective | Dreamer corpus | one file |
| 9 | two eval suites (§5) | `evals/` | the other main piece |
| 10 | DEC-133 (1) deprecated in half; new DEC for D3–D5; 33 §8 banner; #103 updated | `design/09-decision-index.md`, 33, GitHub | governance |

Items 3 and 9 are the work. Everything else is a line or a paragraph. R-1b
and its test do not change.

## 7. Open

- The phase offset's time of day. Morning HK is the intent; the number is
  chosen when item 4 is written, with the curator's HK-midnight reasoning as
  the template.
- Whether Standing writes after a fire turn (it reads there, S5). If not, a
  room that only ever hears Nora's proactive messages has a Standing that
  never moves. Read `nora/app/fire.py` before assuming.
- Naming. «Proactive adaptor» is still provisional; nothing here depends on
  it.
- One stale Honcho session (`nora-conv-100000000000000203_100000000000000206`,
  a Ridgeline Discord channel not in `policy.toml`). Harmless; noted.

## Status / Checkpoint

Design only. No code. Supersedes 38; revises 33 §8; deprecates DEC-133 (1) in
half. The build is issue #103, which should be updated to point here and to
drop its knowledge-tier language. The note tier's own follow-ups stay in
#118.
