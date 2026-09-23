from pathlib import Path

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


# test_the_egress_script_governs_firecrawls_ssrf_surface REMOVED (DEC-73,
# 2026-08-08): egress.sh + the systemd units are gone with Firecrawl — Tavily
# searches from its own servers, so there is no self-hosted fetcher to cage.


# test_the_watchdog_uses_the_300_second_threshold REMOVED (2026-09-09): the
# shell watchdog is deleted. Its one condition — SSE silence — is the
# `PlatformSilent` rule in `deploy/prometheus/rules/nora.yml`, at 900s rather
# than 300s so a quiet night does not cry wolf, and with a delivery path the
# script never had (its alert channel was `echo >&2`). What now guards the
# threshold is `promtool check rules` plus the rule file itself; what guards
# the delivery is `tests/unit/test_alert_bridge.py`.


def test_the_install_script_creates_a_shell_less_system_user():
    text = (DEPLOY / "install.sh").read_text()
    assert "useradd" in text
    assert "--system" in text
    assert "/usr/sbin/nologin" in text or "/bin/false" in text


def test_the_deploy_readme_documents_the_rollback():
    text = (DEPLOY / "README.md").read_text()
    assert "rollback" in text.lower()
    assert "messages.db" in text


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
    code into a directory the keystone's §5 puts neither in `nora/agent/` nor
    `nora/language/`. It is also what makes the bind mount safe: replacing a
    Python package under a running process is not the same as replacing text
    it re-reads."""
    assert not list((ROOT / "nora" / "dreamer").rglob("*.py"))
    assert not (ROOT / "nora" / "dreamer" / "__init__.py").exists()


def test_the_baseline_revision_is_on_disk_where_alembic_looks():
    from nora.infra.sql.migrations_runner import head_revision

    versions = ROOT / "nora" / "infra" / "sql" / "migrations" / "versions"
    assert list(versions.glob("*.py")), "no revision files"
    # The SQLite chain (0001_baseline … 0004_media_json) was squashed to a single
    # Postgres-native baseline (design/20 §2). Assert that baseline is on disk and
    # that a head resolves — without pinning a name a future migration would have
    # to come back and edit.
    assert (versions / "0001_pg_baseline.py").exists(), "pg baseline revision missing"
    assert head_revision(), "no alembic head resolves"


def test_the_dockerfile_copies_alembic_ini():
    assert "alembic.ini" in (ROOT / "Dockerfile").read_text()


def test_compose_passes_every_search_backend_key_into_the_container():
    """DEC-157 claims flipping `[search] provider` is a CONFIG change and not a
    compose change. That is only true if BOTH backends' keys are passed.

    This is a gate earned the hard way, 2026-09-06: the Exa switch was one step
    from deploying with `EXA_API_KEY` present in `.env`, named correctly by
    `[search.exa] api_key_env`, and never passed through compose. Compose does
    not inherit host `.env` values into a service, so the container would have
    seen nothing — Nora logs «search enabled on provider exa but EXA_API_KEY is
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
    """The alert on a dead LIFE360_AUTH_TOKEN (2026-09-24) must name the SAME
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
