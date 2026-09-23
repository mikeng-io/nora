# Self-Awareness: who she is, where she is, what she can do

## The problem, from the operator

Nora is on Signal, WhatsApp, Discord and Instagram, in several rooms per
network, across more than one unit. Her doctrine tells her how to behave in a
room. Nothing tells her which rooms exist, which one she is standing in beyond
its network, which of her transports are up, or what she can do. Asked
"which groups are you in?" or "is the Discord side working?", she has nothing
to answer from, and a model with nothing to answer from answers anyway.

## The evidence that this is real

- **It has already produced a wrong answer.** With two platforms live she
  answered a Discord tag saying she was on Signal (operator, live,
  2026-08-14). The fix was `_where_line` in `nora/agent/prefill.py`: one
  sentence, "You are on Discord right now." That is the only situational fact
  she gets today, and it covers the network, not the room or the unit.
- **The tools say so in their own descriptions.** `group_members` in
  `nora/agent/tools.py`: *"It only ever describes THIS conversation, and it
  cannot tell you about anyone outside it."* `load_transcript` reads *"this
  conversation"*. `send_to` knows *"one group with one room on each network"*
  and has her name the network. No tool answers "which rooms am I in".
- **`_where_line`'s own name table is incomplete.** `_PLATFORM_NAMES` maps
  `signal` and `discord` only, so on WhatsApp and Instagram she is told the
  wire key (`whatsapp`, `instagram`), not the name the room uses.

## Why the doctrine does not carry it, and must not

`nora/doctrine/identity.md` forbids it in its own frontmatter (`not_here`):
*"No capability claims, and no instance of anything… a copy of it here would
go stale in silence, as this file carried one for eight weeks."* The rule is
right. A room list or capability list typed into doctrine is correct on the
day it is written and silently wrong the day a room is added.

So self-awareness cannot be written. It has to be **generated, every time,
from the system that actually knows the fact.** That is the whole design.

## The three layers

| Layer | Question | Source of truth | Delivered as |
|---|---|---|---|
| **Who** | what kind of thing am I | `identity.md` (unchanged) | doctrine, cached prefix |
| **Where** | which networks and rooms, which is in front of me, which transports are up | the room table loaded from `policy.toml` + each adapter's `health()` | a generated `<presence>` block, per turn (`02-presence.md`), plus room tools (`03-room-tools.md`) |
| **What** | what can I do | the tool registry | **the tool catalogue** (`design/tool-catalogue/`), not a new block |

`01-three-layers.md` argues each row. The short version: the tool catalogue
already *is* the capability layer, generated from the registry, so this
bundle adds no capability text of its own. DEC-150 (`design/capability-drop/`)
deleted the last generated capability block for exactly that reason.

## Scope

**In:**
- `<presence>`: a generated per-turn block extending `_where_line` (`02`)
- three room-awareness tools, `my_rooms`, `room_activity`, `read_room`, each
  with its own disclosure tier (`03`)
- the order, and how to prove each part helps (`05`)

**Out:**
- rewriting `identity.md`. Its rule is kept, not relaxed.
- any tool built before the tool catalogue lands (`05` says why)
- social fetching (`social_fetch` and any split of it). That is a separate
  topic, in `design/instagram-adoption/`.

## Open decisions (operator)

- **D1, the unit boundary.** In the friends group, may `<presence>` and
  `my_rooms` show that ridgeline rooms exist? May `read_room` read one? `03`
  sets out the options and recommends one. Nothing in `02`–`03` is buildable
  until this is decided, because every block and tool's content depends on it.
- **D2, heartbeat and proactive.** Does `<presence>` render on the proactive
  loop too, or only on turns that answer a person? `02` recommends both.
