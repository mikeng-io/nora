"""The selected Ory job cannot disappear behind a successful aggregate."""

from pathlib import Path

import pytest
import yaml

from scripts import ci_plan


@pytest.mark.parametrize("path", [
    "deploy/ory/keto/namespaces.ts", "deploy/ory/kratos/person.schema.json",
    "nora/infra/ory/kratos.py", "nora/people/app/identity_resolution.py",
    "nora/domain/management.py", "scripts/check_ory_clients.py", "uv.lock",
    "tests/contract/test_authority_backend_contract.py",
    "tests/contract/test_admission_identity_contract.py",
    "tests/contract/test_admission_probe_contract.py",
    "tests/infra/test_managed_admission_sql.py",
    "tests/integration/test_managed_admission_ory.py",
    "tests/integration/test_managed_export_ory.py",
    "tests/integration/test_managed_identity_journey.py",
    "tests/integration/test_managed_identity_restore.py",
    "tests/integration/test_managed_runtime_recovery.py",
    "tests/integration/test_managed_community_cost.py",
    "tests/integration/managed_ory.py",
    "tests/contract/test_binding_store_scoped_contract.py",
])
def test_managed_identity_changes_require_the_real_ory_job(path):
    plan = ci_plan.classify([path])
    assert plan["ory"]
    needs = {name: {"result": "success" if selected else "skipped"}
             for name, selected in plan.items()}
    needs["classify"] = {"result": "success"}
    assert ci_plan.verify(plan, needs)
    needs["ory"]["result"] = "skipped"
    assert not ci_plan.verify(plan, needs)



def test_manual_full_backend_runs_with_its_pinned_ory_dependencies():
    jobs = yaml.safe_load((Path(__file__).resolve().parents[2]
                          / ".github/workflows/ci.yml").read_text())["jobs"]
    job = jobs["ory"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    steps = job["steps"]
    names = [s.get("name") for s in steps]
    full = next(s for s in steps if s.get("name") == "Selected backend acceptance")
    startup = next(s for s in steps if s.get("name") == "Start disposable pinned Ory services")
    assert "docker compose -p nora-ory-contract -f deploy/ory/compose.yaml up" in startup["run"]
    assert names.index(startup["name"]) < names.index(full["name"])
    assert names.index(full["name"]) < names.index("Remove disposable services and data")
    assert full["if"] == "github.event_name == 'workflow_dispatch'"
    assert "if targets == 'ALL'" in full["run"]
    assert "--ignore" not in full["run"] and "continue-on-error" not in full
    assert "&& 90 || 4" in job["timeout-minutes"]
    assert job["env"]["NORA_TEST_MAX_WORKERS"] == "4"
    assert not any(s.get("name") == full["name"] for s in jobs["contracts"]["steps"])
