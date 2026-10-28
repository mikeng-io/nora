---
type: proposal
title: "The Proactive Adaptor Is Nora's Other Half — One OODA Loop, Two Entry Points"
description: "Decides what node 38 left open. The proactive adaptor is a Dreamer adaptor, unit-keyed through a home room, with the whole tool registry to READ and exactly one act verb — her own `remind`, stamped by provenance to write a proactive reminder row; its turn decides WHEN and the fire path speaks THEN. Passive (tagged) and active (clocked) Nora run the same observe–orient–decide–act loop, whose one structural brake is on the note. Every decision carries where it was checked and what would reopen it. Revised once against two reviews."
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
version: 3
---

# The Proactive Adaptor Is Nora's Other Half

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 0. What this node decides, and what it supersedes

Node 38 was a fact sheet and decided nothing. This node decides. Every
decision below was made by the operator on 2026-09-09, in conversation, and is
recorded with the evidence it rests on and the reading that would reopen it.
Version 2 folds in two reviews of version 1 (a claim-by-claim read-back
against the tree and an adversarial design review); where a review changed a
mechanism, the decision says so.

**It supersedes 38 whole.** 38 stays as the record of what was true before the
decisions; its seven open questions are answered here, and one of them
(«what R-31 returns for a turn with no asker») was the wrong question —
`Whereis.locate` never reads the asker (D2). One clause of nuance: `Room.
scope()`'s degraded branch does narrow to the tagger when the roster cannot
be read, so a decide turn with no tagger answers `unavailable` there, which is
the honest answer.

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

> **REVISED 2026-09-11 by `design/ambient-loop/00` (DEC-176).** This section
> wrote the loop from the proactive adaptor's seat: tag as the "passive half"
> entering at Act, proactive entering at Decide. The loop is the system's
> second backbone; tag is the *other* backbone and **a writer of the loop's
> held state** (its debrief writes Standing and proposes notes); and Decide has
> **two** deciders — participation and proactive — which this section never
> placed. The stage table and D1–D13 stand as an account of the proactive
> decider. The brake table stands except one row, narrowed by DEC-179:
> «Nora's message → note | curator filter» — her own sent row becomes
> admissible as a note's closing *citation* and nothing else; the sentence
> below, «Nothing Nora says can create, revise or close a note», therefore
> reads *create or revise*, and *close* only by citing her own delivered
> reply, never by grounding a discharge on it.

Both halves run the same loop. It is an OODA loop — observe, orient, decide,
act — and it is closed *through a human*.

| stage | what it is in Nora | where |
|---|---|---|
| **Observe** | Human rows land in the record; Honcho derives her model of each person | `RecordStore`, `is_nora == 0` in `since`; Honcho `(nora → person)` |
| **Orient** | Conversation becomes tracked uncertainty; each room gets a one-paragraph position | note tier (`note-candidates`, curator); Standing (design 26) |
| **Decide** | Given the whole unit, what is worth doing and *when* | the proactive adaptor's turn (this node) |
| **Act** | Say it, in a room, at the decided time | the fire path, via a `kind="proactive"` reminder row |

The passive half enters this loop at *Act*: somebody tags her, she reads the
same observe/orient state, answers in that room. The active half enters at
*Decide*: the clock fires, she reads the same state across the unit, and
either schedules an act or does nothing. Same tools, same doctrine substrate,
same deliverer chain. What differs is the trigger, the scope, and the fact
that the active half is owed nothing and may stay silent.

**Where the loop is closed structurally, and where it is not.** The
operator's worry, verbatim: a reminder sends a message, the message reaches
memory, memory becomes a note, the note is refined, a reminder is set again,
«一路落去». The review of version 1 found that «Nora's own words are not
observation» is true of exactly one input and false of three. Stated
precisely:

*The note is the structural brake.* `since` excludes `is_nora` rows in its
SQL predicate (`nora/infra/sql/audit.py`), the curator keeps them out of the
evidence it derives every signal from (`note_curator.py::_gather_evidence`),
and design 27 §11.3 states the rule — «her saying *I'll book Yardbird* is an
intention she stated, not the group settling anything». Nothing Nora says can
create or revise a note. A human replying to her message is a human row; that
is how her proposal reaches the note.

*NARROWED 2026-09-11 on evidence (issue #199), and the word that changed is
CLOSE.* The brake read «create, revise or close». Closure was carrying the
other two for free, and it cost a note: a closing condition can name something
only SHE can do — «Nora successfully sends a route» — so with her rows
filtered out, the one pass whose job is to retire that note could never see
the thing that satisfied it. It stayed open with its condition already met and
the proactive pass raised it at the operator the next morning, in the wrong
room. The curator now also renders her DELIVERED rows, under a marker saying
what they are, and a `closed` verdict may cite one. Create is untouched (the
candidates path still reads `since`); revise is untouched (the curator has no
revision verb); `expired`/`moot` are untouched (`anchor_covered` is computed
from the human evidence alone and `_covers` fails closed on an empty one); the
quiet-room reading is untouched (`EvidenceState` is derived from the human
rows too); and DISCHARGE IS REFUSED on a self-citation, which is
D7 in this same node. One evidence tuple serves both slots, so a row
admitted to the citation allow-list necessarily reaches `_run_discharge`'s
single `cited_row` resolution — the one the commitment route and the
person-claim route share — and without a guard THERE a legal self-citation
would make her the requester of a Gathering event and the subject of a
Honcho person-claim. The guard sits past that resolution, covers both
routes at once, and carries its own `self_cited` outcome rather than
`refused` (which already means «the sender maps to nobody» — a roster gap
an operator can close, where this boundary never closes). The retirement
still lands. Keeping the evidence filter strict instead was measured and
does not work: on the incident's own note her row was the ONLY row since
the note was created, so the allow-list is empty and the `closed` verdict
is dropped as `no_evidence` before any citation is read. The line is `delivery_status == SENT`: the sender writes that back
after the platform accepted the message, so what is admitted is a fact about
the record, not a claim she made. *The reading that would reopen it:* a
retirement grounded on a row of hers whose closing condition was about the
GROUP rather than about her — which is what `nora_note_retirements_total` and
the `why` on each retirement are there to make findable.

*Three inputs do carry her own words into the next decide turn, and the doc
must not pretend otherwise:* Standing is folded from the transcript
*including* her rows (`nora/engagement/stages/debrief.py`,
`since_including_nora`), so a proactive message enters the room's paragraph
at the next tag; `<self_card>` is Honcho's `(nora → nora)` distillation of
her own replies; and `load_transcript` reads `recent`, which has no
`is_nora` predicate. On these three the brake is not structural. It is the
framing (D7: a record she may contradict, never evidence), the strikes cap
(D6), and the measurement (§5). D9 drops `load_transcript` from the decide
turn's tool set for this reason — Standing already carries the room, and the
verbatim window is where the self-echo would be loudest.

**The brakes on each edge the operator named:**

| edge | brake | where | exists |
|---|---|---|---|
| Nora's message → note (create/revise) | excluded by predicate | `since` `is_nora == 0`; the curator has no create or revise verb | yes |
| Nora's message → note (close) | only a DELIVERED row, only as a `closed` citation, never as the group speaking | `_own_sends`, issue #199 | **narrowed** |
| act turn → another reminder | `remind` refuses inside a fire turn | `tools.py:6205-6212` «a reminder that can set a reminder is a loop with no bound» | yes |
| decide turn → unbounded rows | per-requester cap, requester is the `ai_peer` id | `max_pending_per_requester = 20` (D5) | yes |
| decide turn → a second row on the same note | at most one open (pending or firing) proactive row per note; a row on a note merged into it counts, a row whose note the tag path moved does not and is cancelled | `remind` refuses `duplicate_note`; `uq_reminders_proactive_open_note` (issue #158, added 2026-09-25) | yes |
| decide turn → its own note | no note-content writer declared; `record_wake` cannot reset its own strike | D6; `sql/notes.py:526-540` | yes |
| act turn on a note that moved | screening cancels the row on a tag-path revision | D11 | **new** |
| same note, nobody replies | strikes | `record_wake`, `MAX_DATELESS_STRIKES = 4` | yes |
| her own words via Standing / self card | framing + measurement, not a predicate | D7, §5 | partial |

## 2. Decisions

Each decision: what, the evidence, the reading that would reopen it.

### D1. It is a Dreamer adaptor. Not a new runtime, not a fire-path occasion.

38 listed three shapes. The operator's question settled it: *why build a
separate thing when the runtime is built?* Design 03 §2's own finding is that
`DreamerRuntime` «is a correct implementation of a research agent, wired to
adaptors that give it nothing to research with». The proactive adaptor is
the first adaptor that gives it something.

Two properties of the runtime fit the active half exactly and the tag/fire
turn does not: the stopping condition is «the model declining to call
anything, under `tool_choice="auto"`, inside a wall clock» (`dreamer.py:
35-36`) — which *is* «may stay silent» — and `AdaptorBudget` is per adaptor,
so a once-a-day pass can afford more turns and tokens than a tag turn's
fixed budgets. Note the roster read D2 adds is one `RosterSource.roster` call
per run, cached per `Room` instance, and it is *not* inside
`AdaptorBudget.timeout_seconds`; the wall clock wraps the whole loop, so it
is bounded there.

**Reopens if:** the decide turn needs a terminal verb after all (it would then
be a fire-path occasion, and D3's «no `send_reply`» is what keeps it from
needing one).

### D2. Unit-keyed, with a home room per unit.

`key_kind = "unit"`, like `note-curation`. The operator's word is
*circle*; the codebase's word is *unit*, defined in `policy.example.toml`
as «the SOCIAL UNIT a room belongs to — the same people reachable in more than
one place» (DEC-105, R-49). Notes are already unit-scoped
(`Note` docstring: «one tracked uncertainty, scoped to a unit»;
`established_in` is «a routing key, never a filter»).

**The home room.** A unit has no conversation, and every room-scoped
tool — `where_is`, `about`, `ask_memory`, `group_members` — resolves through
a `Room` that needs `platform` and `conversation_id`. `policy.toml` therefore
gains one room per unit marked as its home. The adaptor's `Room` is
built from that room with a real roster source, and the act verb (D3) writes
into it.

**What «through the home room» means, said plainly.** Roster-gated reads
see the home room's roster; notes and Standing come from every room of the
unit; `search_memory_workspace` is the one cross-room memory read. So
«every read tool is accessible» is accessible *through one of the
unit's two or three rooms*. There is no disclosure consequence: the act
turn speaks into that same room, and R-31 is evaluated against it.

**This answers 38's «R-31 with no asker» by dissolving it.** `Whereis.locate`
reads `room.scope()`, the guard, `view.unnamed` and `location_trusted`
(`whereis.py:380, 412-440`) — never the asker. `UNIT_SAFE_TOOLS`'
refusal of room-scoped tools for unit-keyed adaptors was written because
«binding one to an arbitrary room of that unit would be a disclosure
decision (R-31) nobody has made» (`dreamer.py:274-275`). The home room is
that decision, made by the operator per unit, in the file where room
trust already lives.

**Two arguments the Dreamer build does not pass today, without which
`where_is` refuses in every configuration** (review finding): `self_ids`,
so Nora's own account in the roster is skipped rather than counted as
`unnamed=1`; and `location_trusted` from the home room's `location` flag,
which on a tag is threaded `policy.toml` → `Conversation.location` →
`ToolRegistry.build(location_trusted=…)` → `Whereis` (DEC-74) and which
`RegistryToolSource.build` currently leaves at its `False` default. Both are
in the build map. Degraded roster (read fails): `scope()` narrows to the
tagger, there is none, `locate` answers `unavailable` — honest, not a hole.

Live on 2026-09-09 (read back on `norty`; `policy.toml` is not in the tree):
three units — `ridgeline` (2 rooms), `friends` (3 rooms), `wedding` (1
room).

**Reopens if:** a unit needs two home rooms. The old platform-count premise
is superseded by room-topology migration step 3: unique labels permit several
rooms per platform; the one-home-room rule remains independent. **Superseded in part by 40 (issue #248, 2026-09-15):**
a proactive row LINKED to a note fires into the note's `established_in` room,
not the home room — the home room stays where she stands, and the write verb
routes by the note's own «routing key, never a filter» (`notes.py:277`).
See `40-proactive-origin-delivery.md`.

### D3. Every read tool is accessible; the one act verb is `remind`, stamped by provenance.

The operator's position: «所有嘅 capability 都要 accessible». For reads this
is literal — the registry's whole read set, room-scoped included, through the
home room. For acts it is exactly one verb, and it is the `remind` Nora
already has — not a new tool. Version 1 assumed `remind` as-is would do;
version 2 replaced it with a new verb; this version keeps the verb and fixes
what v1 missed, because the codebase already has the pattern for it.

**What `remind` as-is cannot do.** Its model-facing signature is
`remind(when, text, repeat, every_n, times, until)` (`tools.py:6138-6142`).
Three fields the row needs are not the model's to set: `kind` and `payload`
take their defaults `"reminder"` / `None` (`reminders.py:348-349`), so the
row would fire as an ordinary «X asked you to remind…» with no note id for
D11 to screen; and the destination is `bound_conversation_id`, the
`conversation_id` argument of the build — which for a unit-keyed
adaptor is the *unit name* (`dreamer.py:588`), so swapping the `Room`
alone writes rows into a conversation that does not exist and every one dies
`failed_policy`. `gathering` shows the right shape: its rows get
`kind="gathering"` and a payload from *code* (`nora/app/gatherings.py:
378-379`), never from the model.

**The pattern that fixes it is already in `remind`.** `fire_turn` is «this
turn's PROVENANCE» (`tools.py:2365`), threaded from `ToolRegistry.build`, and
`remind` reads it to behave differently inside a fire turn (`:6205`). The
Dreamer build threads a second provenance, `dream_adaptor=<name>`, and under
it `remind`'s closure stamps in code: `kind="proactive"`, `requester_person_id
= <ai_peer>`, `platform` and `conversation_id` from the home room, and
`payload={"note_id", "revision_id", "intent"}` — with `note_id` from a new
optional parameter `note_id: str | None = None` that the tag path may also
use (a human's reminder about a note is a useful link, and it costs nothing
there). `text` is the intent. Before `create`, the model-authored text
passes `dream_screen.screen` — the runtime's docstring says «if the model
writes directly there is no sink left to screen at» (`dreamer.py:18-23`),
and this is the sink. The decision *is* the tool call (D4). Nothing new is
put in front of the model; one of her existing verbs learns where it is.

**Tools the decide turn holds, and the ones it does not:** `remind`;
`where_is`, `saved_places`, `route`, `group_members`; `about`, `ask_memory`,
`search_memory_workspace`; `note_read`, `note_search`; `web_search`,
`web_fetch`, `weather`, `now`. Not `send_reply` (the Dreamer does not speak),
not `send_to` (no tagger to attribute a relay to), not `note_create` /
`note_update` (D6), not `load_transcript` (§1).

**What this changes in DEC-133.** DEC-133 (1) said a background process «that
can speak to the group unprompted — or set a reminder, or write to the events
store — with nobody waiting on it is a categorically different thing». Half
stands: the Dreamer still cannot *speak*. `deliverer=None` does not change;
R-1b holds, and its test's own text already classifies a row-driven occasion
as «a fifth occasion on the second path, not a third path»
(`tests/architecture/test_only_two_paths_can_send.py:14-17`). The half that
is deprecated is «or set a reminder», for this adaptor, through a verb that
is screened. DEC-133 (2)'s «the ceiling is not the grant» is unchanged: the
ceiling gains one act verb for one adaptor. The `dreamer.py` module
docstring's «there is no write tool and there must not be one» is rewritten
to say what is now true.

**Mechanically**, the ceiling is enforced in three places, not one, and all
three need the allowance: `RegistryToolSource.build`'s `& self.read_only()`
(`dreamer.py:584`); `tool_subset_faults` axis 1, `declared - ceiling`
(`:397-406`), raised at startup by `verify_tool_subsets`; and the per-run
re-check in `DreamerRuntime.run` (`:722-723`). Axis 2
(`UNIT_SAFE_TOOLS`) is exempted for this adaptor. The build passes the
home room's `conversation_id` and `conversation_type` (not the key),
`self_ids`, `location_trusted`, and keeps `supplied_unit=key`. Four
tests pin what this changes and go red on purpose:
`tests/contract/test_dream_adaptor_contract.py::test_the_tool_subset_narrows_the_read_only_ceiling`,
`tests/test_composition.py::test_every_wired_adaptor_stays_inside_the_read_only_ceiling`,
`tests/unit/test_dreamer_runtime.py::test_the_tool_source_never_builds_a_tool_outside_the_ceiling`,
`::test_a_unit_keyed_adaptor_is_refused_a_room_scoped_tool`.
`test_remind_refuses_on_a_fire_turn` stays green.

**Reopens if:** measurement (§5) shows the decide turn needs to speak
*immediately* often enough that a 120-second lead is the wrong shape. Then
`send_reply` enters the Dreamer and R-1b gets a third caller — a separate
decision with its own DEC.

### D4. Decide and Act are two turns. The decide turn writes rows; the fire path speaks.

The operator's test for whether something is proactive: «明明應該晏晝先同佢講，
但係你朝早八點鐘 run 一次嘅話，呢個唔係 proactive，呢個純粹叫 scheduling.» A daily
slot with `send_reply` would let her choose *whether*; it would not let her
choose *when*. Choosing when is the capability.

So the **decide turn** (Dreamer, D1) reads the unit, uses tools, and its
only act is `remind(...)` under Dreamer provenance: zero or several rows. It never composes the
message.

The **act turn** is the fire path. `event_registry` (composition root) maps a
due row's `kind` to an occasion factory and holds `reminder` and `gathering`
today (`composition.py:1947-1950`); it gains `proactive` → a new factory in
`nora/app/fire.py`, built with `person_id=None` — the «nobody's behalf» shape
`participation_occasion` already uses (D5). Its header is not «X asked you to
remind about Y» but «at 08:00 you decided to raise this at 14:00 because …;
here is the note now». The fire turn builds the full registry, so `where_is`
and `route` are called *at 14:00*, which is when «where everyone is coming
from» is a question worth asking. With no tagger, `send_to` refuses in the
act turn (it attributes a relay to `room.tagger`), so the act turn speaks
into the home room and nowhere else — which is what §3 says.

**The envelope becomes bookkeeping.** 33 §8's «model proposes, code disposes»
does not describe this adaptor: the proposal *is* the `remind` call,
already landed when the envelope is parsed. The envelope carries the decision
record — which notes it considered, what it scheduled or declined, why — and
the sink writes `record_wake` (D6). A run whose `inputs()` is blank commits
nothing and records no wake (the curator's precedent). 38's open question 7
(«research in the Dreamer turn or the fire turn») is answered: *both*, split
by what each needs to know.

**Reopens if:** the act suite (§5) shows the fire turn routinely contradicts
the decide turn's intent. Then the payload carries more, or the decide turn
composes a draft.

### D5. Nora owns her own rows — as the reserved `ai_peer` id, not as a person.

The operator, on why she is not in `identity.toml`: «identity.toml 嘅存在意義，
就係俾 Nora 去 identify 返有乜嘢 identity。佢自身已經有一個 identity.» She has
one: `[honcho].ai_peer` and `[agent].name`, which DEC-66 (5) calls «the
agent's machine identity», reserved by the composition root so no human may
hold it. `identity.toml` is the book of the people she identifies, and she is
not one of them.

Version 1 said «her `PersonRef` from `identity.toml`». That is a startup
refusal: the composition root reserves the `ai_peer` id and raises
`StartupError` if `identity.toml` gives it to anyone
(`composition.py:578-590`). And even past that, the fire path resolves the
occasion's `person_id` through `persons.by_person`, finds nothing, and
returns `FAILED_POLICY` (`fire.py:684-687, 1041-1058`) — every row would die
silently. The heartbeat's own screen, `is_allowed(person_id=row.
requester_person_id)` (`heartbeat.py:259-263`), admits only listed persons.

**The principle stands; the mechanism is:** `requester_person_id` is the
`ai_peer` string (`"nora"`), stamped by `remind` under Dreamer provenance (D3), so the
per-requester cap of 20 becomes the global ceiling on unfired proactive rows.
The `proactive` occasion is built with `person_id=None`. The fire-time screen
for `kind="proactive"` checks a per-room policy flag `proactive = true`
(the `participation = true` precedent, `policy.example.toml:77-84`) instead
of persons membership; a home room without the flag refuses the row. The
decide-turn `Room` has no tagger — `remind` under Dreamer provenance does not read one, and
nothing else in the decide turn does.

Not the note's anchor person: «Miles: dinner at 8» and «dinner at 8» are
different claims (R-49), and a row under Miles's name that Miles never asked for
is the first.

**Reopens if:** `identity.toml` gains a non-human entry kind. Then Nora could
be a person and the flag could go.

### D6. Note content is read-only to proactive. Bookkeeping is not.

> **AMENDED 2026-09-09 (issue #136), with D10's revision.** «One wake per
> note per decision» becomes one wake per note per Hong Kong day, recorded
> in `dream_schedule_state` (`proactive:wake`), because a change-driven
> pass may decide the same note several times a day and each is one
> decision the room has not yet had a chance to answer; and, as the audit
> of the build recorded, a `decline` charges no strike — the number is
> «raised and nobody replied».

The self-refine edge in §1 runs through `note_update`. The proactive
adaptor's declared tool set therefore holds `note_read` and `note_search` and
not `note_create` / `note_update`. Notes are written from human rows
(`note-candidates`) and from tag-path note tools where a human is present.

What proactive *does* write on a note is a wake: the store port's
`record_wake` with `strikes`, the curator's own mechanism. One wake per note
per decision; `MAX_DATELESS_STRIKES = 4` ends it; the reset branch fires only
on a *tag-path* revision (`sql/notes.py:526-540`, «reset says the head had
moved on the tag path»), so a proactive wake cannot reset its own strike.
This is how «I have raised this three times and nobody replied» is a number
on the note rather than something the model has to notice in memory.

**Reopens if:** revisions gain a provenance field and the adaptor's inputs can
exclude its own revisions the way `since` excludes `is_nora`. Then content
writes could be admitted. Not cheaper than refusing.

### D7. Self-observation is a READ, not a store.

The operator: a proactive agent «應該某程度上要 observe 返自己». Checked: Nora
already observes herself in three places, none of which a Dreamer turn reads.

| what she keeps of herself | where | who reads it today |
|---|---|---|
| Distilled conclusions from her own replies | Honcho `(nora → nora)`, `observe_me=true` on her peer in every room session (verified live, §4) | tag path `<self_card>` (DEC-96; `nora/engagement/context.py`, not `fire.py`) |
| Her own words verbatim | record rows, `is_nora=True` | `recent` (tag window); never `since` |
| What she decided, when, and how it went | reminder rows: `kind`, `payload`, `state`, `outcome`, `fired_at` | heartbeat, due rows only |

No third store. Two reads are added to the decide turn's `inputs()`: the
`<self_card>` block, rendered as DEC-96 frames it («a record she may
contradict, at transcript authority»); and a **decision log** — rows with
`requester_person_id = "nora"` over the last N days, with outcomes. `Reminder`
has no unit column (`reminders.py:325-349`), so the read joins through
policy's room → unit map. The store port has `pending_for`,
`all_pending`, `count_pending` and no history read (`reminders.py:593-730`);
one is added.

**The decision log needs D12.** Today a fire turn that ends without sending
is `FAILED` by `HeartbeatLoop._state_for` (`DONE if outcome == SENT else
FAILED`, `heartbeat.py:371-380`). Without D12's third mapping the log reads
every restraint as a failure.

**The discipline:** she may see herself; she may not cite herself as the
state of the world. The world's state comes from human rows, notes, and
Standing — and §1 says honestly that Standing and the self card carry her
words too.

### D8. Per-room Standing is a primary input.

«Standing» here is design 26 — one paragraph per room, Nora's own position on
where that conversation stands, revised by a second model after each tag
turn, keyed `(platform, conversation_id)` (`schema.py:2166-2199`,
`standing.py:39-41`) — not `NoteStore.standing()`, which is a query for
not-retired notes (`sql/notes.py:306`). 38 and version 1's reasoning
conflated the two; 33 §8's «standing» means design 26.

Live on 2026-09-09: `[standing] enabled = true`, all six policy rooms have a
row (§4). The decide turn injects every room of the unit's Standing body
*with its `updated_at`*, because Standing is written only after tag turns —
the fire path reads it and never writes it (`fire.py:519-524`, «S1 (fire
turns debrief too) is blocked») — so a room that only ever hears proactive
messages has a paragraph that never moves, and a quiet room's is days old.

### D9. Inputs: inject the index, call for depth.

The same shape as `<notes>` + `note_read`. Design 03 §3 names the failure
this avoids: «everything it knows arrives as a string rendered before the loop
started, and it can never ask for more, ask differently, or ask again».

| injected by `inputs()` | called by the model |
|---|---|
| open notes of the unit (title, anchor, `revisit_at`, strikes) | `note_read`, `note_search` |
| Standing per room, with age (D8) | `search_memory_workspace` (drops her own peer, `tools.py:7157-7163`) |
| decision log (D7) | `about`, `ask_memory` for a person |
| `<self_card>` (D7) | `where_is`, `saved_places`, `route`, `group_members` |
| `now` | `web_search`, `web_fetch`, `weather` |

`load_transcript` is deliberately absent (§1).

### D10. Trigger: a daily slot on the Dreamer's existing clock.

> **REVISED 2026-09-09 by the operator (issue #136), on the evidence of the
> first live day.** The slot is now HOURLY and the adaptor's own
> deterministic check decides whether a tick renders: the daily floor
> (07:00 HK, no rendered pass yet that day), or a change since the last
> rendered pass — a note created, revised on the tag path, or newly past
> its `revisit_at`, or a room's Standing rewritten — once every room of the
> unit has been quiet for `[dreamer] quiet_seconds`. A tick that does
> not render is one store read and no model call, which is the same shape
> the note tier's watermark and the curator's blank render already have.
> «Last rendered pass» is durable in `dream_schedule_state`
> (`proactive:pass`). The reason the daily slot was wrong: a note
> `note-candidates` creates at 10:00 about tonight was first seen the next
> morning, and a unit where nothing moved still paid a full tool loop
> daily. The message-time distribution is not estimated — the note tier's
> watermarks and Standing's `updated_at` already compress conversation
> rhythm into state. **Reopens if** `nora_proactive_pass_total{reason=
> "changed"}` runs far outnumber `remind` decisions over a week: the
> detector fires on noise, and a slot is the honest cadence.
>
> **REVISED AGAIN 2026-09-10 by the operator, on the second live day:** the
> quiet condition reads the HOME ROOM, not every room of the unit. A
> proactive row fires into the home room and `policy.py` refuses it anywhere
> else, so the home room is the only room the pass can interrupt; every
> other room was a proxy for that, and on `friends` the proxy was the whole
> gate. Zone 42 is a games room on Discord and the busiest of the three; the
> note that moved (09:40Z) was raised in it, and four consecutive hourly
> ticks held `held_quiet` on Zone 42's traffic rather than speak into the
> Signal room where the row would have landed. Measured over the 14 days to
> 2026-09-10, 337 hourly ticks on that unit: quiet on every room 257
> (76%), quiet on the home room alone 313 (93%); longest unbroken
> unit-busy run 6 hours — long enough to swallow a note anchored on the
> day it was raised. **Reopens if** a `changed` pass is answered as a
> barge-in: what this drops is «a sibling room is still settling the thing
> she is about to raise», and the fix for that is a hold that follows the
> room that CHANGED, which `_changed_since` does not report today.
> **How coupled that is, stated rather than left to be discovered:**
> `_changed_since` fires on a Standing rewrite of ANY room, and Standing is
> written only after a tag turn — so a sibling room having just been active
> is the ordinary cause of a `changed` pass, not an edge of it. The dropped
> protection is therefore load-bearing more often than «sibling» suggests,
> and the reopen condition above should be read as likely rather than
> remote.

`dream_loop` (`runtime.py:758`) is the Dreamer's one trigger and the sole
caller of `DreamerRuntime.run`; `AdaptorSchedule(min_interval_seconds,
batch_limit, max_items, phase_offset_seconds, samples)` is its per-adaptor
setting, introduced 2026-09-03 (commit dad9927c, design 32 §3.1, 33 §5 N1a)
and used by both live adaptors — `note-curation` at 86400 with
`phase_offset_seconds = 16 * 3600` (HK midnight), `note-candidates` at 3600
(`composition.py:2205-2219, 2290-2291`). A watermark-free adaptor fires on
its slot alone — «which is what lets a time-driven cron fire in a silent
room». The proactive adaptor adds a third entry: 86400 and an offset that
lands the slot in the Hong Kong morning; the curator's comment is the
template for choosing it.

### D11. Screening cancels a row whose note moved on the tag path.

When the heartbeat claims a `kind="proactive"` row, it reads the note named in
the payload. Retired → `cancelled`. A *tag-path* revision since the row's
`revision_id` → `cancelled`. Not «any head move»: the debrief's note sink
appends revisions with a run-id `established_by` on ordinary tag turns
(`notes.py:610-616`), and a row cancelled by a paraphrase would cancel on
every tag. The domain already has the predicate —
`tag_path_progressed_since_wake` / `is_tag_path_revision`
(`notes.py:594-640`). No model is called for a cancelled row. A human
settling the thing at 11:00 means Nora does not raise it at 14:00.

### D12. Doctrine and state: the act turn may stay silent, and silence is not failure.

38 §2.3 found the gap: the fire audience (`applies_to: heartbeat`) has no
may-speak clause because a reminder turn is owed to whoever asked. A
`proactive` occasion is owed to nobody. Two changes, and version 1's «small,
precedent exists» undersold the second:

- *Doctrine:* the act turn must be able to end without speaking.
  Participation's silent path through `FirePath.speak` (`SILENT`,
  `fire.py:155`) is the precedent, cited for its structure only (38 §0's
  warning stands). Today `SILENT` is reachable only for `occasion.kind ==
  "participation"` (`fire.py:1037-1039`); it must be reachable for
  `proactive`.
- *State:* `HeartbeatLoop._state_for` maps anything but `SENT` to `FAILED`.
  A third mapping — `silent` → `DONE` with `outcome="silent"` — so a
  restraint is a restraint in the row, the metric and D7's log.

The decide turn's instructions are an objective in the Dreamer corpus, not
doctrine; it says what earns a row and what does not, and it is the text §5
tunes.

### D13. The two deleted tiers are not inputs.

The knowledge (fact) tier and the room-language tier were deleted 2026-09-07
(issue #115, commits `4d3db830`, `0342199c`; `nora/domain/facts.py` keeps two
DDL ceilings). 33 §8, issue #103 and node 38 name them as settled state; those
references are historical.

## 3. What the loop reads and writes, in one place

**Reads** (decide turn): open notes; Standing per room; decision log;
`<self_card>`; Honcho via `about` / `ask_memory` / `search_memory_workspace`;
presence via `where_is` and friends through the home room; the web.

**Writes** (decide turn): reminder rows, `kind="proactive"`, owner `"nora"`,
via `remind`; `record_wake` on each note it decided about. Nothing else.

**Reads** (act turn): everything a fire turn reads today, plus the payload.

**Writes** (act turn): a message in the home room, through the deliverer
chain; the row's `outcome`. It cannot write a reminder, and with no tagger it
cannot relay.

## 4. Live state, verified 2026-09-09 on `norty`

Read back over `ssh norty` against the running deployment (HEAD `24c246f4`).
`policy.toml`, `identity.toml` and `config.toml` are not in the tree; these
values are what the host held that day.

```
[dreamer]   enabled=true  model=nora-dreamer  reasoning_effort=none
[notes]     enabled=true  curator_enabled=false  candidates_enabled=true
[standing]  enabled=true  max_chars=1200  event_enabled=true
[honcho]    workspace_id=nora  self_card_conclusions=8
[presence]  enabled=true
```

**Standing rows**, one per policy room (folds / body chars / last update HKT):
Friends signal 5 / 203 / 09-09 07:58; Zone 42 discord 2 / 133 / 09-07 21:59;
Ridgeline discord 1 / 228 / 09-06 23:01; Ridgeline signal 2 / 140 / 09-06 19:16;
WhatsApp Friends 2 / 65 / 09-05 04:18; Wedding signal 4 / 179 / 09-04 17:23.

**Honcho observe configuration**, identical in all six room sessions
(`GET /v3/workspaces/nora/sessions/{id}/peers/{peer}/config`):
`nora` → `observe_me=true, observe_others=true`; every human →
`observe_me=true, observe_others=false`. This is what
`nora/infra/honcho/client.py:411, 426, 477` sets; DEC-66 records Nora's
`observe_others` and the humans' `observe_me`, and the two `False`s are the
client's. So Honcho derives Nora's model of each person and Nora's model of
herself, and no human's model of Nora. The operator's recollection that
«observe me» was off refers to the human side, which is off on purpose.

**The one live note** (from 38 §2.7): «AI Hackathon dinner plan», anchor
2026-09-12, `revisit_at` 2026-09-09 08:00 HKT — now past; the curator is off
and nothing else reads `revisit_at`, so nothing happened. It is the first
`remind` case of the decide suite.

## 5. Measurement

The operator's rule: what earns an unprompted message is a measurement
question. Two suites, both driving the real runtime over in-memory stores
with only the model live, both grading structurally on the state left
behind, both treating `unavailable` as ERROR — the rule `evals/
note_candidates.py:20` states and `evals/run.py` enforces.

**Decide suite.** Input: a unit's open notes, Standing rows, decision
log, memory snippets, `now`. `expect`: `none`, or a set of rows each with a
note id and a `due_at` window. PASS = exactly those rows, in those windows,
owner `"nora"`, read back from the reminder store. This suite measures
*when* — the thing that makes it proactive.

**Act suite.** Input: one proactive row plus the room's state at fire time.
`expect`: `speak` with `must_cite`, or `silent` (note moved, or already said).
Graded on `Trajectory.sent` — participation's shape (design 35), borrowed
for its structure.

**What the harness lacks today** (review finding), so item 9 of the build map
is not small: the eval Dreamer tool source is `_NoDreamTools`
(`eval_support.py:584-596`); `build_eval_tool_registry` wires no reminder
store, so `remind` would answer `unavailable` and every case would be
ERROR; `InMemoryReminderStore` exists (`nora/testing/reminders.py`) and is
unwired; `FakeMemoryStore` serves `peer_card` only, so `about` /
`ask_memory` / `search_memory_workspace` have no canned backend; and
`ProductionSeed.standing` seeds one row onto one room, where the decide turn
needs N rooms. The suite needs an eval `RegistryToolSource` with the
in-memory reminder store, a canned memory backend, per-room Standing
seeding, and a grader over the reminder store.

**Numbers the corpus yields:** false-positive rate on `none`/`silent`
(primary — an ambient agent's costly failure is being annoying, not being
late); recall on `remind`/`speak`; cost per fire from
`nora_dream_run_seconds` and `nora_dream_stop_reason_total`. The corpus must
be majority `none`/`silent`: most days there is nothing to say, and a corpus
of only should-speak cases breeds a Nora who speaks daily. The labels are one
rater's threshold — the operator's — so the rate is relative to that
threshold, not absolute.

The objective text (D12) is what gets tuned against these numbers. The
threshold is the operator's; it is a number.

## 6. Build map

| # | change | file | size |
|---|---|---|---|
| 1 | ceiling allowance in three places; home-room `Room` with `self_ids`; `conversation_id`/`conversation_type` from the home room; `location_trusted` threaded; `UNIT_SAFE_TOOLS` exemption; module docstring rewritten | `nora/agent/dreamer.py` | a dozen lines, four tests rewritten |
| 2 | `home_room` and `proactive = true` per room | `policy.toml`, `nora/policy.py`, `nora/config.py`, `policy.example.toml` | two fields |
| 3 | `remind` reads a `dream_adaptor` provenance: stamps kind/payload/owner/destination in code, screens the text; optional `note_id` parameter | `nora/agent/tools.py`, `nora/agent/registry.py` | one closure branch, one parameter |
| 4 | the adaptor: `inputs()` (D9), envelope → `record_wake` sink | new module under `nora/language/` | the main piece |
| 5 | schedule entry | `nora/app/composition.py` | one entry |
| 6 | reminder history read, joined to unit via policy | `nora/domain/reminders.py`, both stores | one method |
| 7 | `proactive` occasion factory (`person_id=None`) + `event_registry` entry + screening on flag and tag-path revision (D11) | `nora/app/fire.py`, `nora/app/heartbeat.py`, composition | small |
| 8 | `SILENT` reachable for `proactive`; `_state_for` third mapping | `nora/app/fire.py`, `nora/app/heartbeat.py` | small |
| 9 | decide objective | Dreamer corpus | one file |
| 10 | two eval suites plus the harness pieces §5 lists | `evals/`, `nora/app/eval_support.py` | the other main piece |
| 11 | DEC-133 (1) deprecated in part, DEC-162 added (NOT ENFORCED until built), 33 §8 banner, #103 updated — **done 2026-09-09** | `design/09-decision-index.md`, 33, GitHub | governance |

Items 4 and 10 are the work. R-1b and its test do not change.

## 7. Open

- ~~The phase offset's time of day.~~ Chosen with item 5 (2026-09-09):
  07:00 Asia/Hong_Kong, `phase_offset_seconds = 23 * 3600` — deliberately
  not 08:00 HK, which is the field's default (00:00 UTC) and would be
  indistinguishable from an unset value. `nora/app/composition.py` carries
  the reasoning and the reading that would move it.
- Naming. «Proactive adaptor» is still provisional; nothing here depends on
  it. The code calls it `proactive` everywhere (`nora/language/proactive.py` (deleted 2026-10-03, DEC-277),
  the objective, the schedule and grant keys, the `dream_adaptor` provenance).
- One stale Honcho session (`nora-conv-100000000000000203_100000000000000206`,
  a Ridgeline Discord channel not in `policy.toml`). Harmless; noted.

## Status / Checkpoint

**Revised 2026-09-09, after the first live day (issue #136):** D10 is
change-driven with a daily floor and D6's wake is per note per day; the
banners on both decisions carry the evidence and the reversal condition.

**Built 2026-09-09**, in the order §6 numbers, on branch
`mikeng-io/feat-proactive-ooda-adaptor`; DEC-162's test column names the
tests, `scripts/mutations/proactive-adaptor.json` is the mutation record,
and `release-fragments/released/V5/proactive-adaptor.json` is the product
note. One deviation from §2, on evidence: D3's optional `remind` parameter
is `note` — a TITLE — rather than `note_id`, because `<notes>` renders no
ids and `note_read` resolves titles («no id ever enters the prompt»,
`nora/agent/tools.py`), and the Dreamer's screen drops any envelope item
carrying a uuid (the curator's handle scheme); the id and the head revision
are resolved in code into the payload exactly as D3 intends. A second,
found by the same day's independent audit and stated here rather than
left implicit: D6's «one wake per note per decision» is one wake per note
the pass decided to RAISE — a `decline` charges no strike — because D6's
own number is «raised three times and nobody replied» and the curator
already wakes every due note daily, so waking on every look would strike a
quiet dateless note past the ceiling in two days. Two additions the node
did not name: `remind` under Dreamer provenance refuses a repeat (one
decision is one moment; tomorrow's pass decides again), and the act turn's
header names the note but does not carry its body — the fire prefill
renders the `<notes>` index and she reads the body with `note_read`. Two
things the build did not decide for the operator: the Dreamer doctrine file
(`nora/dreamer/doctrine/dreamer.md`, operator-only) still says «you look;
you do not act», which the objective overrides in text for this one pass;
and no live measurement has been taken — §5's two suites exist and are what
tune the objective's version 1. Supersedes 38; revises 33 §8; DEC-162
records it; DEC-133 (1) is deprecated in half. Version 2 after two reviews and the operator's read of v2: D3 keeps `remind` and stamps it by provenance, D5's owner mechanism, D11's
predicate and D12's state mapping changed; §1 now says which inputs carry her
own words; §5 names what the harness lacks. The build is issue #103, which
should be updated to point here and to drop its knowledge-tier language. The
note tier's own follow-ups stay in #118.
