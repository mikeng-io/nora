"""The managed-mode contract assets under `deploy/ory/` — pinned at
self-hosted Ory v26.2.0.

What is proven here is the *declared content* of the artifacts: the Kratos
person schema is a valid draft-07 JSON Schema with the shape Kratos requires
(`properties.traits`, closed, credentialless), generated instances validate
and refuse correctly, the OPL source declares the model
`docs/design/people/authority.md` describes, and the
development configs pin the v26.2.0 keys so later setup does not invent
their shape.

What is NOT proven: OPL compilation or runtime behaviour. These tests assert source properties only.
`scripts.check_ory_policy` checks effective permissions against a disposable
local Keto; these string assertions do not replace that service check. Instances are generated,
never copied from production.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ORY = ROOT / "deploy" / "ory"
SCHEMA_PATH = ORY / "kratos" / "person.schema.json"
OPL_PATH = ORY / "keto" / "namespaces.ts"
KETO_CFG_PATH = ORY / "keto" / "keto.dev.yaml"
KRATOS_CFG_PATH = ORY / "kratos" / "kratos.dev.yaml"

SCHEMA = json.loads(SCHEMA_PATH.read_text())
OPL = OPL_PATH.read_text()
KETO_CFG = yaml.safe_load(KETO_CFG_PATH.read_text())
KRATOS_CFG = yaml.safe_load(KRATOS_CFG_PATH.read_text())

VALIDATOR = jsonschema.Draft7Validator(SCHEMA)


def test_person_schema_is_a_valid_draft7_schema():
    jsonschema.Draft7Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "http://json-schema.org/draft-07/schema#"


def test_person_schema_has_the_kratos_shape():
    """Kratos requires the identity schema to be a JSON Schema object whose
    `properties.traits` is itself a typed object — the shape the pinned
    meta-schema enforces."""
    assert SCHEMA["$id"].startswith("https://")
    assert SCHEMA["type"] == "object"
    traits = SCHEMA["properties"]["traits"]
    assert traits["type"] == "object"
    assert traits["properties"], "traits declares at least one property"
    assert traits["additionalProperties"] is False
    assert SCHEMA["additionalProperties"] is False


def test_person_schema_is_credentialless_and_carries_no_ownership():
    """No `ory.sh/kratos` extension key anywhere — a platform-originated
    person may have no credential at all — and no account/binding field can
    creep into mutable traits (bindings live in Nora's store, grants in
    Keto)."""
    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    assert not [k for k in keys(SCHEMA) if k.startswith("ory.sh/")]
    trait_names = set(SCHEMA["properties"]["traits"]["properties"])
    forbidden = {
        "email", "password", "username", "phone", "credentials",
        "account", "accounts", "person_id", "owner", "kratos_id",
        "external_id", "verifiable_addresses", "recovery_addresses",
    }
    assert not (trait_names & forbidden), trait_names & forbidden


def test_generated_instances_validate_and_refuse():
    assert VALIDATOR.is_valid({"traits": {"display_name": "M"}})
    assert VALIDATOR.is_valid(
        {"traits": {"display_name": "M", "labels": {"main": "personal"}}})
    assert VALIDATOR.is_valid({}), "traits may be absent entirely"
    assert VALIDATOR.is_valid({"traits": {}})


def test_social_is_an_explicit_boolean_never_a_truthy_string():
    """The opt-out's shape: `false` is recorded, anything that merely
    LOOKS enabled is a malformed instance Kratos itself refuses."""
    assert VALIDATOR.is_valid({"traits": {"social": True}})
    assert VALIDATOR.is_valid({"traits": {"social": False}})
    for instance in (
        {"traits": {"social": "yes"}},
        {"traits": {"social": 1}},
        {"traits": {"social": None}},
    ):
        assert not VALIDATOR.is_valid(instance), instance


@pytest.mark.parametrize("instance", [
    {"traits": {"email": "x@example.invalid"}},
    {"traits": {"owner": "miles"}},
    {"traits": {"account": "signal:+10000000001"}},
    {"traits": {"display_name": "M"}, "kratos_id": "i-1"},
    {"traits": {"labels": {"a": 1}}},
])
def test_account_ownership_and_credentials_cannot_enter_traits(instance):
    assert not VALIDATOR.is_valid(instance)


def _opl_class_body(name: str) -> str:
    if re.search(rf"class {name} implements Namespace \{{\s*\}}", OPL):
        return ""
    match = re.search(
        rf"class {name} implements Namespace \{{(.*?)\n\}}", OPL, re.S)
    assert match, f"{name} is not declared in the OPL source"
    return match.group(1)


def _permits_of(name: str) -> set[str]:
    return set(re.findall(r"(\w+): \(ctx: Context\)", _opl_class_body(name)))


def test_opl_imports_the_official_type_library():
    assert 'import { Namespace, Context } from "@ory/keto-namespace-types"' in OPL


def test_opl_declares_the_four_namespaces():
    for name in ("Person", "Workspace", "Unit", "Room"):
        _opl_class_body(name)


def test_opl_scope_relations_are_declared_on_every_scope():
    for scope in ("Workspace", "Unit", "Room"):
        body = _opl_class_body(scope)
        for rel in ("member", "admin", "restricted",
                    "grant_speak", "deny_speak", "grant_view", "deny_view",
                    "grant_manage_members", "deny_manage_members",
                    "grant_manage_grants", "deny_manage_grants",
                    "grant_delegate", "deny_delegate"):
            assert re.search(rf"{rel}: Person\[\]", body), f"{scope}.{rel}"


def test_opl_containment_edges_are_the_topology_direction():
    """Room → unit → workspace, and never the reverse: the only cross-scope
    relations are the parents Nora validates."""
    assert "unit: Unit[]" in _opl_class_body("Room")
    assert "workspace: Workspace[]" in _opl_class_body("Unit")
    assert "Unit[]" not in _opl_class_body("Workspace")
    assert "Room[]" not in _opl_class_body("Unit")
    assert "Room[]" not in _opl_class_body("Workspace")


def test_opl_permit_inventory_matches_the_declared_model():
    """Every declared permit exists with the scope the design assigns it —
    all access and management permits on every scope, with matching
    per-permit deny evaluators."""
    permits = {"speak", "view", "manage_members", "manage_grants", "delegate"}
    for scope in ("Room", "Unit", "Workspace"):
        assert _permits_of(scope) == permits | {f"blocked_{p}" for p in permits}
    assert _permits_of("Person") == set()


def test_opl_deny_relations_are_per_permit_and_distinct_from_grants():
    for scope in ("Workspace", "Unit", "Room"):
        body = _opl_class_body(scope)
        for permit in ("speak", "view", "manage_members", "manage_grants", "delegate"):
            assert f"!this.permits.blocked_{permit}(ctx)" in body
            assert f"this.related.deny_{permit}.includes(ctx.subject)" in body
            assert f"this.related.grant_{permit}.includes(ctx.subject)" in body
            assert not re.search(rf"^    {permit}: Person\[\]", body, re.M)


def test_opl_inheritance_flows_downward_through_containment():
    """A workspace permit reaches contained units and transitively rooms;
    deny inherits the same way. Nothing grants upward."""
    assert "workspace.traverse((w) => w.permits.manage_members(ctx))" in \
        _opl_class_body("Unit")
    assert "unit.traverse((u) => u.permits.manage_members(ctx))" in \
        _opl_class_body("Room")
    assert "unit.traverse((u) => u.permits.blocked_speak(ctx))" in \
        _opl_class_body("Room")
    assert "workspace.traverse((w) => w.permits.blocked_speak(ctx))" in \
        _opl_class_body("Unit")
    assert "traverse" not in _opl_class_body("Workspace")


def test_opl_membership_confers_no_management_permit():
    """`member` participates only in Room speak/view — the roster fact
    and delegated authority stay distinct."""
    assert _opl_class_body("Room").count(
        "related.member.includes(ctx.subject)") == 2
    assert "member.includes" not in _opl_class_body("Unit")
    assert "member.includes" not in _opl_class_body("Workspace")


def test_keto_dev_config_pins_the_v26_keys():
    assert KETO_CFG["namespaces"]["location"].startswith("file://")
    assert KETO_CFG["namespaces"]["experimental_strict_mode"] is True
    assert KETO_CFG["limit"]["max_read_depth"] == 16
    assert KETO_CFG["dsn"].startswith("sqlite://")
    assert KETO_CFG["serve"]["read"]["host"] == "127.0.0.1"
    assert KETO_CFG["serve"]["write"]["host"] == "127.0.0.1"


def test_kratos_dev_config_binds_the_committed_schema():
    schemas = KRATOS_CFG["identity"]["schemas"]
    assert KRATOS_CFG["identity"]["default_schema_id"] == schemas[0]["id"]
    assert schemas[0]["url"].endswith(SCHEMA_PATH.name)
    assert KRATOS_CFG["dsn"].startswith("sqlite://")
    assert KRATOS_CFG["serve"]["admin"]["host"] == "127.0.0.1"


def test_domain_permit_vocabulary_matches_the_opl_model():
    """`nora.domain.management`'s permit constants are the same names the
    OPL source declares — the typed contract and the permission model cannot
    drift apart."""
    from nora.domain.management import ACCESS_PERMITS, MANAGEMENT_PERMITS
    declared = (
        _permits_of("Room") | _permits_of("Unit") | _permits_of("Workspace")
    )
    declared = {p for p in declared if not p.startswith("blocked_")}
    assert declared == ACCESS_PERMITS | MANAGEMENT_PERMITS
