# 06 — Implementation plan for steps 1 and 2

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


Implements `05`'s steps 1 and 2 against `02-presence.md`. Written before the
code, so it records what was decided and where, and the reading that would
change each decision.

## Step 1: names

`_PLATFORM_NAMES` gains `whatsapp` → `WhatsApp` and `instagram` → `Instagram`,
first in `nora/agent/prefill.py` where it lives today. Step 2 then MOVES the
table into `nora/domain/self_presence.py` (as `PLATFORM_NAMES`, with `x` →
`X` and `threads` → `Threads` for the handle line), because the proactive pass
in `nora/language` renders the same block and may not import `nora.agent`.
One table, read by one renderer.

## Step 2: `<presence>`

### The model (`nora/domain/self_presence.py`)

Pure data, no I/O:

- `TransportState = Literal["up", "reconnecting", "needs_a_person",
  "not_running", "unknown"]`, and `transport_state(health)`:
  `degraded` → `needs_a_person` whatever `connected` says; else `connected` →
  `up`; else `reconnecting`. No adapter registered for the network →
  `not_running`; `health()` raised → `unknown` (R-27: «I could not check» is
  never rendered as «up»). `detail` is never read.
- `PresenceRoom(platform, label, state)`: one room of this unit. `label` is
  the policy label or `""`, never an id (R-18): `Conversation.label` falls
  back to nothing, NOT to `primary_id` the way `UnitRoom` does.
- `Presence(platform, label, direct, here_state, unit_rooms, other_groups,
  handles)`. `unit_rooms` is `None` when the reader could not resolve the room
  in policy, so «no other rooms» and «did not look» stay two states.
  `handles` is `((network, handle), ...)`.
- `Presence.minimal(platform)`: the network alone, for a context nobody
  built a presence for (an offline harness). It renders the here line and
  nothing it cannot ground, which is exactly what `_where_line` said.

### The reader (`nora/engagement/self_presence.py`, `PresenceReader`)

One implementation, three callers. Holds `Policy`, the adapter map
(`Mapping[str, PlatformAdapter]`, the same `registry.adapters` the tag path
already sends through), the declared handles, and the live handle sources.
`read(platform, conversation_id, conversation_type)`:

1. Resolve the room in policy. Its unit's other conversations become
   `unit_rooms` (network, label). Any conversation in ANOTHER unit sets
   `other_groups=True` (D1 = B); none of them is named or counted.
2. `health()` on each distinct network among here + unit rooms, gathered, at
   call time. Never cached: the reader holds no state between calls.
3. Handles: declared (`[agent.handles]`) overlaid with live reads. A live
   read that answers wins; one that fails or times out leaves the declared
   value if any; a network with neither is omitted, never guessed.

Never raises: a failure anywhere degrades to less text, and a failed health
read is `unknown`, not `up`.

### Her handles

- **Declared**: `[agent.handles]` in `config.toml`, a table of network key →
  handle, e.g. `signal = "@nora.000"`. `[agent]` is already «the agent's own
  identity» and is the one section whose subject is her. Not
  `[platforms.<p>]`: X and Threads have no platform section, and
  `PlatformsConfig` is `extra = "forbid"` over real adapters. Added to
  `config.example.toml` and `config.evaluation.toml`. Composition passes the
  mapping in; `nora.engagement` never reads config.
- **Live**: `nora/platform/instagram/self_id.py` gains `read_username`, which
  reads `self.username` from the same `GET /health` body and returns it as a
  plain string. `InstagramAdapter.own_handle()` calls it with a 1s bound and
  answers `None` on any failure. Composition wires it as the `instagram`
  live source when the adapter is registered.
- **Never an identity**: the handle is display text. It never becomes an
  `Identifier`, never enters `derive_identity`'s set or `self_aliases()`, and
  nothing matches a sender against it (`nora/domain/identity.py`).

### Where it reaches the prompt

- `TagContext.presence: Presence | None = None`, filled by
  `TranscriptInjection.assemble` (tag) and `FirePath.build_context`
  (participation, reminders, proactive act). Both get the reader through
  their builder, so `test_assembler_parity` sees `presence=` on both
  construction sites of each.
- `nora/language/presence_render.py`, `render_presence(presence, *,
  room_is=None) -> ContextBlock`. The ONE builder.
- `prefill._BLOCKS` gains `("presence", 5, "turn")`: first in the per-turn
  message, where `_where_line` sat, before `<self_card>`. `_where_line` is
  deleted and `build_prefill` no longer concatenates a bare string before
  `assemble(declared)`; `_presence_block(ctx)` renders `ctx.presence` or
  `Presence.minimal(ctx.message.platform)`.
- Proactive (D2): `proactive._BLOCKS`' `home_room` row becomes `presence`.
  `ProactiveAdaptor` takes `presence_of: Callable[[UnitRoom],
  Awaitable[Presence | None]]`; composition wires it to the same reader on
  the home room. The operator's description rides as the block's
  `this_room_is` attribute (02: «keeping the operator's description as an
  attribute»). `rooms` stays. The `home_room` block type is deleted, since
  nothing renders it.

### Wording

Situation, never ability. The type description is 02's, plus one sentence
that carries what `home_room` used to say on the proactive pass without
naming tools: the room she is in is where what she says or sets for later
lands. Lines:

```
You are in «Label» on Instagram.
You are @noraexample on WhatsApp, X, Instagram and Threads, and @nora.000 on Signal.
This group also has rooms on Signal («Label») and Discord («Label»).
Discord is reconnecting right now; the others are up.
You are also in other groups, which you do not discuss here.
```

### Tests (TDD, each proven by reverting its line)

Unit, over the renderer and reader with fakes from `nora/testing`:
- WhatsApp and Instagram render as display names, not wire keys (named
  platforms, not a loop over the table).
- A degraded AND connected adapter renders «needs a person».
- A room in another unit is never named; the D1 = B line appears.
- A handle with no declared value and no live answer is omitted.
- A live read beats the declared value; a failed live read falls back.
- No room id reaches the block.
- `health()` is read on every call (two calls, two states).
- `_where_line` is gone and `<presence>` is a row in `prefill._BLOCKS`.
- The proactive render carries `<presence>` and no `<home_room>`, with the
  operator description as an attribute.
- The Instagram username never becomes an `Identifier`.

## What would change this plan

- If `02`'s eval cases show she recites the room list unprompted, the block
  is worded as a topic: reword it, do not add rules around it.
- If the per-turn Instagram `/health` read measures above ~50ms on the tag
  path, read it on the listener's heartbeat instead and keep the last value.
