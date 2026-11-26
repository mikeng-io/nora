---
class: design
subject: context assembly, how every model-facing prompt is built from declared blocks
owns:
  - "block: one declared unit of a pass's context, with a type, an order and a scope"
  - "assembler: the one renderer that walks an occasion's declared blocks"
  - "floor: the context an occasion always carries, never a candidate for removal"
  - "exposure: which already-built blocks reach the model, decided after retrieval"
area: agent
links:
  - {to: docs/design/agent/README.md, rel: uses}
  - {to: docs/design/cognition/context.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Context assembly

Context assembly builds every model-facing prompt from declared blocks through one mechanism. What
a pass's blocks present, and the four layers they come in, belong to
[context](../cognition/context.md); this file owns how they are declared, rendered and exposed.

## Parts

- **The declaration** names an occasion's blocks, their order and their scope as data. It is the
  review boundary: a new block is a deliberate row, never a side effect of another feature.
- **Block types** say what a block is and how to read its fields: citation grammar, subject, age and
  state. One store has one meaning and one builder, even where occasions read different slices.
- **The assembler** renders the declared blocks in order, escapes untrusted text and keeps explicit
  empty and unavailable states.
- **Framing builders** own authored headers and closing text. Values enter through typed, escaped
  paths, never by interpolation.
- **Exposure** optionally filters which built blocks reach the model.

## Rules

- **An occasion reads what its decisions need.** Occasions share builders where meanings match and
  inherit nothing else; a new occasion declares its own blocks rather than copying another's list.
  Widening an occasion is a change to its declaration, with its own evidence.
- **Production and evaluation assemble the same declaration.** Evaluation replaces environmental
  dependencies, never blocks, tool permits or ordering.
- **A block states its subject, author, time and state,** naming people and authors by canonical id
  beside any display name. Empty means the store has none, unavailable means the read failed, absent
  means the occasion did not declare it. A silent omission never stands for an empty world.
- **Text from members and strangers is escaped by the shared renderers.** A speaker label cannot
  forge another row and markup cannot close a block. Authored framing is typed, with no bare
  interpolation.
- **A quoted message resolves through one timestamp mapping,** and message handles are labels that
  live for the turn and never enter durable context or outgoing prose.
- **Unknown blocks, invalid composition and malformed output are errors,** never a successful
  smaller prompt.
- **The declaration is found and enforced.** Any module that runs a prompt it does not build is
  classified as mechanism, runner or exempt, and a runner names the builders it uses.

## The floor

The current and quoted messages, the reply header, the clock, the seed, law, tool permits, the
roster and presence are the floor: fixed for the occasion, never relevance choices. A transcript's
extent changes only while every quote and positional reference still resolves.

**A quoted parent outside the window.** When a message quotes a parent older than the window, the
window is extended to reach it plus a small margin, up to a fixed multiple of its normal size, so
the model reads the parent's text and not only who wrote it. Beyond that bound, or when the parent
is not found, the window stays as it was and the reply renders unthreaded. Which parent text is shown
at all is [conversation](../conversation/README.md)'s rule.

## Exposure

Exposure runs after full assembly and before the call. It selects among advisory blocks, those whose
absence costs an affordance and not truth, and it never nulls a field, reparses the message or
changes a tool's permit. A tool is never told context is present when exposure withheld it, and it
stays able to fetch that context explicitly.

- **The default is the full prompt.** An invalid answer, a timeout or a provider failure keeps it,
  and a good reply under that fallback is not evidence the filter worked.
- **Code owns the floor, dependencies and thresholds.** A decision model may judge which optional
  blocks a turn needs, from short cards each block carries; it never writes the reply or chooses
  whether to engage. Uncertain selection includes the block.
- **Zero-call inclusion is the baseline.** A model-based filter must show a downstream benefit after
  its own latency, input and cache cost; a smaller first call is not a benefit.
- **Cache boundaries follow the rendered bytes.** A smaller prompt that moves a cached prefix can
  cost more.
- **A finite per-turn intake budget** may stop further fetching as results accumulate, never evicting
  what the model already saw, and truncation is explicit in the result.
- **Selecting before retrieval** inserts a decision ahead of concurrent reads and can add latency. It
  is a separate change with its own measured benefit.

## Why

- **One declaration** so a prompt is readable as data and a model's input can be inspected before its
  output is blamed.
- **Escaping at the renderer** because prompt integrity is a rule, not a presentation choice.
- **Filtering at render, never at the source** so every consumer sees the same frozen context
  whether or not a block was exposed.
