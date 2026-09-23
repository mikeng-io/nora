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
## Her own handles: a line in `<presence>`, read live

The public names people use to reach her: `@nora.000` on Signal;
`@noraexample` on WhatsApp, X, Instagram and Threads (operator, 2026-09-23).
Without them she cannot recognise "@noraexample" as herself in text that is
not a platform mention, or answer "what's your IG?".

**Read from the adapter, not copied.** The Instagram account was renamed
from `not_____nora` to `noraexample` between 2026-09-22 (measured on the live
session) and 2026-09-23 (operator). A copy in config would have been wrong
from that moment, silently: the failure `identity.md`'s `not_here` names
("no page name… no address"), moved from prose into TOML. So the handle
is read live wherever something can report it, and declared only where
nothing can:

| Network | Source today | Why |
|---|---|---|
| Instagram | live: the bridge's `GET /health` carries `self.username` (`design/instagram-adoption/03-transport-contract.md`); `nora/platform/instagram/self_id.py` reads that response and ignores the field today, so the build reads it | the adapter knows the logged-in account |
| Discord | live: `GET /users/@me` carries `username` and `discriminator` (`design/discord-integration/03`); `nora/platform/discord/self_id.py`'s `derive_identity` reads them off the SAME call `resolve_identity()` already makes at startup, and `DiscordAdapter.own_handle()` reuses the cached result — no second call per turn. Rendered `@username#discriminator`, or `@username` alone once the account carries Discord's migrated `discriminator = "0"` | the adapter already calls this endpoint once at startup; reusing it costs nothing |
| Signal | declared, until the build confirms the adapter can report it | `self_id.py` derives her id and deliberately drops the username (below); whether the transport exposes her own handle is a build-time check, not assumed here |
| WhatsApp | declared | the bridge contract (`design/whatsapp-adoption/03-transport-contract.md`) exposes `{phone, lid}` only; there is no handle to read |
| X, Threads | declared | no adapter |

A declared handle is a copy, with exactly the risk above. It is the fallback,
not the design, and each declared row is replaced by a live read the day its
adapter can report one. A handle neither source has is rendered from nothing,
not guessed.

**This does not reopen the no-username rule.** `nora/domain/identity.py`
(operator, 2026-08-14): no `username` is ever INDEXED on any platform,
because a handle is re-assignable and matching on it resolves a stranger
as a known person. That rule is about recognising other people. Her own
handle here is display text in her prompt: it is never an `Identifier`,
never matched against a sender, and never enters `self_aliases()`, so
mention detection stays on her immutable ids.

**Inside `<presence>`, not a block of its own in the cached head.** The head is
one fixed `context_header` block (`_context_header`), and no `_BLOCKS` row is
served there (`test_no_block_in_this_table_belongs_in_the_cached_head`
rejects a `global` key). A `global` block for about 20 tokens would need a
new head-assembly path, and a live-read value would break the prefix cache
on every rename anyway. One line per turn is the cheaper shape.

A public handle is not an id in R-18's sense (R-18 is somebody else's
identifiers): it is the name she would give anyone who asked.

## What it looks like

```
<presence description="where you are right now. Generated each turn from
  the rooms you are configured for and your live connections; not something
  you remember, so trust it over any memory that disagrees.">
You are in <room name> on Instagram.
You are @noraexample on WhatsApp, X, Instagram and Threads, and @nora.000 on Signal.
This group also has rooms on Signal, WhatsApp and Discord.
Discord is reconnecting right now; the others are up.
You are also in other groups, which you do not discuss here.
</presence>
```

Roughly 80–100 tokens. The last line is the D1 = B wording.

## Where it goes

- **Per-turn message, not cached prefix.** Health varies turn to turn;
  `_where_line` already lives in the per-turn message for the same reason.
- **A `ContextBlock` with a row in `prefill._BLOCKS`**, key `turn`, rendered
  through `assemble` like `standing` and `culture`. Today `_where_line` is not
  one: it is a bare string concatenated in front of `assemble(declared)`
  (`nora/agent/prefill.py`, the per-turn message). Replacing it removes that
  special case. One builder; no occasion builds its own copy.
- **Occasions:** tag, participation, and proactive (D2, decided yes
  2026-09-23). As built, reminders and the proactive ACT turn get it too:
  they share participation's `FirePath.build_context` and render through the
  same prefill, so the block rides every fire turn rather than only the
  participation one. The proactive loop decides *where* to raise something, and it
  cannot choose a room it does not know exists. The proactive prompt has
  its own `_BLOCKS` table (`nora/language/proactive.py`), and it already
  carries part of this: `home_room` (platform, label, the operator's
  description) and `rooms` (standing lines per room). So D2 is a second row
  calling the same builder, and `<presence>` **replaces `home_room`**,
  keeping the operator's description as an attribute, rather than becoming a
  second statement of where she is. `rooms` stays: it is what each room
  stands on, not which rooms exist.

## What it must not do

- **No room ids** (R-18). Labels only.
- **No member names for rooms other than this one.** Who is in another room is
  `room_read`'s territory and its disclosure tier (`03`), not a line every
  turn pays for.
- **No capability wording.** "You can send messages to Signal" is the
  catalogue's job and the framing `01` rules out.
- **No stale health.** Rendered from `health()` at turn start, never cached
  across turns. A cached "up" is how she promises a send that then fails.

## The fix it absorbs

`_PLATFORM_NAMES` gains `whatsapp` → `WhatsApp` and `instagram` → `Instagram`.
Today both fall through to the wire key. Small, but it is the bug `_where_line`
was written to fix, recurring on the two networks added since.
