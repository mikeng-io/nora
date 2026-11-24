"""The selected Ory jobs cannot disappear behind a successful aggregate."""

import re
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
    "tests/contract/test_binding_store_contract.py",
    "tests/infra/test_managed_admission_sql.py",
    "tests/infra/test_account_link_requests.py",
    "tests/infra/test_roster_provisioning_sql.py",
    "tests/integration/test_managed_admission_ory.py",
    "tests/integration/test_managed_export_ory.py",
    "tests/integration/test_managed_identity_journey.py",
    "tests/integration/test_managed_identity_restore.py",
    "tests/integration/test_managed_runtime_recovery.py",
    "tests/integration/test_managed_community_cost.py",
    "tests/integration/test_managed_analytics.py",
    "tests/integration/test_managed_circle_cost.py",
    "tests/integration/test_managed_ingest_authority.py",
    "tests/integration/test_managed_lineage.py",
    "tests/integration/test_managed_presence.py",
    "tests/integration/test_managed_routes.py",
    "tests/integration/test_platform_observation_ingest.py",
    "tests/integration/managed_ory.py",
    "tests/unit/test_account_linking_state.py",
    "tests/unit/test_grouped_account_observation.py",
    "tests/unit/test_holder_link.py",
    "tests/unit/test_link_publication.py",
    "tests/web/test_holder_links.py",
    "tests/web/test_roster_provisioning.py",
    "tests/web/test_managed_ory_credentials.py",
    "tests/contract/test_binding_store_scoped_contract.py",
])
def test_managed_identity_changes_require_the_real_ory_job(path):
    plan = ci_plan.classify([path])
    assert plan["ory"] and plan["ory-journey"]
    needs = {name: {"result": "success" if selected else "skipped"}
             for name, selected in plan.items()}
    needs["classify"] = {"result": "success"}
    assert ci_plan.verify(plan, needs)
    needs["ory-journey"]["result"] = "skipped"
    assert not ci_plan.verify(plan, needs)
    needs["ory-journey"]["result"] = "success"
    needs["ory"]["result"] = "skipped"
    assert not ci_plan.verify(plan, needs)


# The two Ory jobs partition the managed-identity contract list: every module
# that ran before the split still runs, on exactly one of them. A dropped or
# duplicated module fails here, not silently at the join.
EXPECTED_ORY_MODULES = frozenset({
    "tests/contract/test_binding_store_contract.py",
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
    "tests/integration/test_managed_analytics.py",
    "tests/integration/test_managed_circle_cost.py",
    "tests/integration/test_managed_ingest_authority.py",
    "tests/integration/test_managed_lineage.py",
    "tests/integration/test_managed_presence.py",
    "tests/integration/test_managed_routes.py",
    "tests/integration/test_platform_observation_ingest.py",
    "tests/contract/test_binding_store_scoped_contract.py",
    "tests/infra/test_account_link_requests.py",
    "tests/infra/test_link_publication_sql.py",
    "tests/infra/test_managed_otp.py",
    "tests/infra/test_managed_mutation_gate.py",
    "tests/infra/test_kratos_identity_contract.py",
    "tests/web/test_management_bootstrap.py",
    "tests/web/test_management_auth_bootstrap.py",
    "tests/web/test_management_sessions.py",
    "tests/web/test_management_operator.py",
    "tests/web/test_managed_viewer.py",
    "tests/web/test_managed_otp_flow.py",
    "tests/web/test_managed_ory_credentials.py",
    "tests/web/test_roster_provisioning.py",
    "tests/web/test_holder_links.py",
    "tests/infra/test_roster_provisioning_sql.py",
    "tests/unit/test_roster_sources.py",
    "tests/infra/test_otp.py",
    "tests/web/test_otp.py",
    "tests/web/test_management_api.py",
    "tests/unit/test_managed_authority.py",
    "tests/unit/test_managed_identity_handoff.py",
    "tests/unit/test_managed_registration.py",
    "tests/unit/test_account_linking_state.py",
    "tests/unit/test_holder_link.py",
    "tests/unit/test_grouped_account_observation.py",
    "tests/unit/test_link_publication.py",
    "tests/domain/test_management.py",
    "tests/unit/test_management_request.py",
    "tests/unit/test_management_authentication.py",
    "tests/architecture/test_port_fakes_live_in_nora_testing.py",
    "tests/architecture/test_ory_ci.py",
})


def _ory_jobs() -> dict:
    return yaml.safe_load((Path(__file__).resolve().parents[2]
                          / ".github/workflows/ci.yml").read_text())["jobs"]


def _contract_modules(job: dict) -> set[str]:
    step = next(s for s in job["steps"]
                if s.get("name") == "Managed identity contracts")
    return set(re.findall(r"tests/\S+\.py", step["run"]))


def test_the_two_ory_jobs_partition_the_managed_identity_list():
    jobs = _ory_jobs()
    first, second = (_contract_modules(jobs[name]) for name in ("ory", "ory-journey"))
    assert not first & second
    assert first | second == EXPECTED_ORY_MODULES


def test_the_journey_job_mirrors_the_ory_gate_wiring():
    jobs = _ory_jobs()
    ory, journey = jobs["ory"], jobs["ory-journey"]
    assert journey["if"] == ory["if"].replace("['ory']", "['ory-journey']")
    assert journey["timeout-minutes"] == ory["timeout-minutes"]
    assert journey["env"] == ory["env"]
    names = [s.get("name") for s in journey["steps"]]
    for required in (
        "Start disposable pinned Ory services",
        "Execute policy and production-client contracts",
        "Managed identity contracts",
        "Remove disposable services and data",
    ):
        assert required in names
    assert (names.index("Start disposable pinned Ory services")
            < names.index("Managed identity contracts")
            < names.index("Remove disposable services and data"))



def test_manual_full_backend_runs_with_its_pinned_ory_dependencies():
    jobs = yaml.safe_load((Path(__file__).resolve().parents[2]
                          / ".github/workflows/ci.yml").read_text())["jobs"]
    job = jobs["ory"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    steps = job["steps"]
    names = [s.get("name") for s in steps]
    full = next(s for s in steps if s.get("name") == "Selected backend acceptance")
    startup = next(s for s in steps if s.get("name") == "Start disposable pinned Ory services")
    redis = next(s for s in steps if s.get("name") == "Install Redis for isolated eval subjects")
    portal = next(s for s in steps if s.get("name") == "Install portal browser test dependencies")
    assert "docker compose -p nora-ory-contract -f deploy/ory/compose.yaml up" in startup["run"]
    assert names.index(startup["name"]) < names.index(full["name"])
    assert names.index(full["name"]) < names.index("Remove disposable services and data")
    assert full["if"] == "github.event_name == 'workflow_dispatch'"
    assert redis["if"] == full["if"]
    assert names.index(redis["name"]) < names.index(full["name"])
    assert "apt-get install -y redis-server" in redis["run"]
    assert portal["if"] == full["if"]
    assert names.index(portal["name"]) < names.index(full["name"])
    assert "npm ci --prefix app" in portal["run"]
    assert "playwright install --with-deps chromium" in portal["run"]
    assert "if targets == 'ALL'" in full["run"]
    assert "--ignore" not in full["run"] and "continue-on-error" not in full
    assert "&& 90 || 5" in job["timeout-minutes"]
    assert job["env"]["NORA_TEST_MAX_WORKERS"] == "4"
    assert not any(s.get("name") == full["name"] for s in jobs["contracts"]["steps"])
