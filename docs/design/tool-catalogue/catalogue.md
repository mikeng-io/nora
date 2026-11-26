# Catalogue and surface composition

## One admitted set

`ToolRegistry.surface()` is the single builder for engagement, fire occasions
and Dreamer adaptors. `ToolScope` supplies the platform, occasion and optional
grant ceiling; worker-only, tag-only and relay-refused capabilities remain
explicit scope rules rather than filters reimplemented by each caller.

The builder returns a `ToolSurface`: the tools handed to the agent, the
resident-name set, a per-turn shelf, admitted names and any required loader
hooks. Catalogue text is rendered lazily from the admitted names and the same
opening paragraphs used by the native tools.

A catalogue, resident set and shelf are three views of one admission decision.
This is the invariant that prevents a Signal room from being offered Discord
music, a fire turn from launching tag-only work, or a worker from receiving a
capability reserved for a person's turn.

## Selection text

A catalogue line is the tool description's opening paragraph: what the tool
does, when to reach for it and, where needed, what it returns. Confusable
siblings spend their few words on the discriminator between them, not on
parameters, status catalogs, examples or reporting instructions. A hard cap is
never stated in catalogue text: stating one makes the model decline work it
could do.

The opening is a gated tool-authored surface, not a second summary maintained
beside the docstring. Detailed invocation constraints belong later in the
schema; result interpretation belongs in the result. The tool-surface ADR owns
naming, description levels and their measurement.

Resident tools are marked consistently with the actual resident set. `send_reply`
must remain immediately available under deferral because a turn that cannot end
is worse than one that pays its schema. Other hot tools come from observed use;
a tool may also declare a measured occasion, such as voice, where an extra
loading round trip is especially costly.

Occasion-hot declarations are still intersected with scope. A text turn does
not inherit a voice-specific surface, and occasion-specific schema growth is
weighed against its cache split and the loading hop it avoids.

## Prompt placement and caching

The catalogue is a declared context-assembly block derived from the surface,
not operator-authored doctrine. It is present only when deferral leaves
something to discover; a fully resident surface already has its schemas and
adds no second capability block.

The relevance gate does not automatically inherit the catalogue. Its narrow
read-only capability digest shares the line renderer but remains a separate
admission decision.

Resident prefixes can be stable across turns. Loading changes the tools array
on the next model call, so a smaller prompt may still cause a cache miss and
an additional model call. Evaluating the catalogue therefore means measuring
whole turns, including cached and uncached input, model calls, latency and
selection, not admiring a smaller first call.

## Failure and drift

The catalogue cannot be the only place a real capability is declared, and it
cannot name a tool absent from the shelf. Duplicate or indistinguishable lines
are selection defects to repair at their source, not a reason to enlarge every
line.

If Nora never requests a needed shelved tool, that is a visible routing
failure to measure, not evidence that the capability does not exist. If she
loads resident tools repeatedly, the marker and opening need repair. If a
turn grows more expensive or less accurate, deferral loses even when its
resident-token diagnostic improves.
