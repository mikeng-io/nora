# 40. Proactive delivery: answer where the note was born (issue #248)

## Supersedes

D3's «the row's destination is the home room the tool source bound»
(`nora/agent/dreamer.py`, `conversation_id=home.conversation_id ... passed
to `build_tool_specs`), for **note-linked** proactive rows only. D2's
home-room-as-where-she-stands is unchanged: the home room is still the
roster, the self_ids, the location trust, and the room-scoped reads.
Only the WRITE verb's destination changes.

## What was wrong

2026-09-14, Blake asked for the night off in **Discord Zone 42**
(`100000000000000201/100000000000000202`). The note-candidates pass
folded it into a friends-workspace note with `established_in = Zone 42`.
~2h later the proactive fire path spoke into **Signal Friends** — the
workspace's home room (`policy.toml` L93-94) — and the person the answer
was about never saw it. The answer was delivered where Nora stands, not
where the question was asked.

Two separable facts were conflated:

1. **Where the note was born** (`Note.established_in`) — Zone 42 Discord.
2. **Where the adaptor stands** (`home_room`) — Signal Friends.

## The decision (operator-approved 2026-09-15)

A proactive row **linked to a note** fires into the note's
`established_in` room — **not** the home room — when that room is
proactive-trusted. Otherwise the row is refused, fail-closed, and it
NEVER falls back to the home room (a fallback is the bug).

- **Note-linked only.** A proactive decision not tied to a note keeps the
  home-room destination — the note link is what carries the provenance
  of «where this came from»; without it there is none.
- **Room resolution.** `established_in` is a conversation id without a
  platform. Resolve it through a new policy lookup: scan all
  conversations for one whose `ids` contains the id (primary + `also_ids`
  — the same id-matching rule `Conversation.conversation()` applies for a
  known platform). A room that does not resolve is a refused row
  (`origin_unresolvable`), never a guess.
- **Trust check.** The resolved room must be `proactive = true` in
  `policy.toml`. `ProactiveScreen` already re-checks the ROW's
  platform/conversation at fire time (`proactive_screen.py:102`), so the
  row carries the origin room and the screen enforces the same flag.
- **Workspace check.** The resolved room must belong to the same workspace
  the adaptor is keyed on. Cross-workspace origin is refused
  (`origin_wrong_workspace`) — a note born in another social unit is not
  something this pass may raise into.
- **`policy.toml` change.** Zone 42 gains `proactive = true` (operator
  decision, 2026-09-15): the friends actually plan there, and the
  incident was a plan raised there. The Ridgeline test room and Signal
  Friends keep it. A foreign-born note whose room is not
  proactive-trusted refuses — the operator's flag is the gate.

## Why this is not #199

#199 (closed) was the standing-debrief fold writing a room position from a
turn outcome on the *standing* path. This is the proactive fire path
writing a row built by the Dreamer. Same family — «a turn speaks into a
room its content did not come from» — different writer, different row
shape, different fix path (`remind` destination stamping).

## Build map

| change | file | what |
|---|---|---|
| `established_in` into the note-linked payload | `nora/agent/tools.py` (`remind`, payload build ~:6906) | `linked.established_in` is already in scope from `_resolve`; add it beside `note_id`/`revision_id`/`note_title` |
| conversation-by-id resolver into the tool build | `nora/agent/dreamer.py` `RegistryToolSource` → `build_tool_specs` | a `conversation_for(conversation_id)` callable scanning policy ids (mirrors the `workspace_for` plumbing — same shape, same refusal when absent) |
| destination stamping | `nora/agent/tools.py` (`remind`, `row = Reminder(...)`) | proactive + note-linked: resolve origin; refuse on unresolvable / wrong workspace / not proactive; else set `platform`/`conversation_id`/`conversation_type` from the origin room; unlinked keeps home room |
| `proactive = true` | `policy.toml` | Zone 42 |
| `proactive` flag on the resolved room | `Conversation.proactive` (already exists, `policy.py:112`) | read by `conversation_for` — no schema change |
| tests | `tests/unit/test_remind_destination.py` (new) | note born in same-workspace proactive room → origin; born in untrusted room → refused, home is NOT used; unresolvable id → refused; no note → home preserved; `also_ids` match resolves |

## Fail-closed behaviour, said plainly

- The row is refused before `reminders.create` — no row, no turn, no
  speech. The decide turn sees the refusal (`refused_origin_*` statuses
  on `nora_reminders_created_total`).
- The screen at fire time is unchanged and still applies (`proactive_allowed`
  on the row's own platform/conversation) — defence in depth, not a second
  decision.

## Open / not in this change

- A note whose `established_in` room is not in `policy.toml` at all: the
  row refuses today; whether such notes should be raised by tagging the
  home room with an origin notice is a separate question (leave closed).
- The same origin-routing for the EVENT promotion path (`notes.py`
  «routing key» for `Event`) is already the note's own rule and unchanged.
