from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _unit() -> str:
    return (DEPLOY / "nora.service").read_text()


def test_the_unit_runs_as_the_nora_user():
    assert "User=nora" in _unit()


def test_the_unit_restarts_always():
    assert "Restart=always" in _unit()
    assert "RestartSec=10" in _unit()


def test_the_unit_hardens_the_filesystem():
    unit = _unit()
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=" in unit
    assert "ReadWritePaths=" in unit
    assert "NoNewPrivileges=true" in unit
    assert "PrivateTmp=true" in unit


def test_the_unit_loads_the_secret_from_an_environment_file():
    unit = _unit()
    assert "EnvironmentFile=" in unit
    assert "OMNIROUTE_API_KEY=" not in unit     # the value is never in the unit


def test_the_unit_restricts_egress_to_localhost():
    unit = _unit()
    assert "IPAddressDeny=any" in unit
    assert "IPAddressAllow=localhost" in unit


def test_the_install_script_creates_a_shell_less_system_user():
    text = (DEPLOY / "install.sh").read_text()
    assert "useradd" in text
    assert "--system" in text
    assert "/usr/sbin/nologin" in text or "/bin/false" in text


def test_the_deploy_readme_documents_the_rollback():
    text = (DEPLOY / "README.md").read_text()
    assert "rollback" in text.lower()
    assert "PostgreSQL" in text
    assert "compatible with both schema and config" in text


def test_migrations_are_declared_as_package_data():
    """A versions/ directory has no __init__.py, so packages.find will not
    install it. Without this declaration the image ships without its
    migrations and Nora cannot create her own database."""
    text = (ROOT / "pyproject.toml").read_text()
    assert "infra/sql/migrations/*" in text
    assert "infra/sql/migrations/versions/*" in text


def test_the_dreamer_instructions_are_declared_as_package_data():
    """`nora/dreamer/` has no `__init__.py` — it is data, not a package — so
    `packages.find` will not install it, exactly like `migrations/versions/`
    above. Without these two lines a wheel built from this tree ships without
    the Dreamer's doctrine and objectives, and every adaptor fails at startup
    with a missing-file error nobody would trace back to packaging."""
    text = (ROOT / "pyproject.toml").read_text()
    assert "dreamer/doctrine/*.md" in text
    assert "dreamer/objectives/*.md" in text


def test_the_dreamer_tree_is_bind_mounted_read_only():
    """The hot-reload half of the same fact: the package data covers a WHEEL,
    the bind mount covers the running container. Without the mount an operator
    edits an objective on the host, sees nothing change, and concludes the
    reload does not work — when what actually happened is that the container is
    reading the copy baked into the image."""
    text = (ROOT / "docker-compose.yml").read_text()
    assert "./nora/dreamer:/app/nora/dreamer:ro" in text


def test_the_dreamer_tree_is_data_and_never_a_package():
    """An `__init__.py` here would make `nora.dreamer` importable and invite
    code into a directory the keystone puts neither in `nora/agent/` nor
    `nora/language/`. It is also what makes the bind mount safe: replacing a
    Python package under a running process is not the same as replacing text
    it re-reads."""
    assert not list((ROOT / "nora" / "dreamer").rglob("*.py"))
    assert not (ROOT / "nora" / "dreamer" / "__init__.py").exists()


def test_the_baseline_revision_is_on_disk_where_alembic_looks():
    from nora.infra.sql.migrations_runner import head_revision

    versions = ROOT / "nora" / "infra" / "sql" / "migrations" / "versions"
    assert list(versions.glob("*.py")), "no revision files"
    # Assert the Postgres-native baseline is on disk and that a head resolves —
    # without pinning a name a future migration would have to come back and edit.
    assert (versions / "0001_pg_baseline.py").exists(), "pg baseline revision missing"
    assert head_revision(), "no alembic head resolves"


def test_the_dockerfile_copies_alembic_ini():
    assert "alembic.ini" in (ROOT / "Dockerfile").read_text()


def test_compose_passes_every_search_backend_key_into_the_container():
    """DEC-157 claims flipping `[search] provider` is a CONFIG change and not a
    compose change. That is only true if BOTH backends' keys are passed.

    Compose does not inherit host `.env` values into a service: with
    `EXA_API_KEY` present in `.env`, named correctly by
    `[search.exa] api_key_env`, but never passed through compose, the container
    sees nothing — Nora logs «search enabled on provider exa but EXA_API_KEY is
    unset» once at startup and then answers `unavailable` to every `web_search`
    and `web_fetch`, forever, with a green test suite and a healthy container.

    The failure is invisible in exactly the way R-27 exists to make impossible:
    honest at the tool boundary («I couldn't check»), and silent everywhere a
    human would look.
    """
    compose = (ROOT / "docker-compose.yml").read_text()
    nora_service = compose[compose.index("\n  nora:"):]
    from nora.config import ExaBackendConfig, TavilyBackendConfig

    for backend in (TavilyBackendConfig(), ExaBackendConfig()):
        var = backend.api_key_env
        assert f"- {var}=${{{var}:-}}" in nora_service, (
            f"{var} is named by a [search.*] api_key_env but is not passed into "
            f"the nora service in docker-compose.yml. The key being in .env is "
            f"not enough — compose does not inherit host .env into a service, so "
            f"selecting that backend would answer `unavailable` on every call."
        )


def test_the_life360_token_alert_matches_the_reason_the_client_emits():
    """The alert on a dead LIFE360_AUTH_TOKEN must name the SAME
    `reason` label `Life360Client` increments: a rule on a label nobody emits is
    silent forever and looks exactly like a healthy one. Derived from the
    client's own `_alarm(...)` calls, not hand-copied. The generic
    `PresenceAuthRefused` excludes that reason, so a dead token pages once, with
    the fix in hand."""
    import re

    import yaml

    client = (ROOT / "nora" / "infra" / "life360" / "client.py").read_text()
    assert "token_rejected" in set(re.findall(r'_alarm\("([a-z_0-9]+)"\)', client))
    rules = {
        r["alert"]: r
        for g in yaml.safe_load(
            (DEPLOY / "prometheus" / "rules" / "nora.yml").read_text()
        )["groups"]
        for r in g["rules"]
    }
    token = rules["Life360TokenRejected"]
    assert 'reason="token_rejected"' in token["expr"]
    assert 'reason!="token_rejected"' in rules["PresenceAuthRefused"]["expr"]
    # The annotation is the operator's whole recovery path at 3am.
    assert "LIFE360_AUTH_TOKEN" in token["annotations"]["description"]
    assert "--force-recreate" in token["annotations"]["description"]


def test_the_presence_alert_rules_pass_their_promtool_tests():
    """`deploy/prometheus/tests/presence.test.yml` drives PresenceNotRecording
    through a restart with no fixes (fires), a sparse 30-min night with a
    restart (never fires) and a series that never existed (fires). promtool is
    not a Python dependency, so this runs wherever it is installed and skips
    elsewhere. Without it: `docker run --rm --entrypoint promtool -v
    "$PWD/deploy/prometheus:/p:ro" -w /p/tests prom/prometheus:v3.7.3 test rules
    presence.test.yml`."""
    import shutil
    import subprocess

    promtool = shutil.which("promtool")
    if promtool is None:
        pytest.skip("promtool not installed")
    result = subprocess.run(  # noqa: S603 — a fixed local binary, fixed args
        [promtool, "test", "rules", "presence.test.yml"],
        cwd=DEPLOY / "prometheus" / "tests",
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_subagent_doctrine_is_data_declared_as_package_data():
    """`nora/subagent/` has no `__init__.py` (data, not a package), so a wheel ships its
    doctrine only if pyproject declares it; the image gets it from `COPY nora/`."""
    assert "subagent/doctrine/*.md" in (ROOT / "pyproject.toml").read_text()
    assert (ROOT / "nora" / "subagent" / "doctrine" / "subagent.md").is_file()
    assert not list((ROOT / "nora" / "subagent").rglob("*.py"))
    assert not (ROOT / "nora" / "subagent" / "__init__.py").exists()


def test_signal_cli_runs_as_the_account_the_deployment_states_in_env():
    """The account is the deployment's own number: it comes from `.env`
    (`SIGNAL_ACCOUNT`), never a literal in git. A committed literal would be
    scrubbed to a pseudonym and signal-cli would come up unregistered."""
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    command = compose["services"]["signal-cli"]["command"]
    account = command[command.index("--account") + 1]
    assert account.startswith("${SIGNAL_ACCOUNT:?"), account


def test_nora_is_handed_the_pulse_base_it_reads():
    """`PulseGamesClient`'s base comes from `PULSE_API_BASE`; compose hands the
    `nora` service only the variables it lists, so it must list this one too."""
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["nora"]["environment"]
    names = {e.split("=", 1)[0] for e in env} if isinstance(env, list) else set(env)
    assert {"PULSE_API_TOKEN", "PULSE_API_BASE"} <= names


def test_stt_cpu_index_does_not_shadow_general_python_packages():
    import tomllib

    config = tomllib.loads((DEPLOY / 'stt/pyproject.toml').read_text())
    cpu = next(index for index in config['tool']['uv']['index']
               if index['name'] == 'pytorch-cpu')
    assert cpu.get('explicit') is True
