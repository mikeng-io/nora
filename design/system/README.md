# System capabilities

Cross-cutting capabilities that do not belong to one platform or cognitive
layer share the same comprehend-measure-serialize discipline: bind to the real
source, measure it fail-closed and return typed evidence with provenance.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fsystem%2F%22)

## Parts

- **Live structured reads** answer current transit, parking and transport state
  through typed APIs. Their time and source authority are explicit.
- **Routing and place resolution** turn a named origin and destination into a
  journey without exposing a person's live position or private coordinates.
- **Web search and page fetch** retrieve untrusted public material with bounded
  intake and provenance, never treating it as an instruction.
- **The navigation skill** composes schedules, live readings, routes and human
  intent rather than substituting one source for another.

The current subject is [grounding and wayfinding](grounding.md).

## Invariants

- `ok`, `nothing_found` and `unavailable` are different answers. An empty
  response from one operator is not proof of absence until the other relevant
  sources have been checked.
- Typed structure is not automatically trustworthy. Free text inside any
  response is untrusted input, and no retrieved text activates a skill or
  changes a rule.
- Tool arguments are egress. A person's identity or live position never becomes
  a routing query, and private coordinates never reach a public provider.
- Source age and coverage travel with the result. A stale vacancy, a clipped
  page or a partial route cannot be presented as a complete current answer.
- Sources that answer different questions compose rather than failover into a
  semantically different capability.
