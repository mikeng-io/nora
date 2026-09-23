# 02 — `<presence>`: where she is, every turn

Realizes the **Where** layer of `01-three-layers.md` for the facts needed on
every turn. Replaces `_where_line` in `nora/agent/prefill.py` rather than
sitting beside it: two sources for "which network am I on" is the drift the
context-assembly registry (`design/context-assembly/01-the-registry.md`)
exists to prevent.

## What it says

Three facts, all generated, none typed anywhere:

1. **Here**: the network and the room's label.
2. **This unit's other rooms**: network and label for each, and whether each
   transport is up. This is what `send_to` needs her to know already; today she
   has to guess which networks the unit has.
3. **Beyond this unit**: depends on D1 (`03-room-tools.md`). Under the
   recommended option it says only that other groups exist, never which.

Transport state uses `AdapterHealth`'s three states and nothing finer:
`connected`, `reconnecting` (disconnected, expected to heal), `needs a person`
(degraded). A room she is in but cannot reach is worth one word, because
`send_to` into it will fail and she should say so before trying rather than
after.

## What it looks like

```
<presence description="where you are right now. Generated each turn from
  the rooms you are configured for and your live connections; not something
  you remember, so trust it over any memory that disagrees.">
You are in <room name> on Instagram.
This group also has rooms on Signal, WhatsApp and Discord.
Discord is reconnecting right now; the others are up.
You are also in other groups, which you do not discuss here.
</presence>
```

Roughly 60–80 tokens. Line 4 is the D1-recommended wording; under the other
options it changes or disappears.

## Where it goes

- **Per-turn message, not cached prefix.** Health varies turn to turn;
  `_where_line` already lives in the per-turn message for the same reason.
- **Registered as a block** in the context-assembly registry with key `turn`,
  so every occasion that declares it gets the same builder. No occasion builds
  its own copy.
- **Occasions:** tag and participation (a person is addressing her). D2
  recommends proactive too: the proactive loop decides *where* to raise
  something, and it cannot choose a room it does not know exists.

## What it must not do

- **No room ids** (R-18). Labels only.
- **No member names for rooms other than this one.** Who is in another room is
  `read_room`'s territory and its disclosure tier (`03`), not a line every
  turn pays for.
- **No capability wording.** "You can send messages to Signal" is the
  catalogue's job and the framing `01` rules out.
- **No stale health.** Rendered from `health()` at turn start, never cached
  across turns. A cached "up" is how she promises a send that then fails.

## The fix it absorbs

`_PLATFORM_NAMES` gains `whatsapp` → `WhatsApp` and `instagram` → `Instagram`.
Today both fall through to the wire key. Small, but it is the bug `_where_line`
was written to fix, recurring on the two networks added since.
