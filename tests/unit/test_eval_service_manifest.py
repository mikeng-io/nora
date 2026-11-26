"""Private recordings keep corpus and exact sampled draw identities separate."""

import json

import pytest

from evals.services import load_services


@pytest.mark.parametrize("damage", [None, "version", "empty", "names", "recording"])
def test_service_manifest_keeps_full_draw_names_and_rejects_bad_inputs(tmp_path, damage):
    recording = {"version": 1, "bindings": {"llm.base_url": "model"},
                 "services": {"model": []}}
    data = {"version": 1, "corpora": {"one": {"generated#1": recording},
                                     "two": {"generated#1": recording}}}
    if damage == "version":
        data["version"] = True
    elif damage == "empty":
        data["corpora"] = {}
    elif damage == "names":
        data["corpora"]["one"] = {"": recording}
    elif damage == "recording":
        recording["services"] = {}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(data))
    if damage:
        with pytest.raises(ValueError):
            load_services(path)
    else:
        inputs = load_services(path)
        cases = inputs.corpora
        assert list(cases) == ["one", "two"]
        assert list(cases["one"]) == ["generated#1"]
        assert "generated" not in cases["one"]
        assert cases["one"]["generated#1"] is not cases["two"]["generated#1"]
        original = path.read_bytes()
        path.write_text("changed after load")
        recorded, files = [], {}
        from types import SimpleNamespace

        inputs.record(SimpleNamespace(
            retain=lambda name, data: files.__setitem__(name, data),
            record=lambda kind, **data: recorded.append((kind, data))))
        assert files["external-services.json"] == original
        import hashlib

        assert recorded[0][1]["sha256"] == hashlib.sha256(original).hexdigest()


def test_a_journey_world_plan_constructs_the_declared_finite_world(tmp_path):
    """A `journey_world` entry is the declarative services plan: policy,
    room, identity, overrides and the two judge aliases, all read into the
    `JourneyServices` the live-clock drive opens."""
    data = {"version": 1, "corpora": {"autonomous_journey": {"the_outing": {
        "kind": "journey_world",
        "policy": "[rooms]\nallow = []",
        "room": ["signal", "grp"],
        "identity": "[self]\nhandle = 'nora'",
        "overrides": {"cognition.enabled": False},
        "judges": ["alpha", "beta"]}}}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(data))
    plan = load_services(path).corpora["autonomous_journey"]["the_outing"]
    assert plan.policy == "[rooms]\nallow = []"
    assert plan.room == ("signal", "grp")
    assert plan.identity == "[self]\nhandle = 'nora'"
    assert plan.overrides()["cognition.enabled"] is False
    assert plan.judge_models == ("alpha", "beta")
    # No declaration means NO live endpoint — the generated world, never
    # a default upstream.
    assert plan.model_gateway is None


def test_a_journey_world_plan_binds_its_declared_model_gateway(tmp_path):
    """`model_gateway` declares the case's one live model boundary: an
    upstream plus the identities a dispatch may name, carried on the
    `JourneyServices` the live-clock drive opens."""
    upstream = "http://127.0.0.1:9876/v1"
    data = {"version": 1, "corpora": {"autonomous_journey": {"the_outing": {
        "kind": "journey_world",
        "policy": "[rooms]\nallow = []",
        "judges": ["alpha", "beta"],
        "model_gateway": {"upstream": upstream,
                          "models": ["nora", "alpha", "beta"]}}}}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(data))
    plan = load_services(path).corpora["autonomous_journey"]["the_outing"]
    assert plan.model_gateway is not None
    assert plan.model_gateway.upstream == upstream
    assert plan.model_gateway.models == frozenset({"nora", "alpha", "beta"})


def test_a_journey_world_plan_carries_its_declared_external_recording(tmp_path):
    """`external` is the plan's embedded version-1 recording, carried into
    the `JourneyServices` the live-clock drive opens."""
    recording = {"version": 1,
                 "bindings": {"platforms.discord.base_url": "discord",
                              "platforms.signal.base_url": "bridge"},
                 "services": {
                     "discord": [{"method": "GET", "path": "/v1/groups",
                                  "response": {"status": 200, "json": []}}],
                     "bridge": [{"method": "GET", "path": "/v1/self",
                                 "response": {"status": 200,
                                              "json": {"primary": "+8521"}}}]}}
    data = {"version": 1, "corpora": {"autonomous_journey": {"the_outing": {
        "kind": "journey_world",
        "policy": "[rooms]\nallow = []",
        "judges": ["alpha", "beta"],
        "external": recording}}}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(data))
    plan = load_services(path).corpora["autonomous_journey"]["the_outing"]
    from nora.subject.recorded_services import RecordedServices

    assert isinstance(plan.external, RecordedServices)
    assert plan.external.bindings == {"platforms.discord.base_url": "discord",
                                    "platforms.signal.base_url": "bridge"}


def test_an_explicit_recorded_kind_stays_the_positional_recording(tmp_path):
    recording = {"kind": "recorded", "version": 1,
                 "bindings": {"llm.base_url": "model"}, "services": {"model": []}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(
        {"version": 1, "corpora": {"one": {"generated#1": recording}}}))
    from nora.subject.recorded_services import RecordedServices

    plan = load_services(path).corpora["one"]["generated#1"]
    assert isinstance(plan, RecordedServices)


@pytest.mark.parametrize("plan", [
    {"kind": "journey_world", "policy": "p"},                     # no judges
    {"kind": "journey_world", "judges": ["a", "b"]},              # no policy
    {"kind": "journey_world", "policy": "p", "judges": ["a", "a"]},
    {"kind": "journey_world", "policy": "p", "judges": ["a"]},
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "room": ["only-platform"]},
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "surprise": 1},
    {"kind": "a_kind_nobody_declared", "policy": "p", "judges": ["a", "b"]},
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": "http://127.0.0.1:9/v1"},                       # not an object
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"upstream": "http://127.0.0.1:9/v1"}},          # no models
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"models": ["a", "b"]}},                         # no upstream
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"upstream": "http://127.0.0.1:9/v1",
                      "models": ["a", "b"], "api_key": "generated"}},  # a credential
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"upstream": "http://127.0.0.1:9/v1",
                      "models": []}},                                  # no identities
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"upstream": "http://127.0.0.1:9/v1",
                      "models": ["nora", "a"]}},                       # a judge omitted
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"upstream": "https://user:pw@127.0.0.1:9/v1",
                      "models": ["nora", "a", "b"]}},                  # upstream credential
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "model_gateway": {"upstream": "ftp://127.0.0.1:9/v1",
                      "models": ["nora", "a", "b"]}},                  # not http(s)
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "external": "not-a-recording"},                                   # not an object
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "external": {"version": 2, "bindings": {"platforms.discord.base_url": "m"},
                  "services": {"m": []}}},                             # not version 1
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "external": {"version": 1, "bindings": {"llm.base_url": "model"},
                  "services": {"model": []}}},                         # recorded model dispatch
    {"kind": "journey_world", "policy": "p", "judges": ["a", "b"],
     "external": {"version": 1,
                  "bindings": {"providers.judge.base_url": "model"},
                  "services": {"model": []}}},                         # recorded provider dispatch
    "not a plan at all",
])
def test_malformed_and_unknown_service_plans_are_refused(tmp_path, plan):
    path = tmp_path / "services.json"
    path.write_text(json.dumps(
        {"version": 1, "corpora": {"autonomous_journey": {"the_outing": plan}}}))
    with pytest.raises(ValueError):
        load_services(path)
