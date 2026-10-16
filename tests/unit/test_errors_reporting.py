"""`nora.obs.errors` — Sentry wiring that is off by default and never
load-bearing.

The properties worth guarding are all about what it does NOT do: it does not
turn itself on without a DSN, it does not take the process down when the SDK is
missing or the DSN is nonsense, and it does not start a second tracing pipeline
alongside `nora.obs.tracing`.
"""

from __future__ import annotations

import logging
import sys

import pytest

from nora.obs.errors import setup_errors


def test_it_is_off_when_disabled(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://k@example.ingest.sentry.io/1")
    assert setup_errors(enabled=False, dsn_env="SENTRY_DSN") is False


def test_it_is_off_when_the_named_variable_is_unset(monkeypatch):
    """The shipped default, and the laptop case. `enabled = true` with no DSN
    is a half-finished configuration, not an error — refusing to start over it
    would make an optional instrument load-bearing.
    """
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert setup_errors(enabled=True, dsn_env="SENTRY_DSN") is False


def test_an_empty_or_whitespace_dsn_is_off_rather_than_an_init_attempt(
    monkeypatch, caplog,
):
    """A `SENTRY_DSN=` line in `.env` yields `""`, and a copy-pasted one can
    yield whitespace. Both mean "not configured".

    **Asserted on the LOG, not on the return value**, and the difference is the
    whole test. Without the `.strip()` guard a whitespace DSN still returns
    `False` — it reaches `sentry_sdk.init`, which rejects it — so a
    return-value assertion passes either way and guards nothing. What changes
    is which STATE the operator is told about: "not configured" (INFO,
    expected) versus "init failed" (WARNING, reads like a bug in a deployment
    that simply has a blank line in its dotenv)."""
    monkeypatch.setenv("SENTRY_DSN", "   ")
    with caplog.at_level(logging.INFO, logger="nora.errors"):
        assert setup_errors(enabled=True, dsn_env="SENTRY_DSN") is False
    assert any("is unset" in r.message for r in caplog.records), (
        "a whitespace DSN was passed to sentry_sdk.init instead of being read "
        f"as 'not configured': {[r.message for r in caplog.records]}"
    )


def test_a_missing_sdk_costs_error_reporting_and_nothing_else(monkeypatch):
    """`sentry` is an optional extra. A deployment without it must run.

    Simulated by making the import fail, which is what an uninstalled package
    does — not by checking a flag, which would test the test.
    """
    monkeypatch.setenv("SENTRY_DSN", "https://k@example.ingest.sentry.io/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert setup_errors(enabled=True, dsn_env="SENTRY_DSN") is False


def test_a_broken_dsn_does_not_take_the_process_down(monkeypatch):
    """`sentry_sdk.init` raises on a malformed DSN. A typo in `.env` must cost
    error reporting, never the deployment — the same posture `setup_tracing`
    holds for a bad endpoint.
    """
    pytest.importorskip("sentry_sdk")
    monkeypatch.setenv("SENTRY_DSN", "not-a-dsn")
    assert setup_errors(enabled=True, dsn_env="SENTRY_DSN") is False


def test_it_reports_errors_without_starting_a_second_tracing_pipeline(monkeypatch):
    """THE ONE THAT MATTERS FOR THE OTHER INSTRUMENTS.

    `sentry_sdk` will instrument spans if asked, and this process already has a
    tracing pipeline whose traces are the provenance every claim about a turn
    re-derives from (`docs/observability.md`). Two pipelines would write two
    incompatible trace ids for one turn — a trace that exists twice,
    differently, is worse than one that exists once.

    Asserted on what is PASSED TO `init`, because the property is a
    configuration choice and there is no public accessor for it afterwards; the
    control below proves the call actually happened.
    """
    sentry_sdk = pytest.importorskip("sentry_sdk")
    captured: dict[str, object] = {}

    def fake_init(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    monkeypatch.setenv("SENTRY_DSN", "https://k@example.ingest.sentry.io/1")

    assert setup_errors(
        enabled=True, dsn_env="SENTRY_DSN", environment="test-env",
    ) is True
    # CONTROL: init was reached at all.
    assert captured, "sentry_sdk.init was never called"
    assert captured["traces_sample_rate"] == 0.0, (
        "Sentry is sampling traces — a second tracing pipeline alongside "
        "nora.obs.tracing writes two trace ids for one turn"
    )
    assert captured["include_local_variables"] is False, (
        "a frame's local variable VALUES ship regardless of send_default_pii "
        "(verified against sentry-sdk's own default) — an ACI or a phone "
        "number sitting in a local is the same identifier R-18 keeps off "
        "every other exit"
    )
    assert captured["send_default_pii"] is False, (
        "send_default_pii would put broader request/user PII on a "
        "third-party wire — the same class R-18 keeps off every other exit"
    )
    assert captured["environment"] == "test-env"
    assert captured["auto_enabling_integrations"] is False, (
        "the SDK will scan for installed packages and wire a framework "
        "integration for each — one of which raised ImportError during init "
        "on the deployment (starlette/jinja2), taking the whole reporter with "
        "it and leaving error reporting silently off"
    )
    # The logging integration is what turns this codebase's `log.exception`
    # sites into issues; without it Sentry would only see what escapes to the
    # top, which in a codebase that catches broadly is almost nothing.
    integrations = captured["integrations"]
    assert any(
        type(i).__name__ == "LoggingIntegration" for i in integrations  # type: ignore[union-attr]
    ), "no LoggingIntegration — log.exception sites would not become issues"


def test_a_logged_exception_is_the_shape_the_integration_captures():
    """The `log.exception` sites are `ERROR` records carrying `exc_info`.

    Not a test of Sentry — a test of the ASSUMPTION this module's docstring
    makes about the codebase, which is what would silently stop being true."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    log = logging.getLogger("nora.test.errors")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        try:
            raise TimeoutError("boom")
        except TimeoutError:
            log.exception("something failed")
    finally:
        log.removeHandler(handler)

    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
