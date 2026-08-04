"""Infrastructure adapters: the concrete, framework-bound implementations of
`nora.domain.ports`.

`infra/sql/` is the audit record (SQLAlchemy Core + Alembic); `infra/honcho/`
is the semantic store. They are deliberately separate packages: their
contracts are opposite. A failed audit append drops the message; a failed
Honcho call is logged and returns a benign value, because Honcho is never
load-bearing. Sharing a package would invite sharing an error policy.

May import: sqlalchemy, alembic, aiohttp, `nora.domain`, `nora.obs.metrics`.
May NOT import: `nora.config` (contract 3), `strands`.
"""
