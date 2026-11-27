---
class: design
subject: the agent, one fresh model run per pass, built from a declared occasion
owns:
  - occasion: "any place a prompt reaches a model; each one is declared and assembled, never hand-built"
  - continuity: "what carries a pass over a previous one is persisted context, never a retained agent"
  - capability source: "a tool's schema is the only statement of what Nora can do"
  - sessionless: "Nora's continuity lives in persisted records and the graph, not in a retained agent or SDK session; each pass assembles context for a fresh agent, while bounded computer and browser sessions still exist"
area: agent
links:
  - {to: docs/design/cognition/context.md, rel: uses}
  - {to: docs/design/cognition/surface.md, rel: uses}
  - {to: docs/design/cognition/execution.md, rel: uses}
---

# Agent

The agent is how a pass of either loop becomes a model run: it assembles what the model is shown,
decides what the model may call, and keeps both honest. It holds no knowledge of its own. What Nora
knows is the graph, and what a pass does is the loops'; the agent is the machinery between them
and the model.

## Parts

| part | is | defined in |
|---|---|---|
| **assembly** | declared context blocks rendered once, in one order, under one scope | [assembly](assembly.md) |
| **tools** | the registry, the permit that admits a tool to an occasion, the surface, results | [tools](tools.md) |
| **doctrine placement** | what is authored, what is derived, and what may override what | [doctrine](doctrine.md) |
| **version** | the answer to what build Nora is running | [version](version.md) |

## Runtime

- A pass builds a fresh agent, runs it, and discards it. Nothing is carried in the agent between
  passes; **continuity** is the persisted context the next assembly reads.
- The agent loop, tool dispatch, hooks, tracing and model providers are the framework's, used as
  given. One factory is the only code that builds a framework agent, so a framework change touches
  one place. The framework version is pinned, and an upgrade is gated on exercising the seams Nora
  binds to.
- Every model call reaches one gateway through one process-wide client, never one per pass.
- A pass that cannot reach the model fails as a pass ([execution](../cognition/execution.md)); it
  never sends a guess.

## Invariants

- **Every model call is a declared occasion.** A call reached through a new kind of work joins the
  same declaration and assembly; no prompt is built beside them.
- **Continuity is persisted.** A pass reads what earlier passes recorded and nothing else.
- **Schemas are the capability source.** What Nora can do is stated once, by the tools offered; no
  authored text restates it, and doctrine names no tool.
- **Retrieval and exposure are different facts.** Withholding a block from the prompt never changes
  the context, a resolver or a tool's permit.
- **Records about people are interpreted into answers, never exported as raw data**, to anyone,
  including their subject. A summary is an answer; a row-shaped itinerary or a transcript dump is
  the same disclosure in another form. Generated documents and public media are not personal
  records.
- **The model names what; code resolves where.** An argument code already knows is closed over,
  never asked of the model.

## Why

- **A fresh agent per pass** because a retained agent is a second memory with its own staleness; the
  graph is the only memory ([cognition](../cognition/README.md)).
- **Declared occasions** because an undeclared prompt cannot be reviewed, measured or replayed, and
  evaluation must run the same declaration as production.
- **Schemas as the single capability statement** because every authored copy of a capability list
  went stale the day a tool changed.
