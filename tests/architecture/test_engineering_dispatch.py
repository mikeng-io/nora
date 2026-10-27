"""Privileged workflow source must never execute a PR checkout."""

import yaml

from scripts import engineering_dispatch as dispatch

REPO = dispatch.REPOSITORY


def test_workflow_executes_only_pinned_default_branch_controller_with_global_lock():
    workflow = yaml.safe_load((dispatch.ROOT / dispatch.WORKFLOW).read_text())
    triggers = workflow[True]  # YAML 1.1 loads `on` as True.
    assert set(triggers) == {"pull_request_target", "workflow_run", "workflow_dispatch"}
    assert triggers["workflow_run"] == {"workflows": ["CI"], "types": ["completed"]}
    assert workflow["concurrency"] == {
        "group": "nora-engineering-dispatch",
        "cancel-in-progress": False,
    }
    job = workflow["jobs"]["controller"]
    assert "vars.ENGINEERING_REPOSITORY == github.repository" in job["if"]
    assert (
        "github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'failure'"
        in job["if"]
    )
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "write",
        "checks": "read",
        "actions": "read",
    }
    checkout, runner = job["steps"]
    assert checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    assert checkout["with"] == {"repository": REPO, "ref": "master", "persist-credentials": False}
    assert runner["run"] == "python3 -m scripts.engineering_dispatch"
    assert all("${{" not in step.get("run", "") for step in job["steps"])
    assert not any(
        "pr-reviews" in value
        for value in dispatch.Controller.run.__code__.co_consts
        if isinstance(value, str)
    ), "manual Review creation is absent"
