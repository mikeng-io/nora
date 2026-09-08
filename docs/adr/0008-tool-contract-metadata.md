# 0008 — Tool contract metadata: side-effect state, idempotency, retry, output contracts

- **Status:** accepted
- **Date:** 2026-08-31
- **Index:** DEC-152 (accepted; the row landed 2026-08-31), extends ADR 0007 (tool-surface policy), ADR 0006
  (reasoning effort), DEC-93 (recovery), DEC-95 (bursts), DEC-150 (schema is the
  single source of truth), R-27 (three-state results), R-2 (delivery latch),
  R-25 (partial-reply accounting)
- **Source:** operator review of an external proposal (2026-08-31) — "Nora tools
  should behave as explicit system interfaces consumed by an LLM, not
  natural-language suggestions that happen to invoke functions."

## Context

ADR 0007 fixed the **description** — the routing surface. This ADR fixes the
**contract** — the machine-readable metadata that describes what a call *does*
to the world, and what its result *means*. The two are different layers and the
external review that prompted this got the split right:

```
Doctrine        → global invariants (BCP 14 register, ADR 0007 §2)
Tool Semantics  → what + when-to-call (ADR 0007 §1)
Schema          → input/output contracts (this ADR)
Execution Policy→ per-tool declared metadata (this ADR)
Harness         → deterministic enforcement (ReplyGuard/Deliverer/check_outcome)
```

**What Nora already has** (measured, not assumed):

- `NativeTool` declares `budget`, `terminal`, `time_shiftable`, `source_tier`,
  `cacheable`, `read_only` (tools.py:199-271) — richer than MCP's four hints,
  and test-enforced (`tests/unit/test_tool_registry.py`). `ToolSpec` adds
  `disclose_failure` and `timeout_seconds` (ports.py:96-97) — **both are
  declared-but-unread today** (ports.py:33-39 names `disclose_failure` as
  deferred to its own phase; nothing reads `spec.timeout_seconds` —
  factory.py:295 uses the per-tier `llm.timeout_seconds`). The new fields in
  this ADR must NOT inherit that pattern: each ships with its reader (an
  enforcement test or a harness use), or it is not added.
- R-27's three-state result (`ok` / `nothing_found` / `unavailable`) is a
  **convention** — every data tool returns it, but nothing validates it.
- The harness already protects delivery invariants: `check_outcome` splits
  `delivered`/`called` (deliver.py:222-238), `real_outcome` distinguishes
  `send_reply`/`recovered`/`vetoed`/`silent` (pipeline.py:960-964), R-2's latch
  counts attempts not successes, R-25 counts partial replies.
- The provider ignores `tool_choice=required` (ADR 0007 Rejected section) — the
  harness recovers, it cannot force. That is upstream and out of scope here.

**What is missing** — three gaps, each with a live cost:

1. **Side-effect state is a comment, not a contract.** `pipeline.py:837-851`
   says "Messages may ALREADY have gone out — under multi-message that is the
   common shape" — a timeout does not prove the write failed. That is exactly
   the proposal's `side_effect_state: unknown` case, and it lives in a comment.
2. **Idempotency and retry safety are conflated.** `media_fetch` is
   `read_only=True` but writes a cache row (`tools.py:261-270` names it
   "arguably a lie"). R-2's no-auto-retry policy is a deliberate choice with no
   declared field behind it.
3. **Output contracts are prose.** R-27's three states are enforced by
   convention and by tests that read docstrings — not by a validated schema
   the harness can check.

## Decision

**Extend `NativeTool` with four declared fields, and add an output-contract
schema for data tools. Adopt BCP 14 by reference in ADR 0007. Do NOT adopt RFC
9457 or MCP hints for native tools.**

### 1. `side_effect_state` — what a call may have done to the world

A four-valued field, declared per tool:

| value | meaning | example |
|---|---|---|
| `none` | the call changes nothing outside the turn | `weather`, `calculate` |
| `not_applied` | the call is a write that failed before applying | a refused `send_reply` (R-2/R-4/R-6/R-18 veto) |
| `confirmed` | the write is known to have applied | a delivered `send_reply` |
| `unknown` | the call may have applied and the runtime cannot tell | a `send_reply` that timed out mid-send |

Rules:

- `unknown` is the honest answer for any external write whose outcome the
  runtime cannot observe. A timeout is `unknown`, never `not_applied` — the
  proposal's own "a timeout does not prove an external write failed" is the
  rule, and it is already Nora's live behaviour (pipeline.py:837-851).
- The field is **declared, not derived**: a tool that writes through an
  unobservable boundary declares `unknown` up front, so the harness and the
  model both know what a failure means before it happens.
- `read_only=True` tools MUST declare `side_effect_state="none"` — with the
  `media_fetch` cache exemption narrowed: a content-addressed cache write is
  `none` for *answer* purposes (replaying changes no answer) but the field
  still records the write exists. The exemption stays exactly as narrow as
  `tools.py:261-270` states it.

### 2. `idempotency` and `retry` — separate concepts, separate fields

| field | values | meaning |
|---|---|---|
| `idempotency` | `idempotent` / `non_idempotent` | replaying the call with the same args produces the same effect |
| `retry` | `safe` / `unsafe_without_key` / `never` | whether the harness may retry after an `unknown` outcome |

Rules:

- `idempotency` is a property of the operation; `retry` is a policy of the
  harness. They are not the same axis — the proposal is right and Nora's
  current single `read_only` boolean cannot express either.
- `send_reply` declares `idempotency="non_idempotent"`, `retry="never"` — R-2's
  latch already counts attempts not successes and v2 has no auto-retry; the
  field makes the policy declarative instead of buried in a docstring.
- `media_fetch` declares `idempotency="idempotent"` (content-addressed cache),
  `retry="safe"` — the one case where a retry after `unknown` is free.
- A tool that declares `retry="unsafe_without_key"` must name the key in its
  schema (the proposal's shape); nothing in the current registry needs it, but
  the field exists so a future write tool cannot ship with an implicit retry
  policy.

### 3. Output contracts — the R-27 three-state becomes a schema

Every tool whose result carries the three-state status declares an output
contract — data tools (`source_tier` is not `None`) AND action tools that
report outcome honestly, e.g. `make_artifact` (returns `unavailable` before
launch, `ok` with `url` after — the same R-27 axis on an action verb):

```json
{
  "type": "object",
  "properties": {
    "status": { "enum": ["ok", "nothing_found", "unavailable"] },
    "data": { "type": ["object", "null"] }
  },
  "required": ["status"]
}
```

Rules:

- The harness validates the result against the contract before it reaches the
  model. A result that fails validation is `unavailable`, never a pass — R-27's
  "an ERROR is never a PASS" becomes mechanical.
- `nothing_found` and `unavailable` remain distinct and non-interchangeable:
  a wiring gap is `unavailable` («I couldn't check»), never `nothing_found`
  («there's nothing»). This is the one place the external proposal's RFC 9457
  has no slot — Problem Details models error *types*, not the three-state
  honesty axis Nora's R-27 exists for.
- The contract is declared once per tool (beside the docstring), and the
  enforcement test derives it from the code — the same shape ADR 0007's
  structural test uses for descriptions.

### 4. BCP 14 — already the doctrine loader's enforcement model; adopt by reference, no new work

BCP 14 is **already implemented in two layers**, and the doctrine layer is the
stronger of the two:

- **Doctrine files** (`nora/doctrine/loader.py:93-117`): the kind taxonomy is
  read *through* RFC 2119 — `constraint` is a MUST, `conduct` is a SHOULD,
  `identity`/`voice` describe and carry neither. `_FORBIDDEN_KEYWORDS` makes a
  category error a **startup failure** (a `constraint` containing SHOULD-family,
  a `conduct` containing MUST-family, or any normative keyword in
  identity/voice), scoped to UPPERCASE per RFC 2119 §8. `MAY`/`OPTIONAL` are
  deliberately omitted — permission is not a claim any kind's register
  contradicts. Tests: `test_a_conduct_line_written_in_must_is_a_category_error`,
  `test_a_descriptive_file_carrying_any_normative_keyword_is_rejected`.
- **Tool descriptions** (ADR 0007 §2): the same register split, test-enforced
  across all 49 descriptions.

Formal adoption is recorded HERE (this ADR cites BCP 14 / RFC 8174) and in one
line in the loader's comment block naming the standard it already implements.
ADR 0007 is not edited — accepted ADRs are immutable (adr/README.md) — this
document is the adoption record. The
proposal's example doctrine line — "a completed turn MUST produce no more than
one user-visible response" — is **rejected**: it conflicts with DEC-95, where
bursts are first-class (`send_reply` budget 6, MAY multiple calls). The correct
invariant is "a completed turn MUST deliver via `send_reply`" — the delivery
mechanism is the MUST, not the count.

### 5. Rejected: RFC 9457 and MCP hints for native tools

- **RFC 9457** (Problem Details): adopt the *fields* (`error_type`,
  `retryable`, `retry_after`, `side_effect_state`) as vocabulary, skip the
  media type for native tool results. It would fit at the MCP/external-tool
  boundary (`McpToolGroup`) where structured error bodies are genuinely useful
  — that is a separate decision, not this one.
- **MCP hints** (`readOnlyHint`/`destructiveHint`/`idempotentHint`/
  `openWorldHint`): do not adopt for native tools. `NativeTool` is already
  richer, and MCP hints are advisory while Nora's table is test-enforced. The
  proposal's own "Nora may maintain stronger metadata" is the right call.

## Consequences

**Better.**

- A timeout on `send_reply` stops being a guess: the harness knows the
  side-effect state is `unknown` and can count it as such (R-25's partial
  reply, R-23's stop-reason split — now with a declared field behind them).
- The `media_fetch` "arguably a lie" (`read_only=True` + cache write) gets a
  truthful declaration: `read_only` stays for the Dreamer ceiling, and the
  idempotency field carries the cache exemption honestly.
- R-27 stops being a convention: a tool that returns a fourth status, or
  `nothing_found` for a wiring gap, fails the gate instead of reaching the
  model.
- Future write tools (events family, `send_gif`, `make_artifact`) ship with a
  declared retry policy instead of inheriting an implicit one.

**Worse, and accepted.**

- Four new fields on `NativeTool` means every existing entry must declare them
  (or take a fail-closed default). The defaults are conservative: `none` /
  `non_idempotent` / `never` — a tool that forgets to declare is treated as a
  write with no retry, never as a safe read.
- The output-contract validation adds a runtime check on every data tool
  result. The cost is one dict validation per call — negligible against the
  model round-trip it protects.
- This ADR does NOT fix the provider ignoring `tool_choice=required`. That is
  upstream (ADR 0007 Rejected section) and remains the one invariant the
  harness cannot protect — it recovers, it cannot force.

## Rejected

**«Adopt RFC 9457 wholesale.»** The three-state honesty axis is Nora's R-27
and Problem Details has no slot for it. Fields yes, media type no — for native
tools. (See §5.)

**«Adopt MCP hints for native tools.»** Advisory metadata is a downgrade from
test-enforced declarations. (See §5.)

**«The harness should force `send_reply` (tool_choice enforcement).»** The
provider accepts-and-ignores `required` (ADR 0007 Rejected). The harness
already recovers via DEC-93; forcing is a provider fix, not a harness one.

**«One user-visible response per turn.»** Conflicts with DEC-95 bursts. The
MUST is the delivery mechanism, not the count. (See §4.)
