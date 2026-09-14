---
type: design
title: "Guards and disclosure — the Steam arm is person-linked privacy surface"
description: "Parity decision: does the Steam arm reuse the R-31 DisclosureGuard, or does a friend-circle gate apply? Fail-closed, never omit."
timestamp: '2026-09-15'
edges:
  - {src: 00-overview, rel: realizes}
  - {src: 02-data-sources, rel: depends_on}
---

# Guards and disclosure

## The asymmetry that forces the decision

The three arms are NOT equally sensitive:

| Arm | Sensitivity | Existing guard |
|---|---|---|
| `voice` | **Low** — the room's own participants can see who is in the channel themselves | none needed; the tool answers what the group can already see |
| `online` (Steam) | **Medium** — exposes whether someone is at their machine / away / in-game, visible beyond the room | needs a decision (this doc) |
| `playing` (Steam) | **High** — the exact title tells the room what someone is doing, including a game they might not want announced | needs a decision (this doc) |

Spatial presence got the strict treatment: `where_is` carries the R-31
guard — room ⊄ circle → refuse, never omit (`nora/agent/whereis.py`,
`DisclosureGuard`); coordinates never leave in tool args. Digital activity is
**person-linked the same way a coordinate is**: it identifies what a person
is doing right now, beyond what the room can observe.

## Two candidate positions

### A. Exact parity — reuse DisclosureGuard

The tool composes `Room` + `DisclosureGuard` + `location_trusted` exactly as
`Whereis.locate` does, and resolves names the R-47 way. Refuse into any room
that fails the subset test, exactly as `where_is` does.

- Pro: one seam, no weaker guard invented; "more sensitive than a live fix"
  argument from `presence-analytics-p1` applies a fortiori to "playing what".
- Con: the friends rooms already carry `location = true` (whole-room waiver,
  DEC-79), so in practice every current room passes — the guard's cost is
  complexity, not restrictiveness. It also requires the room to be mapped
  and the roster readable, which is already true for all four policy rooms.

### B. Lighter friend-circle gate

A `[online]` section knob (`disclose = "mapped" | "circle-gated"`) that
answers for mapped persons in any room where Nora already speaks, without
the circle-subset machinery.

- Pro: simpler; the tool is read-only and low-stakes vs location.
- Con: **weakens a principle** — any answer about what a person is doing
  right now is the same disclosure class as where they are. A room-specific
  weaker gate is exactly the pattern `where_been`'s spec warned against
  ("reuse, don't re-derive… must REUSE the same guard, not invent a weaker
  one").

## Position

**A — exact parity.** The `playing` arm is at least as sensitive as a live
fix, and `online` is not far behind. The tool composes the same
`DisclosureGuard` seam as `where_is`/`where_been`. This is the fail-closed
default: no new guard shape, no new privacy surface.

The one nuance: `voice` arm stays UNGUARDED and separate — it answers the
already-visible room state and is the arm the operator's use case ("are we
all here") actually leans on. The docstring must teach that `voice` needs no
room-gate while `online`/`playing` do — otherwise the model conflates the
two and either over-refuses a trivia question or discloses a game title
ungated. (Same class of surface-teaching obligation as the
`where_is`/`where_been` live-vs-history boundary rule.)

## Fail-closed states (R-5, three-state)

- `ok` — real result, per-arm.
- `not_found` — a person genuinely has no Steam id mapped, or the id
  resolves to nothing. NEVER a guess.
- `unavailable` — wiring gap (no key), transport failure, unreadable room
  roster, or the guard refused. `unavailable`, never `not_found`, for a
  guard refusal — the refusal has its own honest wording ("I can't check
  that here"), same as `where_is`.

`refused` is recorded via the same `nora_presence_disclosure_*` metrics
family pattern (a counter, never silent), so a guard that starts blocking
real asks becomes visible, not anecdote.
