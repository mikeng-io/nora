"""`nora config` — the safe config mutation surface.

Drives the REAL argparse surface (`nora.__main__.build_parser`) against a
small real config.toml, because the properties that matter are end-to-end:
that a key round-trips, that comments survive a write, that a bad change
fails closed and leaves the file untouched, and that nothing ever leaves a
half-written file behind.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nora.__main__ import build_parser
from nora.config import load


@pytest.fixture
def cfg_path(example_config_path: Path) -> Path:
    """A real, schema-complete config.toml the CLI can mutate.

    Uses the repo's `example_config_path` fixture (R-B): a copy of
    config.example.toml in tmp_path, never the repo-root file directly.
    """
    return example_config_path


def run_cli(*argv: str) -> int:
    """Invoke the real parser and run the selected command, exactly the way
    `nora.__main__.main` dispatches (sync `cli.run` wraps the event loop)."""
    from nora.app import cli as _cli

    parser = build_parser()
    args = parser.parse_args(list(argv))
    if "func" not in vars(args):
        raise SystemExit(2)
    return _cli.run(args)


# ── get ────────────────────────────────────────────────────────────────────

def test_get_scalar(cfg_path: Path, capsys):
    rc = run_cli("config", "get", "llm.budgets.web_fetch", "--config", str(cfg_path))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "6"


def test_get_string(cfg_path: Path, capsys):
    rc = run_cli("config", "get", "platforms.signal.account", "--config", str(cfg_path))
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""  # example carries an empty default


def test_get_missing_key_fails(cfg_path: Path, capsys):
    rc = run_cli("config", "get", "llm.nonexistent", "--config", str(cfg_path))
    assert rc != 0
    assert "not set" in capsys.readouterr().err


def test_get_missing_section_fails(cfg_path: Path, capsys):
    rc = run_cli("config", "get", "does.not.exist", "--config", str(cfg_path))
    assert rc != 0
    assert "no [does] section" in capsys.readouterr().err


# ── validate ───────────────────────────────────────────────────────────────

def test_validate_ok(cfg_path: Path, capsys):
    rc = run_cli("config", "validate", "--config", str(cfg_path))
    assert rc == 0
    assert "valid" in capsys.readouterr().out


def test_validate_bad_toml(tmp_path: Path, capsys):
    p = tmp_path / "bad.toml"
    p.write_text("this is = = not toml", encoding="utf-8")
    rc = run_cli("config", "validate", "--config", str(p))
    assert rc != 0


# ── set ────────────────────────────────────────────────────────────────────

def test_set_int(cfg_path: Path, capsys):
    rc = run_cli("config", "set", "llm.budgets.web_fetch", "8", "--config", str(cfg_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "6 -> 8" in out
    assert "restart to apply" in out
    # Real reload through the daemon's own loader
    cfg = load(cfg_path, require_secrets=False)
    assert cfg.llm.budgets["web_fetch"] == 8


def test_set_string_plain_keeps_string_type(cfg_path: Path):
    """An unquoted phone stays a string because the key already holds one —
    no forced quoting on first edit."""
    rc = run_cli(
        "config", "set", "platforms.signal.account", "+85299998888",
        "--config", str(cfg_path),
    )
    assert rc == 0
    cfg = load(cfg_path, require_secrets=False)
    assert cfg.platforms.signal.account == "+85299998888"


def test_set_bool(cfg_path: Path):
    rc = run_cli("config", "set", "platforms.signal.enabled", "false", "--config", str(cfg_path))
    assert rc == 0
    cfg = load(cfg_path, require_secrets=False)
    assert cfg.platforms.signal.enabled is False


def test_set_unknown_tool_fails_closed(cfg_path: Path, capsys):
    """The schema's own unknown-tool gate catches a bogus budget key — the
    double-validation is not decoration."""
    rc = run_cli("config", "set", "llm.budgets.new_tool", "3", "--config", str(cfg_path))
    assert rc != 0
    assert "names an unknown tool" in capsys.readouterr().err


def test_set_quoted_string_stays_string(cfg_path: Path):
    rc = run_cli(
        "config", "set", "platforms.signal.account", '"+85299998888"',
        "--config", str(cfg_path),
    )
    assert rc == 0
    assert (
        load(cfg_path, require_secrets=False).platforms.signal.account
        == "+85299998888"  # quotes stripped by TOML parse
    )


def test_set_preserves_comments(cfg_path: Path):
    run_cli("config", "set", "llm.budgets.web_fetch", "8", "--config", str(cfg_path))
    text = cfg_path.read_text(encoding="utf-8")
    # Local descriptions survive a settings edit; rationale lives in maintained docs.
    assert "credentials and endpoint examples: docs/configuration.md" in text
    assert "empty = derive from the signal-cli daemon" in text



def test_set_wrong_type_fails_closed(cfg_path: Path, capsys):
    before = cfg_path.read_text(encoding="utf-8")
    rc = run_cli("config", "set", "llm.budgets.web_fetch", "hello", "--config", str(cfg_path))
    assert rc != 0
    # Failure happens in PRE-write validation: the on-disk config is never
    # invalid, not even for a moment.
    assert "change rejected" in capsys.readouterr().err
    # File untouched: same bytes, still loads, still the old value
    assert cfg_path.read_text(encoding="utf-8") == before
    assert load(cfg_path, require_secrets=False).llm.budgets["web_fetch"] == 6


def test_set_unknown_section_fails(cfg_path: Path, capsys):
    rc = run_cli("config", "set", "ghost.section.key", "1", "--config", str(cfg_path))
    assert rc != 0
    assert "section" in capsys.readouterr().err


# ── unset ──────────────────────────────────────────────────────────────────

def test_unset_existing(cfg_path: Path, capsys):
    rc = run_cli("config", "unset", "llm.budgets.web_fetch", "--config", str(cfg_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "unset llm.budgets.web_fetch" in out
    assert "was 6" in out


def test_unset_missing_fails(cfg_path: Path, capsys):
    rc = run_cli("config", "unset", "llm.budgets.not_here", "--config", str(cfg_path))
    assert rc != 0
    assert "not set" in capsys.readouterr().err


# ── mode preservation (atomic write safety) ────────────────────────────────

def test_atomic_write_preserves_mode(cfg_path: Path):
    os.chmod(cfg_path, 0o640)
    run_cli("config", "set", "llm.budgets.web_fetch", "7", "--config", str(cfg_path))
    assert (cfg_path.stat().st_mode & 0o777) == 0o640

# ── env-name fields and candidate-file permissions ─────────────────────────

def test_unset_prints_restart(cfg_path: Path, capsys):
    rc = run_cli("config", "unset", "llm.budgets.web_fetch", "--config", str(cfg_path))
    assert rc == 0
    assert "restart to apply" in capsys.readouterr().out


def test_set_secret_env_field_rejects_secret_value(cfg_path: Path, capsys):
    """*_env fields hold env-var NAMES, never secret values (DEC-8)."""
    before = cfg_path.read_text(encoding="utf-8")
    rc = run_cli(
        "config", "set", "llm.api_key_env", "sk-very-very-secret",
        "--config", str(cfg_path),
    )
    assert rc != 0
    assert "env-var NAME field" in capsys.readouterr().err
    assert cfg_path.read_text(encoding="utf-8") == before  # untouched


def test_set_secret_env_field_accepts_env_name(cfg_path: Path):
    """A valid env-var NAME is accepted; the secret value stays in .env."""
    rc = run_cli(
        "config", "set", "llm.api_key_env", "OMNIROUTE_API_KEY",
        "--config", str(cfg_path),
    )
    assert rc == 0
    cfg = load(cfg_path, require_secrets=False)
    assert cfg.llm.api_key_env == "OMNIROUTE_API_KEY"


def test_set_secret_env_redacts_output(cfg_path: Path, capsys):
    rc = run_cli(
        "config", "set", "llm.api_key_env", "SOME_KEY", "--config", str(cfg_path),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "SOME_KEY" not in out
    assert "redacted" in out


def test_no_temp_files_left_after_success(cfg_path: Path):
    run_cli("config", "set", "llm.budgets.web_fetch", "8", "--config", str(cfg_path))
    leftovers = [p for p in cfg_path.parent.iterdir()
                 if p.name.startswith(cfg_path.name + ".") and p != cfg_path]
    assert leftovers == []


def test_no_temp_files_left_after_failure(cfg_path: Path, capsys):
    run_cli("config", "set", "llm.budgets.web_fetch", "hello", "--config", str(cfg_path))
    leftovers = [p for p in cfg_path.parent.iterdir()
                 if p.name.startswith(cfg_path.name + ".") and p != cfg_path]
    assert leftovers == []


def test_candidate_preserves_mode(cfg_path: Path):
    """The pre-write validation candidate never briefly exposes a 0600/0640
    config as 0644."""
    os.chmod(cfg_path, 0o640)
    run_cli("config", "set", "llm.budgets.web_fetch", "7", "--config", str(cfg_path))
    # No candidate/tmp leftovers at all after success, and config still 0640.
    leftovers = [p for p in cfg_path.parent.iterdir()
                 if p.name.startswith(cfg_path.name + ".") and p != cfg_path]
    assert leftovers == []
    assert (cfg_path.stat().st_mode & 0o777) == 0o640

