"""The composition root builds the shared-file source only when
`[external_files]` is on AND every credential it names is in the environment
(design/external-files/00-design.md §4, DEC-8); otherwise None, which the
open tool answers `unavailable`."""

from __future__ import annotations

import logging

import pytest

from nora.app.composition import external_files_source
from nora.config import ExternalFilesConfig
from nora.infra.external_files.google_drive import GoogleDriveSource
from nora.obs.metrics import Metrics

ENV = ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN")


def _cfg(example_config, *, enabled: bool):
    return example_config.model_copy(
        update={"external_files": ExternalFilesConfig(enabled=enabled)},
    )


def test_the_template_ships_it_off_and_off_builds_nothing(example_config, monkeypatch):
    assert example_config.external_files.enabled is False
    for name in ENV:
        monkeypatch.setenv(name, "sekrit-9f2")
    assert external_files_source(example_config, metrics=Metrics()) is None


def test_on_with_every_credential_builds_the_drive_source(example_config, monkeypatch):
    for name in ENV:
        monkeypatch.setenv(name, "sekrit-9f2")
    source = external_files_source(_cfg(example_config, enabled=True), metrics=Metrics())
    assert isinstance(source, GoogleDriveSource)


@pytest.mark.parametrize("missing", ENV)
def test_on_with_a_credential_missing_builds_nothing_and_says_which(
    example_config, monkeypatch, caplog, missing,
):
    for name in ENV:
        monkeypatch.setenv(name, "sekrit-9f2")
    monkeypatch.setenv(missing, "  ")
    with caplog.at_level(logging.WARNING, logger="nora.app.composition"):
        got = external_files_source(_cfg(example_config, enabled=True), metrics=Metrics())
    assert got is None
    assert missing in caplog.text and "sekrit-9f2" not in caplog.text
