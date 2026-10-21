"""Synthetic rejection tests at the private TOML and startup boundaries."""

import argparse
import asyncio
import traceback

import pytest
import tomlkit

from nora.config import PRIVATE_ENV_FIELDS, ConfigError, load
from nora.persons import PersonsError, load_persons
from nora.policy import PolicyError, load_policy
from tests.unit.test_config_private_env import source_refs

SENTINEL = "SYNTHETIC_PRIVATE_REJECTED"


@pytest.mark.parametrize("entrypoint", ["daemon", "presence", "analytics"])
@pytest.mark.parametrize("failure", ["schema", "duplicate", "toml"])
def test_identity_entrypoints(
    example_config_path,
    monkeypatch,
    capsys,
    caplog,
    entrypoint,
    failure,
):
    from nora.analytics.composition import build_rollup
    from nora.app.composition import build_dependencies
    from nora.presence.composition import build_collector

    identity = example_config_path.parent / "identity.toml"
    texts = {
        "schema": f'[persons.{SENTINEL}]\n{SENTINEL} = "{SENTINEL}"',
        "duplicate": (
            f'[[persons.{SENTINEL}.signal]]\nphone = "{SENTINEL}"\n'
            f'[[persons.other.signal]]\nphone = "{SENTINEL}"\n'
        ),
        "toml": f'bad = "{SENTINEL}\\q"',
    }
    identity.write_text(texts[failure])
    cfg = load(example_config_path, require_secrets=False)
    cfg = cfg.model_copy(
        update={
            "identity_file": identity,
            "presence": cfg.presence.model_copy(update={"enabled": True}),
            "analytics": cfg.analytics.model_copy(update={"enabled": True}),
        }
    )
    with pytest.raises(Exception) as error:
        if entrypoint == "daemon":
            asyncio.run(build_dependencies(cfg))
        else:
            {"presence": build_collector, "analytics": build_rollup}[entrypoint](cfg)
    output = "".join(traceback.format_exception(error.value))
    assert str(identity) in output
    assert SENTINEL not in output + caplog.text + str(capsys.readouterr())
    remedies = {"schema": "remove", "duplicate": "correct the id", "toml": "repair TOML"}
    assert remedies[failure] in output
    if failure == "duplicate":
        assert "persons[0]" in output and "persons[1]" in output


@pytest.mark.parametrize("entrypoint", ["presence", "analytics"])
def test_missing_circle_entrypoints(example_config_path, entrypoint):
    from nora.analytics.composition import build_rollup
    from nora.presence.composition import build_collector

    identity = example_config_path.parent / "identity.toml"
    identity.write_text(f'[[persons.{SENTINEL}.life360]]\nmember_id = "{SENTINEL}"\n')
    cfg = load(example_config_path, require_secrets=False)
    cfg = cfg.model_copy(
        update={
            "identity_file": identity,
            "presence": cfg.presence.model_copy(update={"enabled": True}),
            "analytics": cfg.analytics.model_copy(update={"enabled": True}),
        }
    )
    with pytest.raises(Exception) as error:
        {"presence": build_collector, "analytics": build_rollup}[entrypoint](cfg)
    output = "".join(traceback.format_exception(error.value))
    assert SENTINEL not in output
    assert str(identity) in output
    assert "memberships [0]" in output
    assert "add circle_id or remove the block" in output


@pytest.mark.parametrize(
    "loader,error", [(load_policy, PolicyError), (load_persons, PersonsError), (load, ConfigError)]
)
def test_file_diagnostics(tmp_path, loader, error):
    path = tmp_path / "settings.toml"
    for state in ("not_found", "unreadable"):
        if state == "unreadable":
            path.mkdir()
        with pytest.raises(error) as caught:
            loader(path)
        assert str(path) in str(caught.value)
        assert state in str(caught.value)
        assert ("create" if state == "not_found" else "permissions") in str(caught.value)


@pytest.mark.parametrize(
    "failure,coordinate,remedy",
    [
        ("label", "conversations[0].label", "Add label"),
        ("duplicate", "conversations[1].id", "remove one or correct the id"),
        ("home", "conversations[1].home_room", "mark one"),
    ],
)
def test_policy_coordinates(tmp_path, failure, coordinate, remedy):
    path = tmp_path / "policy.toml"
    first = f'[[conversations]]\nplatform = "signal"\nid = "{SENTINEL}"\n'
    if failure != "label":
        first += f'label = "{SENTINEL}"\nhome_room = true\n'
        first += (
            f'[[conversations]]\nplatform = "telegram"\nid = "{SENTINEL}"\n'
            f'label = "{SENTINEL}"\nhome_room = true\n'
        )
    if failure == "duplicate":
        first = first.replace('platform = "telegram"', 'platform = "signal"')
    path.write_text(first)
    with pytest.raises(PolicyError) as caught:
        load_policy(path)
    message = str(caught.value)
    assert str(path) in message and coordinate in message and remedy in message
    assert SENTINEL not in message
    if failure != "label":
        assert "conversations[0]" in message


@pytest.mark.parametrize(
    "section,settings,coordinate,remedy",
    [
        (
            "computer",
            {"run_timeout_seconds": 999, "max_run_timeout_seconds": 10},
            "computer",
            "must not exceed max_run_timeout_seconds",
        ),
        ("agent", {"handles": {SENTINEL: SENTINEL}}, "agent", "lowercase network key"),
        (
            "documents",
            {"public_url": "https://example.invalid/" + SENTINEL},
            "documents.public_url",
            "must be an origin",
        ),
    ],
)
def test_config_remedies(example_config_path, section, settings, coordinate, remedy):
    doc = tomlkit.parse(example_config_path.read_text())
    if section == "agent":
        doc[section].pop("handles_env", None)
    if section == "documents":
        doc[section].pop("public_url_env", None)
    doc[section].update(settings)
    example_config_path.write_text(tomlkit.dumps(doc))
    with pytest.raises(ConfigError) as caught:
        load(example_config_path, require_secrets=False)
    message = str(caught.value)
    assert str(example_config_path) in message and coordinate in message and remedy in message
    assert SENTINEL not in message


def literal_source(path):
    from tests.builders import PRIVATE_CONFIG_ENV

    doc = source_refs(path)
    doc["agent"].pop("handles_env")
    doc["agent"]["handles"] = {}
    for field, name in PRIVATE_ENV_FIELDS.items():
        node = doc
        parts = field.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdecimal() else node[part]
        node.pop(parts[-1] + "_env", None)
        node[parts[-1]] = PRIVATE_CONFIG_ENV[name]
    path.write_text(tomlkit.dumps(doc))
    return doc


@pytest.mark.parametrize("command", ["get", "set", "unset"])
@pytest.mark.parametrize("field", PRIVATE_ENV_FIELDS)
def test_cli_private_literals(example_config_path, capsys, command, field):
    from nora.app import config_cli
    from nora.app.cli import CliError

    doc = literal_source(example_config_path)
    # Every leaf uses a synthetically valid private literal, including numeric guilds.
    parts = field.split(".")
    node = doc
    for part in parts[:-1]:
        node = node[int(part)] if part.isdecimal() else node[part]
    literal = str(node[parts[-1]])
    if command == "unset":
        # Unsetting a required account may correctly reject the change; its
        # diagnostic must still be safe. Disable mirrors before dropping channels.
        doc["participation"]["shadow"] = False
        doc["relevance_gate"]["enabled"] = False
        doc["platforms"]["signal"]["enabled"] = False
        example_config_path.write_text(tomlkit.dumps(doc))
    args = argparse.Namespace(config=example_config_path, key=field, value=literal)
    try:
        getattr(config_cli, "_cmd_" + command)(args)
    except CliError as exc:
        assert isinstance(exc, CliError) and command == "unset"
        assert literal not in str(exc)
        assert "required" in str(exc) or "empty endpoint" in str(exc)
        return
    output = capsys.readouterr().out
    assert literal not in output
    assert "redacted" in output


def test_cli_ref_names_visible(example_config_path, capsys):
    from nora.app.config_cli import _cmd_get

    source_refs(example_config_path)
    _cmd_get(argparse.Namespace(config=example_config_path, key="doctrine.canary_env"))
    assert "NORA_DOCTRINE_CANARY" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["kind", "label", "former_live", "former_duplicate"])
def test_identity_semantic_remedies(tmp_path, failure):
    path = tmp_path / "identity.toml"
    texts = {
        "kind": f'[[persons.{SENTINEL}.signal]]\n{SENTINEL} = "{SENTINEL}"\n',
        "label": (
            f'[[persons.{SENTINEL}.signal]]\nphone = "first"\nlabel = "{SENTINEL}"\n'
            f'[[persons.{SENTINEL}.signal]]\nphone = "second"\nlabel = "{SENTINEL}"\n'
        ),
        "former_live": (f'[persons.{SENTINEL}]\nformer_person_ids = ["other"]\n[persons.other]\n'),
        "former_duplicate": (
            f'[persons.{SENTINEL}]\nformer_person_ids = ["{SENTINEL}_former"]\n'
            f'[persons.other]\nformer_person_ids = ["{SENTINEL}_former"]\n'
        ),
    }
    path.write_text(texts[failure])
    with pytest.raises(PersonsError) as caught:
        load_persons(path)
    message = "".join(traceback.format_exception(caught.value))
    assert str(path) in message and "persons[0]" in message
    assert SENTINEL not in message
    assert {
        "kind": "expected one of",
        "label": "distinct name",
        "former_live": "remove one or the other",
        "former_duplicate": "remove the duplicate",
    }[failure] in message
