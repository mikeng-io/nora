# Context assembly

Every model-facing prompt in Nora is built from declared context blocks and
assembled through one mechanism. The declaration names what an occasion may
read, in which order and under which scope; the occasion's decisions explain
why each block earns its place.

**Status:** building

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fcontext-assembly%2F%22)

## Parts

- **The block registry** declares an occasion's blocks as data. It is not a list
  of everything Nora knows, and adding a block is an explicit design change.
- **Block types and builders** say what a block is, how to read it and which
  values it may carry. One store has one meaning and one builder, even where
  occasions need different slices of it.
- **The assembler** renders declared blocks in order, escaping untrusted text
  and preserving explicit empty and unavailable states. No model prompt is
  hand-built beside it.
- **Framing builders** own authored headers and closing text. Values enter
  through typed, escaped paths rather than interpolation.
- **Occasion contracts** bind blocks to the judgements the occasion makes. The
  relevance gate remains deliberately closed; Cognition selects related
  context for its wake rather than copying the reply turn's inventory.
- **Composition** optionally filters exposure after full retrieval without
  changing the typed context or tool admission. A learned composer is an
  optional implementation, never a second reply author or retrieval owner.

The subjects are [registry and rendering](registry.md), [Cognition
context](cognition.md), and [composition and exposure](composition.md).

## Invariants

- No occasion is exempt. A model call reached through a new kind of work joins
  the same declaration and assembly contract.
- Retrieval and exposure are different facts. Filtering a block does not delete
  the source, change a resolver or remove a tool's grant.
- The current and quoted messages, reply references, hard privacy rules and
  required identity seed are a floor, not candidates for opportunistic removal.
- A block states its subject, author, time and state. Empty means the store has
  none; unavailable means the read failed; absent means the occasion did not
  declare it.
- Cache boundaries follow the actual rendered bytes. Composition must not turn
  a smaller prompt into a larger cached-cost or latency bill.
- Records about people are interpreted into answers, not exported as raw data.
  Generated works and public media are outside that personal-record boundary.
