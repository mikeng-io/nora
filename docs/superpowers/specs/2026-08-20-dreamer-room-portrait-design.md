# Dreamer, Plan 3 — Room Portrait

Status: design, self-reviewed, implementing directly against this doc (autonomous
background run — no separate plan document; this doc's "Implementation sequence"
section is the plan). Stacked on Plan 2 (PR #12, unmerged) — this branch is based
on `dreamer-plan2-exchange-patterns`, not `master`.

## Problem

Plan 1 (`room_lexeme`) and Plan 2 (`room_exchange`) each render their own block
into Nora's live context. Miles's ask: a third, synthesized view — "the portrait"
— that reads as a room's overall character rather than two separate itemized
lists. Miles has decided the shape explicitly: a **per-room narrative**, not a
per-person profile — he rejected per-person profiles specifically because they
would conflict with DEC-111's rule.

## The rule this still sits under (DEC-111, quoted verbatim)

> classify the illocutionary act an utterance performs; never adopt its
> propositional content as a belief about the world, and never attribute a
> disposition to a person.

Plan 2 made this unconstructible for *exchange rows* (closed-set `act`, DB
`CHECK (array_length(participants,1) >= 2)`). Plan 3's own hard problem is one
step further downstream: `RoomExchangePattern.participants` and
`LexemeExample.person_id` are real, populated fields on Plan 1/2's OWN read-side
domain types — narrating "this room bickers a lot" is fine; a synthesis step
that is handed those fields at all risks a future edit accidentally turning that
into "Miles and Tyler bicker a lot." The guarantee has to be structural at the
narrative-construction boundary, not "we chose not to print it" (Plan 2's own
render already makes that choice correctly, but choice is not a guarantee).

## Approaches considered

**Trigger shape:**

**A — implement `RoomConsolidator`, trigger off raw-message lag/quiet/age
like Plan 1/2.** Rejected: the portrait's real input is "did `room_lexeme` or
`room_exchange` change", not "did new messages arrive". Both already exist as
weak proxies for that (the shared watermark advances on an EMPTY batch too, to
reset `age`'s clock — `_dream_once`'s own documented plan-defect-fix). Binding
the portrait's trigger to the same proxy would mean it resynthesizes on ticks
where nothing changed, and could in principle skip a tick where something did
if `age`'s reset logic diverges from actual content change. CLAUDE.md: "never
assert what you cannot ground" — this is exactly a proxy standing in for the
real property.

**B — a new, independent polling loop, own tick/backoff/lock machinery.**
Rejected: duplicates `dream_loop`'s per-scope lock/backoff/task-tracking
machinery (`runtime.py:303-429`) for no reason — the actual trigger signal
(did a consolidator append anything) is already computed one call away.

**C — chosen: extend `_dream_once` with an optional post-consolidator step,
triggered on `sum(appended counts) > 0` for that scope this tick.**
`RoomConsolidator.consolidate` already returns "observations appended" (`int`,
`nora/domain/ports.py:696-706` — "fixed here so a second implementation cannot
mean something else by it"), and today's `_dream_once` computes it but never
reads it. This is the actual real-world property ("did Plan 1 or Plan 2 learn
something new this cycle"), not a proxy for it — it fires exactly when there is
new material to narrate, and never on a tick where both consolidators found
nothing. Reuses `dream_loop`'s existing per-scope lock/backoff for free (the
portrait step runs inside the same `_run_one`, so a portrait failure cannot
double-run against a still-running previous synthesis for the same scope).
`_dream_once` gains one new optional collaborator, `portrait:
RoomPortraitSynthesizer | None`, called after the consolidators loop and
wrapped in its own `try/except` (a portrait failure must not trigger Plan 1/2's
own backoff — the concerns are independent) — `metrics.inc("nora_errors_total",
stage="portrait")` on failure, matching the existing per-stage error-counting
convention.

**Synthesis mechanism:**

**A — a third LLM call (a `RoomPortraitObserver` mirroring
`LexemeObserver`/`ExchangePatternObserver`).** Rejected: Plan 1's own module
docstring argues for keeping "LLM glosses, arithmetic decides adoption" as
separate concerns specifically because folding them together "would hand a
policy decision to a stochastic process". A THIRD model call synthesizing prose
from two ALREADY-vetted, ALREADY-structurally-safe outputs would reintroduce
exactly the propositional-content risk DEC-111 exists to close off, for zero
benefit — the inputs are already clean.

**B — chosen: a deterministic template over the SAME reader calls the live
prefill already makes.** No new model call anywhere in Plan 3. The synthesizer
calls `RoomLanguageReader.room_language(scope_id, limit=cfg.language
.max_rendered_lexemes)` and `RoomExchangeReader.room_exchange(scope_id, limit
=cfg.language.max_rendered_exchanges)` — the EXACT ports and limits
`TranscriptInjection` already uses for `<room_language>`/`<room_exchange>`. The
portrait narrates only what she can already see elsewhere in her own context,
never more, and reuses config that already exists (no new tunable knobs). This
also gives Plan 3 the "positions generate, lists close" shape the "doctrine as
portrait" idea (referenced in prior session discussion) argues for: each
synthesis is a fresh UPSERT computed from CURRENT top-N state, never an
accreting list — the narrative is replaced wholesale each time, not appended
to.

## The structural guarantee against person-level attribution

A projection step runs immediately after the two reader calls return, before
any narrative-string code executes, stripping identity out of the TYPE, not
just out of usage:

```python
@dataclass(frozen=True)
class _LexemeFact:
    canonical_form: str
    occurrence_count: int
    # NO person_id, NO examples — LexemeExample.person_id never crosses this
    # boundary.

@dataclass(frozen=True)
class _ExchangeFact:
    act: str
    occurrence_count: int
    # NO participants — RoomExchangePattern.participants never crosses this
    # boundary.
```

The narrative-building function's signature takes `Sequence[_LexemeFact]` and
`Sequence[_ExchangeFact]` — a person's identity is not merely unread, it is
**not a field that exists on the type the narrative code can see**, the same
"the invariant lives where a caller cannot forget it" move DEC-111/DEC-120
already make one layer down (`is_nora = 0` inside `RecordStore.since`; the DB
`CHECK` on `participants`). `act`'s rendered gloss reuses
`nora.language.exchange_render.ACT_GLOSSES` (repo text, not model text) —
imported, not duplicated.

## Rendering

`<room_portrait>` — a new prefill block, threaded exactly like
`<room_language>`/`<room_exchange>`: `RoomPortraitReader` port →
`TagContext.room_portrait` field → `TranscriptInjection._room_portrait` fetch
(added to the existing `asyncio.gather` alongside `_room_language`/
`_room_exchange`) → `prefill.py`'s `_room_portrait_block`.  No new config knob
for a render cap — a portrait is ONE row per scope by construction (UPSERT,
primary key `scope_id`), not a top-N list, so there is nothing to cap beyond
what feeds it (already capped by the reused `max_rendered_lexemes`/
`max_rendered_exchanges`).

Template shape (deterministic, no model text beyond repo-authored glosses and
already-vetted `canonical_form`/`act` values):

```
<room_portrait>
This room has its own vocabulary — {canonical_form_1}, {canonical_form_2}(,
{canonical_form_3}) among it.
Its people {act_gloss_1}(, and {act_gloss_2}).
This is a synthesis of the two blocks above — not a claim about any one
person.
</room_portrait>
```

Either sentence is omitted if its source (lexemes / exchanges) is empty; the
whole block renders nothing if BOTH are empty (mirrors Plan 1/2's own "empty
renders no block" ruling). The closing line is the SAME defence-in-depth move
Plan 2's closing frame makes — structural guarantee stated in plain language on
top of the structural guarantee in code, not instead of it.

## Schema (migration `0020_room_portrait`)

```
room_portrait (one row per scope, UPSERT — not append-only like the
evidence/consolidated pairs above; a portrait has no separate "evidence"
layer of its own, it is entirely derived from already-durable evidence)
  scope_id text PRIMARY KEY
  narrative text NOT NULL
  lexeme_basis text[] NOT NULL DEFAULT '{}'   -- canonical_forms used, for
                                                -- debugging/audit only, never
                                                -- rendered
  exchange_basis text[] NOT NULL DEFAULT '{}' -- acts used, same
  synthesized_at bigint NOT NULL              -- epoch ms, for operator visibility
```

No watermark table entry, no `consolidator_name` key — Plan 3 has no cursor of
its own; it is entirely re-derived on each trigger from Plan 1/2's current
top-N state (see "Synthesis mechanism" above). `lexeme_basis`/`exchange_basis`
store `canonical_form`/`act` only — the same non-identifying vocabulary already
proven safe one layer down — kept for an operator to audit what a narrative was
built from without needing to reconstruct it from history.

## New behavioural rule → decision index + acceptance test

`DEC-121`: *"The room portrait's narrative-construction function cannot be
called with participant or speaker identity — `_LexemeFact`/`_ExchangeFact`
carry no `person_id`/`participants` field, so a person's identity is stripped
at the type boundary before any narrative string is built, not merely unused
by discipline."*

Test: `test_acceptance_rules.py::RULE_TESTS["DEC-121"] =
"test_room_portrait_narrative_cannot_reference_a_person"` — constructs
`RoomLexeme`/`RoomExchangePattern` fixtures carrying a deliberately
identifiable, adversarial `person_id`/participant string (e.g.
`"WITNESS_MARKER_person_should_never_appear"`), runs them through the REAL
synthesizer end to end, and asserts the produced narrative text does not
contain that marker substring anywhere — the behavioral, grounded version of
the guarantee (R-27: bind to the real property), proved at the layer that
cannot be bypassed (the actual synthesizer, not a mock of it), the same
"prove it can't get through, not that we didn't send it" shape
`test_exchange_pattern_cannot_attribute_a_disposition_to_a_person` already
uses for DEC-120.

## Config

No new config section, no new fields on `LanguageConfig`. Gated by the SAME
`[language].enabled` flag Plan 1/2 already use (this is still the one Dreamer);
sized by the SAME `max_rendered_lexemes`/`max_rendered_exchanges` Plan 1/2
already expose. This is a deliberate YAGNI cut — introducing independent knobs
for a feature with no independent tunable behavior (no model, no timeout, no
threshold of its own) would be config for its own sake.

## Testing plan

- `tests/unit/test_room_portrait_synthesizer.py` — template construction: both
  sources present, lexemes-only, exchanges-only, both empty (renders nothing),
  `_LexemeFact`/`_ExchangeFact` projection strips identity (unit-level check
  that the projection functions never copy `person_id`/`participants`
  forward).
- `tests/unit/test_room_portrait_render.py` — the "empty renders no block"
  ruling, the closing-frame text, entry composition — mirrors
  `test_exchange_render.py`'s structure.
- `tests/infra/test_room_portrait_store.py` — UPSERT semantics (replace, not
  append; a second synthesis for the same scope overwrites, never
  accumulates), against a real Postgres test database.
- `tests/test_boundaries.py`-style check (folded into the runtime test file) —
  `_dream_once` calls `portrait.synthesize` if AND ONLY IF
  `sum(consolidator appended counts) > 0` for that scope this tick; a fake
  portrait collaborator records whether it was called, across: (a) both
  consolidators append 0, (b) exactly one appends >0, (c) a portrait failure
  does not raise out of `_dream_once` and does not block the watermark
  advance.
- `tests/test_acceptance_rules.py` — DEC-121's test, per above.
- Prefill/engagement wiring gets the SAME test-shape `room_language`/
  `room_exchange` wiring already has at each layer (`test_context.py`,
  `test_prefill.py`).
- `tests/infra/test_schema_parity.py` — regenerated baseline through
  `0020_room_portrait`.

## Implementation sequence

1. Schema: `nora/infra/sql/schema.py` — `ROOM_PORTRAIT` table + migration
   `0020_room_portrait.py`. Regenerate schema-parity baseline.
2. Domain: `nora/domain/models.py` — `RoomPortrait` (scope_id, narrative,
   generated_ms, `__bool__` on narrative non-empty — mirrors `RoomLanguage`).
3. `nora/domain/ports.py` — `RoomPortraitReader` Protocol (mirrors
   `RoomLanguageReader`/`RoomExchangeReader`) and `RoomPortraitSynthesizer`
   Protocol (the write/trigger side — `async def synthesize(self, *,
   scope_id: str) -> None`, no `batch` parameter, deliberately NOT
   `RoomConsolidator` — see "Trigger shape" above for why).
4. `nora/language/portrait_synthesizer.py` — `_LexemeFact`/`_ExchangeFact`
   projections, the deterministic template, `PortraitSynthesizer` class
   (takes `language: RoomLanguageReader`, `exchange: RoomExchangeReader`,
   `store: RoomPortraitWriter` as `__init__` collaborators — reuses
   `nora.language.exchange_render.ACT_GLOSSES`, imported not duplicated).
5. `nora/infra/sql/room_portrait.py` — `SqlRoomPortraitStore` (UPSERT +
   read), implements both `RoomPortraitReader` and a write-side port.
6. `nora/app/runtime.py` — `_dream_once` gains `portrait:
   RoomPortraitSynthesizer | None = None`; sums consolidator-returned counts;
   calls `portrait.synthesize` in its own try/except when the sum is > 0.
   `dream_loop`'s own signature threads the same optional param through.
7. Wiring: `TagContext.room_portrait`, `TranscriptInjection._room_portrait`,
   `nora/agent/prefill.py::_room_portrait_block`, `EngagementPipeline`/
   `FirePath` thread the reader through (same shape as `room_exchange`'s own
   wiring from Plan 2), `nora/app/composition.py` (build
   `SqlRoomPortraitStore`, build `PortraitSynthesizer`, pass into
   `dream_loop`'s call site in `nora/__main__.py`).
8. Decision index `DEC-121` + `tests/test_acceptance_rules.py` entry.
9. Release fragment.
10. Gates: `pytest -q`, `ruff check`, `mypy` (scoped dirs), `lint-imports`,
    schema-parity, release-fragment checker.
11. Commit, push, PR against `dreamer-plan2-exchange-patterns` (stacked on
    PR #12, not master — noted in the PR description).

## Spec self-review

- Placeholders: none.
- Internal consistency: the trigger (Plan 1 OR Plan 2 appended something)
  matches the rendering rule (empty renders nothing) — a portrait is only ever
  synthesized when there is real new material, and only ever rendered when the
  resulting narrative is non-empty; these are two different empty-checks
  (trigger-time vs. render-time) and both are needed since a synthesis can
  still legitimately produce an empty narrative on a tick where, e.g., only
  exchanges appended but lexemes+exchanges together don't clear whatever
  minimal-content bar the template applies — the template's own logic is the
  single source of truth for that, not duplicated elsewhere.
- Scope: this doc covers Plan 3 only; it consumes Plan 1's `room_lexeme` (on
  master) and Plan 2's `room_exchange` (on the branch this stacks on) as
  read-only inputs and adds no new evidence-writing behavior of its own beyond
  the one derived table.
- Ambiguity: "does Plan 3 implement `RoomConsolidator`" is resolved explicitly
  (no, a new `RoomPortraitSynthesizer` Protocol) with the reasoning recorded
  above, not left implicit.
