"""The `version` tool's three states, at the tool boundary (DEC-97).

Built through the real `ToolRegistry` rather than by calling `build_tool_specs`
directly, because the wiring is half the behaviour under test: a registry that
forgot to thread its manifest would leave `version` answering `unavailable`
forever, and a test that constructed the closure by hand could not see it.

The manifest is a real file every time — see `tests/infra/test_version_manifest.py`
for why this module has no double for it.
"""

from __future__ import annotations

import json
from pathlib import Path

from nora.agent.registry import ToolRegistry
from nora.infra.version import VersionManifest
from nora.obs.metrics import Metrics

STAMP = {
    "version": "V3",
    "versions": [
        {"version": "V3", "released": "2026-08-11", "notes": "The version tool.",
         "changes": ["feat(version): a baked stamp"]},
        {"version": "V2", "released": "2026-07-01", "notes": "Reminders.",
         "changes": ["feat(reminders): the heartbeat", "fix(fire): the header"]},
    ],
}


def _version_tool(registry: ToolRegistry, metrics: Metrics):
    specs = registry.specs(
        store=None, conversation_id="g1", conversation_type="group",
        persons=None, honcho=None, deliverer=None, metrics=metrics, room=None,
    )
    return next(spec.call for spec in specs if spec.name == "version")


def _stamped(tmp_path: Path, payload=None) -> ToolRegistry:
    path = tmp_path / "version.json"
    path.write_text(json.dumps(payload if payload is not None else STAMP),
                    encoding="utf-8")
    return ToolRegistry(versions=VersionManifest(path))


async def test_she_reads_the_version_she_is_running(tmp_path: Path):
    metrics = Metrics()

    answer = await _version_tool(_stamped(tmp_path), metrics)()

    assert answer["status"] == "ok"
    assert answer["version"] == "V3"
    assert answer["notes"] == "The version tool."
    assert answer["known"] == ["V3", "V2"]
    assert metrics.get("nora_tool_calls_total", tool="version", status="ok") == 1


async def test_she_reads_what_was_in_an_earlier_version(tmp_path: Path):
    answer = await _version_tool(_stamped(tmp_path), Metrics())(which="V2")

    assert answer["status"] == "ok"
    assert answer["version"] == "V2"
    assert answer["changes"] == [
        "feat(reminders): the heartbeat", "fix(fire): the header",
    ]


async def test_a_version_that_never_existed_is_nothing_found(tmp_path: Path):
    """The manifest WAS read and has no V9. `known` is what makes the refusal
    useful — «there is no V9, there is V3 and V2» beats «no»."""
    metrics = Metrics()

    answer = await _version_tool(_stamped(tmp_path), metrics)(which="V9")

    assert answer["status"] == "nothing_found"
    assert answer["known"] == ["V3", "V2"]
    assert "version" not in answer          # nothing invented to name
    assert metrics.get(
        "nora_tool_calls_total", tool="version", status="nothing_found") == 1


async def test_an_unstamped_build_is_unavailable_not_nothing_found(tmp_path: Path):
    """R-27 at the tool boundary, on BOTH shapes of the call.

    This is the assertion that stops the wiring gap becoming a false claim: a
    build with no stamp must have her say she cannot check, never that she has
    no versions. The `note` is what tells the model which of the two to say.
    """
    metrics = Metrics()
    registry = ToolRegistry(versions=VersionManifest(tmp_path / "unstamped.json"))
    tool = _version_tool(registry, metrics)

    current = await tool()
    named = await tool(which="V2")

    assert current["status"] == "unavailable"
    assert named["status"] == "unavailable"
    assert "known" not in current           # a file that did not load is not quoted
    assert "can't read my own build stamp" in str(current["note"])
    assert metrics.get(
        "nora_tool_calls_total", tool="version", status="unavailable") == 2
    assert metrics.get(
        "nora_tool_calls_total", tool="version", status="nothing_found") == 0


async def test_a_registry_with_no_manifest_wired_is_unavailable(no_build_stamp):
    """`ToolRegistry(versions=…)` default-constructs a manifest at the baked
    path, which in a source checkout is absent — so the honest answer here is
    the same wiring-gap `unavailable`, never a guess and never a `nothing_found`.

    `no_build_stamp` FORCES that absence rather than trusting the checkout: a
    real build leaves a gitignored `nora/version.json` behind, and without the
    fixture this test silently starts exercising the stamped path.
    """
    answer = await _version_tool(ToolRegistry(), Metrics())()

    assert answer["status"] == "unavailable"


async def test_a_version_with_no_note_recorded_says_the_version_and_no_more(
    tmp_path: Path,
):
    """`ok` with the version and nothing else: the manifest recorded no note and
    no subjects, so the payload carries neither. An empty string in `notes`
    would invite the model to fill it in."""
    registry = _stamped(tmp_path, {
        "version": "V1", "versions": [{"version": "V1"}],
    })

    answer = await _version_tool(registry, Metrics())()

    assert answer["status"] == "ok"
    assert answer["version"] == "V1"
    assert "notes" not in answer
    assert "changes" not in answer


# ---- ADR 0015 §3: how to report a status rides in the result ----------------


async def test_every_non_ok_status_carries_a_note(tmp_path: Path, no_build_stamp):
    """The description only NAMES `nothing_found` and `unavailable`; what to
    say for each is the result's `note`, on every path that returns one."""
    unstamped = ToolRegistry(versions=VersionManifest(tmp_path / "unstamped.json"))
    cases = {
        "nothing_found/no_such": await _version_tool(_stamped(tmp_path), Metrics())(
            which="V9"),
        "unavailable/unstamped": await _version_tool(unstamped, Metrics())(),
        "unavailable/not_wired": await _version_tool(ToolRegistry(), Metrics())(),
    }
    for case, answer in cases.items():
        assert answer["status"] == case.split("/")[0], (case, answer)
        assert isinstance(answer.get("note"), str) and answer["note"].strip(), case
        assert "—" not in answer["note"] and "–" not in answer["note"], case


async def test_an_ok_note_follows_whether_notes_were_recorded(tmp_path: Path):
    """The ok-reporting rules that left the description: paraphrase `notes`
    when there are some, and say none were recorded when there are not. Each
    clause arrives only with the case it is about."""
    noted = await _version_tool(_stamped(tmp_path), Metrics())()
    assert "own words" in noted["note"]
    assert "No notes" not in noted["note"]

    bare = await _version_tool(_stamped(tmp_path, {
        "version": "V1", "versions": [{"version": "V1"}],
    }), Metrics())()
    assert "No notes" in bare["note"]
    assert "own words" not in bare["note"]
