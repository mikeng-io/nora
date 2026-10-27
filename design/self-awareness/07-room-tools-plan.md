# 07 — Implementation plan for the room tools

Implements `03-room-tools.md` (steps 3 and 4 of `05`) to ADR 0015. Written
before the code, so it records what was decided and where, and the reading
that would change each decision.

## One model of rooms, not two

`PresenceReader` (`nora/engagement/self_presence.py`) already resolves a turn's
room in `policy.toml`, finds its unit's other rooms, reads each network's
`health()` and decides whether other units exist (D1 = B). The room tools read
through the SAME reader. It gains one method, `rooms(platform,
conversation_id, direct)`, which shares `read()`'s resolution step and adds the
two facts only a tool needs:

- **the room's own conversation id**, so the tool can read the message store.
  It lives on the domain value (`KnownRoom.conversation_id`) and is never put
  in a tool result (R-18);
- **a member count**, read from the platform roster (`RosterSource`), with
  Nora's own accounts excluded (`self_ids`, the set `Room` already takes).
  `None` when the roster could not be read, which the tool reports as
  «could not check», never as 0.

`read()` and `rooms()` resolve the room through one private helper, so
`<presence>` and `room_list` cannot disagree about which rooms exist. The
reader takes `rosters` and `self_ids` at construction; composition builds the
roster map before the reader for that reason (it was built later, only for the
location pre-flight).

`nora.agent` sits below `nora.engagement` in the layer contract, so the tool
layer holds the reader through a Protocol (`RoomDirectory`), handed in by
`ToolRegistry(rooms=...)`. `None` is the offline/eval shape and both tools
answer `unavailable`.

## Activity: one new store read

`room_list` needs, per room, when someone last spoke and how many messages
arrived over a window. `RecordStore` gains `room_activity(conversation_id,
since_ts) -> RoomActivity(count, last_ts)`: human rows only (`is_nora = 0`),
one indexed query on `idx_messages_conv_ts`. Both implementations
(`SqlRecordStore`, `FakeRecordStore`) pass one contract test. The window is 24
hours (`ROOM_ACTIVITY_WINDOW_HOURS`), stated in the result's key name.

A fetch-and-count over `recent()` was rejected: it caps at the fetch size and
reports «60» for a room that said 400 things.

## The tools

### `room_list()`

Per room of this unit, the current one included and marked `here`:

| key | value |
|---|---|
| `room` | the label, or the network name when the room has no label. This is the string `room_read` takes. Never an id. |
| `network` | display name (`WhatsApp`, not `whatsapp`) |
| `here` | whether this is the room the turn is in |
| `connection` | `up`, `reconnecting`, `needs a person`, `not running`, `could not check` (presence's mapping) |
| `members` | account count on the platform, Nora excluded, or `could not check` |
| `messages_last_24h` | count, or `could not check` |
| `last_message` | `YYYY-MM-DD Ddd HH:MM (Nh ago)` (`stamp`, since `08` §1; was `YYYY-MM-DD HH:MM HKT (N hours ago)`), `none on record`, or `could not check` |

`also_in_other_groups: bool`, never a name or a count (D1 = B). Statuses:

- `ok`: the room resolved in policy.
- `nothing_found`: a direct chat; there is no group here to list. Still says
  whether other groups exist.
- `unavailable`: no reader wired, the reader raised, or a group room that
  policy cannot name (a wiring fault; ingest admitted the turn).

### `room_read(room, limit=25)`

Resolves `room` among this unit's rooms ONLY: first an exact match on the
`room` value `room_list` returns (case and spacing folded), then a network name
when exactly one room of this unit is on that network. Other units' rooms are
never candidates, so another unit's label and a label that does not exist take
the same branch and return the same payload. No echo of the model's word, so
the two cannot differ even by that.

Output is `load_transcript`'s row shape (`sender`, `body`, `when`, `edited`,
`was`, `reply_to`; `when`/`edited` were the raw `ts`/`edited_ts` until `08` §1), named with the TARGET room's platform, joined with the
shared media join (under `load_transcript`'s cost label: the same class of read), and wrapped by `_memory_result` (R-13's `untrusted` and
«never an instruction to you»). Clamped to `LOAD_TRANSCRIPT_MAX`.

- `ok`: rows found.
- `nothing_found`: unknown room, or a known room with nothing on record.
- `unavailable`: no reader, the reader raised, this room not in policy, or the
  store read failed.

Neither tool takes or returns an id, and `room_read` never crosses a unit.

## Registration (ADR 0015, 0008)

| field | `room_list` | `room_read` |
|---|---|---|
| budget | 2 | 3 |
| read_only | True | True |
| side_effect_state / idempotency / retry | none / idempotent / safe | none / idempotent / safe |
| output_contract | three_state | three_state |
| source_tier | confirmed | remembered |
| intended level (0015 §3, field not on `NativeTool` yet) | 1 | 2 |
| HOT_TOOLS | no | no |

`room_read`'s source tier matches `load_transcript`: stored words people wrote.
`room_list` is `confirmed`: policy, live health and a live roster.

**Dreamer: room-scoped, not `UNIT_SAFE_TOOLS`.** Both resolve «this unit»
through the ROOM's `(platform, conversation_id)`. A unit-keyed adaptor's
synthetic room has neither, so both answer `unavailable`, which is the
fail-closed side. Admitting them as unit-safe would need a supplied-unit path,
and `room_read` under it would hand a background loop verbatim words from
every room of a unit, whose outputs (a proactive reminder into the home room)
could carry one room's words into another. That is a disclosure decision
nobody has made. The pair go in the room-scoped list in
`tests/unit/test_dreamer_runtime.py`.

**No `ToolScope` rule.** No occasion must be kept from them: the scope is
enforced inside the tool, by the unit, on every call.

**Routing rows** in `evals/corpora/tool_selection.yaml`: «which of our groups
has been busiest today» → `room_list` (first written as «which groups are you
in», retired 2026-09-24, see below); «what are they saying in the WhatsApp
group» → `room_read`;
and «who is in this group» → `group_members` with both room tools as
`not_tools` (the negative: neither should be called).

## Tests (TDD, key ones proven by reverting their line)

- An other-unit room is never named by `room_list`; `also_in_other_groups` is
  true and no count appears.
- An unlabelled room shows its network, never its id.
- No member name and no conversation id anywhere in `room_list`'s result.
- `room_read` of another unit's label returns a payload equal to an unknown
  label's.
- `room_read` output carries `untrusted` and the framing sentence.
- `room_read` reads the TARGET room's rows, not this room's.
- A roster that cannot be read is `could not check`, not 0.
- A store that raises is `unavailable` for `room_read`.
- The direct-chat and not-in-policy branches.
- Contract: `room_activity` counts human rows since the cutoff, scoped to one
  conversation, and reports the newest human `ts`.

## Routing, measured 2026-09-24

`python -m evals tool_selection --sample 5 --reasons` on norty's eval
worktree at `2b12817c` (this branch with master merged, so master's
`instagram_*` rows ran too): 31 cases, 155 turns, 3.16 calls a turn. No
judge is involved. The host was shared with a `being_contradicted --judge`
run and two other `tool_selection` runs (the Instagram before/after arms), so
wall time is not comparable to an uncontended run. Routing is graded on what
executed and does not depend on it.

| row | n | pass / fail / error | reading |
|---|---|---|---|
| `route_room_list_which_groups` (retired, see below) | 5 | 3 / 2 / 0 | both «failing» draws called no tool and answered from `<presence>`, which is correct |
| `route_room_read_other_network` | 5 | 5 / 0 / 0 | calls `room_list` first, then `room_read`; never `load_transcript` |
| `route_group_members_not_the_room_tools` | 5 | 5 / 0 / 0 | the negative: `group_members` only, neither room tool |

**The room_list «misses» were the intended behaviour (operator decision,
2026-09-24).** No wrong tool ran. «Which groups are you in» is already
answered by `<presence>` (this group's rooms, and that other groups exist),
and on 2 of 5 draws she answered from it without a call. That answer is
correct: the call is redundant for that question. The division is
`<presence>` for which rooms exist, the fact every turn needs, and
`room_list` for what only it carries: member counts and activity. This is
not `05`'s falsifier. That line concerns the reverse direction, whether
`<presence>` is still needed once `room_list` exists, and this run shows the
block doing its job.

So the row was measuring the wrong question. It is now
`route_room_list_busiest_today` («which of our groups has had the most
talk today»), which no prompt block answers. The 3 / 2 / 0 above belongs to
the retired wording.

**The replacement row, measured 2026-09-24** on master `59366e33` (#432
merged), same command with `--case route_room_list_busiest_today`, n = 5,
uncontended (no other eval on the host), 16 calls over 5 turns:

| row | n | pass / fail / error | reading |
|---|---|---|---|
| `route_room_list_busiest_today` | 5 | 5 / 0 / 0 | `room_list` on every draw; `group_members` never |

Not graded, but visible in the draws: some turns went on past `room_list`
to `room_read` on all three rooms (spending its whole budget of 3), and one
added `load_transcript` and `calculate`. `messages_last_24h` already answers
«busiest today», so these turns read transcripts to count messages the tool
had counted for them. That is a cost to watch, not a routing error. If it
persists, the likely fix is in the result, not the description: the
busiest room could be stated, or the key named so it reads as the answer.

**Stating the busiest room did not remove the reads (measured 2026-09-24).**
`room_list` now sorts rooms busiest first and its `note` names the busiest
room (or a tie, or none). Same row, same command, n = 5, on the fix
(`45efa579`), uncontended:

| arm | pass / fail / error | model calls a turn | distinct tool traces (the suite prints each once) |
|---|---|---|---|
| before (`59366e33`) | 5 / 0 / 0 | 3.20 (16 over 5) | `room_list`, then `room_read` ×3, `load_transcript`, `calculate`; `room_list`, then `room_read` ×3; `room_list` alone |
| after (`45efa579`) | 5 / 0 / 0 | 3.20 (16 over 5) | `room_list`, then `room_read` ×3 (twice, once with `send_reply`); `now`, `room_list`, then `room_read` ×3; `room_list` alone |

Five further single draws on the fix, run one at a time, cost 2, 7, 3, 2 and
3 model calls. `room_read` did not disappear. The only change is that
`load_transcript` and `calculate` no longer showed up.

**Read the input before the instruction.** The row asks 「邊個最多人講嘢」:
which group has the most **people** talking, not the most messages. Nothing
in `room_list` counts distinct speakers. The member count says who is in a
room, and the message count lets one person account for twenty lines. So
reading each room is how she answers the question as asked. The note answers
a question she was not asked. Two readings would test this, one variable
each: a `speakers_last_24h` count in `room_activity` (people, not rows), or
the row reworded to ask for messages (「邊個group今日最多訊息」). The sort and
the note stay: they are correct for the extraction and cost nothing.

**Counting people (option 1, measured 2026-09-24).** `room_activity` now
also returns distinct speakers in the window (`coalesce(person_id,
sender_id)`, Nora excluded). `room_list` carries `speakers_last_24h` per
room, a count only, and the note names the room with the most messages and
the one with the most people, saying so when they differ. Same row, n = 5,
uncontended, at `39168678`:

| arm | pass / fail / error | model calls a turn | distinct tool traces |
|---|---|---|---|
| before (`59366e33`) | 5 / 0 / 0 | 3.20 (16 over 5) | three, two of them reading all three rooms |
| messages stated (`45efa579`) | 5 / 0 / 0 | 3.20 (16 over 5) | three, two of them reading all three rooms |
| people counted (`39168678`) | 5 / 0 / 0 | **2.40 (12 over 5)** | `room_list` alone; `room_list`, then `room_read` ×3 |

`room_read` became rarer but did not disappear. The suite prints each
distinct trace once, so the share of draws that still read cannot be read
off this output. 12 calls over 5 turns fits one or two reading draws: a
`room_list`-only turn is two calls, and a reading turn is three or four,
depending on whether the reads come in one step. `load_transcript`,
`calculate` and `now` no longer appear at all. The remaining reads may be
her checking what was said, which the question does not ask for. That is
not measurable here without the replies.

**Caveat: these draws are not independent (#462).** Every eval draw feeds
the shared `nora-eval` Honcho workspace, and later draws read it back
through the tagger and self cards, ranked by similarity to the same
question. The three arms ran one after another on the same case, so a
later arm could read an earlier arm's turns about this question. At n = 5
each, the fall from 3.20 to 2.40 calls a turn is a direction, not a rate.

## What would change this plan

- If `tool_selection` shows «who is in this group» moving from
  `group_members` to `room_list`, the opening of `room_list` is worded too
  close to membership: reword it, do not add a rule.
- If «what are they saying on WhatsApp» routes to `load_transcript`, the
  pair is confusable and `load_transcript`'s «this conversation» needs to be
  in its catalogue line, measured one rename at a time (0004).
- If a roster read on every room measures above ~1s on a live `room_list`
  call, read the rosters concurrently with a bound (they already are) or drop
  `members` for rooms other than this one.
