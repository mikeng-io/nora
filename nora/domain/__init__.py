"""Framework-free contracts. Imports only the standard library.

If a type here needs aiohttp, strands, sqlalchemy, pydantic or prometheus_client
to express itself, it does not belong here — the port is wrong. Enforced by
tests/test_boundaries.py and by import-linter.
"""
