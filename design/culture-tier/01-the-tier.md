# The tier — shape, trigger, scopes

## 1. Two scopes, and the Dreamer already schedules both

`runtime.py` schedules each adaptor on its own key: `scopes()` for a
`key_kind == "scope"` adaptor, `workspaces()` for a workspace-keyed one. Both
modes exist. This tier is two adaptors, one on each.

The scopes are real and populated. `policy.toml` on the live deploy:

- three workspaces — `ridgeline`, `friends`, `wedding`
- **`friends` holds three rooms, across Signal and Discord** (DEC-105: at most
  one room per platform per workspace)
- each workspace names a `home_room`

| | key | answers |
|---|---|---|
| **room culture** | `(platform, conversation_id)` | how it works *here*, and where she stands in it |
| **workspace culture** | `workspace` | what she is carrying across rooms |

This follows what is already keyed that way — `standing` is room-keyed, `note`
is workspace-keyed. Neither synthesizer invents a new grouping.

**DEC-181 is the blocker.** `room_portrait` used `scope_id`, `note` uses
`workspace`, `standing` uses `(platform, conversation_id)`. Three tiers, three
keys, and #202 — Observe knew the room, Orient overwrote it to workspace, and
semantic resolution was lost — is what that costs. Settle it before the first
line of code.

## 2. The body has a shape, and the shape is not a list of topics

A slot table (`self_name`, `her_role`, `register`, …) was drafted and rejected
during the session, because it is the same enumeration error as
`participation.md`'s five shapes, one layer down. A room's culture does not
arrive in four named flavours. Nobody could have declared a `her_role` column
in advance and had 「秘書」 land in it.

The shape is therefore over **the kind of claim**, not the subject:

| section | answers | how it is checked |
|---|---|---|
| `stable` | what is durably true here — register, tempo, how things get decided | re-run the same window: should produce something compatible |
| `shifted` | what changed since the previous revision | must name what it changed *from*; binds to `parent_revision_id` |
| `standing_in_it` | where she stands here | must be supported by `decision_log` rows, or it may not be written |

Three sections, all prose, three different falsification routes. Nothing is
about a topic, so nothing unforeseeable is excluded.

The worked example, from the room that prompted this bundle:

```
stable:          廣東話短句，鬧得緊要。有人提議就有人接，冇正式 RSVP。
shifted:         「六七小隊」改口叫「六七集團」，並且開始講「我哋有秘書」。
standing_in_it:  我喺度出過 3 次聲，兩次有人接。佢哋開始當我係入面一份子 ——
                 「秘書」好大機會係指我。
```

The third section is the difference between an observer and a participant, and
it is only writable because `decision_log` is in the substrate. Without her own
acts, every sentence she can write about a room is third-party.

## 3. Provenance goes around the prose, not inside it

The repo already solved this. `note_revision`:

```
body                text     -- free prose, 32000 chars, not decomposed
parent_revision_id  uuid     -- the chain; nothing is silently overwritten
established_by      text     -- who wrote it
instruction_sha256  text     -- WHICH VERSION of the instruction wrote it
model               text     -- which model wrote it
written_at          bigint
```

Notes are semantic descriptions — `title`, `description`, `uncertainty`,
`closing_condition` are all free text — and they are CMS-clean anyway. Being
prose was never the problem. `standing`'s problem is that it is prose *with no
history and no source, overwritten in place*.

`instruction_sha256` is the load-bearing one: it makes "did the descriptions
get worse after I edited the objective?" a question with an answer.

## 4. The store

```
culture
  key            text        -- room: "<platform>/<conversation_id>"; workspace: "<workspace>"
  scope_kind     text        -- 'room' | 'workspace'   (CHECK, closed set)
  head_revision_id uuid
  updated_at     bigint
  PRIMARY KEY (scope_kind, key)

culture_revision
  id                 uuid
  culture_key        text
  scope_kind         text
  body               jsonb   -- the three sections
  parent_revision_id uuid
  written_at         bigint
  established_by     text
  instruction_sha256 text    -- CHECK ~ '^[0-9a-f]{64}$'
  model              text
  support_from_id    bigint  -- the message range this was derived from
  support_to_id      bigint
```

`body` is `jsonb` with a CHECK that all three keys are present, non-empty and
under a per-section length cap.

**A missing section is an ERROR, not an empty value.** A revision that could
not produce `standing_in_it` must not enter the store as though she has no
position here. This is CMS's `ERROR is never a PASS`, expressed as a
constraint rather than as care.

`support_from_id` / `support_to_id` are the only addition beyond
`note_revision`'s pattern, and they exist to make one specific check possible:
**same window + same `instruction_sha256` + same `model` should re-derive a
compatible body.** If it does not, the tier is noise. This is not optional
rigour — the gate suite already measured these cheap models scoring 0.20–0.50
recall on an *identical* prompt. Without this check there is no way to tell
"the room changed" from "the model felt different today".

## 5. The trigger

**Dreamer clock plus a watermark of new messages since the last revision.**
Not turn-completion.

`standing` is the argument: it fires on tag-turn completion, so three of six
rooms are stale by more than five days, and they are stale in exactly the
untagged rooms ambient participation exists for. A tier consumed by the
participation path must not be driven by the tag path.

Because every write is a **new revision** rather than an in-place update,
"when does it fire" is a low-stakes decision — firing early or late adds a
revision, it does not corrupt state. `standing` overwrites, which is what makes
its trigger a high-stakes one. Do not over-tune this.

Freshness is fail-closed on read: a head revision older than the tier's TTL
renders as **absent**, never as an empty or stale block. `room_portrait` has no
TTL today, which is why a row from 2026-08-29 would render as though current.

## 6. What each scope reads

**Room culture** — `messages` for the arithmetic (quiet-window distribution,
answer latency, unanswered fraction, who opens and who closes, active hours),
plus `decision_log` for her own acts.

**Workspace culture** — `note` (what is open, what anchored and passed) and
`decision_log` across the workspace's rooms.

`decision_log` is read **only by the synthesizer**, never by the gate. This is
what keeps DEC-179 intact:

> `RelevanceGate` neither writes nor reads, deliberately … «admission control
> and nothing else»

and its stated reason — that a `kind="proactive"` row carries
`payload["note_title"]`, smuggling *"does Nora hold something to add?"* into the
gate under a new label — is untouched, because the gate receives a folded O(1)
block exactly as it already receives `standing`, not a decision feed.

`gate`, `go` and `no_go` are **not** reintroduced to the `decision_log`
vocabulary. They were removed from the 0053 CHECK constraints and
`test_the_trigger_records_nothing_to_a_decision_log` reddens a reintroduction.
Nothing here fights that.

### Reading her own acts is not self-attestation

The distinction matters and was got wrong once during the session:

- **Self-attestation** — "I judged `no_go`, therefore `no_go` was right."
  Circular. This is what `PARTICIPATION_DECISION_WRITERS` excludes, correctly,
  on the *live per-window* read.
- **Self-observation** — "I spoke here three times last week; twice someone
  answered." Not circular: the judgement comes from the room's response. She
  wrote the act; she did not write the consequence.

The join to consequence is what makes it grounded. `decision_log` alone would
be circular; `decision_log × messages` is not.

## 7. Cold start

`participation` has zero rows, because the gate has never admitted. The
`standing_in_it` section therefore has almost nothing to stand on at first —
only `tag` (3 rows) and `proactive_decide` (7).

This is not a deadlock: the tag path acts continuously and accumulates. But
v1 must render `standing_in_it` honestly when the evidence is thin rather than
inventing a position, and the section's own rule — no `decision_log` support,
no sentence — is what enforces that.

## 8. What is written in Phase 1 but read in Phase 2

The room's self-naming and register need a model call. Phase 1 does not
consume them: answering a route question needs notes and standing, not slang.

They are written from day one anyway, because lore cannot be back-filled
cheaply and Phase 2 would otherwise start cold. One small batched call per
room per cycle — at most daily — buys months of accumulation by the time
anything reads it.
