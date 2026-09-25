# 08 — Transcript time: a readable time on every row, and `before`

Extends `07-room-tools-plan.md` to `load_transcript` as well as `room_read`,
because both tools render through one function (`_transcript_messages`,
`nora/agent/tools.py`) and a change to one is a change to both. Written
2026-09-25, before any code.

## The question it answers

«Check the event details we discussed yesterday in Signal», asked on Discord.
She has the route: `room_list`, then `room_read("Signal")`. Two things stop it
from working.

## §1 Defect: a transcript row's time is not readable

`_transcript_messages` returns `"ts": r.ts`, epoch **milliseconds**
(`TranscriptRow.ts: int`), and `edited_ts` the same way. The prefilled
transcript renders the same rows through `render_messages` with
`age="13h ago"` (`nora.domain.prompt.age`). So the model gets a readable time
for the rows she did not ask for and a 13-digit integer for the rows she went
and fetched, for the exact question those rows are fetched to answer.

This is the failure `age()`'s own docstring was written for («every message
looked equally recent, which is what made 今晚 and 琴日 unanswerable even with a
clock in the header») and the one the Relevance Gate measured (error rate cut
by two thirds when an age was rendered). The tool path never got the fix.

It is also R-18's floor. `prefill.py` keeps `ts` off the wire on purpose («the
real handles never reach the model… it is why the index and not the timestamp
is rendered»). Both tools hand the raw handle back.

**Fix.** Replace `ts` with `when` and `edited_ts` with `edited`, both as
`"2026-09-24 Wed 21:14 (13h ago)"`, rendered by one formatter,
`nora.domain.prompt.stamp`, beside `age`. `room_list`'s `last_message` uses it
too. It gives the calendar day and weekday for «yesterday» and «last
Tuesday», and an age for «is this still current», in one string (16 cl100k
tokens, against the raw integer's 5).

- **Both parts, not relative or ISO alone** (operator, 2026-09-25). Relative
  answers freshness. «13h ago» read at 10:00 is yesterday only after
  arithmetic across midnight. ISO costs the same 16 tokens, carries no
  weekday, and is a machine format.
- **No zone on the row.** The clock line names it once per turn.
- **The age is `age`'s wording** («13h ago»), so the prefilled transcript and
  a tool row agree. `room_list`'s own «13 hours ago» (`_ago`) is gone.
- **Only these three tools.** The model reads at least six other time forms:
  proactive's «2 h» and «24 Sep 21:14», note candidates' `… HKT`, and ISO on
  reminders and events. Each moves on its own with its own suite, because
  several of those prompts are graded (`age`'s docstring says so for
  proactive). Moving them all at once would leave no suite able to say which
  change moved a number. Nothing reads `ts` back
from a tool result: `send_reply(reply_to=)` takes the prefill's index, not a
timestamp. The `load_transcript` edit note that names `edited_ts` changes with
it.

**Read back live, 2026-09-25.** In Langfuse, 13 `load_transcript` calls
between 09-11 and 09-25 each carried `ts` as a bare 13-digit integer
(for example `1788289654479`), and `edited_ts` the same way. `room_read`: 0
calls in that window (HTTP 200, `totalItems: 0`).

## §2 Gap: nothing reads a time window

Both tools return the newest `limit` rows (default 25, max 60) and nothing
else. When a room has said more than 60 things since yesterday, yesterday cannot
be reached. `search_memory_workspace` is the only fallback, and it is semantic
recall over Honcho, not a read of the record.

**Change.** One optional argument on both tools:

```python
async def load_transcript(limit: int = 25, before: str | None = None) -> dict
async def room_read(room: str, limit: int = 25, before: str | None = None) -> dict
```

- `before`: HKT, `"YYYY-MM-DD HH:MM"`, or `"YYYY-MM-DD"`, which means the end
  of that day. Returns the `limit` newest rows sent before it, oldest first,
  in the same shape as today. Omitted, or at or after now: today's behaviour,
  byte for byte.
- **Inclusive of the named minute** (`ts < minute + 60s`). Paging passes the
  oldest row's `when` back as `before`. At minute resolution an exclusive bound
  would skip rows that share the oldest row's minute, so the bound errs toward
  a repeated row rather than a gap.
- **A "more before this" signal.** `recent()` already over-fetches by one
  (`nora.domain.bounds`), so the tool knows when the window was full. Only
  then does the result carry a `note`: there is more before the first row, and
  to go further back, call again with `before` set to its `when`. The
  description stays short, and the note is paid for only when it applies (ADR
  0015 §3).
- **A malformed `before`** never silently falls back to "newest". That would
  answer a question about yesterday with today's messages, and it would look
  like an answer. `room_read` (`three_state`) returns `unavailable` with a
  note giving the format. `load_transcript`'s contract is `messages` and has
  no status, so it returns `messages: []` with that note. Giving it a status is
  a contract change and is not part of this doc.
- **Budget.** Unchanged: `load_transcript` 4 and `room_read` 3 calls per turn.
  `before` jumps straight to the time in question instead of walking back to
  it, so reaching yesterday costs one call however busy today was. Paging
  depth stays bounded by the budgets (4 × 60 and 3 × 60 rows).
- **Store.** `RecordStore.recent(conversation_id, limit, before_ts=None)`: one
  added predicate on the same `idx_messages_conv_ts` seek. `SqlRecordStore` and
  `FakeRecordStore` both get it, with one contract test
  (`tests/contract/test_audit_store_contract.py`).
- **The clock.** "Now" comes from the same `now_fn` `room_list` uses, and HKT
  from `_HKT`. The model already sees «Right now it is …» on every turn
  (`prefill.state_time`), which is what lets it turn «yesterday» into a date.

### Why `before` and not `date`

`where_been` takes `date: "YYYY-MM-DD"`, and copying it was the first idea.
It fails on the case this is for: a busy day holds more than 60 rows, so a
`date` argument needs a second, cursor-like argument to reach the rest, and
then it is `before` with extra steps. `before` is one argument that answers
«yesterday», «this morning» and «further back» the same way, and it is the
shape Slack's `conversations.history` (`latest`) and Discord's message history
(`before`) use. Recalled from those APIs, not re-fetched for this doc.

### Rejected

- **A `room` argument on `load_transcript`, retiring `room_read`.** The same
  unit, the same renderer and one less tool. But `07` measured the routing
  clean («what are they saying on Signal» goes to `room_list` then
  `room_read`, never `load_transcript`), and the unit boundary (unknown room
  and another unit's room produce identical results) would move into the tool
  called on nearly every turn. Split versus unify is decided by a
  `tool_selection` arm (ADR 0015 §1), and there is no misroute to justify
  running one.
- **Since/until pairs.** Two arguments where one does it.
- **Raising `LOAD_TRANSCRIPT_MAX`.** Its measured cost is recorded beside the
  constant, and a bigger window still has an end.

## Tool text (ADR 0015)

Both tools keep their names, and both keep their levels (`load_transcript` 1,
`room_read` 2). Each docstring gains the argument under `Args:`. The only
change above `Args:` is a clause in each opening saying they can read from an
earlier time, so a question about a past day routes here and not to
`search_memory`. That clause is a first-paragraph change, so routing is
measured (below). Measure the level ceilings with the skill's tiktoken
one-liner, and run the humanizer over both openings.

## Sequence

1. **§1 first, alone.** A render fix with no argument change. Tests:
   `_transcript_messages` rows carry `when` and never `ts`, and the same for
   `edited` and `edited_ts`. Prove it by reverting (`nora-prove-fix`). Ship
   it.
2. **§2**: the store predicate and contract test, both tools, the note, the
   parse. Tests: paging across a shared minute repeats a row and never skips
   one; a malformed `before` never returns newest rows; an omitted `before`
   gives today's result.
3. **Measure** (`nora-eval`), before and after on the deployed alias:
   - `tool_selection`: add a case, «what did we decide yesterday» in a room
     whose prefill cannot reach yesterday, expecting `load_transcript` and not
     `search_memory` in `tools`. Add the same on another network, expecting
     `room_list` then `room_read`.
   - **Check the eval can reproduce the input first.** The case needs a
     sandbox room seeded with more than 60 rows after the target discussion,
     with real timestamps. If a case state cannot seed that, the case proves
     nothing (#454). Say so and verify live instead.
4. **One real call after deploy**: a `room_read` with `before` in Langfuse,
   with its input and result read back.

## Not in scope

- The workspace culture account (`design/culture-tier`, DEC-182, «not built»).
  It is tracked there.
- `reply_to` in tool rows naming only the speaker, not which message. It is a
  separate gap, and nothing here makes it worse.
