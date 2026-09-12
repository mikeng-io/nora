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
| `ask_memory` — the dialectic `chat`, 17–30s | tool registry |
| `search_memory`, `web_search`, `web_fetch` | tool registry |
| agent loop, tool ceiling, budget | Dreamer runtime |
| watermark / volume / timeout trigger | `AdaptorSchedule` |
| `instruction_sha256`, `model`, `established_by` | **the runtime stamps these onto every `Proposal`** |
| R-18/R-28 screening of every string | runtime, before `accept` |

That last row deleted a whole column-set from the first draft: provenance is
not this tier's problem, it is the runtime's, and `accept` returns payloads
precisely so an adaptor never has to know its own `instruction_sha256`.

```
culture adaptor            DreamAdaptor, key_kind = "scope"
  schedule                 watermark / volume / timeout via AdaptorSchedule
  tools                    ask_memory   -> the account
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

One question, per room, on the Dreamer clock: *describe this room's culture as
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

`AdaptorSchedule`, on the Dreamer clock, keyed per room.

The one thing to get right is that a **time-driven pass must not require
backlog**. `runtime.py:703` is `fires = lag > 0 and age >= min_interval`, and
`runtime.py:635` already records the lesson — *"requiring `lag > 0` would make
a silent room's schedule never fire at all"* — with `note_curator.py:525`
already fixed for it (`uses_watermark = False`, because *"retirement is
TIME-driven, not evidence-driven"*).

Culture is the same class. A room that has gone quiet still has a culture, and
this is the defect this bundle criticises `standing` for; reintroducing it
under a different trigger would be the joke writing itself.

Because every write is a **new revision** rather than an in-place update,
firing early or late costs one extra revision and corrupts nothing. This is
genuinely low-stakes — unlike `standing`, which overwrites.

## 5. The block

The block vocabulary and its abstraction belong to #219/#220, not here. This
bundle declares the fields a culture block fills and pins no rendering.

Per that work (confirmed with the session doing it):

- **`placement`: the volatile tier, room-keyed.** Not the cached head — and
  the reason is the correction that matters. Daily change is *not* the
  problem; a once-a-day prefix miss is nothing. The problem is that the static
  head is **shared across rooms**, so a room-keyed block in it fragments the
  ~13–17k prefix per room, permanently, against a 95–99% hit rate.
  `_notes_block` is already held out of the head for exactly this reason at
  workspace granularity. **Culture's daily cadence is a production win — one
  dialectic call a day instead of per turn — not a cache win**, and that is
  the honest argument for it.
- **`note`** and **`header`** are this tier's to write, because nothing else
  can: what the block *is*, in the second person, and its line in the context
  header's catalogue. A block shipped without them is the `<loop_decisions>`
  defect — served every turn with no entry telling her what she was reading.
- The value set of `placement` is still moving; this bundle says "volatile
  tier, room-keyed" in prose and lets the field land.

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
the gate getting **worse** when given more context, and #216 shows the turn
behind the gate false-speaking on 4 of 5 silence twins today. Adding a block
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

The store's own key is DEC-181 and is not answered here. The scope scheduler
hands a bare `primary_id`; `standing` and `decision_log` key rooms as
`(platform, conversation_id)`. A store key that does not match the loop key
makes the watermark match zero rows and the adaptor never fire — silently, no
error, no metric.
