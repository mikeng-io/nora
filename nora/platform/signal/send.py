"""signal-cli JSON-RPC send (spec C.3). Filled in by Task 18."""

import os

# Task 16 guard (spec E.3): assert in tests that the real send path is inert under pytest.
_PYTEST_GUARD = "PYTEST_CURRENT_TEST" in os.environ
