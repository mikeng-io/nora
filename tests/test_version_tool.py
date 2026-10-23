"""Release tools through the real registry and immutable manifest files."""

import json

import pytest

from nora.agent.registry import ToolRegistry
from nora.infra.version import VersionManifest
from nora.obs.metrics import Metrics

STAMP = {
    "version": "v0.0.1",
    "versions": [
        {"version": "v0.0.3", "released": "2026-01-03", "commit": "abc1234",
         "notes": "Synthetic new feature.", "changes": ["feat: synthetic feature"]},
        {"version": "v0.0.2", "released": "2026-01-02", "commit": "def5678"},
        {"version": "v0.0.1", "released": "2026-01-01", "commit": "fed4321",
         "notes": "Synthetic first release.", "changes": ["feat: synthetic start"]},
    ],
}


def _tools(tmp_path, *, payload=STAMP, raw=None):
    path = tmp_path / "version.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    elif payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")
    registry = ToolRegistry(versions=VersionManifest(path))
    metrics = Metrics()
    specs = registry.specs(store=None, conversation_id="g1", conversation_type="group",
                           persons=None, honcho=None, deliverer=None, metrics=metrics, room=None)
    assert "version" not in {spec.name for spec in specs}
    return {spec.name: spec.call for spec in specs}, metrics, path


async def test_list_returns_running_build_and_metadata_in_manifest_order(tmp_path):
    tools, metrics, _ = _tools(tmp_path)
    listed = await tools["version_list"]()
    assert listed["status"] == "ok"
    assert listed["running"] == {"status": "ok", "version": "v0.0.1"}
    assert listed["releases"] == [
        {key: entry[key] for key in ("version", "released", "commit")}
        for entry in STAMP["versions"]
    ]
    assert "shipped history" in listed["note"]
    assert "after this build" in " ".join(listed["note"].split())
    assert metrics.get("nora_tool_calls_total", tool="version_list", status="ok") == 1


async def test_running_identity_and_history_do_not_change_under_a_live_process(tmp_path):
    tools, metrics, path = _tools(tmp_path)
    listed = await tools["version_list"]()
    path.write_text(json.dumps({"version": "v0.0.9", "versions": [
        {"version": "v0.0.3", "notes": "Replacement notes."},
        {"version": "v0.0.9", "notes": "New release."},
    ]}), encoding="utf-8")
    assert await tools["version_list"]() == listed
    read = await tools["version_read"](version="v0.0.3")
    assert read["notes"] == "Synthetic new feature."
    assert read["changes"] == ["feat: synthetic feature"]
    assert (await tools["version_read"](version="v0.0.9"))["status"] == "nothing_found"
    assert metrics.get("nora_tool_calls_total", tool="version_list", status="ok") == 2


@pytest.mark.parametrize("version", ["v0.0.3", "V0.0.3", " 0.0.3 "])
async def test_read_returns_selected_recorded_notes_and_changes(tmp_path, version):
    tools, metrics, _ = _tools(tmp_path)
    answer = await tools["version_read"](version=version)
    assert answer == {"status": "ok", **STAMP["versions"][0], "note": answer["note"]}
    assert "shipped release summaries" in answer["note"]
    assert "2,000" in answer["note"] and "20 subjects" in answer["note"]
    assert "not a complete live GitHub body" in answer["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="ok") == 1


async def test_recorded_version_with_empty_content_does_not_invent_notes(tmp_path):
    tools, metrics, _ = _tools(tmp_path)
    answer = await tools["version_read"](version="v0.0.2")
    assert answer["status"] == "ok" and answer["notes"] == "" and answer["changes"] == []
    assert answer["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="ok") == 1


@pytest.mark.parametrize("payload,raw", [
    (None, None), (None, "{"), ({"versions": STAMP["versions"]}, None),
])
async def test_absent_or_unreadable_manifest_is_unavailable_for_both_tools(tmp_path, payload, raw):
    tools, metrics, _ = _tools(tmp_path, payload=payload, raw=raw)
    listed = await tools["version_list"]()
    read = await tools["version_read"](version="v0.0.3")
    assert listed["status"] == read["status"] == "unavailable"
    assert "running" not in listed and "releases" not in listed
    assert "notes" not in read and "changes" not in read
    assert listed["note"] and read["note"]
    for tool in ("version_list", "version_read"):
        assert metrics.get("nora_tool_calls_total", tool=tool, status="unavailable") == 1
        assert metrics.get("nora_tool_calls_total", tool=tool, status="nothing_found") == 0


async def test_empty_history_keeps_the_running_build_known_without_inventing_an_entry(tmp_path):
    tools, metrics, _ = _tools(tmp_path, payload={"version": "v0.0.9", "versions": []})
    listed = await tools["version_list"]()
    assert listed["status"] == "ok" and listed["releases"] == []
    assert listed["running"] == {"status": "ok", "version": "v0.0.9"}
    assert metrics.get("nora_tool_calls_total", tool="version_list", status="ok") == 1
    answer = await tools["version_read"](version="v0.0.9")
    assert answer["status"] == "nothing_found" and answer["known"] == [] and answer["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="nothing_found") == 1


async def test_unrecorded_version_and_invalid_input_have_distinct_outcomes(tmp_path):
    tools, metrics, _ = _tools(tmp_path)
    absent = await tools["version_read"](version="v0.0.9")
    invalid = await tools["version_read"](version="")
    assert absent["status"] == "nothing_found" and absent["note"]
    assert absent["known"] == ["v0.0.3", "v0.0.2", "v0.0.1"]
    assert "not recorded" in absent["note"] and "may exist" in absent["note"]
    assert invalid["status"] == "unavailable" and invalid["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="nothing_found") == 1
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="unavailable") == 1


async def test_an_unstamped_registry_stays_unavailable_without_network(no_build_stamp):
    specs = ToolRegistry().specs(store=None, conversation_id="g1", conversation_type="group",
                                 persons=None, honcho=None, deliverer=None,
                                 metrics=Metrics(), room=None)
    tools = {spec.name: spec.call for spec in specs}
    listed = await tools["version_list"]()
    read = await tools["version_read"](version="v0.0.1")
    assert listed["status"] == read["status"] == "unavailable"
    assert listed["note"] and read["note"]
