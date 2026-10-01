# 01 — The catalogue (selection)

Realizes `00-overview.md`. Covers category 1 only: *does this capability
exist, and is it the one I want?*

## What it is

One generated block in the prompt, one line per tool, every tool, every turn.
It is an **index**, not a description: enough to decide *this one* or *not
this one*, and nothing more.

```
<tool_catalogue description="everything you can do. Each line is a name and
  when to reach for it. To use one, ask for it by name with `load_tools` —
  that gives you its arguments. The six marked * are already loaded.">
* web_search — search the open web. Last resort; prefer your own sources.
* web_fetch — read one web page you already have the URL for.
* note_read — read a note you or the room already opened.
* search_memory — find what was said about a topic before.
* now — the current time, here.
* send_reply — say something into this room. Ends the turn.
  where_is — where somebody in this room is RIGHT NOW.
  where_been — where somebody HAS been: a day, a pattern, a rhythm, a route.
  find_parking — car parks near a place the group names, with live vacancy.
  parking_status — live vacancy for one car park the group names.
  route — how to get from one place to another, and how long it takes.
  transit — public transport departures and arrivals for a stop or route.
  ...
</tool_catalogue>
```

## The line

`name — when to reach for it.` One clause. Target **≤ 20 tokens**; the block
is generated, so the ceiling is enforceable at build time rather than left to
whoever writes the next docstring (`04-code-surface.md` §2).

What a line must carry:

- The **capability**, in the words the room would use for it.
- The **discriminator against its nearest sibling**, where one exists —
  `where_is` says RIGHT NOW, `where_been` says HAS been. Selection errors
  concentrate between confusable pairs, so that is where the token budget goes.

What a line must **not** carry: parameters, arms, `status` values, reporting
rules, examples. Those are categories 2 and 3 and have their own homes
(`02`, `03`). A line that starts explaining how to call the tool has taken
budget from 53 other lines to answer a question nobody has asked yet.

## Where it comes from — the generator already exists

`ToolRegistry.capability_digest()` (`nora/agent/registry.py:569`) already
renders exactly this shape: one line per tool, `name — first sentence`,
derived from the registry so it cannot go stale. It was built 2026-09-10
(#144) for the Relevance Gate, which had to judge what Nora can answer while
holding a hand-written five-item list against 53 registered tools.

Two differences from what this bundle needs, and both are small:

- It renders **read-only tools only** (35 of 54 — the gate asks whether an
  answer could exist, so act verbs are irrelevant to it). The catalogue needs
  all 54.
- It is scoped to the gate, not the tag path.

**An earlier draft of this file claimed the catalogue must come from a new
authored field, because "a truncated first sentence discriminates nothing."
That was asserted, then measured, and it is wrong.** The existing first
sentences discriminate well on most confusable pairs:

| pair | rendered lines | verdict |
|---|---|---|
| `find_parking` / `parking_status` | "the nearest car parks… around a spot the group names" / "how many spaces are free at a **named** car park, **RIGHT NOW**" | clean |
| `search_memory` / `search_memory_workspace` | "in **this group's** history" / "across **EVERY room** of this workspace" | clean |
| `note_read` / `note_search` | "read what a note actually says" / "look **INSIDE** notes for something their titles don't say" | clean |
| `where_is` / `where_been` | "where somebody **was last seen**" / "where somebody **HAS BEEN** — today, usually, repeatedly" | **weak** |

So: **first sentence by default, targeted repair where it under-discriminates.**
That is a handful of docstring edits, not 54 new authored fields, and it keeps
one source of truth per tool rather than adding a second thing to keep in sync.

`where_is` is the one measured failure: "was last seen" does not say RIGHT
NOW, while the full schema does ("forcing a fresh reading first so the answer
is where they ARE"). Its first sentence needs to carry the liveness.
`04-code-surface.md` §2 lists the repairs and the gate that keeps them.

## Where it sits

**Revised 2026-09-23** — a context block through the context-assembly registry,
rendered per scope from `ToolSurface.catalogue`; see `04` §6. The catalogue
varies by room and by Dreamer adaptor, which a lane-static doctrine tier
cannot express. The original plan follows for the record:

~~The doctrine's tier order, at the slot the capability block used to occupy
(order 35, between `conduct` and `constraints`).~~ It would have been a **derived** kind, so:

- It cannot be authored as a file — `check_kind_boundaries` already refuses a
  file claiming a derived kind.
- The "no doctrine file may name a tool" gate is unaffected: it is scoped to
  `identity` and `conduct` kinds (`nora/doctrine/loader.py:397`), and a
  generated catalogue is neither.

## Budget — measured, not estimated

Rendering `name — first sentence` for all 54 tools from today's docstrings:

| | tokens |
|---|---|
| 54 lines | **991** (measured, cl100k) |
| mean per line | 18.4 |
| header/footer | ~40 |

For comparison, `capability_digest()` on its 35 read-only tools renders 2,961
chars / **662 tokens** today.

Two lines already blow the ≤20-token target and should be capped at the
source: `recall_lookups` (46) and `set_music_volume` (45). `remind` (32),
`search_memory` (29) and `online_now` (29) are the next tier.

**Gate:** if the rendered catalogue exceeds **1,400 tokens**, the line budget
has failed and lines are being written as descriptions. Fix the lines, not the
threshold. `05-measurement.md` §2.

## The hot set

Six tools keep their full schemas resident and are marked `*`. Measured over
2,000 calls (2026-08-12 → 2026-09-21):

| tool | calls | share | schema |
|---|---|---|---|
| `web_search` | 497 | 24.9% | 480 |
| `send_reply` | 195 | 9.8% | 1,146 |
| `note_read` | 152 | 7.6% | 274 |
| `web_fetch` | 135 | 6.8% | 270 |
| `now` | 63 | 3.1% | 102 |
| `search_memory` | 59 | 3.0% | 334 |
| **total** | | **55.2%** | **2,606** |

Those turns never call `load_tools`. Anthropic's guidance is to keep the 3–5
most frequent tools non-deferred; six is that, plus the one structural
requirement below.

**`send_reply` is in the hot set regardless of usage.** It is the terminal
verb — `ReplyGuard`'s turn-ending hook fires on observing it
(`nora/agent/guards.py`, DEC-21/DEC-32). A turn that must call `load_tools`
before it can reply is a turn that can fail to end.

**The hot set is a measured value, not a constant.** It is derived from
observed call counts and re-derived when they move; `05-measurement.md` §4
sets the cadence. The next two candidates are `play_music` (3.5%, 612 tokens
— already Discord-scoped, so free there and absent elsewhere) and `where_is`
(3.2%, 774).

## Tools hot on one occasion (DEC-256, 2026-10-01)

Some tools are rare across all turns and common on one kind of turn. The music
verbs are the case that raised it: a spoken turn in a Discord voice channel is
waiting on her voice, each extra model call is heard as silence (wake to first
speech measured at a 6s median over 14 voice turns, 2026-09-26..30, and a
`load_tools` call is a full model call over ~20k input tokens), and since
`[llm] defer_tools` went on (2026-09-24) six of the seven voice turns that used
a music verb paid a `load_tools` call for it first.

**The convention.** A tool declares the occasions it is hot on, once, beside its
own entry: `NativeTool.hot_on`, a set drawn from `TOOL_OCCASIONS` in
`nora/agent/tools.py`, a closed vocabulary that holds only `voice` today. The
loop that builds a turn's surface derives the occasion from the turn's message
(`turn_occasion(voice=msg.voice)`, on the tag path and the fire path alike) and
passes it to `ToolRegistry.surface()`, which keeps `hot_tools(occasion)`
resident: `HOT_TOOLS` plus every tool whose `hot_on` names it, still inside the
scope (`ToolScope` cuts the music verbs from every non-Discord room first). A
text turn has no occasion, so its surface is byte-identical to what it was
before the field existed; a test asserts it by stripping every declaration.
There is no per-tool special case in the builder.

**The practice.** A tool joins an occasion on a measurement, never on intuition:
`scripts/measure_voice_tool_calls.py` counts the tools called on voice turns
against text turns in the same rooms (read-only, aggregates only, run on the
deploy host), printing the window and n beside every count. The bar is the one
`HOT_TOOLS` already meets: called on at least the share its smallest member
holds (3.0%). Measured 2026-10-01 over 30 days (voice turns 2026-09-17..09-30,
n=26, a small sample: one turn is 3.8%):

| tool | voice turns calling it | declared |
|---|---|---|
| `play_music` | 5/26 | yes |
| `set_music_volume` | 5/26 | yes |
| `stop_music` | 3/26 | yes |
| `music_status` | 1/26 | yes |
| `top_music` | 1/26 | yes |
| `skip_music` | 0/26 | no |

14 of the 26 voice turns called a music verb; text turns in the same rooms did
so on 21 of 169 over the same 30 days, and on 1 of 34 since deferral went on.

**What it costs, and why the cost is accepted.** The five schemas are 658
cl100k tokens on every call of a voice turn (ceiling 750, pinned). They also
give a voice turn a tools array no text turn shares, so the prefix cache splits
by occasion. Read from Langfuse for the same 26 turns: a voice turn's first call
reuses ~11k of cached prefix (the system prompt, which sits before the tools
and is unaffected) plus ~3k for the tools when the previous call was compatible;
voice turns come in bursts (12 of 26 followed another voice turn within two
minutes), and a burst shares the new array. Against that, a voice turn that used a music verb paid one
whole extra model call for `load_tools` (~20k input) and a cache drop to the
system prompt on the call after it, because loading changes the tools array.
Text turns are untouched. This is an estimate from recorded turns, not a
before/after; the before/after is the next reading below.

**The reading that would reopen it:** voice turns calling a music verb falling
under the 3.0% bar over a 30-day window, or a voice-turn before/after (turn
latency and cached/uncached input per turn, from Langfuse) showing the first-call
miss costing more than the hop it removes.

## Why the catalogue is resident and not itself deferred

To ask for a catalogue you must already suspect the capability exists. When
Nora does not suspect it, she answers "I can't do that" and nothing anywhere
records that she could have. That is DEC-28's failure — a model read as
refusing to reach for a tool it had never been shown — and it is
constraints.md's "a broken Nora and a quiet Nora look the same" in the one
place the doctrine cannot catch it.

991 tokens buys the property that Nora always knows the full shape of what
she can do. It is the cheapest thing in this bundle and the least negotiable.

## Open question

Whether a one-line catalogue holds selection accuracy as well as full
descriptions is **not established**. LongFuncEval's degradation figures were
measured with full schemas resident; nothing in the literature measures
selection from one-liners at this catalogue size. `05-measurement.md` §2 is
the gate, and it is the gate that can sink phase 1.

One piece of evidence already exists on the encouraging side and should not be
over-read: the Relevance Gate has judged Nora's capability surface from
exactly these first sentences since 2026-09-10 (#144), and did so as a
*replacement* for a stale five-item hand-written list. That is a different
decision from tool selection — "could an answer exist" rather than "which
verb do I call" — so it supports the render being **legible**, not that it is
**sufficient for selection**.
