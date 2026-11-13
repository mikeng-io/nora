# Deferred loading

**Status:** designed; built mechanism remains disabled by default

## Model-driven discovery

Deferral keeps a small measured resident set and places other admitted tools
on a per-turn `ToolShelf`. The resident catalogue tells Nora what those tools
are for; `load_tools` names the ones she wants to invoke. A pre-model intent
classifier is not a substitute: silently omitting a needed tool gives her no
way to ask for it.

The loader is a fetch by exact name, not semantic retrieval over a remote tool
store. It can resolve several names in one bounded call, reports loaded,
already-loaded, unknown and refused names, and returns no schemas in its
result body. Partial success is still success for the names that resolved.

Loading a resident or previously loaded name is idempotent. Unknown names
produce no authority and prompt a fresh reading of the catalogue. An unavailable
registry is different from a name not found. Every load remains bounded by
names, calls and the turn's ordinary shared ceilings.

## Framework and lifetime

The loader registers resolved tools on the running agent's own dynamic
registry. The framework re-reads tool specifications for the next model call,
so the schema appears natively and dispatch uses the same registered tool as
any resident call. The model is never asked to invent a call for an undeclared
tool, and schemas are not returned as prompt text.

The shelf and dynamic registry belong to one turn and one agent. Loaded tools
persist for the remainder of that turn to avoid repeated hops, then disappear.
Nothing accumulates in the process-long native registry or leaks into a
concurrent room.

Deferral adds a turn-ceiling allowance for the loading hop rather than silently
taking the call from real work. It never makes the terminal reply tool
conditional on a successful load.

## Cost and activation boundary

The first call has fewer schemas. The call after loading has a different tools
array and can miss the provider prefix cache, and the turn can contain an extra
model call. Selection can also fail when a one-line opening does not supply
enough argument information before the schema arrives.

Activation therefore requires a same-corpus, same-lane before/after showing
acceptable tool and parameter selection, reporting, end-to-end latency, model
calls and total priced input. Cached and uncached tokens remain separate;
per-call savings are diagnostics, not acceptance. The reading that changes the
default is evidence that those whole-turn properties hold, not growth in the
tool count alone.

## Failure behaviour

A failed or partial load never licenses guessing a schema or result. Unknown
names cannot be dispatched, duplicate loads do not raise through the turn, and
an absent dynamic registry returns unavailable. A turn can still end through
its resident reply path.

If loaded state appears in another turn, selection quality falls, or cache and
round-trip costs exceed the saving, the deferral design has failed its
activation boundary and remains off. The unified surface builder and selection
text remain useful independently of that decision.
