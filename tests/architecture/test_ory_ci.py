"""The selected Ory job cannot disappear behind a successful aggregate."""

import pytest

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

