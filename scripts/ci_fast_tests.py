"""Bounded automatic CI baseline; complete backend acceptance is manual."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.architecture.test_ci_runs_the_plumbing_guards import PLUMBING_GUARDS

# Measured slow modules in hosted runs 43459625923 and 43471519120.
# They remain in `pytest_gate`'s complete suite, run through workflow_dispatch.
ON_DEMAND = (
    "test_adoption.py",
    "test_bridge_capture_review.py",
    "test_cognition_failure_backoff.py",
    "test_cognition_loop_eval.py",
    "test_computerd_instance.py",
    "test_dreamer_runtime.py",
    "test_engagement_pipeline.py",
    "test_eval_main_budget.py",
    "test_eval_main_shared_db_guard.py",
    "test_fire_path.py",
    "test_generate_image_description.py",
    "test_memory_search_tool.py",
    "test_nora_computer_backup.py",
    "test_nora_computer_lifecycle.py",
    "test_nora_computer_script.py",
    "test_pii_scan.py",
    "test_pr584_astra_readiness.py",
    "test_profile_coverage.py",
    "test_release_deployment.py",
    "test_rollup_determinism.py",
    "test_room_list_and_read.py",
    "test_runtime.py",
    "test_scoped_computer.py",
    "test_summons_occasion.py",
    "test_testmon_selection.py",
)


def main() -> int:
    # Explicit files keep excluded timing modules out of the runner's serial
    # phase as well as ordinary collection. Directory targets re-add that phase.
    unit = Path("tests/unit")
    targets = sorted({
        str(path) for pattern in ("test_*.py", "*_test.py") for path in unit.rglob(pattern)
        if path.relative_to(unit).as_posix() not in ON_DEMAND
    })
    if not targets:
        raise RuntimeError("unit test tree unavailable")
    command = [
        sys.executable, "-m", "scripts.pytest_gate", "--junit-dir", "backend-results",
        *targets, "tests/domain",
        *PLUMBING_GUARDS,
        "tests/architecture/test_ci_plan.py",
        "tests/architecture/test_ci_runs_the_plumbing_guards.py",
    ]
    print("Fast baseline only; full backend acceptance: dispatch CI manually.", flush=True)
    # GNU timeout signals the process group; the runner forwards INT and cleans
    # up workers. Its outer KILL also bounds teardown if a worker wedges.
    return subprocess.run(  # noqa: S603
        ["timeout", "--signal=INT", "--kill-after=10s", "170s", *command],  # noqa: S607 - CI GNU coreutils timeout
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
