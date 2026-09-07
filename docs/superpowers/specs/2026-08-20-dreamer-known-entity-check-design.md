> **DEPRECATED 2026-09-07 — the language tier this document designs has been deprecated and its code deleted**, as part of the same decision that deprecated the knowledge tier.
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The language tier never ran in production — `[language] enabled = false` throughout its life. Its tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.

# Dreamer known-entity check — design

Status: proposed, implementing same session. Autonomous build (Miles: "add
web_search + web_fetch capability" to the Dreamer, "just build ALL plan first").
No live human review of this specific doc before implementation — the choices
below are justified inline so they can be reviewed after the fact.

## Problem

`nora/language/observer.py`'s `LexemeObserver` (Plan 1 of the Dreamer, the only
plan shipped) flags "slang this room has made its own" from one LLM completion
over a batch of messages. Two confirmed false positives from live data
(2026-08-17 → 2026-08-19, scope `MP4ODQOAWL/…`):

- **"Big Walk"** — recorded as a room-coined lexeme (`room_lexeme` rows for
  both `Big Walk` and `big walk`, occurrence_count=1 each). It is an actual
  game already tracked by Pulse (`nora/web/games.py`), not local slang.
  DEC-112 (`design/09-decision-index.md`) independently records Nora's main
  chat agent successfully answering a real Big-Walk question via 4 `web_search`
  calls (sportskeeda, gamersky) — so the knowledge to disambiguate this
  already exists and is reachable, just not from the Dreamer.
- **"小KAM"** (message id 873, same scope) — the message itself asks "定係你哋
  已經有晒內部典故,我唔識貨" ("or do you already have inside lore I don't know
  about?") and was NOT extracted at all, despite self-flagging as a
  known-vs-local question — evidence the single extraction call under-catches
  as much as it over-catches, but out of scope for this change (see
  "Out of scope" below).

## Approach

Add an optional **known-entity verification step** after extraction, not a
multi-turn tool loop inside `observe()`. Reasons:

1. `nora/language/` may not import `strands` (`tests/test_boundaries.py::
   test_strands_import_boundary`) — a real agentic tool loop would need
   Strands or an equivalent, which this package cannot reach.
2. The module's own stated design principle (`observer.py`'s docstring): "an
   LLM glosses, arithmetic decides adoption, and folding them into one call
   would hand a policy decision to a stochastic process." A second, narrow,
   single-purpose call for ONE question ("is this already a known public
   thing?") keeps that separation rather than growing one call's
   responsibility.
3. `nora.domain.ports.SearchProvider` already exists, already has one
   implementation (`TavilySearchProvider`) and a passing contract test
   (`tests/contract/test_port_conformance.py::
   test_tavily_provider_satisfies_the_search_provider_port`), and `nora.domain`
   sits BELOW `nora.language` in the `pyproject.toml` layers contract — so
   `nora.language` can import it directly. No new port, no new contract test:
   this is a new CONSUMER of an existing port, not a new implementation.

### Flow, per well-formed candidate (only when `search` is wired and the batch
extraction `parsed=True` with at least one candidate)

```
for candidate in candidates[:verify_max_candidates]:
    result = await search.search(query=candidate.canonical_form)
    match result["status"]:
        "unavailable"   -> KEEP candidate  (fail open — see below)
        "nothing_found" -> KEEP candidate  (no evidence it's a public thing)
        "ok":
            verdict = await model.complete(prompt=<candidate + snippets, judge>)
            parsed  = strict {"known_entity": bool} or nothing else
            parsed is None       -> KEEP candidate  (fail open)
            known_entity == True -> DROP, count reason="known_entity"
            known_entity == False -> KEEP
candidates[verify_max_candidates:] -> KEEP unchanged (cap, see below)
```

### Fail-open, in every failure branch — and why

A verification-step failure (search unavailable, or the classification
response does not parse) always **keeps** the candidate rather than dropping
it. Two failure directions were possible; this repo already answered the
question once, for the SAME field, in DEC-111: `_extract`'s existing rule is
"fabricated provenance is worse than a missing entry" — i.e. when in doubt,
prefer keeping real (if imperfect) evidence over discarding it, because
`_dream_once` advances the watermark past this batch regardless of what
`observe()` returns (append-first / watermark-last, `nora/app/runtime.py`) —
a dropped-for-the-wrong-reason candidate is gone forever, with nothing to
retry. This verification step is a NEW filter layered on top of extraction
that did not exist before; failing it open reproduces exactly today's
(imperfect, but shipped and accepted) behaviour, so a Tavily outage or a
model hiccup can never make the Dreamer WORSE than it is without this change
— only occasionally not-better. The opposite choice (fail closed / drop on
any verification uncertainty) would mean a third-party outage silently
suppresses genuine room slang with no operator signal beyond a metric nobody
is paged on — a worse failure mode than the false positive this change exists
to fix.

### `verify_max_candidates` — bounding the tail, not the common case

Live data shows 0-3 candidates per fired batch. A cap of 10 (new config,
`ge=1`, default 10) bounds `_dream_once`'s worst-case added latency to
`10 * (search_timeout + model_timeout)` without touching the common case.
Candidates beyond the cap are kept unverified — same fail-open reasoning as
above: "we didn't get to check" is treated identically to "the check was
unavailable," never as "assume the worst and drop."

### No new timeout config

- The search call already carries its own bound: `TavilySearchProvider`
  wraps its own HTTP calls in `aiohttp.ClientTimeout` from `[search]
  .timeout_seconds` (12.0s default) and NEVER raises (R-5) — `unavailable` on
  any transport/timeout failure, already handled by the fail-open branch
  above.
- The classification call reuses the SAME injected `_ModelClient` instance
  `observe()` already calls for extraction — in production that is
  `_LanguageModelClient` (`nora/app/composition.py`), which already wraps
  EVERY `complete()` call in `asyncio.wait_for(timeout=self._timeout_seconds)`
  using `[language].timeout_seconds` (120.0s default). No new knob needed;
  the existing bound already covers this second call for free, since it is
  the exact same collaborator object making a second, smaller-prompt call.

## Config (`LanguageConfig`, `nora/config.py`)

Two new fields, both defaulting to the shipped-inert `[language]`/`[search]`
precedent — a new capability never turns itself on:

```python
verify_known_entities: bool = False
verify_max_candidates: int = Field(default=10, ge=1)
```

`verify_known_entities` is a THIRD gate, not implied by `[language].enabled`
and `[search].enabled` both being true — verifying spends an extra Tavily
credit AND an extra model completion per candidate, and an operator who
turned search on for the chat agent has not thereby decided to spend that
budget on the Dreamer too. Composition wires the collaborator only when all
three are true AND a search client was actually built (mirrors the existing
`cfg.language.enabled and language_store is not None` guard pattern).

`config.example.toml` ships both new fields commented out at their defaults,
matching every other `[language]` field's own precedent. The tracked
`config.toml`'s live values are NOT touched by this change — enabling
verification in the actually-deployed config is an operator decision, same
class as the 2026-08-17 "false → true" decision for `[language].enabled`
itself, out of scope here.

## `LexemeObserver.__init__` — new collaborator

```python
def __init__(
    self, model: _ModelClient, *, metrics: Metrics | None = None,
    search: SearchProvider | None = None, verify_max_candidates: int = 10,
) -> None: ...
```

`search: SearchProvider | None = None` — the `metrics`-style optional
collaborator pattern this class already uses: a caller with no search wired
(every existing unit test, and any deployment with `verify_known_entities =
false`) gets EXACTLY today's behaviour, unchanged. `SearchProvider` is
imported from `nora.domain.ports` directly (not a new local structural
Protocol) — see "Approach" §3.

## Observability

- Extends the EXISTING `nora_language_candidates_dropped_total{reason=...}`
  counter with a new label value, `reason="known_entity"` — same metric,
  same shape as the existing `no_evidence`/`not_found` values documented in
  `nora/obs/metric_specs.py`, not a new series.
- New counter `nora_language_verification_total{status="ok"|"nothing_found"|
  "unavailable"}` — the search call's own outcome, independent of whether it
  led to a drop. Without this, an operator watching only the drop counter
  cannot tell "verification is running and finding nothing to drop" from
  "verification is silently never running" — the exact liveness gap
  `416d68a` fixed for the loop itself, applied here to the new sub-step.

## Out of scope (explicitly, so it is not silently assumed later)

- Fixing 小KAM's non-extraction (a miss in the FIRST call, not something this
  known-entity check touches — it only ever narrows what extraction already
  found, never widens it).
- Any change to `web_search`'s own egress screening (`nora/agent/tools.py`'s
  identifier/coordinate guard) — this change calls `SearchProvider.search`
  directly, one layer below that tool-level screening, the same way
  `TavilySearchProvider` itself has no screening and relies on its callers
  for it. The query here is always a `canonical_form` the model itself
  produced from real message evidence, not raw user input, and Tavily fetches
  from its own servers (no SSRF surface either way, unchanged DEC-73
  argument) — but this is worth a second look before `verify_known_entities`
  is ever flipped true in the live `config.toml`, since it is a new place a
  model-influenced string leaves the process.
- Plan 2 (exchange patterns) and Plan 3 (the portrait) — separate specs.
