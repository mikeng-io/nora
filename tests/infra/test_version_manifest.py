"""`VersionManifest` — the baked build stamp, read fail-closed (DEC-97).

NO FAKE ANYWHERE IN THIS FILE, deliberately. The thing under test reads ONE
file, so a double would have to be a file to behave like one; every case below
writes a real manifest (or deliberately does not) and points the real reader at
it. That is also why the module has no port and no second implementation.

The load-bearing assertions are the two that separate «I can't check» from
«there is nothing» — `test_an_absent_manifest_is_unavailable_not_nothing_found`
and its malformed sibling. Collapsing those is R-27's registered trap, and here
it would have Nora state, confidently, that she has no versions at all because a
build forgot to stamp one.
"""

from __future__ import annotations

import json
from pathlib import Path

from nora.infra.version import (
    NOTHING_FOUND,
    OK,
    UNAVAILABLE,
    VersionManifest,
)

MANIFEST = {
    "version": "V3",
    "commit": "0131897",
    "built_at": "2026-08-11T02:00:00+00:00",
    "versions": [
        {"version": "V3", "released": "2026-08-11", "commit": "0131897",
         "notes": "She can tell you which version she is.",
         "changes": ["feat(version): the version tool"]},
        {"version": "V2", "released": "2026-07-01", "commit": "abc1234",
         "notes": "Reminders.", "changes": ["feat(reminders): the heartbeat"]},
        {"version": "V1", "released": "2026-06-01", "commit": "def5678",
         "notes": "", "changes": ["feat: hello"]},
    ],
}


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "version.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_running_version_is_read_off_the_stamp(tmp_path: Path):
    lookup = VersionManifest(_write(tmp_path, MANIFEST)).current()

    assert lookup.status == OK
    assert lookup.entry is not None
    assert lookup.entry.version == "V3"
    assert lookup.entry.notes == "She can tell you which version she is."
    assert lookup.known == ("V3", "V2", "V1")


def test_an_earlier_version_is_read_by_name(tmp_path: Path):
    lookup = VersionManifest(_write(tmp_path, MANIFEST)).lookup("V2")

    assert lookup.status == OK
    assert lookup.entry is not None
    assert lookup.entry.version == "V2"
    assert lookup.entry.changes == ("feat(reminders): the heartbeat",)


def test_a_version_the_manifest_does_not_hold_is_nothing_found(tmp_path: Path):
    """The manifest WAS read and genuinely has no V9. This is the one state
    that may say «there is no such version», and `known` is what turns the
    refusal into a useful answer."""
    lookup = VersionManifest(_write(tmp_path, MANIFEST)).lookup("V9")

    assert lookup.status == NOTHING_FOUND
    assert lookup.entry is None
    assert lookup.known == ("V3", "V2", "V1")


def test_an_absent_manifest_is_unavailable_not_nothing_found(tmp_path: Path):
    """R-27, and the whole reason this class exists rather than a `json.load`.

    An unstamped build has no manifest. Reporting that as `nothing_found` would
    be Nora asserting she has no versions — a false claim about herself,
    manufactured out of a wiring gap. Asserted on BOTH verbs, because a caller
    that only ever asks one of them must not be able to hit the wrong state.
    """
    manifest = VersionManifest(tmp_path / "never-stamped.json")

    assert manifest.current().status == UNAVAILABLE
    assert manifest.lookup("V2").status == UNAVAILABLE
    assert manifest.current().reason == "absent"
    # And nothing is quoted from a file that could not be read: a manifest that
    # did not load cannot be cited for what it contains either.
    assert manifest.current().known == ()


def test_a_malformed_manifest_is_unavailable_not_nothing_found(tmp_path: Path):
    """Half a file — a build interrupted mid-write, a hand edit. Same rule: the
    reader could not read it, so it says so."""
    path = tmp_path / "version.json"
    path.write_text('{"version": "V3", "versions": [', encoding="utf-8")

    lookup = VersionManifest(path).lookup("V2")

    assert lookup.status == UNAVAILABLE
    assert lookup.reason == "unreadable"


def test_a_manifest_that_names_no_running_version_is_unavailable(tmp_path: Path):
    """Well-formed JSON with entries but no `version` key. The one question the
    file exists to answer is unanswered, so quoting the rest of it would be
    describing an artifact nothing ties to this process."""
    path = _write(tmp_path, {"versions": MANIFEST["versions"]})

    assert VersionManifest(path).current().status == UNAVAILABLE
    assert VersionManifest(path).lookup("V2").status == UNAVAILABLE


def test_a_json_document_that_is_not_an_object_is_unavailable(tmp_path: Path):
    """A JSON array parses perfectly and is still not a manifest — the shape
    check, not the exception handler, is what catches this one."""
    assert VersionManifest(_write(tmp_path, ["V3"])).current().status == UNAVAILABLE


def test_a_version_recorded_with_no_notes_is_ok_with_nothing_to_say(tmp_path: Path):
    """V1 was tagged without an annotation. That is `ok` — the version exists —
    with an empty `notes`, which is what stops the announcement path inventing
    one out of the commit subjects."""
    lookup = VersionManifest(_write(tmp_path, MANIFEST)).lookup("V1")

    assert lookup.status == OK
    assert lookup.entry is not None
    assert lookup.entry.notes == ""
    assert lookup.entry.changes == ("feat: hello",)


def test_the_running_version_with_no_entry_still_answers_what_it_is(tmp_path: Path):
    """«I am V4» and «here is what was in V4» are two facts. A build stamped
    before its tag was annotated knows the first without the second, and must
    not borrow another version's contents to fill the gap."""
    lookup = VersionManifest(
        _write(tmp_path, {"version": "V4", "versions": MANIFEST["versions"]}),
    ).current()

    assert lookup.status == OK
    assert lookup.entry is not None
    assert lookup.entry.version == "V4"
    assert lookup.entry.notes == ""
    assert lookup.entry.changes == ()
    # …and asking about V4 BY NAME is nothing_found, because the manifest really
    # holds no record of its contents.
    assert VersionManifest(
        _write(tmp_path, {"version": "V4", "versions": MANIFEST["versions"]}),
    ).lookup("V4").status == NOTHING_FOUND


def test_a_version_name_is_matched_however_it_was_typed(tmp_path: Path):
    """The model is quoting a person, and «v2»/«V2»/«2» are the same version. A
    case mismatch is not a reason to tell somebody their version never existed."""
    manifest = VersionManifest(_write(tmp_path, MANIFEST))

    for typed in ("v2", "V2", " 2 ", "2"):
        assert manifest.lookup(typed).status == OK, typed


def test_a_release_version_is_matched_with_or_without_its_v(tmp_path: Path):
    """Releases are `vX.Y.Z` (DEC-314); «6.0.0» and «V6.0.0» name the same one,
    and a bare «6» is not «6.0.0»."""
    manifest = VersionManifest(_write(tmp_path, {
        "version": "v6.0.0", "versions": [{"version": "v6.0.0"}]}))

    for typed in ("v6.0.0", "V6.0.0", "6.0.0", " 6.0.0 "):
        assert manifest.lookup(typed).status == OK, typed
    assert manifest.lookup("6").status == NOTHING_FOUND


def test_the_stamp_is_read_once_and_does_not_change_under_a_live_process(
    tmp_path: Path,
):
    """The grounding property, asserted rather than asserted-about: the running
    artifact cannot change under a live process, so neither may the answer. A
    per-call read would let a manifest replaced at runtime change what she says
    about the code she is already executing."""
    path = _write(tmp_path, MANIFEST)
    manifest = VersionManifest(path)
    assert manifest.current().entry is not None
    assert manifest.current().entry.version == "V3"

    _write(tmp_path, {**MANIFEST, "version": "V99"})

    assert manifest.current().entry is not None
    assert manifest.current().entry.version == "V3"
    # A reader constructed AFTER the swap does see the new file — so the test
    # above is pinning the read-once rule, not an accidentally cached path.
    assert VersionManifest(path).current().entry.version == "V99"
