# Glossary

Words in this repo that mean something specific, and words that have collided.

Written 2026-08-06, after a naming discussion surfaced three terms doing double
duty and two tools whose names describe something they are not. **A tool's name
is the model-facing schema** (Strands derives it from the callable), so a
misleading name misleads the agent, not only the reader. Brought back in step
2026-09-09, when the note tier, Standing, participation, the Relevance Gate
and the proactive adaptor had all landed without a word here.

Ordered by how much confusion each has actually caused.

---

## Identity — three different things

The worst collision in the codebase. All three are live, all three are called
"identity" somewhere.

| term | means | where |
|---|---|---|
| **identity (resolution)** | mapping a platform *account* — phone number, ACI — to a person | `nora/ingest/stages/identity.py` |
| **`PlatformIdentity`** | **Nora's own account** on a platform, used for self-tag checks (R-12) | `domain/models.py`, `adapter.resolve_identity()` |
| **`doctrine/core/identity.md`** | who Nora *is* — her voice and conduct, not accounts (her place in a room is `doctrine/rooms/belonging.md`) | the doctrine layer |

Because of this, tools that answer *"who is 阿豹?"* are named **`person_*`**,
never `identity_*`. See Person below. (This entry used to say "character, not
accounts" — dropped since `design/cognition-loop/10-tiers.md` made **Character**
the name of a tier, "what an entity is like and how it behaves"; who Nora *is*
is doctrine, never that tier.)

## Person, and the things that are not a person

- **person** — a human being. Identified by **`person_id`** (`miles_novak`), which
  is stable, operator-assigned, and is also their Honcho peer id (A2, DEC-14).
- **`PersonRef`** — the resolved record: person_id, name, first/last, aliases.
- **platform account** — one platform login, always spelled out this way and
  never bare "account" (`design/cognition-loop/10-tiers.md` §6 gives "account"
  to a Character row's body instead). **One person may hold several** (DEC-14);
  the Ridgeline room holds three platform accounts and two people, one of whom
  is Nora.
- **alias** — a nickname the *operator* wrote in `identity.toml` (阿豹妹,
  峰哥). Trusted, because a human on disk authored it.
- **display name** — what someone set on their own Signal profile.
  **Untrusted** — anyone can set it to anything, which is why design/10 §1.3
  forbids an alias or display name ever resolving a *sender*. Resolving a
  lookup *target* is a different, safe operation.
- **unmapped sender** — an account with no person in `identity.toml`. Reported
  as a **count**, never as an id, because the only thing Nora holds for them is
  a phone number or ACI. `nora_unmapped_senders_total` is the tell.

## Memory — record versus interpretation

**Memory is Honcho, and only Honcho** (`design/cognition-loop/10-tiers.md`
§6's words table: memory means Honcho, never "any store of ours"). Postgres
holds two windows onto the same layer, the **Record**, not memory:

| window | layer | store | holds | tool |
|---|---|---|---|---|
| **hot** | Record | PostgreSQL | last 40 messages, verbatim | *(injected, no tool)* |
| **deeper** | Record | **same PostgreSQL** | everything older, verbatim | `load_transcript`, `who_said` |
| **derived** | memory | Honcho | conclusions *about people*, and about herself | `about` (retrieve), `ask_memory` (reason — Honcho's dialectic, 14–19 s, budget 1), `remember` (write one conclusion), `search_memory` (this room), `search_memory_workspace` (every room of the unit) |

`search_memory`/`search_memory_workspace` were filed under "deeper" here
until `design/cognition-loop` step 0: both call `HonchoClient.search`
(`nora/agent/tools.py::search_memory`), so despite reading like a transcript
tool by name they are memory, not Record, and belong in the row above.

**One memory surface at a time** (DEC-316). The table above is the Honcho
surface, what `[memory_index] enabled = false` offers (the default, and
production). With it on, the memory graph's surface replaces it: one search
verb, `memory_search` (search every room of the unit by question, person,
dates and kinds; the index, no Honcho behind it), and none of `ask_memory`,
`search_memory`, `search_memory_workspace`, `who_said` or `about`: the graph
surface holds no Honcho tool. A profile question there is `memory_search`
with `person`. `remember` is on both: a Honcho conclusion off the graph, a
Persona `told` line on it (DEC-317). `load_transcript` is on both. `who_said` is deprecated: the
graph surface does without it, and it goes with the rest of the Honcho
surface at step 9.

Two more things she carries between turns are NOT memory in this sense and
have their own sections below: **notes** (the unit's open questions) and
**Standing** (her own position, per subject).

**The record store is the RECORD** — the same word `10-tiers.md` uses for the
layer. **Honcho is memory**, its only store. The record store is authoritative
and is what R-2/A9 guarantee; Honcho is best-effort and may be absent — which
is why `nora/infra`'s two clients have opposite error contracts.

- **`RecordStore`** (port) / **`SqlRecordStore`** (its one concrete, over
  `postgresql+asyncpg://`) — the record store's read/write seam. **Renamed from
  `AuditStore`/`SqlAuditStore` in the 2026-08-07 PostgreSQL migration**
  (design/20): "audit" read as a compliance log, but this store IS the record
  (hot + deeper above), not a side-channel of it. `FakeRecordStore`
  (`nora.testing`) is its contract-bound double. Was SQLite until that
  migration; the store is now one `nora` PostgreSQL database that presence
  (design/19) will share.

- **`load_transcript`** was `read_buffer`, which was misnamed — nothing is
  buffered; it reads durable rows from disk. **Renamed to `chat_earlier`
  2026-08-07 (DEC-58), then to `load_transcript` 2026-08-08**, the honest name,
  after the MEASURED selection harness confirmed no regression. The
  `nora_tool_calls_total{tool}` label moved with it; the metric NAME is
  unchanged (append-only).
- **`recall` was misnamed** — it fetched an accumulated *profile*, not
  memories. **Renamed to `about` 2026-08-06 (DEC-39).** The metric
  `nora_recall_total` keeps the old word: metric names are append-only.
- **representation** — Honcho's derived model of one peer AS SEEN BY another:
  every read is keyed `(observer, observed)` (DEC-66), so `about` reads
  `(nora → person)` — her model of them — and `(nora → nora)` is her model of
  herself, distilled from her own replies (no longer injected: the `<self>`
  block is the Character · Nora account instead). Measured 2026-08-06 as
  ~88% *events* rather than *traits*; built on anyway, deliberately, and
  framed as a record she may contradict rather than a description.
- **self block** — the `<self>` block: her Character · Nora account for the
  turn's unit (design/self-growth/20), capped in lines, first in the tag, fire
  and Cognition prompts, directly after the doctrine. No block while the unit
  has no account. It replaced the `<self_card>` block (her Honcho conclusions
  about herself, DEC-96), retired by DEC-292.
- **peer card** — a Honcho endpoint (`HonchoClient.peer_card`), Honcho's own
  model-authored distillation of who a person is, injected as `<peer_card>`
  on a tag when Honcho has one (`nora/agent/prefill.py::_peer_card_block`,
  live since the 2026-08-20 prompt-architecture audit). **DEC-27's "returned
  null in this deployment" no longer holds** — this entry named a dead
  endpoint that answers now; corrected by `design/cognition-loop` step 0. The
  `<peer_history>` block a tag gets is `representation`, not the card — the
  two are deliberately not merged. Never Character evidence
  (`design/cognition-loop/10-tiers.md` §6, `50-character-and-standing.md` §5,
  DEC-214): it is memory, read as a hint, unscoped within its Honcho
  workspace, and the Character adaptor cites only the Record and the Rollup.
- **nothing found ≠ could not ask.** `load_transcript` empty means *nothing
  was said*. `about` empty means *nothing was concluded* — weaker. Hence
  `Recollection.reachable`. Collapsing the two hid a dead endpoint for the
  life of the deployment.

## The Dreamer, notes, Standing, and the proactive half

The most-used words in this repo, and none of them was here until 2026-09-08.

- **The Dreamer** — **the runtime, never a tier**
  (`design/cognition-loop/10-tiers.md` §6: "Dreamer — the runtime hosting the
  four adaptors | a tier; a background pass that never acts"), that runs
  *between* turns, on its own clock (`[dreamer] tick_seconds`, every adaptor
  on its own `AdaptorSchedule` today, moving to a per-adaptor wake rule,
  `design/cognition-loop/20-observe.md` §3), and never in the reply path. It
  reads what the record already holds and writes derived state back; **it
  never speaks to a room directly** — its one bridge out is `remind`, a row
  the fire path composes and sends. Since 2026-10-03 (DEC-277) that bridge is
  the Cognition session's `remind`; the proactive adaptor is deleted. It hosts
  **adaptors** (`nora/language/`, wired in the composition root under
  `dream_adaptors`; the settled set is Cognition, Character, Lessons and
  Assess, `design/cognition-loop/00-intent.md` §3 — not "units", which now
  names the social unit, DEC-105, below), each with an **objective** — an
  authored prompt file under `nora/dreamer/objectives/` — and each bounded by
  a **DreamGuard** with its own budget. `design/dreamer-agent-loop/` is the
  runtime's original bundle; `design/cognition-loop/` is what its adaptors
  become.

  **The live adaptors** are the four that `[culture] enabled` turns on:
  `culture` (a room's account), and `character-person`, `character-unit` and
  `character-nora` (the Character sessions, below, DEC-290). Since 2026-10-03
  (DEC-277) the note-candidates, note-curation, note-merge and proactive
  adaptors are deleted: the Cognition session is the one background writer of notes,
  events, Standing and Nora's own reminders.
  Two earlier tiers — the knowledge tier (the "fact tier") and the
  language tier — were deprecated 2026-09-07 and their code deleted; a design
  node numbered 20, 21, 23, 24, 25, 28, 31 or 32 in that bundle describes code
  that no longer exists.

- **culture** — her account of what a room has BECOME: how they talk, how
  things get decided, what they call themselves (design/culture-tier/,
  DEC-207). The `culture` adaptor reads the room's own messages when it has
  said enough (DEC-288: `[culture] wake_batch` new human messages, or any new
  one once `stale_seconds` have passed; a silent room never fires) and appends
  ONE short account to a chain (`culture`/`culture_revision`, keyed on the
  room's `primary_id`). It writes through `character_revise`, citing the
  messages it rests on, and reads no presence and no Standing: its account is
  evidence for accounts that render in other rooms (R-31). Every
  occasion in a room reads it as `<culture>` except the Relevance Gate; the
  public summons reads no room and no culture. It is a
  baseline, not a rule: a window read against it becomes a deviation. Not
  `<room>`, which is the operator's statement of what a room is FOR.

  **Culture IS the Character tier's room case** (DEC-290,
  `design/cognition-loop/10-tiers.md` §5): a room's account keeps its
  `primary_id` key, and the person, unit and Nora cases hang off the unit
  (**Character**, below). "Her own standing there", which this entry used to
  claim, was Standing's question, not Character's, and the room fold no longer
  reads it.

  **The deterministic check comes first.** Every adaptor's `inputs()` is a
  store read that returns `""` when there is nothing to reason about — no new
  messages for a watermark-bearing adaptor, no due note for the curator, no
  change in a quiet unit for proactive — and the runtime makes no model
  call on a blank prompt. An unchanged hour costs one query and zero tokens.

- **note** — one tracked uncertainty shared by a unit's people: «dinner on
  the 12th, venue open», with a **title**, a **description**, a **body** (the arrangement as
  it stands), an **anchor** (the date it is about) and a **closing
  condition**. Scoped to a **unit**, not a room. Written by people on
  the tag path (`note_create`/`note_update`) and by the Cognition session; read
  everywhere as the `<notes>` index plus `note_read`. A note has a
  `revisit_at` and **strikes** — how many times it was raised with nobody
  answering — and the Cognition session retires it (`note_retire`) when the
  room settles it or its moment passes. A note that duplicates an older one is
  folded into it by `note_merge` and retired `retired-merged`; anything that looks it up after
  that follows `replaced_by` to the note it went into. **Nothing Nora says can create, revise or close a note**:
  her own rows are excluded from what note-writing reads. (Not "the note
  tier" — `design/cognition-loop/10-tiers.md` names the tier **Register**;
  note is one of its three kinds, alongside memo and event.) That is the one
  structural brake on her talking to herself (39 §1).

- **unit** — the SOCIAL UNIT a room belongs to (DEC-105): the same
  people reachable in more than one place (a Signal group and its Discord
  channel). Rooms sharing one share notes, memory and relays (`send_to`);
  rooms in different ones never do, however trusted each is. Set per room in
  `policy.toml` (`unit = "friends"`); defaults to one unit. A unit holds AT
  MOST ONE room per platform — refused at startup since DEC-214.

  The hierarchy is **workspace > unit > room** (DEC-214): every unit belongs
  to one **workspace**, and — once `policy.toml` declares `[units]` — inside
  that workspace's **Honcho workspace** each unit is a Honcho *named scope* — its rooms' Honcho sessions are the scope's
  members (bare "session" means a Cognition session, never a Honcho session,
  `design/cognition-loop/10-tiers.md` §6), and every memory read made for the unit passes the scope, so what
  Nora recalls for a unit comes from that unit's rooms. The note discharge
  that once addressed a Honcho workspace named after a unit (2026-09-19) is
  why the memory port takes a unit and never a Honcho workspace id.

- **workspace** — a group of UNITS, and the boundary of a person's model
  (DEC-214). Units in one workspace share its Honcho workspace, so a person is
  ONE Honcho peer across all of them; different workspaces are separate person
  models entirely. Declared in `policy.toml` (`[units.friends] workspace =
  "67"`). With no `[units]` table every unit is in one implicit workspace
  (`default`). Not a Honcho workspace — see the next entry. **Retired with Honcho**
  (operator 2026-10-16, `design/unified-memory/35-persona.md` row 6): the
  unit becomes the only boundary; this entry describes the code until then.

- **Honcho workspace** — Honcho's tenant: where one workspace's peers,
  sessions and conclusions live. Each workspace is bound to exactly one, in
  `identity.toml` (`[workspaces.67] honcho_workspace = "nora"`); unbound, it
  uses the workspace's own name; the implicit workspace uses `config.toml`'s
  `memory.workspace_id`. The phrase is always «Honcho workspace» — a bare
  «workspace» in this codebase means the grouping of units above. Honcho
  workspaces are shared by every application on the server, so a bound one
  must be Nora's: marked `nora_owner` in its metadata, or absent or empty
  (then claimed); anything else refuses startup.

- **Standing** — Nora's held position toward a subject: today, one paragraph
  per room, what she currently thinks, distinct from what was said, plus its
  **working set** (below). It is *derived* state, written by the debrief path
  (`nora/engagement/stages/debrief.py`, which has created no notes since
  DEC-202) after each tag turn, and by the Cognition session (DEC-277,
  2026-10-03; the ingest-spawned event/rollup fold is deleted), stored through `StandingStore`,
  and screened for egress before it is written — not before it is read,
  because a screen a cache hit skips is not a screen. Standing is an
  **input** to a later turn, never an output of the current one. Since
  2026-09-24 it is also where she keeps what she found on a turn and has
  not yet given the room — the compact of her work in that room
  (design/standing-compact/) — folded by ONE model call.

  **Today's fold keeps no history beyond its head**, so anything that must
  survive belongs in a note. `design/cognition-loop/10-tiers.md` §1 and
  `12-schemas.md` §5 change this: the head stops being a lossy fold, and each
  section carries its own support and age, and may hold an undated position
  that persists across folds — `I hold open`, `What I am following`. The
  ROOM schema (`Where it stands`, `I hold open`, `I owe`, `Found, not yet
  given`) ships live at migration step 3, written by both the debrief and the
  ingest fold through one shape; UNIT and PERSON Standing, new subjects, ship
  at step 8. Not built yet; described above as it runs today.

  Standing is Nora's held **position** — do not read it as status or a queue
  position. And do not read `NoteStore.standing()` — the query for
  not-retired notes — as this tier; the two collided once (39 D8).

- **working set** — the part of Standing that holds what she found on a turn
  and has not yet given the room: options, findings, a shortlist (#445,
  design/standing-compact/30). Bounded (`[standing] working_set_max_items`,
  ceiling 20 items), aged out at render time (48 hours), and rendered
  wherever Standing is: the tag turn and every fire turn. Not a note: it holds her work, not the group's open
  question.

- **proactive** — Nora's other half (design/dreamer-agent-loop/39, DEC-162).
  The passive half answers when tagged; the active half acts when nobody
  asked. Both run one **OODA loop** — observe (human rows), orient (notes,
  Standing), decide, act — closed through a human; that loop is the
  **ambient loop** (below), and the proactive adaptor is one of its two
  deciders. The active half is a
  Dreamer adaptor whose **decide turn** reads a unit's open notes, each
  room's Standing with its age, its own **decision log** (what it scheduled
  before and how each went), asks memory about the people
  involved, and may set ITSELF a reminder for a chosen hour with `remind` —
  its one act verb, and the only act any Dreamer adaptor holds. The **act
  turn** is that reminder coming due: a fire turn (`kind="proactive"`) that
  reads the room again and speaks or declines. Choosing *when* is the
  capability; a fixed daily slot would only be scheduling.

  **Superseded by `design/cognition-loop`** (DEC-228, `design/09-decision-index.md`;
  `70-migration.md` §3): the decide and act turns described above become the
  Cognition adaptor's act step, one session instead of a separate adaptor.
  What survives, clause by clause: the home room, generalised to each agenda
  item's bound room; the decide/compose split; the per-room `proactive`
  flag; D11's screening (a row whose subject was retired or revised on the
  tag path is cancelled when claimed); silent recorded as done; the pending
  ceiling; strikes. **2026-10-03 (DEC-277): the decide pass is deleted and the
  Cognition session's `remind` writes the rows; the paragraph above describes
  the retired adaptor. The act turn (`kind="proactive"` fire) stays.**

- **home room** — the one room of a unit the (retired, DEC-277) proactive
  adaptor stood in (`home_room = true` in `policy.toml`, 39 D2): its roster is what
  `where_is`, `about`, `ask_memory` and `group_members` resolve through, its
  `location` flag applies, and the rows she writes fire into it. A unit
  has no conversation of its own, so a room-scoped read needs one room, and
  that room is a disclosure decision the operator makes — exactly once per
  unit. No home room, no proactive pass.

- **pass** — one tick of the proactive adaptor (deleted 2026-10-03, DEC-277;
  the Cognition session and its `remind` verb replaced it, and the text below
  describes the adaptor). It ticked hourly and RENDERED
  (calls the model) only on the daily floor (07:00 HK, once) or on a change
  since the last rendered pass — a note created, revised by a person, or
  newly due, or a room's Standing rewritten — once the unit's HOME ROOM
  has been quiet for `[dreamer] quiet_seconds`. Every room, until 2026-09-10:
  a row can only fire into the home room, so the others were a proxy.
  `nora_proactive_pass_total{reason}` said which (metric deleted).

- **decision log** — Nora's own reminder rows (`requester = "nora"`) over
  the last days, with outcomes: `sent`, `silent`, `cancelled_note_moved`,
  `pending`. Injected into every pass so she does not repeat herself.

## The layers and the Notebook's tiers

**`design/cognition-loop/05-data-model.md` is canonical for the topology and
the vocabulary** (DEC-249): five **layers** in one flow, each defined by who
writes it, **Authored** (humans) → **Record** (ingestion) → **Rollup**
(code) → **Notebook** (the subconscious, one writer per tier) → **Views** (assembled at read
time through one path, never stored), with **Memory** (Honcho) beside the
flow; and the hierarchy **Layer › Tier › Kind › Entry**. A **tier** is a
subdivision inside the Notebook only (Register, Character, Standing,
Lessons); the Record and the Rollup are layers, not tiers.
**`design/cognition-loop/10-tiers.md` is canonical for the Record and Rollup layers and the four
tiers below and every word they use**; this section only resolves what
collides with words already in use elsewhere in this file, and adds what had
no entry.

- **Record** — the observed layer: messages (hers included) with their edits,
  deletes and reactions; her loop decisions and reminder results; presence
  fixes and the collector's polls; social items; play samples; perceptions
  of media (what it shows, whose face); face labels and `character_dispute`
  items (`10-tiers.md` §1's words). Written by code. This is what "the
  record store" / "the RECORD" above already names — never "a database row"
  in general.
- **Rollup** — the computed layer: **episodes** (a Rollup row for one bounded
  happening, with a start, an end and participants — a stay, a co-location,
  an outing, a person's day) and **aggregates** (a Rollup row of counts,
  patterns or clusters over a window — place, rhythm, tie). Code only, a
  pure function of the Record. Never a model's summary, and never Standing's
  fold of folds: `nora/analytics/rollup.py` keeps its own name for the
  presence part of this layer. (The Standing rollup fold and its
  `rollup_max_folds` / `nora_standing_rollup_folds_total` were deleted
  2026-10-03, DEC-277.)
- **Register** — the unit's affairs: **note** (unchanged, DEC-213), **memo**
  (a settled thing the unit wants kept, that does not close — distinct from
  `remember`, which is about the speaker alone), **event** (a dated thing
  people attend; the code name stays `Gathering`). "Event" here never means
  the fire-turn *occasion* — that is code `Event` (`nora.domain.events.Event`,
  DEC-139, see *occasion* below), a different thing with the same English
  word. Written by the Cognition adaptor and the tag path until the cut-over, then by the Cognition adaptor alone (DEC-249).
- **Character** — what a **person**, a **room** (culture's new home, above),
  the **unit** or **Nora** is like and how it behaves. **account** is the
  body of one Character row — always "platform account" for a login (the
  *platform account* entry above). Written only by the Character sessions,
  through `character_revise`. Built as DEC-290 (design/self-growth/10): ONE
  Dreamer session per (unit, entity), stored in the `culture` chain under four
  key shapes: a room's `primary_id`, `{unit}#character:{person_id}`,
  `{unit}#unit` and `{unit}#nora`.
  - **person session** (`character-person`) — one person's account in one
    unit, woken by `[culture] person_wake_batch` (100) of their messages across
    the unit's rooms or the stale rule. Handed their newest messages, the
    unit's CITED room cultures and its previous account; memory is a tool
    (`ask_memory`, `search_memory`), never a pre-fetch and never a citation.
    Renders as `<character>` in the rooms of the unit that list the person.
  - **unit session** (`character-unit`) — what the unit is across its rooms,
    woken by `unit_wake_batch` (5) new room revisions; reads only cited room
    cultures and holds no read tool. Renders as **`<unit_culture>`** after
    `<culture>` on tag and fire turns in the unit's rooms.
  - **Nora session** (`character-nora`) — what she is like in the unit, woken
    by `nora_wake_batch` (100) closed response episodes that are evidence
    (`Episode.is_evidence`), citing at least three of them; the echo rule
    refuses a revision resting on her own sends. Renders as **`<self>`**, first
    in the tag, fire and Cognition prompts.
  - **`character_revise(entity, body, citations)`** — the one write verb. Code
    checks the body (non-empty, within the cap, the R-18/R-28 screen) and that
    every citation is a Record row id the session was shown or a
    `response:<id>` episode it was shown; a refusal names its reason. **`character_no_change`**
    closes an entity with its account unchanged. A session that settles
    neither holds its batch and is given up on the third try.
- **Lessons** — what Nora has learned about how to act in a unit: short
  prescriptive lines, each with the outcomes that taught it. Written only by
  the Lessons adaptor, through `lesson_write`/`lesson_retire`; a session that
  changes nothing closes with `lessons_no_change` (DEC-297, off by default
  behind `[lessons] enabled`). Not doctrine (a rule she may not break) and not
  a runtime skill. Every lesson passes the **doctrine check** first (DEC-298).
- **Tension** — a lesson the doctrine check refused because acting on it would
  loosen or contradict a law rule: the rule quoted, what the lesson would have
  said, its evidence (`self_tension`, `nora lessons tensions`). Operator-only;
  she never reads one.
- **Standing** — see above; now one of the Notebook's four tiers rather
  than a lone concept.

Other words this bundle introduces:

- **lineage** — the `(kind, subject, origin room)` set a derived row's
  citations reach down to; decides which rooms may be shown it, checked at
  assembly — this file's own "enforced at assembly, not at egress" (below),
  never at egress. Not the citations themselves.
- **reasoning** — the mode a derived row was reached by: `computed`, `told`,
  `deduced`, `induced`, `abduced`, `said` (`10-tiers.md` §4). Never the
  model's chain of thought.
- **stimulus** (plural **stimuli**) — what ingestion, the Rollup, the clock,
  a `self` wake Cognition scheduled, or a tag turn's `handoff` raises to wake
  Cognition. Never the Signal platform, never a Python/OS
  signal, never an event.
- **cognition** — the fast adaptor: orient, decide, act in one session per
  unit per wake. Never a tier, never "the cognition tier" or "a cognition
  layer" (see *placement*, below).
- **event** — Register's third kind, a dated thing people attend; code:
  `Gathering` (above). Never the fire-turn occasion (code `Event`, DEC-139,
  see *occasion*, below).
- **subconscious / conscious** — the operator's own words (潛意識 / 明意識,
  `design/cognition-loop/55-self.md` §6) for the conscious and the subconscious: the Dreamer's
  four adaptors run underneath, always; the tag turn and the fire turn
  speak. Not code names, and never "tier" or "layer" (a layer is a data
  layer, DEC-249).

## Doctrine — what was decided

**Doctrine**, not "persona". Renamed 2026-08-06 (DEC-29): "persona" names a
put-on role, not a person, and a directory named for a role invites sentences
about what that role is like — which is how a capability claim ended up in
`soul.md` and went silently false. (Read "role" here as ordinary English, not
`design/cognition-loop/10-tiers.md`'s **Character** tier — "what an entity is
like and how it behaves" — which this rename predates and is not about.)

Five `kind`s, and the line through the middle is the whole design:

| kind | source of truth | authored? |
|---|---|---|
| `constraint` | the operator's decision | yes |
| `identity` / voice | the operator's taste | yes |
| `conduct` | the operator's judgement | yes |
| `instrument` | the operator's tuning of a cheap model call that is not her | yes |
| **`situation`** | **the running turn** | **no — generated** |

`capability` was a sixth, generated from the tool registry, until DEC-150
deleted it (2026-08-30): each tool's schema is now the only description of what
it does, and a file claiming `kind: capability` **fails to load** as an unknown
kind. The files sit in four scope folders — `core/` (wherever she is),
`rooms/` (a group she belongs to), `public/` (a public summons),
`instruments/` — and an audience loads only the folders written for it.

## A turn

- **ambient** — a message Nora records but does not answer. The default. (An
  untagged message in a participation-opted-in room is still ambient — it is
  only NOTED, and may later close a quiescence window; see **participation**.)
- **ambient loop** — Nora's second backbone (design/ambient-loop, DEC-176).
  The tag path answers when she is called; the ambient loop is how she is
  present when she is not. One observe–orient–decide–act loop, closed through
  a human: **Observe** is the ingest path, **Orient** is the memory layer
  (notes, Standing, Honcho), **Decide** is two deciders — **participation**
  (this window closed: is it hers?) and **proactive** (across the unit:
  anything worth raising, and when?) — and **Act** is the fire path. The loop
  is the system's, not the proactive adaptor's; the components are its
  stages, and each seam between two of them carries a contract
  (design/ambient-loop/01). The tag path is the *other* backbone and feeds
  this one: its debrief writes Standing (it has proposed no notes since
  DEC-202). The **shared
  decision log** (DEC-179, a new store) is what lets the participation turn,
  the proactive turns and the tag path see each other's recent verdicts; it is
  not the proactive adaptor's own **decision log** of its reminder rows,
  which keeps that name in code.

  **DEC-176 still holds** — the loop is named as the system's, and this
  entry's framing stands. **One of its two deciders does not**: proactive is
  superseded by the Cognition adaptor (see the *proactive* entry above),
  whose orient/decide/act run as one session rather than a separate decider
  reading the same seams (`design/cognition-loop`, migration step 4).
  Participation is unchanged (`design/cognition-loop/30-cognition.md` §7).
- **tag** — a message that triggers her. One tag = one turn.
- **turn** — everything from admission to reply. Contains several **model
  calls**; each call is a **round trip** (~4s) and each retained tool result is
  **re-sent on every subsequent call**, which is why intake compounds.
- **fire turn** — a turn nobody asked for: same stages, same guards, but no tag
  behind it. The second of two paths that may send into a room (R-1b); the
  public **summons** is a third sender that reaches no room (DEC-212).
- **occasion** — WHY a fire turn is happening, as a value (`nora.domain.events.Event`, DEC-139):
  where she speaks, on whose authority, the words put to her, and the header
  that explains the moment. The factories on `nora.app.fire` enumerate them —
  a reminder coming due, a gathering, a
  compute job, a relay, participation (below) and proactive (above). A
  reminder names its requester; a participation turn and a
  proactive turn name NOBODY, which is what stops the turn being refused
  under a rule about a grant it never held.
- **audience** — which occasion a doctrine file speaks to (`applies_to`):
  `tag`, `heartbeat`, `participation`, `proactive`, `summons`, and the
  instruments' own (`relevance_gate`, `reaction`, and `dreamer` for the
  Dreamer runtime). The same word labels every failure counter
  (`nora_silent_failures_total{audience}`), so a fire turn's failures are
  never charged to the tag budget.
- **participation** — the occasion triggered by the room itself
  (design/dreamer-agent-loop/35): an untagged message in an operator-opted-in
  room closes a quiescence window once the room goes quiet, and ONE
  determination runs with the right to stay silent. NOT "ambient" — ambient
  means recorded, never answered; participation is the governed exception to
  it. Ships disabled (`[participation] enabled = false` plus a per-room
  `relevance_gate = true` in `policy.toml`, the opt-in's name since it was
  renamed from `participation`). **Shadow mode** mirrors every
  determination to a review channel and holds the live send, for reading
  before the flip.
- **summons** — a public occasion (design/instagram-adoption/19, DEC-212):
  somebody in `identity.toml` tags her under a public Instagram post, and ONE
  turn on the `summons` audience answers in the comments or says nothing. It
  builds no room and writes no memory; it loads only `core/` and `public/`
  doctrine; its text is HELD by a staging deliverer and leaves only through
  the instagram-bridge's guarded comment write. Ships `enabled = false`,
  `mode = "dry_run"`.
- **surface** — where her words land. A **room** is a private surface she
  belongs to; a public post is a **public surface** read by strangers
  (design/threads-adoption/10 names the `SocialSurface` port a second one
  would need). A voice channel is a surface OF a room, not a room (DEC-191).
- **Relevance Gate** — a cheap, separate model call in front of participation
  (design 36, `[relevance_gate]`): reads the transcript and Standing and
  answers whether the room is ASKING; it also reads a one-line-per-tool
  digest of what she can look up (`ToolRegistry.capability_digest()`). Only a
  `go` spends a full turn. Its
  instruction is `doctrine/instruments/participation.md`, on an audience she
  never reads.
- **terminal verb** — a tool that ends the turn: `send_reply` and `send_gif`,
  on both paths. A participation turn and a proactive act turn are the turns
  that may choose silence: they compose `tool_choice="auto"`, so ending in
  prose IS declining the floor — and `silent` is recorded as a success.
- **prefill** — the three messages built before the tag message.
- **context header** — the block explaining how the turn works. Its opening
  paragraph is the one thing that differs between a tag and a fire turn:
  "somebody just called you" versus "nobody has, and here is why you are awake"
  — and between the two fire occasions, which is why the occasion carries it.
- **block** — one labelled unit of context in a prompt: `<group_transcript>`,
  `<notes>`, `<standing>`, `<peer_card>`. The context side's counterpart to
  doctrine's **section**: a section is operator-authored and static, a block is
  data and is built per turn. Not called a *data block* — "block" already means
  the data one throughout this file and the code (`_notes_block`,
  `_peer_card_block`), and the pairing with `section` carries the rest.
- **block source** — the function that builds one block: `_peer_card_block`,
  `render_notes`, `render_standing`. `ContextBlock` is what one returns, and
  `ContextBlock.render()` is how it becomes text. There is no `BlockSource`
  Protocol — one was declared ahead of #220 and deleted when the assembler
  landed without needing it; a source is an ordinary function, and the table
  that places it (`prefill._BLOCKS`) is the interface.
- **row renderer** — a configuration of `render_rows`: one record in, one line
  out, with a **row style** saying which line grammar. Eight of them exist and
  all eight go through it (#220). The `<group_transcript>` transcript is NOT
  one — it is the one block that gave up lines entirely, and `render_messages`
  is its renderer: one `<m>` element per message, so a body keeps its line
  breaks and the element boundary, not the newline, is the separator.
- **row style** — the parameters for turning one record into one line: whether
  it carries a handle, which time format, whether the speaker is bracketed,
  and what her own rows are called. Four fields, all of them plain data;
  anything that READS a row (the speaker, the body, a room label, a reply
  mark) is an accessor passed alongside, not a field. Escaping and the
  one-line collapse are NOT parameters — they are always on, which is the
  point of having one renderer.
- **placement** — where a block goes: `header` (the static, cache-hit prefix)
  or `turn` (rebuilt every turn). Deliberately not called a *tier* — a tier in
  this file is one of the Notebook's Register, Character, Lessons or
  Standing (`design/cognition-loop/10-tiers.md`), each with its own store,
  and this is a position in one prompt. One volatile byte placed in `header`
  ends the 95-99% prefix-cache hit for everything after it.

**Never `injection` or `injector` for any of the above.** The word already
carries three unrelated meanings here: prompt injection (the attack, R-14),
dependency injection (the test-seam policy), and `TranscriptInjection` (the tag
path's whole context assembler, a runtime rather than a block). The verb
"injected" stays fine in prose; it is the type name that would collide.

## Reminders

- **reminder** — a *deferred request*: a row holding what someone asked for and
  when it comes due. Not a stored message to replay — at fire time it is put to
  her as a request and she answers it, so anything she can do now she can be
  reminded to do later. A row has a **kind** — `reminder` (a person's), `gathering`
  (an event's notify), `proactive` (her own, owner `nora`) — and each kind fires
  through its own occasion. A `note` link on a reminder cancels it before it
  fires if the note is retired or a person revises it (39 D11).
- **heartbeat** — the tick that wakes on an interval, finds due rows and runs
  their turns. Knows that time passed; knows nothing about why it matters.
  (Not `nora.obs.logging.heartbeat`, which is a 60s counters log line.)
- **claim** — `pending → firing` as a compare-and-set inside the statement, not
  a read-then-write. This is what makes a row fire *once, ever* (R-37).
- **at-most-once** — the decision behind a crash. A row left `firing` by a dead
  process resolves to `failed`, never re-queued: losing one reminder visibly
  beats sending one twice (R-37b).
- **the slot** — one in-flight turn per conversation, shared by both paths, so
  a reminder and a tag can never run at once in the same room. Held by
  `ConversationSlots`; a tick that cannot take it defers to the next one.
- **ownership** — group-owned in a group, person-owned in a DM. Not enforced
  separately: it falls out of cancellation being scoped to the conversation.

## Guards

- **egress** — anything leaving Nora. Two predicates since 2026-08-06:
  **`scan_identifiers`** (phone numbers, ACIs, keys — unsafe *anywhere*) and
  **`scan_reply`** (those plus model names — replies *only*, since she must
  never name the model she runs on).
- **canary** — a string planted in the doctrine. If it appears in her output
  she is regurgitating her prompt, and the reply is blocked. A **tripwire, not
  a watermark** — it is not something she emits.
- **fails closed** — a guard that blocks the *whole* reply on any hit, so she
  says nothing rather than something unsafe.
- **R-5 / silent failure** — Nora is **silent when broken**, so broken is
  indistinguishable from quiet. Nearly every design decision here is shaped by
  making failures *legible* instead.
- **decorative test** — one that passes whether or not the code works. The
  standard: *if reverting the code under test does not turn it red, it is not a
  test.*

## A room, and the three things it is not

"Room" is the word everyone reaches for and it names a VIEW, not a unit of
storage. Four things share it in prose; two of them key stores, and they key
them differently.

- **conversation** — one room's entry, not the unit. One `[[conversations]]`
  entry in `policy.toml`, resolving to `nora.policy.Conversation`: one
  platform, its unit, a label, and **`ids: frozenset[str]`** — a SET of
  platform thread ids — of which `primary_id` is the one chosen to be stable
  across runs.
- **conversation id** — one platform thread identifier. A conversation may
  hold several (a channel that moved, a re-created group, a thread id). It is
  what ARRIVED, not what it belongs to.
- **room** — the conversation a tag happened in, resolved to people
  (`nora/agent/room.py`). A runtime view, built per turn, never stored. The
  scope every room-scoped tool resolves *within*.
- **scope** — what a Dreamer adaptor iterates over (`scopes()`,
  `key_kind = "scope"`). Today one conversation's `primary_id`. Distinct from
  *unit*, which is the other `key_kind`. The Cognition adaptor
  (`design/cognition-loop/30-cognition.md`) is unit-keyed, one session per
  unit per wake, not scope-keyed like `culture`: its agenda items, not the
  adaptor itself, each bind one room (`10-tiers.md` §2 rule 6; the unit read
  boundary this settles is `70-migration.md` §4).

**Which key a store uses follows from WHICH SCHEDULER HANDS IT THE KEY, and is
not a choice:**

| written by | gets handed | keys on | e.g. |
|---|---|---|---|
| ingest / tag path | the message that arrived | `(platform, conversation_id)` | `messages`, `standing`, `decision_log` |
| a Dreamer adaptor | `scopes()` | `primary_id` | `consolidation_watermark` |

Both are correct for their writer. A store that picks the other one matches
zero rows on the watermark query, so its adaptor is scheduled, raises nothing,
logs nothing and never runs.

**They are equal today and that is an accident of data, not a licence to pick
either.** Every live conversation holds exactly one id (measured 2026-09-13),
so `primary_id` IS the only `conversation_id`. Give any conversation a second
and they diverge: two `standing` rows and two `decision_log` streams for one
`scope`, hence one Dreamer pass. **Nothing reports it, because nothing holds
both keyings and so nothing compares them.**

  The guard that makes it reportable: `load_policy` asserts no
  `conversation_id` appears in two conversations, so a collision is a startup
  error rather than a room whose rows are written under another's platform.
  What a multi-id conversation SHOULD mean for the per-room stores is still
  undecided, and "undecided" is currently being relied on.
- **resolve within scope**, never *resolve then filter* — a person in
  `identity.toml` but not in this room is **absent from the index**, so "not
  here" is indistinguishable from "does not exist" (R-21). Filtering afterwards
  would leak membership through the difference.
- **enforced at assembly, not at egress** — by the time text exists the
  disclosure is already authorised. The 2026-08-06 roster leak is the proof:
  R-18 saw nothing to block because names are not phone numbers.

## Rule prefixes

- **R-n** — a behavioural rule. Registered in `design/09-decision-index.md`
  and named in `tests/test_acceptance_rules.py::RULE_TESTS`.
- **DEC-n** — a decision, with its rationale; `DEPRECATED` rows are marked,
  never renumbered.
- **A-n** — an architectural invariant from the original spec.

R-19 is **reserved but never assigned** (design/11). Never take the next free
number from memory or from chat: re-read the index at merge time
(`CONTRIBUTING.md` «Numbering discipline»).
