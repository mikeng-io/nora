---
class: design
subject: the tool registry, the permit that admits a tool to an occasion, and how results report
owns:
  - tool pool: "the tools an occasion may call, one admitted set drawn from the registry"
  - permit: "an occasion's admission of one tool, by platform, occasion, grant and executor"
  - three-state result: "every tool answers ok, nothing found or unavailable, and never conflates them"
  - tool surface: "the resident tools, the shelf and the catalogue, three views of one admitted set"
  - terminal verb: "a delivery tool that ends the pass, such as the reply tool; a pass allowed to stay silent may end without calling one, and silence is recorded as an outcome"
area: agent
links:
  - {to: docs/design/agent/README.md, rel: uses}
  - {to: docs/design/cognition/surface.md, rel: uses}
  - {to: docs/design/cognition/execution.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
---

# Tools

A tool is an atomic action with a typed schema. Which tools a pass is shown, per pass, is
[surface](../cognition/surface.md); how a tool call waits, is handed over and settles is
[execution](../cognition/execution.md); what makes one an act is [acts](../cognition/acts.md). This
file owns the registry the surface is drawn from and the contract of a tool's result.

## The registry, the permit and the surface

- **The registry** declares every tool with its behaviour. Its descriptions are the source of
  selection text; no catalogue is written beside it.
- **A permit** admits a tool to an occasion by platform, occasion, delegation grant and executor.
  Capabilities reserved for a person's turn, for a worker or for one platform are removed before any
  prompt or loader sees them, so a surface cannot advertise what its permit refuses.
- **The tool pool** is that admitted set. The **surface** derives the resident tools, any shelf of
  deferred ones and the catalogue from it, so the three can never disagree.

## What each text answers

| text | answers | paid |
|---|---|---|
| a catalogue line | what a capability is for and when to choose it | when something is deferred |
| the schema | how to invoke it | on every call that offers the tool |
| the result | what happened and what the reply may say | only for the tool that fired |

A catalogue line is the opening of the tool's own description, spent on telling confusable siblings
apart; it states no cap on the work. A fully resident surface already carries every description and
adds no second capability block. Naming, description levels and their measurement follow
[the tool-surface decision](../../adr/0015-tool-surface-policy-v2.md).

## Deferral

Deferral keeps a measured resident set and shelves the rest for the turn; the model asks for a shelved
tool by exact name and the framework registers it for the remainder of that turn.

- The reply tool is always resident: a turn that cannot end is worse than one that pays a schema.
- A load reports loaded, already loaded, unknown and refused names, and partial success stands for
  the names that resolved. An unknown name grants nothing; an unreachable registry is unavailable,
  never "not found". A failed load never licenses guessing the tool's result.
- Loaded state belongs to one turn and one agent. It never persists in the process or reaches
  another room.
- Deferral is not inherently a saving: it adds a model call and changes the tools array, which can
  miss the provider's cache. Whole-turn cost, latency, selection and reporting decide its use,
  never resident tokens alone, and it stays off until measured.

## Results

- **Three states.** A tool answers `ok`, `nothing_found` or `unavailable`. A missing, partial or
  failed source is `unavailable` with its cause, never an empty answer, and a result that claims no
  outcome is no evidence of success. A partial result says which parts succeeded.
- **The result explains itself.** Its note is authoritative for the outcome that occurred:
  what the status means, what a field means, the safe next step and what the reply may disclose.
  A shape check preserves extra data and never invents a note or turns an error into a lookup.
- **A result cannot widen its permit.** A note may explain safe disclosure; it never grants another
  room, coordinate, identifier or action.
- **A note guides reporting and does not prove it.** Whether the reply obeyed is measured on real
  failures, refusals and empty answers, through the real tool path.
- **Retrieved text is untrusted input.** A page, a file or a release note is reference data; it
  never instructs, activates a skill or changes a rule. Source age and coverage travel with the
  result: a stale reading, a clipped page or a partial route is never a complete current answer.
- **Sources that answer different questions compose,** never fail over into a different capability.

## The pool and its limits

A pass's calls draw on the shared slot pool [execution](../cognition/execution.md) defines. These
limits follow from it:

- A call budget and a slot are different bounds: the first limits how many calls a pass may make,
  the second how many may overlap. Both apply.
- A slot is released on success, error, abandonment and cancellation.
- A tool needs its own bounded operation; the pool supplies no timeout. A call queued behind others
  runs later than it was issued, so a per-call bound must fit the turn's deadline after queuing.
- A tool never waits on another agent that draws on the same pool, because nested slot ownership
  can deadlock both.

## Why

- **One admitted set** because a catalogue built beside the registry leaves advertisements behind
  when a permit changes.
- **Results carry the interpretation** so a long instruction is paid only when it is needed.
- **Silence over a guess:** an unreachable source is never presented as a negative.
