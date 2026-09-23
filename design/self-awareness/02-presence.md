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
3. **Beyond this unit**: D1 = B (`03-room-tools.md`): it says only that
   other groups exist, never which.
## Her own handles: a companion block, not part of `<presence>`

The public names people use to reach her (operator, 2026-09-23): `@nora.000`
on Signal; `@noraexample` on WhatsApp, X, Instagram and Threads. Without them
she cannot recognise "@noraexample" as herself in text that is not a platform
mention, or answer "what's your IG?".

They are the same in every room and change rarely, so they do not belong in
the per-turn block. They are an `<accounts>` block keyed `global`, in the
cached prefix: under the registry's rule (`nora/domain/blocks.py`, `BlockKey`)
only a `global` block may sit there, and this one is.

**Static, but data, not doctrine.** `identity.md`'s `not_here` forbids it by
name ("no page name… no address"), and a new doctrine `.md` would carry the
same risk that rule names: prose nobody checks, stale in silence. So the
handles are declared once in `config.toml`, beside each platform's settings,
and X and Threads (no adapter) in a small table of their own. Config can be
checked where prose cannot: where an adapter can report its own username at
startup (the `self_id` modules already derive her identifiers per platform),
a mismatch with the declared handle is logged, not rendered. A public handle
is not an id in R-18's sense (R-18 is somebody else's identifiers): it is the
name she would give anyone who asked.

Transport state uses `AdapterHealth` (`nora/domain/ports.py`) and nothing
finer. The type is two booleans, `connected` and `degraded`, so all four
combinations are reachable; the render reads them in this order: `degraded`
→ `needs a person` (whatever `connected` says, because a degraded adapter
cannot act until a human intervenes); else `connected` → `up`; else
`reconnecting` (disconnected, expected to heal). `detail` is not rendered: it
is operator-facing text and may name an account. A room she is in but cannot reach is worth one word, because
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

Roughly 60–80 tokens. The last line is the D1 = B wording.

## Where it goes

- **Per-turn message, not cached prefix.** Health varies turn to turn;
  `_where_line` already lives in the per-turn message for the same reason.
- **Registered as a block** in the context-assembly registry with key `turn`,
  so every occasion that declares it gets the same builder. No occasion builds
  its own copy.
- **Occasions:** tag, participation, and proactive (D2, decided yes
  2026-09-23): the proactive loop decides *where* to raise something, and it
  cannot choose a room it does not know exists. The proactive prompt is
  built on its own path (`nora/language/proactive.py`,
  `nora/subject/proactive.py`), not through `prefill._BLOCKS`, so D2 is a
  second call site that must call the same builder. It is not free, and it
  is not done by registering the block.

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
