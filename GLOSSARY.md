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
| **`doctrine/identity.md`** | who Nora *is* — character, not accounts | the doctrine layer |

Because of this, tools that answer *"who is 阿豹?"* are named **`person_*`**,
never `identity_*`. See Person below.

## Person, and the things that are not a person

- **person** — a human being. Identified by **`person_id`** (`miles_novak`), which
  is stable, operator-assigned, and is also their Honcho peer id (A2, DEC-14).
- **`PersonRef`** — the resolved record: person_id, name, first/last, aliases.
- **account** — one platform login. **One person may hold several** (DEC-14);
  the Ridgeline group contains three accounts and two people, one of whom is Nora.
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

Three layers, two stores, and the distinction that matters most:

| layer | store | holds | tool |
|---|---|---|---|
| **hot** | PostgreSQL | last 40 messages, verbatim | *(injected, no tool)* |
| **deeper** | **same PostgreSQL** | everything older, verbatim | `load_transcript`, `who_said`, `search_memory` (this room), `search_memory_workspace` (every room of the workspace) |
| **derived** | Honcho | conclusions *about people*, and about herself | `about` (retrieve), `ask_memory` (reason — Honcho's dialectic, 14–19 s, budget 1), `remember` (write one conclusion) |

Two more things she carries between turns are NOT memory in this sense and
have their own sections below: **notes** (the group's open questions) and
**Standing** (her own position per room).

**The record store is the RECORD. Honcho is the INTERPRETATION.** The record
store is authoritative and is what R-2/A9 guarantee; Honcho is best-effort and
may be absent — which is why `nora/infra`'s two clients have opposite error
contracts.

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
  `(nora → person)` — her model of them — and the **self card** reads
  `(nora → nora)`, distilled from her own replies. Measured 2026-08-06 as
  ~88% *events* rather than *traits*; built on anyway, deliberately, and
  framed as a record she may contradict rather than a description.
- **self card** — the `<self_card>` block: things she said or did before,
  from her own memory (DEC-96). Injected on every tag, and into the proactive
  pass. Undated on purpose, and never an instruction.
- **peer card** — a Honcho endpoint that returned null in this deployment
  (DEC-27). The `<peer_history>` block a tag gets is `representation`, not
  the card.
- **nothing found ≠ could not ask.** `load_transcript` empty means *nothing
  was said*. `about` empty means *nothing was concluded* — weaker. Hence
  `Recollection.reachable`. Collapsing the two hid a dead endpoint for the
  life of the deployment.

## The Dreamer, notes, Standing, and the proactive half

The most-used words in this repo, and none of them was here until 2026-09-08.

- **The Dreamer** — the background tier that runs *between* turns, on its own
  clock (`[dreamer] tick_seconds`, every adaptor on its own
  `AdaptorSchedule`), and never in the reply path. It reads what the record
  already holds and writes derived state back; nothing it produces reaches a
  room directly. Its units are **adaptors** (`nora/language/`, wired in the
  composition root under `dream_adaptors`), each with an **objective** — an
  authored prompt file under `nora/dreamer/objectives/` — and each bounded by
  a **DreamGuard** with its own budget. The name is not a metaphor for
  creativity: it is the tier that runs while nobody is asking, which is
  exactly why it never speaks. `design/dreamer-agent-loop/` is its bundle.

  **The three live adaptors** are `note-candidates` (hourly, per room, reads
  the new messages and stages notes), `note-curation` (daily, per workspace,
  retires notes that closed or expired — off by default) and `proactive`
  (below). Two earlier tiers — the knowledge tier (the "fact tier") and the
  language tier — were deprecated 2026-09-07 and their code deleted; a design
  node numbered 20, 21, 23, 24, 25, 28, 31 or 32 in that bundle describes code
  that no longer exists.

  **The deterministic check comes first.** Every adaptor's `inputs()` is a
  store read that returns `""` when there is nothing to reason about — no new
  messages for a watermark-bearing adaptor, no due note for the curator, no
  change in a quiet workspace for proactive — and the runtime makes no model
  call on a blank prompt. An unchanged hour costs one query and zero tokens.

- **note** — one tracked uncertainty of a group: «dinner on the 12th, venue
  open», with a **title**, a **description**, a **body** (the arrangement as
  it stands), an **anchor** (the date it is about) and a **closing
  condition**. Scoped to a **workspace**, not a room. Written by people on
  the tag path (`note_create`/`note_update`) and by `note-candidates`; read
  everywhere as the `<notes>` index plus `note_read`. A note has a
  `revisit_at` and **strikes** — how many times it was raised with nobody
  answering — and the curator retires it when the room settles it or its
  moment passes. **Nothing Nora says can create, revise or close a note**:
  her own rows are excluded from what the note tier reads. That is the one
  structural brake on her talking to herself (39 §1).

- **workspace** — the SOCIAL UNIT a room belongs to (DEC-105): the same
  people reachable in more than one place (a Signal group and its Discord
  channel). Rooms sharing one share notes, memory search and relays
  (`send_to`); rooms in different ones never do, however trusted each is.
  Set per room in `policy.toml`; defaults to one workspace.

- **Standing** — Nora's held position on where a room's conversation stands:
  one paragraph per room, what she currently thinks, distinct from what was
  said. It is *derived* state, written by the debrief path
  (`nora/engagement/stages/debrief.py`) after each tag turn and stored
  through `StandingStore`, and it passes the same egress screen a reply does
  before it is written. Standing is an **input** to a later turn, never an
  output of the current one — and it is rewritten each time and keeps
  nothing, so anything that must survive belongs in a note.

  Do not read "standing" as status or as a queue position; it is a *stance*.
  And do not read `NoteStore.standing()` — the query for not-retired notes —
  as this tier; the two collided once (39 D8).

- **proactive** — Nora's other half (design/dreamer-agent-loop/39, DEC-162).
  The passive half answers when tagged; the active half acts when nobody
  asked. Both run one **OODA loop** — observe (human rows), orient (notes,
  Standing), decide, act — closed through a human. The active half is a
  Dreamer adaptor whose **decide turn** reads a workspace's open notes, each
  room's Standing with its age, its own **decision log** (what it scheduled
  before and how each went) and its self card, asks memory about the people
  involved, and may set ITSELF a reminder for a chosen hour with `remind` —
  its one act verb, and the only act any Dreamer adaptor holds. The **act
  turn** is that reminder coming due: a fire turn (`kind="proactive"`) that
  reads the room again and speaks or declines. Choosing *when* is the
  capability; a fixed daily slot would only be scheduling.

- **home room** — the one room of a workspace the proactive adaptor stands
  in (`home_room = true` in `policy.toml`, 39 D2): its roster is what
  `where_is`, `about`, `ask_memory` and `group_members` resolve through, its
  `location` flag applies, and the rows she writes fire into it. A workspace
  has no conversation of its own, so a room-scoped read needs one room, and
  that room is a disclosure decision the operator makes — exactly once per
  workspace. No home room, no proactive pass.

- **pass** — one tick of the proactive adaptor. It ticks hourly and RENDERS
  (calls the model) only on the daily floor (07:00 HK, once) or on a change
  since the last rendered pass — a note created, revised by a person, or
  newly due, or a room's Standing rewritten — once the workspace's HOME ROOM
  has been quiet for `[dreamer] quiet_seconds`. Every room, until 2026-09-10:
  a row can only fire into the home room, so the others were a proxy.
  `nora_proactive_pass_total{reason}` says which.

- **decision log** — Nora's own reminder rows (`requester = "nora"`) over
  the last days, with outcomes: `sent`, `silent`, `cancelled_note_moved`,
  `pending`. Injected into every pass so she does not repeat herself.

- **Standing** — Nora's held position on a topic in a room: what she currently
  thinks, distinct from what was said. It is *derived* state, written by the
  debrief path (`nora/engagement/stages/debrief.py`) and stored through
  `StandingStore`, and it passes the same egress screen a reply does before it
  is written — a screen applied before the write, not before the read, because
  a screen a cache hit skips is not a screen. Standing is an **input** to a
  later turn, never an output of the current one.

  Do not read "standing" as status or as a queue position; it is a *stance*.

## Doctrine — what was decided

**Doctrine**, not "persona". Renamed 2026-08-06 (DEC-29): "persona" names a
*character*, and a directory named for a character invites sentences about what
that character is like — which is how a capability claim ended up in
`soul.md` and went silently false.

Five `kind`s, and the line through the middle is the whole design:

| kind | source of truth | authored? |
|---|---|---|
| `constraint` | the operator's decision | yes |
| `identity` / voice | the operator's taste | yes |
| `conduct` | the operator's judgement | yes |
| **`capability`** | **the tool registry** | **no — generated** |
| **`situation`** | **the running turn** | **no — generated** |

A capability claim in an authored file now **fails to load**.

## A turn

- **ambient** — a message Nora records but does not answer. The default. (An
  untagged message in a participation-opted-in room is still ambient — it is
  only NOTED, and may later close a quiescence window; see **participation**.)
- **tag** — a message that triggers her. One tag = one turn.
- **turn** — everything from admission to reply. Contains several **model
  calls**; each call is a **round trip** (~4s) and each retained tool result is
  **re-sent on every subsequent call**, which is why intake compounds.
- **fire turn** — a turn nobody asked for: same stages, same guards, but no tag
  behind it. The second of exactly two paths that may send (R-1b).
- **occasion** — WHY a fire turn is happening, as a value (`nora.domain.events.Event`, DEC-139):
  where she speaks, on whose authority, the words put to her, and the header
  that explains the moment. The factories on `nora.app.fire` enumerate them —
  a reminder coming due, a gathering, a version announcement (DEC-97), a
  compute job, a relay, participation (below) and proactive (above). A
  reminder names its requester; an announcement, a participation turn and a
  proactive turn name NOBODY, which is what stops the turn being refused
  under a rule about a grant it never held.
- **audience** — which occasion a doctrine file speaks to (`applies_to`):
  `tag`, `heartbeat`, `participation`, `proactive`, and `relevance_gate` for
  the gate's own instruction. The same word labels every failure counter
  (`nora_silent_failures_total{audience}`), so a fire turn's failures are
  never charged to the tag budget.
- **participation** — the occasion triggered by the room itself
  (design/dreamer-agent-loop/35): an untagged message in an operator-opted-in
  room closes a quiescence window once the room goes quiet, and ONE
  determination runs with the right to stay silent. NOT "ambient" — ambient
  means recorded, never answered; participation is the governed exception to
  it. Ships disabled (`[participation] enabled = false` plus a per-room
  `participation = true` in `policy.toml`). **Shadow mode** mirrors every
  determination to a review channel and holds the live send, for reading
  before the flip.
- **Relevance Gate** — a cheap, separate model call in front of participation
  (design 36, `[relevance_gate]`): reads only the transcript and Standing and
  answers whether the room is ASKING; only a `go` spends a full turn. Its
  instruction is `doctrine/participation.md`, on an audience she never reads.
- **terminal verb** — a tool that ends the turn: `send_reply` and `send_gif`,
  on both paths. A participation turn and a proactive act turn are the turns
  that may choose silence: they compose `tool_choice="auto"`, so ending in
  prose IS declining the floor — and `silent` is recorded as a success.
- **prefill** — the three messages built before the tag message.
- **context header** — the block explaining how the turn works. Its opening
  paragraph is the one thing that differs between a tag and a fire turn:
  "somebody just called you" versus "nobody has, and here is why you are awake"
  — and between the two fire occasions, which is why the occasion carries it.

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

## Scope

- **room** — the conversation a tag happened in, resolved to people
  (`nora/agent/room.py`). The scope every room-scoped tool resolves *within*.
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
