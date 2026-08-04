"""Test doubles and builders, shipped inside the package.

`tests/` AND `evals/` import from here, so there is exactly one definition of
each fake. `evals/harness.py` importing `tests.integration.harness` is what
made evals a second composition root borrowing the test package's; this is the
package that replaces that.

Every fake here is bound by a contract suite in `tests/contract/` that it and
the production implementation both pass. Adding a fake without one is a
project rule violation, not a shortcut.
"""

from nora.testing.fakes import FakeAuditStore

__all__ = ["FakeAuditStore"]
