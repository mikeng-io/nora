# The adaptor

## 1. It is a Dreamer adaptor, and almost nothing else

The operator's description, which is the design:

> Culture tier 都係要存在，但我會形容佢只不過係 dreamer 嘅 adapter，亦即係
> memory tier 嘅一個 wrapper。

Everything it needs exists. `DreamAdaptor`'s own docstring is *"One Dreamer
objective: what to research, over what, **with which tools**, and how to turn
the answer into rows"*, and the runtime owns the loop, the tool ceiling, the
budget, its own doctrine, and R-18/R-28 screening.

| piece | where it already is |
|---|---|
| **a room-scoped dialectic verb** | **DOES NOT EXIST — this tier writes it (§1.1)** |
| `search_memory`, `web_search`, `web_fetch` | tool registry |
| agent loop, tool ceiling, budget | Dreamer runtime |
| watermark / volume / timeout trigger | `AdaptorSchedule` |
| `instruction_sha256`, `model`, `established_by` | **the runtime stamps these onto every `Proposal`** |
| R-18/R-28 screening of every string | runtime, before `accept` |

That last row deleted a whole column-set from the first draft: provenance is
not this tier's problem, it is the runtime's, and `accept` returns payloads
precisely so an adaptor never has to know its own `instruction_sha256`.

## 1.1 The one thing that does not exist: a room-scoped memory verb

An earlier draft of this file said "everything it needs exists" and named
`ask_memory`. **That was false**, and the reason is worth stating because it
also shows the fix is small.

`ask_memory` is **person-scoped by design and correctly so**. Its whole roster
dependency exists to turn a person's NAME into a peer id: `view.find(person)`
→ `matches[0]` → `chat(target=ref.honcho_peer_id)` (`tools.py:3123-3140`). A
scope-keyed adaptor is built with `Room(roster_source=None)`
(`dreamer.py:850`), so `RoomView.available` is `False` and the tool returns
`unavailable` on every call — forever, while the run still reports `ok`.

**That is not a blocker; it is the wrong verb.** Culture does not resolve a
name. It asks about a *place*. So this tier ships one new tool:

```
ask_room_memory(depth, query)      scope: this room     -> chat(target=nora, session_id=<this room's session>)
ask_workspace_memory(depth, query) scope: the workspace -> chat(target=nora)   (no session_id: workspace-wide is the default)
```

**Neither needs a roster**, because neither resolves a person — which is the
only thing the roster was ever for. `Room` already carries `_conversation_id`
(`room.py:160`), and that is what builds the session id. The `AdaptorGrant.
home_room` machinery, which is workspace-keyed and which this bundle's earlier
draft worried about deleting, is not on this path at all.

**Room scoping is real and measured, 2026-09-13.** `session_id` on `/chat` is
validated (a nonsense id is a 404) and genuinely narrows the corpus — asked how
many distinct people have spoken, the one-person room answers 1, the
seven-person room answers 7, a probe room answers 2. The scoping works; only
the verb was missing.

```
culture adaptor            DreamAdaptor, key_kind = "scope"
  schedule                 watermark / volume / timeout via AdaptorSchedule
  tools                    ask_room_memory -> the account (NEW, §1.1)
                           web_search   -> ground a reference the room assumes
  accept                   the prose
  commit                   propose a new revision, link parent
                           (no supersede — nothing here retires)
```

**Screening cuts in the right place for this tier.** R-18 blocks identifiers —
E.164, UUIDs, key-shaped strings, the model's own name — and not names or
nicknames. So an account carrying the room's in-group names survives intact
while a phone number never could, which is exactly the line culture needs.

## 2. What the model is asked

One question, per room, on the Dreamer clock, through `ask_room_memory`: *describe this room's culture as
a short account — how they talk, how decisions get made, what they call
themselves, my own standing here, and **why** it is like that; not dated
observations.*

The "not dated observations" clause is load-bearing. Without it the dialectic
drifts back toward `representation`'s shape, which is the chronicle this tier
exists to not be (`00-overview §3`).

`web_search` earns its place only because the pass is daily: when the room
leans on a reference nobody explains — a game, a meme, a place — the loop can
find out what it is, so the account can say why a name landed rather than
merely recording that it did.

## 3. The store

Two tables, deliberately thin, because the interesting content is prose and
the provenance arrives from the runtime.

```
culture             key, head_revision_id, updated_at
culture_revision    id, culture_key, body TEXT,
                    parent_revision_id,
                    <runtime provenance: instruction_sha256, model,
                     established_by, written_at>
```

**`body` is prose. There is no section schema.** The first draft required
three JSONB sections under a CHECK; that was the same enumeration error as
`participation.md`'s five shapes, one layer down, and it could not have held
「秘書」 — a role the room invented that no column could have anticipated.
`note_revision` is the precedent: free text up to 32000 chars, CMS-clean
because the provenance sits *around* the prose rather than decomposing it.

**No `retired_at`, no `closing_condition`, no TTL.** See `00-overview §5`. A
quiet room's culture is not stale; it is accurate. `parent_revision_id` reads
"the same thing, earlier", never "superseded".

## 4. The trigger

**`uses_watermark = True`, `min_interval_seconds = 86400`.**

```
fires = lag > 0 and age >= min_interval
```

New human messages in this room, and a day since the last fold. A silent room
does not fire, **and that is correct rather than a defect**: nothing here
retires, so a quiet room's culture is accurate, not stale, and there is nothing
to refresh it against. Re-running the dialectic with no new material yields
either the same account (a wasted 25s call) or a different one — and the second
is pure model variance polluting the one signal the revision chain carries.

An earlier draft had `uses_watermark = False`, borrowed from `note_curator.py:526`.
That was wrong. The curator needs it because **an anchor date passing IS the
event** — retirement is time-driven whether or not anyone speaks. Culture has no
equivalent clock event. The precedent does not transfer.

**`lag` excludes her own messages** (`language.py:130`, `is_nora == 0`), so she
cannot trigger her own refresh. The room triggers it; her acts still reach the
CONTENT through `decision_log`. Trigger and content are separate, and only the
trigger needs the guard.

### The watermark is a gate here, not a cursor — and conflating them costs weeks

`batch_limit` bounds how far one run advances the watermark, and a new adaptor
starts at 0. `runtime.py:508-513` names the trap: an adaptor with a new name
*"re-reads the room from the beginning, `batch_limit` rows at a time, paying a
model call per batch… slow, expensive, and looks exactly like a bug."*

At `min_interval = 86400` that is one batch per DAY. The busiest live room holds
1,362 messages; at `batch_limit = 80` culture would spend **17 days** walking a
backlog it never reads, burning a dialectic call a day to catch up to a present
Honcho already knows.

So:

- **`batch_limit = 1`.** Culture never consumes the batch — it asks Honcho,
  which holds the whole corpus already. The batch is dead weight.
- **Seed the watermark at the record HEAD on first run, not 0.** The mechanism
  exists (`runtime.py:515-516`, `advance_watermark(last_message_id=inherited)`).

The watermark answers *"has anything new happened"*. It is not this tier's
cursor into the transcript, because this tier has no cursor.

## 5. The block

The block vocabulary and its abstraction belong to #219/#220, not here. This
bundle declares the fields a culture block fills and pins no rendering.

Per that work (confirmed with the session doing it):

A culture block fills `ContextBlock`: `name`, `placement`, `order`, `note`,
`header`, `body`, `key`, `untrusted`.

- **`placement = "turn"`, `key = "room"`.** Not the cached head, and the
  reason is the correction that matters. Daily change is *not* the problem; a
  once-a-day prefix miss is nothing. The problem is that the static head is
  **shared across rooms**, so a room-keyed block in it fragments the ~13–17k
  prefix per room, permanently, against a 95–99% hit rate. `_notes_block` is
  already held out of the head for exactly this reason at workspace
  granularity, and the rule `BlockKey` now encodes is that **anything keyed
  narrower than `global` belongs in `turn`, whatever its cadence.**

  So **culture's daily cadence is a production win — one dialectic call a day
  instead of per turn — not a cache win.** That is the honest argument for the
  design, and it is the one this bundle originally got backwards.

- **`note`** and **`header`** are this tier's to write, because nothing else
  can: what the block *is*, in the second person, and its line in the context
  header's catalogue. A block shipped without them is the `<loop_decisions>`
  defect — served every turn with no entry telling her what she was reading.

- **The block asserts its own age.** `<standing>` carries `written=` so the
  model knows how stale it may be; culture must carry the same. This tier
  needs it more than standing does, not less: nothing here retires, so a head
  revision may legitimately be weeks old and still be correct — and a block
  claiming to say "how this room works" with no indication of when it was
  derived is R-27's trap written in prose.

**Never `injection` or `injector`.** The word already carries three meanings
here — prompt injection (R-14), dependency injection, and `TranscriptInjection`
(an assembler, not a block). The vocabulary is `block` / `block source`.

## 6. Who reads it, and the one thing #220 does not settle

The participation turn is a straightforward consumer.

The **gate** is not, and this must not be glossed. Adopting the shared row
renderer does not open the gate's block set: `render_context`'s *"every block
it learns to add would silently widen this one"* and
`test_the_prompt_holds_exactly_two_untrusted_blocks` are about which blocks
exist, and the refactor changes neither. Putting culture in front of the gate
is a decision owned by the gate, and #220 neither helps nor blocks it.

It is also the decision with the most evidence against it: #144/#147 measured
the gate getting **worse** when given more context, and #216 — an n=5 reading, not re-taken — reports the turn
behind the gate false-speaking on 4 of 5 silence twins. Adding a block
to the gate is therefore sequenced last and measured, not assumed — see
`02-measurement.md §5`.

That the gate is a new-tier's normal cost, rather than a constitutional bar,
is settled: every tier that ever landed added a block. The first draft of this
bundle mistook a pin for a prohibition.

## 7. Scope and keys

`key_kind = "scope"`, one adaptor. The first draft had two (room and
workspace); the workspace half is deleted, because workspace-wide recall is
what Honcho does by default and re-deriving it is the duplication the operator
flagged.

**Honcho workspace: `nora`, for every conversation.** `_workspace_for` returns
the router's answer *"or the single default when no router is wired (today,
every conversation)"*, and no router is wired. Nora's own policy workspaces
(`ridgeline` / `friends` / `wedding`, used by `note` and `decision_log`) are a
different axis and do not select a Honcho corpus. A Honcho workspace named
`ridgeline` exists and nothing routes to it.

**The store keys on `primary_id`**, and that is a consequence rather than a
choice: a Dreamer adaptor is handed `scopes()`, so `primary_id` is its key.
`standing` and `decision_log` key `(platform, conversation_id)` because the
ingest and tag paths are handed the message that arrived. `GLOSSARY.md` carries
the rule (PR #230) and DEC-181 is withdrawn accordingly.

A store keyed differently from its loop matches zero rows on the watermark
query, so the adaptor is scheduled, raises nothing, logs nothing and never
fires. That is why it is worth stating rather than leaving to whoever writes the
migration.
