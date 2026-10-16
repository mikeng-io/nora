"""The composition root builds Nora's ONE Google identity from `[google]`
when a Google service is on and every value it names is in the environment
(DEC-8), and hands it to the shared-file source; otherwise None, which the
open tool answers `unavailable`."""

from __future__ import annotations

import logging

import pytest

from nora.app.composition import external_files_source, google_credentials
from nora.config import Config, ExternalFilesConfig
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.infra.google.auth import GoogleCredentials
from nora.obs.metrics import Metrics

ENV = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")


def _cfg(example_config, *, enabled: bool):
    return example_config.model_copy(
        update={"external_files": ExternalFilesConfig(enabled=enabled)},
    )


def test_the_template_names_one_google_identity_and_ships_files_off(example_config):
    assert (example_config.google.client_id_env, example_config.google.client_secret_env,
            example_config.google.refresh_token_env) == ENV
    assert example_config.external_files.enabled is False


def test_off_builds_nothing(example_config, monkeypatch):
    for name in ENV:
        monkeypatch.setenv(name, "sekrit-9f2")
    assert google_credentials(example_config, metrics=Metrics()) is None
    assert external_files_source(example_config, google=None) is None


def test_on_with_every_value_builds_one_identity_and_the_drive_source(
    example_config, monkeypatch,
):
    for name in ENV:
        monkeypatch.setenv(name, "sekrit-9f2")
    cfg = _cfg(example_config, enabled=True)
    google = google_credentials(cfg, metrics=Metrics())
    assert isinstance(google, GoogleCredentials)
    source = external_files_source(cfg, google=google)
    assert isinstance(source, GoogleDriveSource)


@pytest.mark.parametrize("missing", ENV)
def test_a_value_missing_builds_nothing_and_says_which(
    example_config, monkeypatch, caplog, missing,
):
    for name in ENV:
        monkeypatch.setenv(name, "sekrit-9f2")
    monkeypatch.setenv(missing, "  ")
    cfg = _cfg(example_config, enabled=True)
    with caplog.at_level(logging.WARNING, logger="nora.app.composition"):
        google = google_credentials(cfg, metrics=Metrics())
    assert google is None and external_files_source(cfg, google=google) is None
    assert missing in caplog.text and "sekrit-9f2" not in caplog.text


def test_a_stale_per_service_table_is_refused(example_config):
    """It fails naming the section that replaced it, not with a generic
    "extra inputs" error."""
    with pytest.raises(ValueError, match=r"\[google\]") as raised:
        ExternalFilesConfig.model_validate({"enabled": True, "google_drive": {}})
    assert "[external_files.google_drive]" in str(raised.value)
    with pytest.raises(ValueError, match=r"\[google\]"):
        Config.model_validate({
            **example_config.model_dump(),
            "external_files": {"enabled": False, "google_drive": {"client_id_env": "X"}},
        })
