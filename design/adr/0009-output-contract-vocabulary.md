# 0009 — Output-contract vocabulary: the three states are a core, not a ceiling

- **Status:** accepted
- **Date:** 2026-08-31
- **Supersedes:** ADR 0008 §3 (the fixed three-value enum), per adr/README.md
  (accepted ADRs are immutable — superseded rather than edited)
- **Index:** DEC-153 (pending), extends ADR 0008 (tool contract metadata),
  R-27 (three-state results), R-31 (disclosure), DEC-152 (Step 2 fields)
- **Source:** BLOCKED implementation report (2026-08-31) — the Step 3
  executor measured the live tool surface and the §3 enum does not fit it.

## Context

ADR 0008 §3 decided the output contract as a fixed enum:

```json
{ "status": { "enum": ["ok", "nothing_found", "unavailable"] } }
```

The Step 3 implementation brief (ADR 0008 §3, closed decision #3) handed that
enum to the executor. The executor BLOCKED with measurement instead of
silently narrowing scope. The live tool surface in `nora/agent/tools.py` uses
**ten** status values, not three:

| status | count | in §3 enum? | meaning |
|---|---|---|---|
| `unavailable` | 85 | ✅ | wiring gap / couldn't check (R-27) |
| `refused` | 42 | ❌ | disclosure veto — may not say (R-31) |
| `ok` | 38 | ✅ | success |
| `nothing_found` | 32 | ✅ | there is nothing (R-27) |
| `ambiguous` | 6 | ❌ | several candidates, cannot pick |
| `not_found` | 4 | ❌ | place/route does not exist (R-27b) |
| `nothing_known` | 2 | ❌ | no durable knowledge (recall) |
| `withheld` | 1 | ❌ | disclosure veto, memory axis (R-31) |
| `not_an_image` | 1 | ❌ | media modality refusal |
| `already_present` | 1 | ❌ | idempotent write outcome |
| `no_route` | (routes client) | ❌ | Google found no way (tools.py:2987) |

**Three individually disqualifying findings** (all verified against the code):

1. **`load_transcript` is status-free.** Both returns are
   `_memory_result({"messages": ...})` (tools.py:2226, 2269); the comment at
   2224-2226 names the empty list "the pre-existing (status-free) answer to a
   failed read". Under `required: ["status"]` every SUCCESSFUL call becomes
   `{"status": "unavailable", "reason": "malformed_result"}` — the recall
   ladder's middle rung dies silently.
2. **`refused` is R-31's disclosure decision, not a wiring gap.** tools.py:1230
   names it: "The three-value tool vocabulary (R-27). Every one of these is
   `refused`". Rewriting `refused` → `unavailable` converts «I may not say»
   into «I couldn't check» — the exact ERROR/absence conflation R-27 exists
   to prevent, produced by the mechanism meant to enforce R-27.
   `search_memory`'s `withheld` is the same shape.
3. **99 existing assertions across 29 test files** pin statuses outside the
   enum (72 on `refused` alone). The brief's own escalation rule fired: a
   real finding, not a test to weaken.

**Why the §3 enum was wrong.** It was written from the ADR's argument — the
proposal's clean three-state model — not from the table. The codebase's
status vocabulary is R-27's three states PLUS the declared extensions the
guards and disclosure rules already depend on. A validator covering only the
11 tools that happen to use the three states would validate nothing that can
go wrong: the tools carrying the widest vocabulary are precisely the recall
and write verbs where the honesty axis matters most.

## Decision

**The output contract is a CORE plus DECLARED EXTENSIONS, not a fixed enum.
The three states remain the semantic spine; every extension is a named,
documented, test-pinned value.**

### 1. Vocabulary

- **Core (R-27, always valid):** `ok`, `nothing_found`, `unavailable`.
- **Declared extensions** (each with a one-line meaning, derived from a named
  constant the way `SIDE_EFFECT_STATES` already is):
  - `refused` — disclosure veto (R-31): may not say, not couldn't check
  - `withheld` — disclosure veto on the memory axis (R-31)
  - `ambiguous` — several candidates, cannot pick
  - `not_found` — the thing does not exist (R-27b: distinct from
    `nothing_found` — a place the route does not serve is not "no such place")
  - `nothing_known` — no durable knowledge (recall ladder)
  - `not_an_image` — media modality refusal
  - `already_present` — idempotent write outcome
  - `no_route` — Google found no way between origin and destination
- **The semantic rule stays with the tool author.** The harness validates
  vocabulary + shape mechanically; it cannot judge whether a `refused` was
  really a disclosure veto or a lazy `unavailable`. That judgment remains with
  the tool author and the existing R-27/R-31 tests. The code comment must say
  this so nobody later claims the harness enforces semantics it cannot.

### 2. `load_transcript` — status-free is a declared shape, not a violation

`load_transcript` returns `_memory_result({"messages": ...})` with no
`status` key on either path. This is a deliberate, documented shape
(tools.py:2224-2226). Its contract is:

```json
{ "type": "object", "required": ["messages"] }
```

A tool whose contract does not require `status` is validated against its own
shape. The three-state axis is the DEFAULT for data tools, not a universal
requirement — a tool that deliberately carries no status declares so.

### 3. Validation seam (unchanged from ADR 0008 §3)

`adapt()` (ports.py:126-147) wraps `spec.call`; the wrapper validates the
returned dict against the tool's declared contract BEFORE Strands wraps it
for the model. Fail-closed: a result that fails validation is replaced with
`{"status": "unavailable", "reason": "malformed_result"}` and logged (keys
only). Exceptions propagate. MCP tools out of scope (RFC 9457 deferred to
that boundary).

### 4. `make_artifact` wrinkle (from ADR 0008 Step 3 brief, confirmed)

`make_artifact` returns `{"status": "ok", "failed": True}` when the job
failed (tools.py:6363) — the counter says `failed`, the payload says
ok+failed. Its contract accommodates an optional `failed` boolean. Normalizing
it to `status="unavailable"` is a tool-surface behavior change, out of scope,
flagged for the operator.

### 5. Enforcement

- A structural test scans every `output_contract="three_state"` declaration:
  the vocabulary constant is the single source; a status outside it fails.
- A test pins the vocabulary constant's contents (core + extensions) so an
  extension cannot be added silently.
- The Step 2 enforcement test (`tests/test_tool_contract_metadata.py`) is
  extended for the new field deliberately.
- `design/09-decision-index.md` gains the DEC-153 row.

## Consequences

**Better.**

- The validator covers ALL data tools — including the 13 with the wide
  vocabulary where the honesty axis matters most. ADR 0008's consequence
  claim — "R-27 stops being a convention" — becomes true in the artifact.
- `refused`/`withheld` keep their meaning: «I may not say» stays distinct
  from «I couldn't check». The guards' information is preserved.
- `load_transcript` keeps its status-free shape; the recall ladder survives.
- 99 existing assertions stay green — no test weakening, no tool-shape change.

**Worse, and accepted.**

- The vocabulary is larger than the proposal's clean three states. The
  extensions are each named and documented; the cost is a longer constant,
  not a looser contract.
- ADR 0008 §3 is superseded — the immutable-ADR convention means this
  document is the record, and the §3 enum is read through it from now on.
- The semantic judgment (is this `refused` really a disclosure veto?) stays
  with tool authors — the harness cannot and must not enforce it.

## Rejected

**«Narrow the declaration to the 11 clean tools and ship.»** (Executor's
option b.) It inverts the ADR's purpose: the tools with the widest vocabulary
are precisely where R-27's honesty axis matters, so a validator covering only
the already-simple tools validates nothing that can go wrong.

**«Normalize the tool surface to three states first.»** (Executor's option c.)
A real behavior change across 13 data tools and 99 assertions — explicitly out
of scope, and it would collapse distinctions the guards depend on.

**«Keep the fixed enum and let the 13 tools fail closed.»** The failure mode
is not a red test — it is `load_transcript`'s every successful call becoming
`unavailable`, and `refused` being relabeled as a wiring gap. Silent
corruption, not a loud error.
