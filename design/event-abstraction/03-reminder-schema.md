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

## Open question

Is a flat `Mapping[str, str]` sufficient for every kind you expect to
register, or does any anticipated kind need structured (non-string)
values? A flat map keeps the migration trivial (one JSON/JSONB column)
but would need revisiting if a kind needs, say, a list or a number.
