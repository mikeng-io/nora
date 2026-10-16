"""`tests/README.md` (R-A): a test directory names what makes the
test TRUE, not what it is about — `unit/` no I/O, `contract/` one suite N
implementations, `integration/` the wired pipeline, `architecture/` properties
of the source. The `tests/` ROOT is legacy and "takes no NEW test files"; the
files already there are a known, deliberately deferred backlog — moving them is
its own large diff.

This file enforces that rule: it pins exactly what is on disk in `_ALLOWED`,
generated from a real listing (not hand-typed), and fails on anything else at
that level, next to the files that are merely grandfathered.

**The list only shrinks.** Removing a file that gets relocated under `unit/`,
`contract/`, or wherever it belongs is the intended way this pin shortens over
time; adding to it defeats the gate's entire purpose.

Checked in BOTH directions, mirroring
`test_port_fakes_live_in_nora_testing.py`'s own `_ALLOWED`: a root file not in
the pin fails (a new legacy-shaped file landed), and a pinned name with no
file on disk fails too (a stale pin silently widens what the gate would allow
back in, the moment a same-named file reappears).

Positive control: a synthetic `tmp_path` "tests root" containing a file
outside a given allowlist must be flagged, and an allowlist entry naming a
file that was deleted from disk must be flagged as stale — both are asserted
against the helper functions directly, so the control exercises the same code
path the real gate below uses.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = ROOT / "tests"

# Generated from `sorted(p.name for p in Path("tests").glob("test_*.py"))`.
# This is the legacy backlog tests/README.md refers to — an inventory to burn
# down by relocating files under unit/, contract/, integration/, or
# architecture/, never a place to add a new one.
_ALLOWED: frozenset[str] = frozenset(
    {
        "test_acceptance_rules.py",
        "test_affected_tests.py",
        "test_agent_runner_stop_reason.py",
        "test_architecture_doc.py",
        "test_boundaries.py",
        "test_builders.py",
        "test_cli_control_plane.py",
        "test_composition.py",
        "test_compute_backend_port.py",
        "test_config.py",
        "test_deploy_assets.py",
        "test_discord_adapter.py",
        "test_discord_listener.py",
        "test_discord_mention_resolve.py",
        "test_discord_parser.py",
        "test_discord_react.py",
        "test_discord_roster.py",
        "test_discord_self_id.py",
        "test_discord_send.py",
        "test_docs.py",
        "test_doctrine_assembly.py",
        "test_doctrine_audiences.py",
        "test_doctrine_content.py",
        "test_doctrine_loader.py",
        "test_doctrine_reload.py",
        "test_doctrine_version_parity.py",
        "test_domain_errors.py",
        "test_env_template.py",
        "test_eval_backends.py",
        "test_evals_rubric.py",
        "test_exa.py",
        "test_factory.py",
        "test_find_parking.py",
        "test_gathering_tools.py",
        "test_gatherings_domain.py",
        "test_gatherings_service.py",
        "test_geocode.py",
        "test_giphy.py",
        "test_giphy_deploy.py",
        "test_guards_deliverer.py",
        "test_guards_hooks.py",
        "test_guards_leak.py",
        "test_guards_replyguard.py",
        "test_guards_terminal.py",
        "test_guards_tool_call_log.py",
        "test_health.py",
        "test_honcho.py",
        "test_honcho_card.py",
        "test_honcho_representation.py",
        "test_intent_router.py",
        "test_life360_client.py",
        "test_links_carry.py",
        "test_logging.py",
        "test_main.py",
        "test_media_describers.py",
        "test_media_fetch.py",
        "test_metrics_export.py",
        "test_metrics_stub.py",
        "test_migrate_media_to_rustfs.py",
        "test_migration.py",
        "test_model_per_tag.py",
        "test_models.py",
        "test_note_tools.py",
        "test_output_contracts.py",
        "test_parking_client.py",
        "test_peer_history.py",
        "test_persons.py",
        "test_platform_registry.py",
        "test_policy.py",
        "test_ports.py",
        "test_prefill.py",
        "test_presence_refresh.py",
        "test_preview_source.py",
        "test_relay.py",
        "test_remember_tool.py",
        "test_reminder_tools.py",
        "test_room_tools.py",
        "test_route_position_trust.py",
        "test_routes.py",
        "test_scaffold.py",
        "test_scripted_agent_fidelity.py",
        "test_signal_account.py",
        "test_signal_adapter.py",
        "test_signal_directory.py",
        "test_signal_listener.py",
        "test_signal_parser.py",
        "test_signal_react.py",
        "test_signal_self_id.py",
        "test_signal_send.py",
        "test_sink_sender.py",
        "test_skill_content.py",
        "test_skill_loader.py",
        "test_smoke_omniroute.py",
        "test_social_bridge_client.py",
        "test_span_skeleton.py",
        "test_strands_api_contract.py",
        "test_street_parking.py",
        "test_tag.py",
        "test_tag_debounce.py",
        "test_tavily.py",
        "test_tool_choice_force.py",
        "test_tool_contract_metadata.py",
        "test_tool_logging.py",
        "test_tool_surface_policy.py",
        "test_tools.py",
        "test_tracing.py",
        "test_transit.py",
        "test_turn_budget.py",
        "test_version_stamp_build.py",
        "test_version_tool.py",
        "test_vision.py",
        "test_weather.py",
        "test_web_fetch_tool.py",
        "test_web_gatherings.py",
        "test_whatsapp_listener.py",
        "test_whatsapp_parser.py",
        "test_whatsapp_react.py",
        "test_whatsapp_self_id.py",
        "test_whatsapp_send.py",
        "test_where_been.py",
        "test_whereis.py",
        "test_who_said.py",
    }
)


def root_test_files(tests_dir: Path) -> frozenset[str]:
    """`test_*.py` filenames directly under `tests_dir` — not `unit/`,
    `contract/`, `integration/`, `architecture/`, or any other subdirectory."""
    return {p.name for p in tests_dir.glob("test_*.py")}


def unpinned(tests_dir: Path, allowed: frozenset[str]) -> set[str]:
    """Files on disk at the root that the allowlist does not name — a new
    legacy-shaped file, or a rename of an existing one."""
    return root_test_files(tests_dir) - allowed


def stale(tests_dir: Path, allowed: frozenset[str]) -> set[str]:
    """Allowlist entries naming a file no longer on disk — moved, renamed, or
    deleted, and the pin was left behind."""
    return allowed - root_test_files(tests_dir)


def test_no_new_test_file_lands_in_the_tests_root() -> None:
    new = unpinned(TESTS_ROOT, _ALLOWED)
    assert not new, (
        "new test file(s) directly under tests/ (R-A, issue #113): "
        f"{sorted(new)}. The tests/ root is legacy and takes no NEW files — "
        "see tests/README.md's table for where this belongs (unit/ no I/O, "
        "contract/ one suite N implementations, integration/ the wired "
        "pipeline, architecture/ a property of the source)."
    )


def test_a_stale_pin_is_a_defect() -> None:
    gone = stale(TESTS_ROOT, _ALLOWED)
    assert not gone, (
        "_ALLOWED name(s) with no file on disk — the pin silently widens what "
        f"this gate would let back in under that name: {sorted(gone)}. Drop "
        "them from _ALLOWED (the list only shrinks)."
    )


def test_an_unpinned_root_file_is_flagged(tmp_path: Path) -> None:
    """Positive control: a synthetic tests/ root with one file the allowlist
    does not name must be caught."""
    (tmp_path / "test_known.py").write_text("")
    (tmp_path / "test_a_new_one_nobody_pinned.py").write_text("")
    allowed = frozenset({"test_known.py"})
    assert unpinned(tmp_path, allowed) == {"test_a_new_one_nobody_pinned.py"}
    assert stale(tmp_path, allowed) == set()


def test_a_pin_naming_a_deleted_file_is_flagged_as_stale(tmp_path: Path) -> None:
    """The other direction of the same control: an allowlist entry survives
    the file it named being deleted, which is exactly the "stale exemption is
    a claim" failure `test_port_fakes_live_in_nora_testing.py` also guards."""
    (tmp_path / "test_still_here.py").write_text("")
    allowed = frozenset({"test_still_here.py", "test_removed_long_ago.py"})
    assert stale(tmp_path, allowed) == {"test_removed_long_ago.py"}
    assert unpinned(tmp_path, allowed) == set()
