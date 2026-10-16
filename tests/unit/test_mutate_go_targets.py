"""`scripts/mutate.py`'s verdict on a `go:` target.

The rule the runner exists for: a mutant that does not COMPILE has proved
nothing, so it is a BROKEN ENTRY (zero collected), never a caught one. With
`./...` that is easy to get wrong: the packages that still compile run and
print `=== RUN` lines, and only the exit code says one package did not build.
Counting those lines alone would report an uncompilable mutant as caught.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mutate", Path(__file__).resolve().parents[2] / "scripts" / "mutate.py",
)
assert _SPEC is not None and _SPEC.loader is not None
mutate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutate)

_RAN = "=== RUN   TestA\n--- PASS: TestA (0.00s)\n=== RUN   TestB\n--- FAIL: TestB (0.00s)\n"


def test_a_red_run_is_red_and_counts_its_tests():
    assert mutate.go_verdict(1, _RAN + "FAIL\tpkg/a\t0.01s\n") == (False, 2)


def test_a_green_run_is_green_and_counts_its_tests():
    assert mutate.go_verdict(0, _RAN.replace("FAIL:", "PASS:") + "ok\tpkg/a\n") == (True, 2)


def test_one_package_that_did_not_build_makes_the_whole_run_collect_nothing():
    """The case that matters: other packages ran and printed `=== RUN`."""
    output = _RAN + "FAIL\tinstagram-bridge/internal/transport/http [build failed]\n"
    assert mutate.go_verdict(1, output) == (False, 0)


def test_a_setup_failure_collects_nothing():
    output = "FAIL\tinstagram-bridge/cmd/instagram-bridge [setup failed]\n"
    assert mutate.go_verdict(1, output) == (False, 0)
