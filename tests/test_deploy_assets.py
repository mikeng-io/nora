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


def test_the_egress_script_governs_firecrawls_ssrf_surface():
    """egress.sh evolved from the bare-metal uid-owner floor into container-era
    governance (2026-08-07): it denies the Firecrawl fetch-surface containers
    the private/host ranges while keeping their own backend + the public
    internet. Pins the load-bearing shape so a revert to the port-allowlist —
    or dropping a blocked range — turns red."""
    text = (DEPLOY / "egress.sh").read_text()
    # raw/PREROUTING, not DOCKER-USER: the latter (FORWARD) never sees a
    # container→HOST packet, so it could not block firecrawl→OmniRoute on the
    # docker gateway — measured, then fixed 2026-08-07.
    assert "raw" in text and "PREROUTING" in text
    assert "firecrawl-api-1" in text
    # The host/private ranges it must deny (172.16/12 covers the docker gateway
    # → OmniRoute/Honcho; 169.254 is link-local + cloud metadata).
    for net in ("172.16.0.0/12", "169.254.0.0/16", "127.0.0.0/8", "100.64.0.0/10"):
        assert net in text, net
    # Reversible + verifiable — the governance contract.
    for sub in ("apply", "down", "verify"):
        assert sub in text


def test_the_watchdog_uses_the_300_second_threshold():
    text = (DEPLOY / "watchdog.sh").read_text()
    assert "300" in text
    assert "/health" in text


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
