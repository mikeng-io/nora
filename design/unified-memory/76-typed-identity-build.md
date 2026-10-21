# Typed Identity implementation plan

Goal: deliver 75's approved contract as one integrated change, keeping live
Persona in shadow until measured cutover acceptance.

> 2026-10-21 (18 / ADR 0026): the architecture and build steps below are the earlier plan. [18](18-message-readings.md) moves source statements to Record message readings and restores bounded Character entries; [ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md) names their parts profile, traits and hypotheses and defers existing code-name changes to one migration. Readings and backfill do not wait for that rename.

Architecture: reuse persona_line, plan_write, SqlPersonaStore and the existing
person/collective adaptor runtime. Typed facts share the account transaction
with character/hypotheses. Historical extraction uses the same writer with a
separate bounded oldest-first input cursor. Postgres/SQLAlchemy/Pydantic and
the existing Strands runtime; no new dependency.

1. Domain and schema: nora/domain/persona.py, nora/infra/sql/{schema,persona}.py,
   the next migration, pg_schema_baseline.sql. Add typed fact fields and one
   validation/formatting owner. Begin with synthetic failing domain and SQL
   round-trip tests; prove invalid or contradictory shapes cannot be stored.
   Preserve old rows and effective/source/write time distinctions; remove only
   the Identity storage-count cap, not character/hypothesis limits.
2. Shared writes and reads: persona.py/persona_collective.py, objective files,
   tools.py's remember, persona_render.py, memory_index.py and portal API/UI.
   Pass typed values through the one write validator; format values once for
   prompt, retrieval and portal. Synthetic birthday/meal-plan, movement,
   language, dietary and correction cases must assert stored/retrieved values.
   Read nora-tool and ADR0015 before the tool schema edit.
3. Complete recovery: trace PersonaEvidence -> SqlCharacterEvidence.person_since
   -> runtime watermark before choosing reuse. Build resumable oldest-first
   bounded pages, same writer, no consumed-unread history, explicit rerun and
   per-account completion/coverage receipts. Name the minimum durable state
   required; reuse existing cursor tables/CLI patterns. Add a real SQL test
   with interleaved streams, failed commit, restart and later new traffic.
4. Unified lifecycle: composition and collective/person evidence. Gate legacy
   writers off at Persona render-on and move their downstream dependencies to
   Persona account revisions at the same switch. Preserve rollback to the old
   surface. Add the smallest topology/caller tests plus existing disclosure
   and offline parity checks.
5. Validate locally: focused tests while iterating; meaningful behavioral
   revert-red/restore-green evidence; final make gate once on final tree.
   Sync existing design nodes and allocate decision numbers from actual master
   only when landing. Fresh low-effort independent review per operator budget
   override; fix verified findings, PR template and T3 registration, merge
   exact reviewed/gated head and read back state.
6. Deployment/coverage: nora-deploy lock and validated-copy backup procedure;
   render stays false. Read back running image and shadow typed writes, then
   complete historical pass. Produce an auditable per-account identity gap
   report before any live prompt switch. Model eval/replay runs only after
   complete build, with bounded cost/time and same production composition.


Build implementation, 2026-10-19: nullable typed columns and canonical read/write
contract are implemented, along with the same-writer historical pass and
render-dependent lifecycle/input rewiring. Historical progress reuses existing
consolidation-watermark rows; no new service, adapter, dependency or legacy
schema removal. Independent review, deployment, source coverage/parity and model
acceptance are owned by root and have not run during this build. A landing DEC
number is intentionally left to root's current-master allocation.
