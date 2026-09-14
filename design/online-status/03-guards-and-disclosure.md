---
type: design
title: "Scope and disclosure — the room's own people, no guard machinery"
description: "The tool answers ONLY about the room's members; no R-31 DisclosureGuard machinery — digital activity is not a location data class. R-27 core vocabulary only."
timestamp: '2026-09-15'
edges:
  - {src: 00-overview, dst: 00-overview, rel: realizes, provenance: asserted}
  - {src: 02-data-sources, dst: 00-overview, rel: depends_on, provenance: asserted}
---

# Scope and disclosure

## The boundary is the room

`online_now` answers **only about the room's own members** — the roster of
the conversation it is invoked from. That is the scope statement and the
whole disclosure story:

- It never accepts a name outside the invoking room's roster. A `names`
  argument referencing a non-member is filtered out, and a question about
  an outsider answers `nothing_found`/`unavailable` — never a look-up of a
  stranger.
- The data itself (who is in a voice channel, who is online/playing on
  Steam) is the friend-circle's own state — visible to participants and
  low-sensitivity, unlike physical location.

This is deliberately NOT the `where_is`/`where_been` R-31 `DisclosureGuard`
machinery. That machinery exists because coordinates are person-linked
sensitive data (home/work addresses, DEC-74/DEC-79). Digital activity is a
different class: the audience is the room that already sees each other's
presence. Applying the location guard here would be mechanism-copying, not
reasoning (operator correction, 2026-09-15: "我唔諗到需要有啲咩 guard").
No new policy flag is introduced.

## Result vocabulary — R-27 core only

The three R-27 states apply (DEC-153 core vocabulary; `refused` is NOT
used — there is no guard that may say no):

- `ok` — real result per arm
- `nothing_found` — person has no steam_id, or the id resolves to nothing
- `unavailable` — wiring gap: no `[online]` config / no key, transport
  failure, non-JSON body, unreadable roster state

`personastate` honesty: an away/Snooze person is reported `away`/`snooze`,
never flattened to `online` (age-honesty rule, same class as `where_been`
never presenting derived history as "right now").

## Fail-closed posture

- A room-state read that cannot be obtained is `unavailable`, never a
  guess — the R-27 discipline: an ERROR is never a PASS.
- The room-scope filter is enforced in code (the invoking conversation's
  roster is the input domain), not taught as a preference: a `names`
  argument that cannot resolve into the roster is filtered, and the answer
  names only roster members. No partial-disclosure subtleties — the tool
  returns the room's state or an honest unavailable.
