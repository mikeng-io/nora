"""Bounded automatic CI baseline; complete backend acceptance is manual."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

from tests.architecture.test_ci_runs_the_plumbing_guards import PLUMBING_GUARDS

# Measured in run 43459625923: these unit modules cost 17-208 seconds each.
# They remain in `pytest_gate`'s complete suite, run through workflow_dispatch.
ON_DEMAND = (
    "test_bridge_capture_review.py",
    "test_cognition_failure_backoff.py",
    "test_nora_computer_backup.py",
    "test_nora_computer_lifecycle.py",
    "test_nora_computer_script.py",
    "test_release_deployment.py",
    "test_rollup_determinism.py",
    "test_testmon_selection.py",
)


def main() -> int:
    command = [
        sys.executable, "-m", "scripts.pytest_gate", "--junit-dir", "backend-results",
        "tests/unit", "tests/domain",
        *PLUMBING_GUARDS,
        "tests/architecture/test_ci_plan.py",
        "tests/architecture/test_ci_runs_the_plumbing_guards.py",
    ]
    # pytest_gate preserves node selectors but owns pytest's command-line flags;
    # pass exclusions through pytest's standard environment option instead.
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = shlex.join([
        *shlex.split(env.get("PYTEST_ADDOPTS", "")),
        *(f"--ignore=tests/unit/{name}" for name in ON_DEMAND),
    ])
    print("Fast baseline only; full backend acceptance: dispatch CI manually.", flush=True)
    # GNU timeout signals the process group; the runner forwards INT and cleans
    # up workers. Its outer KILL also bounds teardown if a worker wedges.
    return subprocess.run(  # noqa: S603
        ["timeout", "--signal=INT", "--kill-after=10s", "170s", *command],  # noqa: S607 - CI GNU coreutils timeout
        env=env,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
