---
type: design
title: "Tool shape and naming — one tool, closed `what` vocabulary"
description: "One `online_now`-family tool with `what ∈ {online, playing, voice}`; naming measured, never freestyled, per ADR 0004/DEC-58."
timestamp: '2026-09-15'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-data-sources, rel: depends_on, provenance: asserted}
---

# Tool shape and naming

## The shape — one tool, not two

The capability is "who is online / who is playing / who is in voice" — one
live-state question family, three facets. Two candidate shapes were
considered:

| Shape | Description | Verdict |
|---|---|---|
| Two tools: `steam_status` + `discord_status` | Each mechanism its own tool | **Rejected** — mechanism names (ADR 0004: a tool's name names the capability the router matches on, and a name is measured, never argued); the `where_been` precedent shows the accepted shape is one capability word |
| One tool, closed `what` vocabulary | `online_now(what, names?)` with `what ∈ {online, playing, voice}` | **Chosen** — mirrors `where_been(what ∈ {day, places, rhythm, routes})` (DEC-155 presence-analytics P1; see the `where_been` docstring, `nora/agent/tools.py`) |

Rationale for one tool, following the `where_been` precedent (`tools.py:541`,
closed vocabulary verified in `tests/unit/test_tool_registry.py` and
`tests/test_where_been.py` — "DEC-155, presence-analytics P1"):
- The three facets share a single live-state property: they answer "right
  now". A tool split by mechanism invites the model to think the answer is
  about a vendor, not about a person.
- Budgets, `time_shiftable=False` (this is live state — same declaration
  `now` and `transit` carry), `cacheable=False` (live state is the point —
  a cached "playing" answer is the exact stale-note failure this tool
  exists to kill; same rule and reason as `where_been`'s live-roster
  declaration), and `read_only=True` are all set once, on one declaration.

## Naming — candidates and the measurement gate

Working name during this bundle: **`online_now`**.

Candidate set for the DEC-39/DEC-58-style identifier measurement:

1. `online_now` — "who is online right now" (digital activity; NOT spatial).
2. `now_playing` — "who is playing what right now" (narrower; covers the
   playing facet but not online-only or voice membership).
3. `activity` — capability word, but risks colliding with `where_is`/`where_been`
   "activity" sense inside doctrine prose.
4. `who_is_online` — question phrasing; longer, and `who_is_around` was
   rejected for colliding with spatial presence.

The name is NOT settled by preference. Per DEC-39/DEC-58: identifier varies,
docstrings byte-identical, against the real tool schema set as it exists at
measurement time, on the real assembled doctrine, with `tool_choice="required"`
on the live model, measuring first-tool selection per question family:

- 「Blake 而家 online 嗎」→ online arm
- 「而家有咩人打緊機」→ playing arm
- 「有冇人喺 voice channel 傾緊偈」→ voice arm
- Negative controls: 「阿豹係邊個」must stay on `lookup_person`; 「Blake
  今日去咗邊」must stay on `where_is`/`where_been` — the tool must NOT drag
  spatial or identity questions onto itself.

If `online_now` pulls 「有咩人打緊機」 correctly and does not steal the
spatial/identity controls, it stands. A name that fails the negative-control
half is replaced, not argued.

## `what` vocabulary — closed, taught by the docstring

```
online_now(what ∈ {online, playing, voice}, names?)
```

- `online` — who is online right now (Steam presence + Discord voice
  membership merged; the "who's around digitally" answer).
- `playing` — who is playing what (Steam `GetPlayerSummaries.gameextrainfo`;
  the "is the group mid-game" cross-check #248 wanted).
- `voice` — who is in which Discord voice channel (existing `voice_states`
  intent; zero privileged changes).

The tag-turn ask 「Blake 而家係咪 online 喺 Steam」 is answered by
`online_now(what="online", names=["blake_lindqvist"])` — the tool is reachable
from any tag turn once registered. Scope is the invoking room's roster; a
`names` argument referencing a non-member of the occasion's input domain
(03-guards) is filtered out.

`names?` is an optional filter — DEC-24-shaped arguments only: Nora already
holds which room and which people; a `names` argument exists to bound a
single query to the people actually on topic, not to invent scope. When
omitted, the answer covers the room's roster (the invoking conversation's
members — the tool's scope boundary, 03-guards).

## ADR 0007 obligations (tool-surface policy)

The docstring is the routing surface — opening = what + when-to-call;
SHOULD-labelled style; RFC 2119 register; English prose with CJK only in
quoted examples. Draft opening:

> "Live digital activity for the people in this room — who is online, who is
> in a voice channel, who is playing what. Call this when a question or a
> decision turns on whether someone is actually present or playing right
> now."

The three arms are multi-state (`ok` / `nothing_found` / `unavailable` — the
R-27 core vocabulary), so the docstring is a Level 3 description (what +
when-to-call + one contract line — ADR 0007's longest ladder rung), matching
`where_been`.
