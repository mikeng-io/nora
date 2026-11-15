# Context

A pass's context is what its model is shown: four layers, built the same way
in both loops through [context assembly](../context-assembly/README.md). A
block is named for what it presents, never for the store it came from.

## Four layers

| layer | is | decided by |
|---|---|---|
| **floor** | what the pass needs regardless of memory | the occasion's declaration; always present |
| **prefetch** | one [retrieval](retrieval.md) from the pass's anchors, returning a bounded subgraph | code |
| **presentations** | that subgraph rendered as a few fixed block types | code |
| **pull** | read tools running the same retrieval from anchors the model chooses | the model, for depth |

An occasion declares three things: its anchors, its presentations and its
bounds. Passes differ only in those.

## Presentation blocks

| block | presents |
|---|---|
| **profile**, one per subject among the anchors | the claims about that entity — a person present, the room, the unit, Nora — in its [parts](claims.md) |
| **Standing** | Nora's current position in the room: open intentions with their children, recently settled ones with their outcomes, and the situation claims about where the conversation stands |
| **lessons** | the claims about Nora's practice in the unit |
| **related** | what the walk reached from the anchors' seeds: items and claims, by distance |

**Rows, not a graph dump.** Each row carries its id, kind, basis, time and
state, and its edges as references to other rows' ids. The model reads meaning
by block and can follow any id with a read tool. One renderer per kind renders
a row the same way in every block and occasion.

**Order within a block** is computed, never a model-made score: distance from
the anchor, then state (due, then open, then closed), then recency.

**Order between blocks** follows how often they change: steady blocks (Nora's
profile, lessons, members, the room's profile) first, so the cached prefix
holds; Standing and related last.

## Per pass

| | called, ambient | proactive | debrief | wake | review |
|---|---|---|---|---|---|
| floor | doctrine; the message and what it quotes, or the room window; transcript; members; clock and presence; spoken-turn framing; the tool catalogue; the room's computer, documents and sub-agent state | doctrine; the due intention, what it rests on and serves, and why now; transcript; members; clock and presence | the pass's items and recorded outcome; the room's messages since the last debrief | doctrine; the agenda; clock; members; transcript; rooms; read scope; the agenda rooms' document index; lesson evidence: corrections and closed response episodes past the lessons cursor | the rows due for verification or grading, their current revisions, later evidence and source health |
| presentations | profile per subject, Standing, lessons, related | profile per subject, Standing, lessons, related | Standing | profile per subject, Standing, lessons with their handles, related | none |
| pull | read tools | read tools | none | read tools | read tools |
| settles on | its reply outcome; then the debrief | its reply outcome; then the debrief | its admitted operations | its own commits | its grades and verdicts |

## Choosing what is shown

Code prefetches from anchors it can resolve without interpreting meaning: the
message, what it quotes, its people, its room. A reference code cannot resolve
— "that place we went last month" — is the model's to pull. No separate model
chooses what is retrieved, and no pass begins with a mandatory query round.
Context assembly's optional exposure filter may still act after retrieval,
under its own rules; it never owns retrieval.

**The reading that would reopen this**: measured passes where prefetch would
have found what the answer needed, the model did not pull it, and the answer
was wrong.

## Today's blocks

| today's block | becomes |
|---|---|
| `self`, `peer_card`, `character`, `culture`, `unit_culture` | profile, one per subject |
| `notes`, `memos`, `loop_decisions`, `standing` | Standing |
| `lessons` | lessons |
| `recalled`, `peer_history` | related |
| `people_in_window` | profile and related for each speaker in the window: what is remembered about them, not who they are |
| `members`, `presence`, `transcript`, `spoken_turn`, `replying_to`, `tag_message` | floor |
| `computer`, `documents`, `subagents`, `tool_catalogue` | floor, owned by their own subjects |
| Cognition's `members`, `agenda`, `transcript`, `rooms`, `clock`, `reads`, `documents`, `lesson_corrections`, `lesson_episodes` | floor of the wake pass |
| Cognition's `standing`, `lessons`, `held_lessons` | Standing; lessons with their handles |
| Cognition's `profile`, `graph`, `notes`, `memos`, `topics`, `events` | profile, related and Standing |
