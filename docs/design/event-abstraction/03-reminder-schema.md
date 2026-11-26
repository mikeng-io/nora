---
type: contract
title: "Reminder: kind + payload"
description: "The exact two fields Reminder gains, their defaults, what does not change, and the migration + test surface they touch. The decidable spec for the schema half of this bundle."
tags: [nora, reminders, schema, migration, contract]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-shape-decision, rel: depends_on, provenance: asserted}
---

# Reminder: kind + payload

`Reminder` (`nora/domain/reminders.py`) gains two fields:

    kind: str = "reminder"
    payload: Mapping[str, str] | None = None

Defaulted so every existing row and every existing call site — the SQL
store, the in-memory fake, `remind`/`cancel_reminder`/`list_reminders`,
all four reminder test files — is byte-identical to today. `payload` is
an opaque string-keyed map; Reminder never interprets it — the same
discipline `reminders.py`'s own docstring already states about `text`
("Reminder never knows what fires"). Recurrence, `claim`, `mark`,
`cancel`, `sweep_firing`, the anchor-based occurrence math — none of it
changes; a `kind`/`payload` pair is not consulted by any of it. This is
the concrete form of the Shape A decision (`01-shape-decision.md`): the
scheduler grows two columns and nothing about its correctness reasoning
is touched.

## Migration

Two nullable/defaulted columns on the `reminders` table. No
`ck_reminders_*` constraint changes — `kind`/`payload` are independent
of the existing `series`-nullability check.

## `text` vs `payload` coexistence

`Reminder.text: str` is non-nullable and stays so. `kind="gathering"`
rows carry the gathering title in `payload["title"]` (denormalized —
`05-gathering-fold-in.md`), and `text` holds the legacy string
`f"Event: {title}"` for compatibility with readers that still
consume `text` (the domain never interprets it — "Reminder never knows
what fires"). The fire path reads `payload["title"]` when
`kind="gathering"`, never `text`. An old row written before this change
carries `text=f"Event: …"` with `kind="reminder"` default — it renders
as a plain reminder through `reminder_occasion`, which is exactly how it
renders today; the migration does not need to rewrite any existing rows.
The one ambiguity — a row with `kind="gathering"` whose `payload` lacks
`"title"` — is a wiring bug and fails closed: the registry's
`gathering_occasion` raises on a missing key, observed via the R-5
guard rather than silently rendering the legacy `text`.

## Payload shape is a closed decision, not an open question

The flat `Mapping[str, str] | None` is sufficient for every kind this
bundle registers (`gathering` needs only `title`), and it is the
binding constraint for FUTURE kinds: the moment a kind needs a number,
a list, or a structured value, that is a schema change on this column
(JSONB permits it without a migration), a per-kind payload-validation
addition, and a decision — not an automatic widening. Per-kind payload
validation ships with the registry (`04-dispatch-and-batching.md`) so a
malformed map fails observably at fire time instead of `KeyError`-ing
out of the blue.

## Store + test surface

- Both `ReminderStore` implementations
  (`nora/infra/sql/reminders.py`, `nora/testing/reminders.py`) round-trip
  the two fields.
- `tests/contract/test_reminder_store_contract.py` — extend to
  round-trip `kind`/`payload` through both implementations (the fake
  must behave like the real server on the new columns too, per this
  repo's fake-fidelity rule).
- `tests/test_reminder_tools.py`, `tests/unit/test_reminder_recurrence.py`,
  `tests/infra/test_reminder_store.py` — unaffected in behavior;
  constructors pick up two new defaulted fields.

## Migration test surface

One migration test guarantees the claimed row-compatibility: an
existing `kind="reminder"` row (legacy shape, `text="Event: …"`) still
round-trips and fires through `reminder_occasion` after the columns
land — proving the migration changes nothing for rows written before
it.
