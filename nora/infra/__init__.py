"""Infrastructure adapters: the concrete, framework-bound implementations of
`nora.domain.ports`.

`infra/sql/` is the audit record (SQLAlchemy Core + Alembic). An adapter whose
error contract differs from it gets its own package: a failed audit append drops
the message, and sharing a package would invite sharing an error policy.

May import: sqlalchemy, alembic, aiohttp, `nora.domain`.
May NOT import `nora.obs` (layers contract) — metrics are injected; see
`audit.py`'s `__init__` docstring. Also may not import: `nora.config`
(contract 3), `strands`.
"""
