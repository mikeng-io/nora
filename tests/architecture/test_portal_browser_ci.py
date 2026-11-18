"""The portal-browser job cannot disappear behind a successful aggregate.

NOR-1 item 5 names `tests/integration/test_managed_portal_browser.py` and
counts it only when it runs — not skips — in a job on every PR head. The
job needs what a `skipped` report would hide: a built Next app, the
pinned Chromium the stdio driver launches, and the journey's pinned
images (`--pull=never` means an absent image fails the fixture, but a
forgotten step here would surface as a fixture error, not a skipped
test — the pins keep that ordering honest).
"""

from pathlib import Path

import yaml


def _job() -> dict:
    jobs = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text()
    )["jobs"]
    return jobs


def test_the_portal_browser_job_runs_the_named_module_on_pr_heads():
    jobs = _job()
    job = jobs["portal-browser"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert "needs.classify.outputs.plan" in job["if"]
    assert "&& 90 || 15" in job["timeout-minutes"]
    assert job["env"]["NORA_TEST_MAX_WORKERS"] == "4"
    assert "portal-browser" in jobs["checks"]["needs"]
    steps = job["steps"]
    names = [s.get("name") for s in steps]
    journey = next(s for s in steps if s.get("name") == "Managed portal browser journey")
    assert journey["run"] == (
        "uv run python -m scripts.pytest_gate tests/integration/test_managed_portal_browser.py"
    )
    assert "if" not in journey  # every event runs the named module
    install = next(s for s in steps if "playwright install" in (s.get("run") or ""))
    images = next(s for s in steps if "docker pull" in (s.get("run") or ""))
    assert names.index(install["name"]) < names.index(journey["name"])
    assert names.index(images["name"]) < names.index(journey["name"])
    for image in ("pgvector/pgvector:pg18", "oryd/kratos:v26.2.0", "oryd/keto:v26.2.0"):
        assert image in images["run"]
    node = next(s for s in steps if s.get("uses", "").startswith("actions/setup-node@"))
    assert node["with"]["node-version"] == "24.22.1"
