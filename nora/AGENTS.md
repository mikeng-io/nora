# nora/: the layer rules

Code is grouped by area (`nora/<area>/`), each in three layers that import
inward only. The area table is ARCHITECTURE.md «Code map»; the procedure for
adding, moving or splitting code is the `nora-structure` skill.

## domain/

Models, rules and the ports the area needs. Imports stdlib and `nora.domain`
only; a framework, SQL or HTTP import means the code is in the wrong layer.
Values are frozen dataclasses that check their own invariants.
Check: nothing here knows how it is stored, sent or shown.

## app/

The area's use cases: they coordinate domain objects through ports. A rule
written here belongs in `domain/`; a query or client written here belongs in
`adapters/`. Results keep R-27's three states.
Check: every side effect is a port call named at the use case.

## adapters/

Implements the domain's ports against real systems (SQL, HTTP, Strands tools,
context blocks). Every failure surfaces here as `unavailable`, never as
`nothing_found`; no framework type leaks into `app/` or `domain/`.
Check: a tool or block is a thin call into a use case, not a second copy of it.

## wire.py

Builds the area's adapters from the shared dependencies and returns what the
runtime registers. Wires nothing outside its area.

## Between areas

One area reaches another only through `nora.<area>`, never a submodule. Shared
types live in `nora.domain`. Code not yet moved into an area follows the same
rules when touched.
