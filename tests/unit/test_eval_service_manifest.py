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
