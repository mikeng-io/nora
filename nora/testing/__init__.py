"""Test doubles and builders, shipped inside the package.

This is the package `tests/` and `evals/` are meant to share, so there is
exactly one definition of each fake. **Not yet load-bearing**: today, only
`tests/contract/` imports from here. `evals/harness.py` still imports
`tests.integration.harness` — evals borrowing the test package's fakes is what
this package is meant to replace, but that rewiring is a later phase's work.

Every fake here is bound by a contract suite in `tests/contract/` that it and
the production implementation both pass. Adding a fake without one is a
project rule violation, not a shortcut.
"""

from nora.testing.fakes import FakeAuditStore

__all__ = ["FakeAuditStore"]
