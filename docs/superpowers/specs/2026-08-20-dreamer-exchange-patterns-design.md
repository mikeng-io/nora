> **DEPRECATED 2026-09-07 — the language tier this document designs has been deprecated and its code deleted**, as part of the same decision that deprecated the knowledge tier.
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The language tier never ran in production — `[language] enabled = false` throughout its life. Its tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.

# Dreamer, Plan 2 — Room Exchange-Pattern Consolidator

Status: design, self-reviewed, implementing directly against this doc (autonomous
background run — no separate plan document; this doc's "Implementation sequence"
section is the plan).

## Problem

`design/09-decision-index.md`'s DEC-111 shipped Plan 1 (the room lexicon,
`nora/language/`) under a rule it explicitly only half-exercised: *"classify the
illocutionary act an utterance performs; never adopt its propositional content as
a belief about the world, and never attribute a disposition to a person."* Plan 1
never needed the hard half of that rule, because a lexeme's `sense` describes a
WORD, never a PERSON. Plan 2 is the half DEC-111 deferred: exchange patterns —
"these two trade insults, in both directions, and keep talking afterward" — are
inherently ABOUT two-or-more people, which is exactly the shape the withhold rule
exists to keep from curdling into "Person X is Y".

Miles (operator) has decided Plan 2's output renders into Nora's live context, the
same way `room_lexeme` does — raising the stakes on getting the withhold rule
right, since a leak now reaches her prefill on every turn, not just an operator's
dashboard.

## Approaches considered

**A — free-text act description + a runtime redaction filter.** The model
writes a short gloss of what happened (mirroring `sense`); a keyword/heuristic
filter scans it for disposition-shaped language before storage. Rejected:
detecting "propositional content" or "a disposition" via keyword heuristics is a
weak proxy, not a measurement — CLAUDE.md's own "never assert what you cannot
ground" rules this out, and false negatives (a claim that slips past the filter)
are exactly the failure DEC-111 exists to prevent.

**B — closed-set illocutionary-act taxonomy, no model-authored gloss field at
all.** The model may only select an `act` from a fixed, repo-authored enum (no
free text field for it to write a claim into), plus point at the evidence
(message ids, ≥2 distinct speakers). The human-readable description of each act
type is REPO text, never model text — the same move `room_lexeme.state`'s
`live`/`fading`/`stale` CHECK constraint already makes for a different field.
**Chosen.** This is a structural guarantee, not a filter: there is no field a
model could put a claim like "X is dumb" into, because the only free-text-shaped
data point in the whole row is the verbatim quote (real message body, not
model-authored), exactly Plan 1's own "the quote is what makes a wrong gloss
visible" argument, reapplied one level up. Paired with a second structural rule
below (participant count), together they make "attribute a disposition to a
person" mechanically unconstructible, not merely against policy.

**C — two-stage LLM (classify, then a second model call audits the first for
leaked disposition claims).** Rejected: an LLM auditing an LLM is still a
stochastic proxy for the property that must hold, doubles cost and latency for
every batch, and CLAUDE.md's "measure" discipline requires grounding, which a
second guess is not.

## The two structural guarantees

1. **Closed-set `act`, no free-text gloss.** The observer's prompt offers a
   fixed vocabulary (`ACTS` below); `_parse_item`-equivalent parsing REJECTS
   any value not in that set, at parse time — the same "reject at parse time"
   discipline `nora.language.observer._parse_item` already applies to
   `_MAX_FIELD_CHARS`. No `sense`/`gloss`/`description` column exists anywhere
   in this feature's schema or domain types for the model to author into.
2. **≥2 distinct participants, enforced at the DB layer.** `room_exchange`'s
   `participants` column is a sorted `text[]` with
   `CHECK (array_length(participants, 1) >= 2)` — a row describing one person
   cannot be constructed, not merely "should not be". This is Plan 1's own
   `is_nora = 0`-lives-inside-`RecordStore.since` move: the invariant lives
   where a caller cannot forget it, not in a caller that could.

`ACTS` (closed set, repo-authored descriptions used only at render time, never
sent to or accepted from the model as free text):

| `act` | repo-authored render gloss |
|---|---|
| `insult_trade` | "trade insults, back and forth" |
| `banter` | "banter — light jabs, not meant seriously" |
| `tease` | "tease each other" |
| `callback_joke` | "run a callback joke between them" |
| `agreement_ritual` | "back each other up, reliably" |
| `disagreement_ritual` | "argue a point out, repeatedly" |
| `compliment_exchange` | "trade compliments" |

Eight is deliberately small — DEC-111's own "closed set" ethos (also this
session's "doctrine as portrait" discussion: concrete examples become closed
sets, not open-ended categories) — and easier to keep every member honestly
non-dispositional (each names an ACT two-plus people DO, none names a TRAIT one
person HAS) than a larger, harder-to-audit list.

## Data flow (mirrors Plan 1 exactly — same `_dream_once`, same watermark)

`nora/app/runtime.py`'s `_dream_once` already accepts
`consolidators: Sequence[RoomConsolidator]` and runs every consolidator over the
SAME batch/watermark per tick — this is what that shape was built for. No
`runtime.py` change is needed; `ExchangeConsolidator` is added alongside
`LanguageConsolidator` in `language_consolidators` (composition.py), sharing one
watermark-per-scope semantics is a deliberate simplification: Plan 1 and Plan 2
stay in lockstep over the same evidence window, rather than each tracking its
own cursor and needing two watermark rows and two lag queries per scope. If a
future need for independently-paced consolidators arises, `consolidator_name`
is already the per-consolidator watermark key (`"language"` today) — Plan 2
uses `"exchange"` — so splitting later costs nothing already paid for.

- `nora/language/exchange_observer.py` — `ExchangePatternObserver`, the one
  LLM call, mirroring `LexemeObserver` file-for-file (same `_ModelClient`
  structural type, same layering constraints, same "reject at parse time"
  discipline, same `ObservationResult`-shaped `parsed` flag).
- `nora/domain/exchange_scoring.py` — deterministic score/decay, mirroring
  `language_scoring` (frequency + recency; no "spread" term, since
  `participants` is already fixed by the natural key, not aggregated).
- `nora/infra/sql/exchange.py` — `SqlExchangeStore`, mirroring
  `SqlLanguageStore`: watermark reuse via the SAME `consolidation_watermark`
  table (`consolidator_name="exchange"`), `exchange_observation` (evidence,
  append-only), `room_exchange` (consolidated, CHECK-guarded).
- `nora/language/exchange_consolidator.py` — `ExchangeConsolidator`, mirroring
  `LanguageConsolidator`'s extract/append/rescore sequencing exactly.
- `nora/language/exchange_render.py` — `render_room_exchange`, mirroring
  `render_room_language`'s three rulings (store owns ordering; an entry with
  no quotable example must not render; empty renders no block) plus a FOURTH
  ruling this feature adds: the closing frame states the withhold rule in
  plain language, the same defence-in-depth `render.py` already applies to
  per-field caps (a structural guarantee PLUS a stated one, not one instead
  of the other).

## Schema (migration `0019_exchange_patterns`)

```
exchange_observation (evidence, append-only, natural-keyed)
  id, scope_id, conversation_id, act, message_ids int[] (>=2, evidence),
  participants text[] (>=2, sorted person_id, NULL entries excluded),
  observed_at bigint, extractor_confidence float
  UNIQUE (scope_id, act, message_ids)   -- natural key: same claim replayed is a no-op

room_exchange (consolidated)
  scope_id, act, participants text[] (PK triple; sorted, CHECK array_length>=2)
  first_seen_at, last_seen_at, occurrence_count, recognition_score, state
  examples jsonb (capped, most-recent-first, same shape as room_lexeme.examples)
  CHECK (array_length(participants, 1) >= 2)
  CHECK (state IN ('live','fading','stale'))
```

`participants` as a sorted `text[]` (not a junction table) mirrors
`room_lexeme.examples`'s own "Postgres-native, `tool_call_log` precedent"
argument — this repo already keeps small structured arrays as native Postgres
arrays/JSONB rather than a normalized side table when nothing needs to query
INTO the array's elements independently (nothing here does: every read is
keyed by the whole `(scope_id, act, participants)` triple, never "find every
row this person appears in").

`message_ids`/`participants` on `exchange_observation` both carry the CHECK
`array_length(...) >= 2` too, at the EVIDENCE layer, not only the consolidated
one — an evidence row that could never legally consolidate should not be
constructible in the first place (the same "reject at parse time, not later"
discipline, pushed one hop further than Plan 1 pushed it, because Plan 2's
extra guarantee has no upstream Python-side equivalent to `_parse_item`'s
per-field length check unless it is repeated at both layers).

## Rendering

`<room_exchange>` — a new prefill block, threaded exactly like
`<room_language>`: `RoomExchangeReader` port → `TagContext.room_exchange` field
→ `TranscriptInjection._room_exchange` fetch (added to the existing
`asyncio.gather` alongside `_room_language`) → `prefill.py`'s
`_room_exchange_block` → `EngagementPipeline`/`FirePath` thread a new
`room_exchange_max_entries` config value, mirroring `room_language_max_entries`
at every one of those same call sites.

Closing frame (the FOURTH rendering ruling, stated on top of the structural
guarantees):

> "This describes patterns of interaction between these people — not a claim
> about any one of them, and not something to reach for."

## Config (`[language]` gains three fields — same section, not a new one: this
is still the Dreamer, one `dream_loop`, one `enabled` flag)

- `exchange_model: str | None = None` — defaults to `language.model` if unset
  (mirrors `[language].model`'s own default-from-`[llm]` precedent one level
  down).
- `max_rendered_exchanges: int = 4` — smaller than `max_rendered_lexemes = 6`
  deliberately: this block sits in the same UNCACHED volatile tier
  (`render.py`'s own cost argument), and an exchange-pattern line is a claim
  about TWO people rather than one word, which argues for being more
  conservative about how many ride on every turn's token cost, not less.
- `exchange_store_limit: int = 10` — mirrors
  `DEFAULT_ROOM_LANGUAGE_STORE_LIMIT`'s "wider than rendered" relation; same
  `Config._render_cap_stays_below_the_store_limit`-shaped guard applies to
  this pair too.

`enabled` is NOT duplicated — Plan 2 is gated by the SAME `[language].enabled`
flag as Plan 1 (both are consolidators the one Dreamer runs); there is no
independent on/off switch for exchange patterns alone in this iteration, since
splitting it would need its own operator decision this design does not make on
Miles's behalf.

## New behavioural rule → decision index + acceptance test

`DEC-120`: *"The exchange-pattern consolidator cannot construct a
disposition-about-a-person row: no field carries a model-authored claim about
either party (only a closed-set `act` plus a verbatim quote), and no row can
describe fewer than two people (DB CHECK, not caller discipline)."*
Test: `test_acceptance_rules.py::RULE_TESTS["DEC-120"] =
"test_exchange_pattern_cannot_attribute_a_disposition_to_a_person"` — asserts
(a) the parser rejects any `act` outside the closed set, at the SAME layer
`_parse_item` rejects an oversized field, and (b) a 1-participant row is
refused by the database, not merely by application code (a direct INSERT
against `room_exchange` with one participant must raise
`IntegrityError`/`DBAPIError`, mirroring
`test_her_own_words_are_never_evidence_for_the_room_lexicon`'s own "prove it at
the layer that cannot be bypassed" shape).

## Testing plan

- `tests/unit/test_exchange_observer.py` — parse/reject shapes (closed-set
  `act`, `message_ids` array length, hallucinated evidence dropped), mirroring
  `tests/unit/test_language_observer.py` structure.
- `tests/unit/test_exchange_scoring.py` — mirrors
  `tests/unit/test_language_scoring.py`.
- `tests/infra/test_exchange_store.py` (or folded into the existing language
  store test file if one exists) — append/rescore/CHECK-constraint-rejects-
  single-participant, against a real Postgres test database, mirroring
  `SqlLanguageStore`'s own test file.
- `tests/unit/test_exchange_consolidator.py` — extract/append/rescore
  sequencing, mirroring `LanguageConsolidator`'s.
- `tests/unit/test_exchange_render.py` — the three inherited rulings plus the
  fourth (closing-frame text), mirroring `test_render.py`'s language-render
  suite.
- `tests/contract/test_room_consolidator_contract.py` — `ExchangeConsolidator`
  added as a second parametrization, proving it too satisfies
  `RoomConsolidator` behaviourally (idempotent replay, scope isolation), not
  only by type.
- `tests/infra/test_schema_parity.py` — regenerated baseline against the full
  chain through `0019_exchange_patterns`.
- `tests/test_acceptance_rules.py` — DEC-120's test, per above.
- Prefill/engagement wiring gets the SAME test-shape `room_language`'s wiring
  already has at each layer (`test_context.py`, `test_prefill.py`,
  `test_fire.py`/`test_pipeline.py` — extend existing tests rather than
  duplicate suites, mirroring how `room_language` itself is tested at each
  hop).

## Implementation sequence

1. Schema: `nora/infra/sql/schema.py` additions + migration
   `0019_exchange_patterns.py` (plain, non-concurrent index — new tables are
   empty at every deployment this reaches, same as `0013`'s own
   `idx_language_observation_scope`). Regenerate schema-parity baseline.
2. Domain: `nora/domain/models.py` — `ExchangeExample`, `RoomExchangePattern`,
   `RoomExchange` (mirroring `LexemeExample`/`RoomLexeme`/`RoomLanguage`);
   `ExchangeCandidate` (mirroring `LexemeCandidate`).
3. `nora/domain/exchange_scoring.py`.
4. `nora/domain/ports.py` — `RoomExchangeReader` Protocol (mirroring
   `RoomLanguageReader`).
5. `nora/language/exchange_observer.py` — the one LLM call.
6. `nora/infra/sql/exchange.py` — `SqlExchangeStore`.
7. `nora/language/exchange_consolidator.py`.
8. `nora/language/exchange_render.py`.
9. Config: `LanguageConfig` gains the three fields; `config.example.toml`
   documents them (inert defaults, matching `[language]`'s own ship-inert
   precedent — the tracked `config.toml` is NOT touched, per this run's own
   constraint against flipping anything live).
10. Wiring: `TagContext.room_exchange`, `TranscriptInjection`,
    `nora/agent/prefill.py`, `EngagementPipeline`/`FirePath`,
    `nora/app/composition.py` (build `ExchangeConsolidator`, add to
    `language_consolidators`, build `RoomExchangeReader` alongside
    `language_store`).
11. Decision index `DEC-120` + `tests/test_acceptance_rules.py` entry.
12. Release fragment.
13. Gates: `pytest -q`, `ruff check`, `mypy` (scoped dirs), `lint-imports`,
    schema-parity, release-fragment checker.
14. Commit, push, PR against `master` (no merge).

## Spec self-review

- Placeholders: none — every field/table/module above is fully specified.
- Internal consistency: rendering cap ordering (`max_rendered_exchanges <
  exchange_store_limit`) matches the existing `Config._render_cap_stays_below
  _the_store_limit` pattern and will be covered by the equivalent guard.
- Scope: this doc covers Plan 2 only. Plan 3 ("the portrait", a per-room
  narrative) is explicitly NOT designed here — it consumes this plan's
  `room_exchange` output as one input, per the earlier conversation's own
  sequencing decision, and gets its own design.
- Ambiguity: the "one shared watermark vs. two independent ones" choice is
  made explicitly above (shared, via `consolidator_name` already being the
  per-consolidator key) rather than left implicit.
