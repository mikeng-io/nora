# Tool catalogue

Every model occasion receives one coherent answer to which tools it may use.
Selection text, invocation schemas and result guidance serve different decisions
and are paid at those decision points, without creating a second capability
registry.

**Status:** built; deferred loading designed (disabled)

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Ftool-catalogue%2F%22)

## Parts

- **The native registry** declares tools and their behavior. Its descriptions,
  not a parallel hand-written catalogue, are the source of selection text.
- **Tool scope** admits by platform, occasion, Dreamer grant, worker and relay
  boundaries. It removes capabilities a turn may not call before any prompt or
  loader sees them.
- **The surface builder** derives resident tools, any deferred shelf and the
  catalogue from that single admitted set. [Catalogue](catalogue.md) defines
  selection and composition.
- **Deferred loading** can register a named shelved tool on the current agent
  for the rest of its turn. [Deferral](deferral.md) defines the mechanism and
  why it remains a measured option rather than a default.
- **Tool results** explain their own statuses and next actions. [Reporting](reporting.md)
  separates pre-call contracts from post-result interpretation.

## Terms and promises

A tool cannot appear in a catalogue when its scope cannot load it, and a loader
cannot fetch a tool the shelf does not hold. The catalogue is derived from the
same registry and scope as the callable tools, so platform filtering cannot
leave an advertisement behind.

The catalogue answers what a capability is for and when to choose it. The
schema answers how to invoke it. The result answers what happened and how to
report it. A fully resident surface already carries invocation descriptions, so
it does not also need a duplicate catalogue block.

Deferral is not inherently a saving. It adds model calls and changes the tools
array, which can invalidate provider caching. Total turn cost, latency,
selection and reporting quality decide its use, never resident tokens alone.

## Change and failure

Tools join or leave through the registry and their declared scope. Selection
openings and occasion-hot membership change on measured routing and cost, not
on intuition or a fixed quarterly calendar. New tools follow the tool-surface
ADR and its routing and reporting measurements.

Unknown names are not capabilities. A partial load keeps successful work
visible, an unwired loader is unavailable, and a failed load never licenses
guessing what the tool would have returned. Deferred state is per turn, so one
room cannot inherit another room's loaded authority.
